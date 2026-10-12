import asyncio
import base64
from dataclasses import replace
import json
import socket
import ssl
import subprocess
import tempfile
import os
import time
from pathlib import Path
from urllib.parse import urlsplit
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from oak.website_navigation_transport import LeaseConnectProxy, Limits, Denied, resolve_public_pin
from oak.website_navigation_transport import WebsiteNavigationTransport, NavigationRequest

HELLO = b'\x16\x03\x01\x00\x04\x01'
HOST = 'fixture.example.test'
PIN = '8.8.8.8'


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.received, self.connects, self.servers, self.proxies, self.clients = [], [], [], [], []
        self.upstream_tasks = set()
        async def echo(reader, writer):
            task = asyncio.current_task(); self.upstream_tasks.add(task)
            try:
                while data := await reader.read(4096):
                    self.received.append(data); writer.write(data); await writer.drain()
            finally:
                writer.close(); await writer.wait_closed(); self.upstream_tasks.discard(task)
        self.server = await asyncio.start_server(echo, '127.0.0.1', 0)
        self.servers.append(self.server)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        for proxy in self.proxies: await proxy.close()
        for writer in self.clients:
            writer.close()
            try: await writer.wait_closed()
            except Exception: pass
        for server in self.servers: server.close(); await server.wait_closed()
        for task in tuple(self.upstream_tasks): task.cancel()
        await asyncio.gather(*self.upstream_tasks, return_exceptions=True)

    async def proxy(self, *, limits=None, factory=None):
        async def upstream(address, port):
            self.connects.append((address, port))
            return await asyncio.open_connection('127.0.0.1', self.port)
        proxy = LeaseConnectProxy(limits=limits, upstream_factory=factory or upstream)
        self.proxies.append(proxy); proxy.register(HOST, PIN); await proxy.start()
        return proxy

    async def connect(self, proxy, *, host=HOST, authority=None, auth=True, extra=b'', raw=None):
        settings=proxy.browser_proxy(); endpoint=urlsplit(settings['server'])
        reader,writer=await asyncio.open_connection(endpoint.hostname,endpoint.port)
        self.clients.append(writer)
        authority=authority or host+':443'
        token=base64.b64encode((settings['username']+':'+settings['password']).encode())
        request=(b'CONNECT '+authority.encode()+b' HTTP/1.1\r\nHost: '+authority.encode()+b'\r\n'
                 +(b'Proxy-Authorization: Basic '+token+b'\r\n' if auth else b'')+extra+b'\r\n')
        writer.write(raw if raw is not None else request); await writer.drain()
        try: response=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),1)
        except (asyncio.IncompleteReadError,ConnectionError): response=b''
        return reader,writer,response

    async def test_authenticated_tls_bytes_pass_without_connect_headers(self):
        proxy=await self.proxy(); reader,writer,response=await self.connect(proxy)
        self.assertTrue(response.startswith(b'HTTP/1.1 200'))
        payload=HELLO+b'opaque-tls-test-data'; writer.write(payload); await writer.drain()
        self.assertEqual(await reader.readexactly(len(payload)),payload)
        self.assertEqual(b''.join(self.received),payload)
        self.assertEqual(self.connects,[(PIN,443)])
        settings=proxy.browser_proxy(); report=json.dumps(proxy.diagnostics())+repr(proxy)
        self.assertTrue(settings['password'] not in report and settings['username'] not in report)

    async def test_missing_wrong_and_other_lease_auth_never_connect(self):
        proxy=await self.proxy()
        _,_,response=await self.connect(proxy,auth=False)
        self.assertTrue(response.startswith(b'HTTP/1.1 407'))
        other=await self.proxy(); settings=other.browser_proxy()
        token=base64.b64encode((settings['username']+':'+settings['password']).encode())
        _,_,response=await self.connect(proxy,auth=False,extra=b'Proxy-Authorization: Basic '+token+b'\r\n')
        self.assertTrue(response.startswith(b'HTTP/1.1 407'))
        self.assertFalse(self.connects)

    async def test_unknown_subdomain_port_ip_and_http_forward_denied(self):
        proxy=await self.proxy()
        for authority in ('other.example.test:443','sub.'+HOST+':443',HOST+':80','127.0.0.1:443','[::1]:443',HOST+':0443'):
            _,_,response=await self.connect(proxy,authority=authority)
            self.assertTrue(response.startswith(b'HTTP/1.1 403'))
        _,_,response=await self.connect(proxy,raw=b'GET http://fixture.example.test/ HTTP/1.1\r\nHost: fixture.example.test\r\n\r\n')
        self.assertTrue(response.startswith(b'HTTP/1.1 405'))
        self.assertFalse(self.connects)

    async def test_body_framing_duplicate_header_and_authority_mismatch_denied(self):
        proxy=await self.proxy()
        for extra in (b'Content-Length: 0\r\n',b'Transfer-Encoding: chunked\r\n',
                      b'Host: other.example.test:443\r\n',b' Cookie: x\r\n',b'Authorization: x\r\n'):
            _,_,response=await self.connect(proxy,extra=extra)
            self.assertTrue(response.startswith(b'HTTP/1.1 403'))
        self.assertFalse(self.connects)

    async def test_all_public_dns_required_and_rebinding_never_changes_pin(self):
        current=[PIN]
        async def resolver(host,port,**kw):
            return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,port)) for ip in current]
        pin=await resolve_public_pin(HOST,resolver)
        proxy=await self.proxy(); proxy.register(HOST,pin)
        current[:]=[PIN,'127.0.0.1']
        with self.assertRaises(Denied): await resolve_public_pin(HOST,resolver)
        current[:]=['10.0.0.2']
        with self.assertRaises(Denied): await resolve_public_pin(HOST,resolver)
        current[:]=['1.1.1.1']
        with self.assertRaises(Denied): proxy.register(HOST,await resolve_public_pin(HOST,resolver))
        _,_,response=await self.connect(proxy)
        self.assertTrue(response.startswith(b'HTTP/1.1 200'))
        self.assertEqual(self.connects,[(PIN,443)])

    async def test_private_ambiguous_and_scoped_pins_denied(self):
        proxy=await self.proxy()
        for address in ('127.0.0.1','10.1.2.3','169.254.169.254','::1','::ffff:8.8.8.8',
                        '2002:7f00:1::','2606:4700:4700::1111%lo','0.0.0.0','224.0.0.1'):
            with self.assertRaises(Denied): proxy.register('other.example.test',address)
        self.assertEqual(proxy.diagnostics()['registered'],1)

    async def test_host_total_and_active_connection_limits(self):
        proxy=await self.proxy(limits=replace(Limits(),hosts=1,connections=3,active=2,per_host=1))
        with self.assertRaises(Denied): proxy.register('two.example.test',PIN)
        _,_,first=await self.connect(proxy)
        _,_,second=await self.connect(proxy)
        self.assertTrue(first.startswith(b'HTTP/1.1 200'))
        self.assertTrue(second.startswith(b'HTTP/1.1 403'))
        await asyncio.sleep(.02)
        _,_,third=await self.connect(proxy,auth=False)
        self.assertTrue(third.startswith(b'HTTP/1.1 407'))
        _,_,fourth=await self.connect(proxy)
        self.assertFalse(fourth)
        self.assertEqual(len(self.connects),1)

    async def test_header_bytes_and_header_timeout_close_without_upstream(self):
        proxy=await self.proxy(limits=replace(Limits(),header_bytes=256,header_seconds=.04))
        _,_,response=await self.connect(proxy,extra=b'X-Large: '+b'a'*300+b'\r\n')
        self.assertFalse(response)
        endpoint=urlsplit(proxy.browser_proxy()['server'])
        reader,writer=await asyncio.open_connection(endpoint.hostname,endpoint.port); self.clients.append(writer)
        writer.write(b'CONNECT '); await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.read(),.5),b'')
        self.assertFalse(self.connects)

    async def test_plaintext_not_forwarded_as_tls(self):
        proxy=await self.proxy(); reader,writer,_=await self.connect(proxy)
        writer.write(b'GET / HTTP/1.1\r\n\r\n'); await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.read(),.5),b'')
        self.assertFalse(self.received)

    async def test_bidirectional_bytes_bounded_before_forward(self):
        proxy=await self.proxy(limits=replace(Limits(),tunnel_bytes=10,lease_bytes=15))
        reader,writer,_=await self.connect(proxy)
        writer.write(HELLO); await writer.drain()
        # Echo would exceed the combined tunnel budget, so it is dropped.
        self.assertEqual(await asyncio.wait_for(reader.read(),.5),b'')
        stats=proxy.diagnostics(); self.assertLessEqual(stats['bytes_up']+stats['bytes_down'],10)
        self.assertEqual(stats['rejections'].get('byte_limit'),1)

    async def test_idle_tunnel_and_lease_expiry(self):
        proxy=await self.proxy(limits=replace(Limits(),handshake_seconds=.04,lease_seconds=.12))
        reader,_,_=await self.connect(proxy)
        self.assertEqual(await asyncio.wait_for(reader.read(),.5),b'')
        await asyncio.sleep(.15)
        self.assertTrue(proxy.diagnostics()['closed'])
        with self.assertRaises(Denied): proxy.browser_proxy()

    async def test_freeze_closes_revoked_tunnel_and_denies_new_hosts(self):
        proxy=await self.proxy(); proxy.register('keep.example.test','1.1.1.1')
        reader,writer,_=await self.connect(proxy); writer.write(HELLO); await writer.drain()
        await reader.readexactly(6)
        proxy.freeze({'keep.example.test'})
        self.assertEqual(await asyncio.wait_for(reader.read(),.5),b'')
        with self.assertRaises(Denied): proxy.register(HOST,PIN)
        _,_,response=await self.connect(proxy)
        self.assertTrue(response.startswith(b'HTTP/1.1 403'))
        self.assertEqual(proxy.diagnostics()['registered'],1)

    async def test_invalid_freeze_revokes_everything_monotonically(self):
        proxy=await self.proxy()
        with self.assertRaises(Denied): proxy.freeze({'unknown.example.test'})
        self.assertEqual(proxy.diagnostics()['registered'],0)
        with self.assertRaises(Denied): proxy.register(HOST,PIN)

    async def test_revocation_during_connect_cannot_publish_tunnel(self):
        entered=asyncio.Event(); release=asyncio.Event()
        async def upstream(address,port):
            entered.set()
            try: await release.wait()
            except asyncio.CancelledError: pass
            return await asyncio.open_connection('127.0.0.1',self.port)
        proxy=await self.proxy(factory=upstream)
        attempt=asyncio.create_task(self.connect(proxy)); await entered.wait()
        proxy.freeze(set()); release.set()
        _,_,response=await attempt
        self.assertFalse(response.startswith(b'HTTP/1.1 200'))
        await proxy.close(); self.assertEqual(proxy.diagnostics()['active'],0)

    async def test_close_cancels_owned_tunnels_and_clears_credential(self):
        proxy=await self.proxy(); reader,writer,_=await self.connect(proxy)
        writer.write(HELLO); await writer.drain(); await reader.readexactly(6)
        started = time.monotonic()
        await proxy.close(); await proxy.close()
        self.assertLess(time.monotonic() - started, 2, 'Shutdown must close active streams before waiting for the listener')
        self.assertEqual(await asyncio.wait_for(reader.read(),.5),b'')
        self.assertEqual(proxy.diagnostics()['active'],0)
        self.assertFalse(proxy._authorization)

    async def test_retained_session_revokes_idp_but_preserves_root_pin_credentials_and_budgets(self):
        proxy = await self.proxy()
        other = 'identity.example.test'; proxy.register(other, '1.1.1.1')
        root_reader, root_writer, _ = await self.connect(proxy)
        idp_reader, idp_writer, _ = await self.connect(proxy, host=other)
        for reader, writer in ((root_reader, root_writer), (idp_reader, idp_writer)):
            writer.write(HELLO); await writer.drain(); await reader.readexactly(6)
        credentials = proxy.browser_proxy(); stats = proxy.diagnostics()
        proxy.retain_session(900, {HOST})
        self.assertEqual(proxy._pins, {HOST: PIN})
        self.assertTrue(proxy._frozen)
        self.assertEqual(await asyncio.wait_for(idp_reader.read(), .5), b'')
        self.assertEqual(proxy.browser_proxy(), credentials)
        after = proxy.diagnostics()
        for key in ('attempts', 'opened', 'bytes_up', 'bytes_down'):
            self.assertEqual(after[key], stats[key])
        root_writer.write(b'opaque-later-record'); await root_writer.drain()
        self.assertEqual(await root_reader.readexactly(19), b'opaque-later-record')
        with self.assertRaises(Denied): proxy.register(other, '1.1.1.1')
        with self.assertRaises(Denied): proxy.retain_session(900, {HOST})

    async def test_retention_reschedules_expiry_without_revival(self):
        proxy = await self.proxy(limits=replace(Limits(), lease_seconds=.12))
        await asyncio.sleep(.04)
        proxy.retain_session(.25, {HOST})
        await asyncio.sleep(.13)
        self.assertFalse(proxy.diagnostics()['closed'])
        await asyncio.sleep(.16)
        self.assertTrue(proxy.diagnostics()['closed'])
        with self.assertRaises(Denied): proxy.retain_session(900, {HOST})
        self.assertFalse(proxy._authorization)

    async def test_retention_clock_absolute_cap_one_use_and_no_expired_revival(self):
        now = [1000.0]
        with patch('oak.website_navigation_transport.time', SimpleNamespace(monotonic=lambda: now[0])):
            proxy = await self.proxy(limits=replace(Limits(), lease_seconds=900))
            now[0] = 1400.0
            with self.assertRaises(Denied) as denied:
                proxy.retain_session(900, {HOST})
            self.assertEqual(denied.exception.code, 'retention_deadline')
            self.assertFalse(proxy._retained)
            proxy.retain_session(800, {HOST})
            self.assertEqual(proxy._expires, 2200.0)
            with self.assertRaises(Denied): proxy.retain_session(1, {HOST})
            expired = await self.proxy(limits=replace(Limits(), lease_seconds=10))
            now[0] += 10
            self.assertFalse(expired._closed)  # Expiry task need not have fired.
            with self.assertRaises(Denied): expired.retain_session(900, {HOST})
            self.assertFalse(expired._retained)

    async def test_retention_rejects_invalid_requests_without_consuming_promotion(self):
        proxy = await self.proxy()
        for seconds, hosts in ((True, {HOST}), (0, {HOST}), (901, {HOST}),
                               (float('nan'), {HOST}), (float('inf'), {HOST}),
                               (900, set()), (900, [HOST]), (900, {'unknown.example.test'})):
            with self.assertRaises(Denied): proxy.retain_session(seconds, hosts)
            self.assertFalse(proxy._retained)
            self.assertFalse(proxy._frozen)
        proxy.retain_session(900, {HOST})
        self.assertTrue(proxy._retained)

    async def test_actual_tls_end_to_end_without_proxy_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory); cert=path/'cert.pem'; key=path/'key.pem'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                '-subj','/CN='+HOST,'-addext','subjectAltName=DNS:'+HOST,
                '-keyout',str(key),'-out',str(cert)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            server_ssl=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); server_ssl.load_cert_chain(cert,key)
            received=[]
            async def tls_server(reader,writer):
                request=await reader.readuntil(b'\r\n\r\n'); received.append(request)
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok')
                await writer.drain(); writer.close(); await writer.wait_closed()
            server=await asyncio.start_server(tls_server,'127.0.0.1',0,ssl=server_ssl); self.servers.append(server)
            port=server.sockets[0].getsockname()[1]
            async def upstream(address,target_port):
                self.assertEqual((address,target_port),(PIN,443))
                return await asyncio.open_connection('127.0.0.1',port)
            proxy=await self.proxy(factory=upstream); reader,writer,response=await self.connect(proxy)
            self.assertTrue(response.startswith(b'HTTP/1.1 200'))
            client_ssl=ssl.create_default_context(cafile=str(cert))
            await writer.start_tls(client_ssl,server_hostname=HOST)
            request=b'GET /fixture HTTP/1.1\r\nHost: '+HOST.encode()+b'\r\n\r\n'
            writer.write(request); await writer.drain()
            self.assertTrue((await reader.read()).endswith(b'ok'))
            self.assertEqual(received,[request])


