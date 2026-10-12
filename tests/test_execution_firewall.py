"""Opt-in real nft/kernel regression, confined to a fresh network namespace.

Run with OAK_FIREWALL_TESTS=1. Needs nft/ip/unshare and root or noninteractive
sudo solely to create the isolated namespace. No host rules or routes change.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_executor import SPEC, setup_module


def namespace_probe():
    subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True, capture_output=True)
    rules = setup_module().firewall(SPEC)
    legacy = rules.replace(
        '  meta skuid 1900 jump executor\n }\n chain executor {',
        '  meta skuid != 1900 return', 1)
    assert rules != legacy

    def load(text):
        subprocess.run(['nft', '-f', '-'], input=text, text=True, check=True, capture_output=True)

    def counters():
        value = subprocess.run(['nft', '-j', 'list', 'table', 'inet', 'fixture'],
                               capture_output=True, text=True, check=True)
        return {item['counter']['name']: item['counter']['packets']
                for item in json.loads(value.stdout)['nftables'] if 'counter' in item}

    def kernel_response():
        # Sending to a closed local UDP port generates an ICMP error inside
        # the kernel, with no socket UID. Confirm missing metadata with two
        # actual nft counters rather than assuming the packet's provenance.
        before = counters()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(.25)
            client.connect(('127.0.0.1', 29999))
            client.send(b'fixture')
            try:
                client.recv(32)
                result = 'unexpected_data'
            except ConnectionRefusedError:
                result = 'icmp_received'
            except TimeoutError:
                result = 'icmp_missing'
        after = counters()
        return {'result': result, 'all_icmp': after['all_icmp'] - before['all_icmp'],
                'owned_icmp': after['owned_icmp'] - before['owned_icmp']}

    load('''table inet fixture {
 counter all_icmp {}
 counter owned_icmp {}
 chain observe {
  type filter hook output priority -20; policy accept;
  ip protocol icmp counter name all_icmp
  ip protocol icmp meta skuid >= 0 counter name owned_icmp
 }
}
''')
    load(legacy)
    negative_guard = kernel_response()
    load(rules)
    positive_guard = kernel_response()

    def udp_delivery(uid, address, port=0):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
            server.bind((address, port))
            server.settimeout(.25)
            port = server.getsockname()[1]
            child = '''import socket,sys
with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
 try:
  sock.sendto(b"fixture",(sys.argv[1],int(sys.argv[2])))
 except OSError:
  pass
'''
            subprocess.run([sys.executable, '-c', child, address, str(port)],
                           user=uid, group=uid, check=True, capture_output=True, timeout=3)
            try:
                return server.recv(32) == b'fixture'
            except TimeoutError:
                return False

    return {'negative_guard': negative_guard, 'positive_guard': positive_guard,
            'executor_private_allowed': udp_delivery(1900, '127.0.0.1'),
            'other_uid_private_allowed': udp_delivery(1902, '127.0.0.1'),
            'executor_dns_allowed': udp_delivery(1900, '127.0.0.53', 53)}


@unittest.skipUnless(os.environ.get('OAK_FIREWALL_TESTS') == '1', 'opt-in isolated nft/kernel fixture')
class FirewallKernelTests(unittest.TestCase):
    def test_missing_socket_uid_bypasses_only_positive_dispatch(self):
        for command in ('nft', 'ip', 'unshare'):
            if not shutil.which(command):
                self.skipTest(command + ' unavailable')
        prefix = [] if os.geteuid() == 0 else ['sudo', '-n']
        result = subprocess.run([*prefix, 'unshare', '--net', sys.executable,
                                 str(Path(__file__).resolve()), '--namespace-probe'],
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        proof = json.loads(result.stdout)
        self.assertEqual(proof['negative_guard'], {
            'result': 'icmp_missing', 'all_icmp': 1, 'owned_icmp': 0})
        self.assertEqual(proof['positive_guard'], {
            'result': 'icmp_received', 'all_icmp': 1, 'owned_icmp': 0})
        self.assertFalse(proof['executor_private_allowed'])
        self.assertTrue(proof['other_uid_private_allowed'])
        self.assertTrue(proof['executor_dns_allowed'])


if __name__ == '__main__':
    if sys.argv[1:] == ['--namespace-probe']:
        print(json.dumps(namespace_probe(), sort_keys=True))
    else:
        unittest.main()