class FakeCDP:
    def __init__(self):
        self.calls, self.listeners, self.detached = [], {}, False

    async def send(self, name, params=None):
        self.calls.append((name, params))
        if name == 'Page.getFrameTree':
            return {'frameTree': {'frame': {'id': 'main'}}}

    def on(self, name, callback): self.listeners[name] = callback
    def remove_listener(self, name, callback): self.listeners.pop(name, None)
    async def detach(self): self.detached = True


class NavigationInterceptionTests(unittest.IsolatedAsyncioTestCase):
    async def transport(self, authorize, **kwargs):
        self.cdp = FakeCDP()
        class Context:
            async def new_cdp_session(inner, page): return self.cdp
        self.blocks = []
        transport = WebsiteNavigationTransport(authorize, on_block=self.blocks.append, **kwargs)
        self.addAsyncCleanup(transport.close)
        await transport.start(); await transport.attach(Context(), object())
        return transport

    def event(self, *, method='GET', resource='Document', redirect=None, url='https://' + HOST + '/entry'):
        value = {'requestId': 'request1', 'frameId': 'main', 'resourceType': resource,
                 'request': {'url': url, 'method': method, 'headers': {'Cookie': 'PRIVATE_HEADER_CANARY'},
                             'postData': 'PRIVATE_BODY_CANARY'}}
        if redirect is not None: value['redirectedRequestId'] = redirect
        return value

    async def drained(self, transport):
        tasks = tuple(transport._tasks)
        if tasks: await asyncio.wait_for(asyncio.gather(*tasks), 1)

    async def test_pending_owner_approval_does_not_register_or_continue_and_only_metadata_crosses(self):
        entered, release, observed = asyncio.Event(), asyncio.Event(), []
        async def authorize(item):
            observed.append(item); entered.set(); await release.wait(); return PIN
        transport = await self.transport(authorize)
        transport._paused(self.event()); await entered.wait()
        self.assertEqual(transport.diagnostics()['proxy']['registered'], 0)
        self.assertFalse(any(name == 'Fetch.continueRequest' for name, _ in self.cdp.calls))
        self.assertIsInstance(observed[0], NavigationRequest)
        self.assertNotIn('PRIVATE_', repr(observed[0]) + json.dumps(vars(observed[0])))
        release.set(); await self.drained(transport)
        self.assertEqual(transport.diagnostics()['proxy']['registered'], 1)
        continued = [params for name, params in self.cdp.calls if name == 'Fetch.continueRequest']
        self.assertEqual(continued, [{'requestId': 'request1'}])

    async def test_private_pin_and_untrusted_authorizer_errors_never_escape(self):
        async def authorize(item): raise ValueError('PRIVATE_EXCEPTION_CANARY')
        transport = await self.transport(authorize)
        transport._paused(self.event()); await self.drained(transport)
        self.assertEqual(self.blocks, ['origin_not_approved'])
        self.assertNotIn('PRIVATE_', json.dumps(transport.diagnostics()))
        self.assertEqual(transport.diagnostics()['proxy']['registered'], 0)
        async def private(item): return '127.0.0.1'
        transport._authorize = private
        transport._paused(self.event()); await self.drained(transport)
        self.assertEqual(self.blocks[-1], 'nonpublic_pin')
        self.assertFalse(any(name == 'Fetch.continueRequest' for name, _ in self.cdp.calls))

    async def test_redirected_post_never_replayed_but_fresh_owner_post_needs_callback(self):
        seen = []
        async def authorize(item): seen.append(item.method); return PIN
        transport = await self.transport(authorize)
        transport._paused(self.event(method='POST', redirect='prior')); await self.drained(transport)
        self.assertEqual(seen, [])
        self.assertEqual(self.blocks, ['redirect_body_replay_denied'])
        transport._paused(self.event(method='POST')); await self.drained(transport)
        self.assertEqual(seen, ['POST'])
        self.assertEqual([params for name, params in self.cdp.calls if name == 'Fetch.continueRequest'],
                         [{'requestId': 'request1'}])

    async def test_redirected_resource_denied_without_blocking_whole_page_or_discovering_host(self):
        async def authorize(item): self.fail('Resource requests cannot authorize origins')
        transport = await self.transport(authorize)
        transport._paused(self.event(resource='Script', redirect='previous'))
        await self.drained(transport)
        self.assertEqual(transport.diagnostics()['reasons'], {'resource_redirect_unsupported': 1})
        self.assertEqual(self.blocks, [])
        self.assertEqual(transport.diagnostics()['proxy']['registered'], 0)
        transport._paused(self.event(resource='Script')); await self.drained(transport)
        self.assertEqual(transport.diagnostics()['continued'], 1)

    async def test_close_cancels_pending_authority_without_publishing_pin(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def authorize(item):
            entered.set()
            try: await asyncio.Event().wait()
            finally: cancelled.set()
        transport = await self.transport(authorize)
        transport._paused(self.event()); await entered.wait()
        await asyncio.wait_for(transport.close(), 1)
        self.assertTrue(cancelled.is_set()); self.assertTrue(self.cdp.detached)
        self.assertEqual(transport.diagnostics()['pending'], 0)
        self.assertFalse(any(name == 'Fetch.continueRequest' for name, _ in self.cdp.calls))

    async def test_close_from_authority_callback_does_not_wait_on_itself(self):
        async def authorize(item): await transport.close(); return PIN
        transport = await self.transport(authorize)
        transport._paused(self.event()); await self.drained(transport)
        self.assertTrue(transport.diagnostics()['closed'])
        self.assertFalse(any(name == 'Fetch.continueRequest' for name, _ in self.cdp.calls))

    async def test_pending_approval_is_bounded_and_freeze_rechecked_after_await(self):
        async def authorize(item): await asyncio.Event().wait()
        transport = await self.transport(authorize, approval_seconds=.02)
        transport._paused(self.event()); await self.drained(transport)
        self.assertEqual(self.blocks, ['approval_timeout'])
        entered, release = asyncio.Event(), asyncio.Event()
        async def next_authorize(item): entered.set(); await release.wait(); return PIN
        transport._authorize = next_authorize
        transport._paused(self.event()); await entered.wait()
        transport.freeze(set()); release.set(); await self.drained(transport)
        self.assertEqual(self.blocks[-1], 'registration_closed')
        self.assertEqual(transport.diagnostics()['proxy']['registered'], 0)

    async def test_proxy_auth_exact_source_origin_scheme_realm_only(self):
        async def authorize(item): return PIN
        transport = await self.transport(authorize)
        correct = {'source': 'Proxy', 'origin': transport.browser_proxy()['server'],
                   'scheme': 'basic', 'realm': 'Oak lease'}
        for key, value in [('source', 'Server'), ('origin', 'https://fixture.example.test'),
                           ('scheme', 'digest'), ('realm', 'Other')]:
            event = {'requestId': 'auth', 'authChallenge': dict(correct, **{key: value})}
            transport._auth_required(event); await self.drained(transport)
            self.assertEqual(self.cdp.calls[-1][1]['authChallengeResponse'], {'response': 'CancelAuth'})
        transport._auth_required({'requestId': 'auth', 'authChallenge': correct}); await self.drained(transport)
        self.assertEqual(self.cdp.calls[-1][1]['authChallengeResponse']['response'], 'ProvideCredentials')
        self.assertNotIn(transport.browser_proxy()['password'], json.dumps(transport.diagnostics()) + repr(transport))

    async def test_resource_or_frame_cannot_bypass_origin_validation(self):
        seen = []
        async def authorize(item): seen.append(item); return PIN
        transport = await self.transport(authorize)
        for url in ('https://127.0.0.1/', 'http://' + HOST, 'https://user:secret@' + HOST, 'https://' + HOST + ':444/'):
            transport._paused(self.event(url=url)); await self.drained(transport)
        event = self.event(); event['frameId'] = 'other-frame'
        transport._paused(event); await self.drained(transport)
        self.assertFalse(seen)
        self.assertFalse(any(name == 'Fetch.continueRequest' for name, _ in self.cdp.calls))


@unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1', 'opt-in controlled local Chromium/TLS')
class NavigationChromiumTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp import web
        from playwright.async_api import async_playwright
        self.hosts = ('first.example.test', 'second.example.test', 'third.example.test')
        self.pins = dict(zip(self.hosts, ('8.8.8.8', '1.1.1.1', '9.9.9.9')))
        self.events, self.received, self.blocks = [], [], []
        self.approval = None
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        directory = Path(self.directory.name); cert, key = directory / 'cert.pem', directory / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
            '-subj', '/CN=' + self.hosts[0], '-addext',
            'subjectAltName=' + ','.join('DNS:' + host for host in self.hosts),
            '-keyout', str(key), '-out', str(cert)], check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); tls.load_cert_chain(cert, key)

        async def fixture(request):
            self.received.append((request.host, request.method, request.path,
                                  request.headers.get('Cookie', ''),
                                  'Authorization' in request.headers or 'Proxy-Authorization' in request.headers))
            if request.path == '/302':
                return web.Response(status=302, headers={'Location': 'https://' + self.hosts[1] + '/307',
                    'Set-Cookie': 'fixture_cookie=synthetic; Path=/; Secure; SameSite=Lax'})
            if request.path == '/307':
                return web.Response(status=307, headers={'Location': 'https://' + self.hosts[2] + '/direct'})
            if request.path == '/private':
                return web.Response(status=302, headers={'Location': 'https://private.example.test/direct'})
            if request.path == '/server-auth':
                return web.Response(status=401, headers={'WWW-Authenticate': 'Basic realm="Oak lease"'})
            if request.path == '/post307':
                return web.Response(status=307, headers={'Location': 'https://' + self.hosts[1] + '/direct'})
            if request.path == '/post302':
                return web.Response(status=302, headers={'Location': '/direct'})
            if request.path == '/script-page':
                return web.Response(text='<script src="/redirect.js"></script>', content_type='text/html')
            if request.path == '/redirect.js':
                return web.Response(status=302, headers={'Location': 'https://' + self.hosts[1] + '/payload.js'})
            return web.Response(text='<h1>Local fixture</h1><a href="https://' + self.hosts[1] +
                '/direct">Next site</a>', content_type='text/html')

        app = web.Application(); app.router.add_route('*', '/{tail:.*}', fixture)
        self.runner = web.AppRunner(app, access_log=None); await self.runner.setup()
        self.addAsyncCleanup(self.runner.cleanup)
        site = web.TCPSite(self.runner, '127.0.0.1', 0, ssl_context=tls); await site.start()
        port = site._server.sockets[0].getsockname()[1]

        async def upstream(address, target_port):
            self.assertEqual(target_port, 443); self.assertIn(address, self.pins.values())
            host = next(host for host, value in self.pins.items() if value == address)
            self.assertEqual(self.transport._proxy._pins[host], address)
            self.assertIn(('approved', host), self.events)
            self.events.append(('connect', host))
            # The only socket factory connects to this controlled local TLS server.
            return await asyncio.open_connection('127.0.0.1', port)

        async def authorize(item):
            host = urlsplit(item.url).hostname
            self.events.append(('paused', host))
            if self.approval is not None and host == self.hosts[1]:
                self.approval[0].set(); await self.approval[1].wait()
            address = '127.0.0.1' if host == 'private.example.test' else self.pins[host]
            self.events.append(('approved', host))
            return address

        self.transport = WebsiteNavigationTransport(authorize, on_block=self.blocks.append,
                                                     upstream_factory=upstream)
        self.addAsyncCleanup(self.transport.close); await self.transport.start()
        self.driver = await async_playwright().start(); self.addAsyncCleanup(self.driver.stop)
        self.browser = await self.driver.chromium.launch(headless=True, chromium_sandbox=True,
            proxy=self.transport.browser_proxy(), args=self.transport.browser_args())
        self.addAsyncCleanup(self.browser.close)
        # Only these self-signed LOCAL fixtures bypass CA validation. The module
        # never returns or modifies ignore_https_errors; production stays strict.
        self.context = await self.browser.new_context(ignore_https_errors=True, service_workers='block')
        self.addAsyncCleanup(self.context.close)
        self.page = await self.context.new_page(); self.page.set_default_timeout(5000)
        async def existing_route(route):
            if route.request.resource_type in {'document', 'script'}:
                await route.continue_()
            else:
                await route.abort()
        await self.context.route('**/*', existing_route)
        await self.transport.attach(self.context, self.page)

    async def asyncTearDown(self):
        started = time.monotonic()
        await self.transport.close()
        self.assertLess(time.monotonic() - started, 5, 'Transport cleanup must not wait for Chromium navigation timeout')
        self.assertEqual(self.transport.diagnostics()['proxy']['active'], 0)
        self.assertEqual(self.transport.diagnostics()['pending'], 0)

    async def test_actual_direct_and_observed_link_keep_context(self):
        await self.page.goto('https://' + self.hosts[0] + '/direct', wait_until='domcontentloaded')
        await self.page.locator('a').click()
        await self.page.wait_for_url('https://' + self.hosts[1] + '/direct')
        self.assertIs(self.page.context, self.context)
        self.assertEqual([host for kind, host in self.events if kind == 'paused'], list(self.hosts[:2]))
        self.assertEqual(self.blocks, [])

    async def test_actual_302_307_hops_register_before_connect_and_preserve_browser_cookies(self):
        await self.page.goto('https://' + self.hosts[0] + '/302', wait_until='domcontentloaded')
        self.assertEqual(self.page.url, 'https://' + self.hosts[2] + '/direct')
        self.assertEqual([host for kind, host in self.events if kind == 'paused'], list(self.hosts))
        self.assertEqual([item[1] for item in self.received], ['GET', 'GET', 'GET'])
        self.assertTrue(all(not item[3] for item in self.received[1:]))  # No first-origin cookie forwarded.
        cookies = await self.context.cookies('https://' + self.hosts[0])
        self.assertEqual([(item['name'], item['value']) for item in cookies], [('fixture_cookie', 'synthetic')])
        await self.page.goto('https://' + self.hosts[0] + '/direct', wait_until='domcontentloaded')
        self.assertEqual(self.received[-1][3], 'fixture_cookie=synthetic')
        self.assertEqual(self.blocks, [])

    async def test_private_redirect_and_new_host_after_freeze_never_connect(self):
        from playwright.async_api import Error
        with self.assertRaises(Error):
            await self.page.goto('https://' + self.hosts[0] + '/private', wait_until='domcontentloaded')
        self.assertEqual([item[0] for item in self.received], [self.hosts[0]])
        self.assertIn('nonpublic_pin', self.blocks)
        self.transport.freeze({self.hosts[0]})
        with self.assertRaises(Error):
            await self.page.goto('https://' + self.hosts[1] + '/direct', wait_until='domcontentloaded')
        self.assertNotIn(('connect', self.hosts[1]), self.events)
        self.assertEqual(len(self.received), 1)

    async def test_actual_pending_redirect_waits_for_private_authority_before_any_target_connection(self):
        entered, release = asyncio.Event(), asyncio.Event(); self.approval = (entered, release)
        navigating = asyncio.create_task(self.page.goto('https://' + self.hosts[0] + '/302', wait_until='domcontentloaded'))
        await asyncio.wait_for(entered.wait(), 3)
        self.assertNotIn(('connect', self.hosts[1]), self.events)
        self.assertEqual([item[0] for item in self.received], [self.hosts[0]])
        release.set(); await navigating
        self.assertEqual(self.page.url, 'https://' + self.hosts[2] + '/direct')

    async def test_actual_owner_post_once_and_307_body_replay_denied(self):
        await self.page.goto('https://' + self.hosts[0] + '/direct', wait_until='domcontentloaded')
        await self.page.evaluate("""() => {
          const form=document.createElement('form'); form.method='POST'; form.action='/post307';
          const input=document.createElement('input'); input.name='fixture'; input.value='synthetic';
          form.append(input); document.body.append(form); form.submit();
        }""")
        for _ in range(50):
            if 'redirect_body_replay_denied' in self.blocks: break
            await asyncio.sleep(.02)
        self.assertIn('redirect_body_replay_denied', self.blocks)
        self.assertEqual([(r[0], r[1], r[2]) for r in self.received],
                         [(self.hosts[0], 'GET', '/direct'), (self.hosts[0], 'POST', '/post307')])
        self.assertNotIn(('connect', self.hosts[1]), self.events)

    async def test_actual_resource_redirect_denied_without_page_block(self):
        await self.page.goto('https://' + self.hosts[0] + '/script-page', wait_until='domcontentloaded')
        self.assertEqual(self.blocks, [])
        self.assertEqual(self.transport.diagnostics()['reasons'].get('resource_redirect_unsupported'), 1)
        self.assertNotIn(('connect', self.hosts[1]), self.events)
        self.assertTrue(all(item[0] == self.hosts[0] for item in self.received))

    async def test_actual_proxy_secret_not_sent_to_server_auth_challenge(self):
        response = await self.page.goto('https://' + self.hosts[0] + '/server-auth', wait_until='domcontentloaded')
        self.assertEqual(response.status, 401)
        self.assertTrue(all(item[4] is False for item in self.received))


if __name__ == '__main__': unittest.main()
