import asyncio
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from multidict import CIMultiDict

from oak.website_auth_hosts import ERROR, WebsiteAuthHosts, _preflight_headers, validate_auth_hosts


ORIGIN = 'https://login.example.com'
HOST = 'auth.example.com'
PUBLIC = '93.184.216.34'
IPV6 = '2606:4700:4700::1111'
CANARY = 'synthetic-private-auth-canary'


def records(*addresses):
    return [(socket.AF_INET6 if ':' in address else socket.AF_INET, socket.SOCK_STREAM, 6, '',
             (address, 443)) for address in addresses]


def request(url='https://' + HOST + '/v1/client?public=1', method='GET', kind='fetch', navigation=False):
    return SimpleNamespace(url=url, method=method, resource_type=kind, is_navigation_request=lambda: navigation)


class AuthHostConfigurationTests(unittest.TestCase):
    def test_empty_is_inactive_and_config_is_copied(self):
        self.assertEqual(validate_auth_hosts({}), {})
        source = {ORIGIN: [HOST, 'second.example.com']}
        checked = validate_auth_hosts(source)
        source[ORIGIN].append('unexpected.example.com')
        checked[ORIGIN].append('another.example.com')
        self.assertNotIn('unexpected.example.com', checked[ORIGIN])
        self.assertNotIn('another.example.com', source[ORIGIN])
        self.assertFalse(WebsiteAuthHosts(ORIGIN).active)
        self.assertFalse(WebsiteAuthHosts(ORIGIN, []).active)

    def test_noncanonical_origins_and_hosts_rejected_with_fixed_error(self):
        bad_origins = ['http://login.example.com', ORIGIN + '/', ORIGIN + ':443', ORIGIN + '?x=1',
                       ORIGIN + '#x', 'https://user:pass@login.example.com', 'https://LOGIN.example.com',
                       'https://127.0.0.1', 'https://[::1]', 'https://localhost', 'https://xn--example.com',
                       'https://exämple.com', 'https://*.example.com', 'https://login.example.com.']
        bad_hosts = [HOST + ':443', 'https://' + HOST, HOST + '/', HOST + '?x=1', HOST + '#x',
                     'Auth.example.com', HOST + '.', '*.example.com', '127.0.0.1', PUBLIC, '127.1',
                     '0x7f.1', '1.0x1', '[::1]', 'localhost', 'auth.localhost', 'auth.local',
                     'auth.internal', 'xn--example.com', 'exämple.com', 'login.example.com',
                     'auth..example.com', 'a' * 64 + '.example.com']
        for origin in bad_origins:
            with self.subTest(origin=origin), self.assertRaisesRegex(ValueError, '^' + ERROR + '$'):
                validate_auth_hosts({origin: [HOST]})
        for host in bad_hosts:
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, '^' + ERROR + '$'):
                validate_auth_hosts({ORIGIN: [host]})

    def test_wrong_types_duplicates_and_all_bounds(self):
        invalid = [None, [], True, {ORIGIN: []}, {ORIGIN: HOST}, {ORIGIN: {}},
                   {ORIGIN: [HOST, HOST]}, {ORIGIN: [True]}, {ORIGIN: [[]]},
                   {f'https://site{i}.example.com': [HOST] for i in range(9)},
                   {ORIGIN: [f'auth{i}.example.com' for i in range(5)]},
                   {f'https://site{i}.example.com': [f'auth{j}.example.com' for j in range(4)]
                    for i in range(5)}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, '^' + ERROR + '$'):
                validate_auth_hosts(value)
        maximum = {f'https://site{i}.example.com': [f'auth{j}.example.com' for j in range(2)] for i in range(8)}
        self.assertEqual(len(validate_auth_hosts(maximum)), 8)

    def test_direct_constructor_validates_even_inactive_destination(self):
        for origin, hosts in [('http://login.example.com', None), (ORIGIN + ':443', []),
                              (ORIGIN, {}), (ORIGIN, (HOST,)), (ORIGIN, [HOST, HOST])]:
            with self.subTest(origin=origin, hosts=hosts), self.assertRaises(ValueError):
                WebsiteAuthHosts(origin, hosts)


class AuthHostCapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        session = patch('oak.website_auth_hosts.ClientSession', return_value=SimpleNamespace(close=AsyncMock()))
        connector = patch('oak.website_auth_hosts.TCPConnector')
        session.start()
        connector.start()
        self.addCleanup(session.stop)
        self.addCleanup(connector.stop)

    async def policy(self, hosts=None, addresses=None):
        resolver = AsyncMock(return_value=records(*(addresses or [PUBLIC])))
        policy = WebsiteAuthHosts(ORIGIN, hosts if hosts is not None else [HOST], resolver)
        await policy.prepare()
        return policy, resolver

    async def test_no_dns_or_grant_without_explicit_hosts(self):
        resolver = AsyncMock()
        policy = WebsiteAuthHosts(ORIGIN, resolver=resolver)
        await policy.prepare()
        resolver.assert_not_awaited()
        self.assertEqual(policy.resolver_rules, [])
        self.assertFalse(policy.allows(request()))

    async def test_prepare_is_required_serialized_and_pins_only_selected_copied_hosts(self):
        hosts = [HOST]
        resolver = AsyncMock(return_value=records(PUBLIC, IPV6))
        policy = WebsiteAuthHosts(ORIGIN, hosts, resolver)
        hosts.append('unexpected.example.com')
        self.assertFalse(policy.allows(request()))
        self.assertEqual(policy.resolver_rules, [])
        await asyncio.gather(policy.prepare(), policy.prepare())
        resolver.assert_awaited_once_with(HOST, 443, type=socket.SOCK_STREAM)
        self.assertEqual(policy.resolver_rules, ['MAP ' + HOST + ' ' + PUBLIC])
        copied = policy.resolver_rules
        copied.append('MAP unexpected.example.com ' + PUBLIC)
        self.assertEqual(len(policy.resolver_rules), 1)
        self.assertFalse(policy.allows(request('https://unexpected.example.com/')))

    async def test_ipv6_pin_is_bracketed(self):
        policy, _ = await self.policy(addresses=[IPV6])
        self.assertEqual(policy.resolver_rules, ['MAP ' + HOST + ' [' + IPV6 + ']'])

    async def test_every_dns_address_must_be_public_and_partial_pins_never_survive(self):
        for address in ['127.0.0.1', '10.0.0.1', '169.254.169.254', '192.168.1.1',
                        '100.64.0.1', '0.0.0.0', '224.0.0.1', '::1', 'fe80::1', 'fc00::1', 'bad']:
            resolver = AsyncMock(return_value=records(PUBLIC, address))
            policy = WebsiteAuthHosts(ORIGIN, [HOST], resolver)
            with self.subTest(address=address), self.assertRaisesRegex(ValueError, '^' + ERROR + '$'):
                await policy.prepare()
            self.assertEqual(policy.resolver_rules, [])
            self.assertFalse(policy.allows(request()))
        resolver = AsyncMock(side_effect=[records(PUBLIC), []])
        policy = WebsiteAuthHosts(ORIGIN, [HOST, 'second.example.com'], resolver)
        with self.assertRaises(ValueError):
            await policy.prepare()
        self.assertEqual(policy.resolver_rules, [])

    async def test_dns_failure_is_sanitized_and_other_pending_dns_is_cancelled(self):
        cancelled = asyncio.Event()

        async def resolver(host, *args, **kwargs):
            if host == HOST:
                await asyncio.sleep(0)
                raise RuntimeError(CANARY)
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        policy = WebsiteAuthHosts(ORIGIN, [HOST, 'second.example.com'], resolver)
        with self.assertRaises(ValueError) as caught:
            await policy.prepare()
        self.assertEqual(str(caught.exception), ERROR)
        self.assertNotIn(CANARY, str(caught.exception))
        self.assertTrue(cancelled.is_set())
        self.assertEqual(policy.resolver_rules, [])

    async def test_dns_timeout_fails_closed(self):
        async def slow(*args, **kwargs):
            await asyncio.sleep(10)
        policy = WebsiteAuthHosts(ORIGIN, [HOST], slow)
        policy.DNS_SECONDS = 0.01
        with self.assertRaisesRegex(ValueError, '^' + ERROR + '$'):
            await policy.prepare()
        self.assertFalse(policy.allows(request()))

    async def test_cancelled_prepare_can_retry_but_never_grants_partial_policy(self):
        entered = asyncio.Event()
        async def pending(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        policy = WebsiteAuthHosts(ORIGIN, [HOST], pending)
        task = asyncio.create_task(policy.prepare())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(policy.resolver_rules, [])
        self.assertFalse(policy.allows(request()))
        policy._resolver = AsyncMock(return_value=records(PUBLIC))
        await policy.prepare()
        self.assertTrue(policy.allows(request()))

    async def test_close_during_prepare_cannot_reenable_capability(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def pending(*args, **kwargs):
            entered.set()
            await release.wait()
            return records(PUBLIC)
        policy = WebsiteAuthHosts(ORIGIN, [HOST], pending)
        task = asyncio.create_task(policy.prepare())
        await entered.wait()
        await policy.close()
        release.set()
        with self.assertRaises(ValueError):
            await task
        self.assertEqual(policy.resolver_rules, [])
        self.assertFalse(policy.allows(request()))
        with self.assertRaises(ValueError):
            await policy.prepare()

    async def test_exact_request_methods_types_and_no_navigation(self):
        policy, _ = await self.policy()
        for method in ['GET', 'HEAD', 'POST']:
            for kind in ['script', 'fetch', 'xhr']:
                self.assertEqual(policy.allows(request(method=method, kind=kind)),
                                 not (kind == 'script' and method == 'POST'))
        for method in ['OPTIONS', 'PUT', 'PATCH', 'DELETE', 'CONNECT', 'get', None, []]:
            self.assertFalse(policy.allows(request(method=method)))
        for kind in ['document', 'websocket', 'image', 'font', 'stylesheet', 'other', None, []]:
            self.assertFalse(policy.allows(request(kind=kind)))
        self.assertFalse(policy.allows(request(navigation=True)))
        self.assertFalse(policy.allows(request(navigation=None)))

    async def test_request_urls_reject_aliases_userinfo_fragments_and_unlisted_hosts(self):
        policy, _ = await self.policy()
        bad = ['http://' + HOST, 'https://' + HOST + ':443/a', 'https://' + HOST + ':444/a',
               'https://user:pass@' + HOST + '/', 'https://' + HOST + '/#',
               'https://Auth.example.com/', 'https://' + HOST + './', 'https://sub.' + HOST + '/',
               'https://127.0.0.1/', 'https://93.184.216.34/', 'https://%61uth.example.com/',
               'https://' + HOST + '/\\evil', 'https://' + HOST + '/\n',
               'https://' + HOST + '/' + 'x' * 4096, None, {}, '']
        for url in bad:
            with self.subTest(url=url):
                self.assertFalse(policy.allows(request(url)))
        self.assertTrue(policy.allows(request('https://' + HOST + '/v1/sign_in?public=1', method='POST')))

    async def test_never_reads_headers_body_cookies_or_frame_and_close_revokes(self):
        class PrivateRequest:
            url = 'https://' + HOST + '/v1/sign_in'
            method = 'POST'
            resource_type = 'fetch'
            def is_navigation_request(self):
                return False
            def __getattr__(self, name):
                raise AssertionError('Private request attribute accessed: ' + name)
        policy, _ = await self.policy()
        self.assertTrue(policy.allows(PrivateRequest()))
        await policy.close()
        self.assertFalse(policy.allows(PrivateRequest()))
        self.assertEqual(policy.resolver_rules, [])


class Response:
    def __init__(self, status=200, body=b'{}', headers=None):
        self.status, self.body = status, body
        self.headers = CIMultiDict(headers if headers is not None else {
            'Content-Type': 'application/json', 'Access-Control-Allow-Origin': ORIGIN,
            'Access-Control-Allow-Credentials': 'true'})
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def iter_chunked(self, size):
        for offset in range(0, len(self.body), size):
            yield self.body[offset:offset + size]


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.close = AsyncMock()

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def relay(responses):
    policy = WebsiteAuthHosts(ORIGIN, [HOST])
    policy._pins, policy._ready = {HOST: PUBLIC}, True
    policy._session = Session(responses)
    return policy


def preflight(**overrides):
    headers = {'Access-Control-Allow-Origin': ORIGIN, 'Access-Control-Allow-Credentials': 'true',
               'Access-Control-Allow-Methods': 'GET, HEAD, POST',
               'Access-Control-Allow-Headers': 'authorization, content-type, x-fixture'}
    headers.update(overrides)
    return Response(204, b'', headers)


def route(**kwargs):
    current = request(**kwargs)
    current.post_data_buffer = b'private=' + CANARY.encode() if current.method == 'POST' else None
    headers = {
        'cookie': 'fixture=' + CANARY,
        'origin': ORIGIN, 'content-type': 'text/plain', 'host': HOST,
        'content-length': '999', 'connection': 'X-Hop', 'x-hop': 'remove',
        'proxy-authorization': 'remove', 'transfer-encoding': 'chunked'}
    if current.method == 'POST':
        headers.update(authorization='Bearer ' + CANARY, **{'content-type': 'application/json'})
    if current.resource_type == 'script':
        headers.pop('origin')  # Classic script fixture; CORS fixtures add it explicitly.
    current.all_headers = AsyncMock(return_value=headers)
    return SimpleNamespace(request=current, fulfill=AsyncMock(), abort=AsyncMock())


class AuthHostRelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_forwards_actual_private_headers_body_and_error_response_cookies(self):
        policy = relay([preflight(), Response(401, b'{"error":"invalid"}', [
            ('Content-Type', 'application/json'), ('Set-Cookie', 'first=one; Secure; HttpOnly'),
            ('Set-Cookie', 'second=two; Secure; HttpOnly'), ('Access-Control-Allow-Origin', ORIGIN),
            ('Access-Control-Allow-Credentials', 'true'), ('Content-Encoding', 'gzip'),
            ('Content-Length', '999'), ('Connection', 'X-Hop'), ('X-Hop', 'remove')])])
        current = route(method='POST')
        self.assertTrue(await policy.handle(current))
        self.assertEqual(policy._session.calls[0][0], 'OPTIONS')
        method, url, sent = policy._session.calls[1]
        self.assertEqual(method, 'POST')
        self.assertFalse(sent['allow_redirects'])
        self.assertTrue(sent['ssl'])
        self.assertEqual(sent['data'], current.request.post_data_buffer)
        self.assertEqual(sent['headers'], {'cookie': 'fixture=' + CANARY,
            'authorization': 'Bearer ' + CANARY, 'origin': ORIGIN, 'content-type': 'application/json',
            'accept-encoding': 'identity'})
        response = current.fulfill.call_args.kwargs
        self.assertEqual(response['status'], 401)
        self.assertEqual(response['headers']['set-cookie'], 'first=one; Secure; HttpOnly\nsecond=two; Secure; HttpOnly')
        self.assertEqual(response['headers']['access-control-allow-credentials'], 'true')
        self.assertNotIn('content-encoding', response['headers'])
        self.assertNotIn('content-length', response['headers'])
        self.assertNotIn('x-hop', response['headers'])
        current.abort.assert_not_awaited()
        diagnostic = policy.diagnostics()
        self.assertEqual(diagnostic['requests'], 2)
        self.assertEqual(diagnostic['preflights'], 1)
        self.assertEqual(diagnostic['fulfilled'], 1)
        self.assertEqual(diagnostic['statuses'], {204: 1, 401: 1})
        self.assertNotIn(CANARY, str(diagnostic))
        diagnostic['statuses'].clear()
        self.assertEqual(policy.diagnostics()['statuses'], {204: 1, 401: 1})

    async def test_only_get_scripts_follow_bounded_same_host_redirects(self):
        policy = relay([Response(307, headers={'Location': '/version.js'}), Response(body=b'void 0', headers={'Content-Type': 'application/javascript',
            'Access-Control-Allow-Origin': ORIGIN, 'Access-Control-Allow-Credentials': 'true'})])
        current = route(url='https://' + HOST + '/latest.js', kind='script')
        self.assertTrue(await policy.handle(current))
        self.assertEqual(len(policy._session.calls), 2)
        self.assertEqual(policy._session.calls[1][1], 'https://' + HOST + '/version.js')
        current.fulfill.assert_awaited_once()
        for method, kind in [('POST', 'fetch'), ('GET', 'fetch'), ('HEAD', 'xhr')]:
            policy = relay(([preflight()] if method == 'POST' else []) + [Response(307, headers={'Location': '/next', 'Access-Control-Allow-Origin': ORIGIN,
                'Access-Control-Allow-Credentials': 'true'})])
            current = route(method=method, kind=kind)
            await policy.handle(current)
            current.abort.assert_awaited_once()
            current.fulfill.assert_not_awaited()
            self.assertEqual(len(policy._session.calls), 2 if method == 'POST' else 1)
            self.assertEqual(policy.diagnostics()['failures'], {'redirect_method_or_type_rejected': 1})

    async def test_script_redirect_rejects_ip_crosshost_userinfo_and_downgrade(self):
        targets = ['https://127.0.0.1/', 'https://169.254.169.254/', 'https://other.example.com/',
                   ORIGIN + '/', 'http://' + HOST + '/', 'https://user@' + HOST + '/',
                   'https://' + HOST + ':443/', '/next#fragment', '/next\\escape']
        for target in targets:
            with self.subTest(target=target):
                policy = relay([Response(302, headers={'Location': target})])
                current = route(kind='script')
                await policy.handle(current)
                self.assertEqual(len(policy._session.calls), 1)
                current.abort.assert_awaited_once()
                current.fulfill.assert_not_awaited()
    async def test_public_script_cookie_redirect_never_forwards_private_state(self):
        policy = relay([Response(307, headers={'Location': '/version.js', 'Set-Cookie': 'first=private'}),
            Response(body=b'void 0', headers={'Content-Type': 'text/javascript; charset=utf-8',
                'Set-Cookie': 'second=private', 'X-Private': CANARY})])
        current = route(kind='script')
        current.request.all_headers.return_value.update({'authorization': CANARY, 'x-fixture': CANARY})
        current.request.post_data_buffer = CANARY.encode()
        await policy.handle(current)
        self.assertEqual(len(policy._session.calls), 2)
        for method, url, sent in policy._session.calls:
            self.assertEqual(method, 'GET')
            self.assertIsNone(sent['data'])
            self.assertEqual(sent['headers'], {'accept': '*/*', 'accept-encoding': 'identity'})
            self.assertNotIn(CANARY, str(sent))
        self.assertEqual(current.fulfill.call_args.kwargs['headers'],
                         {'content-type': 'text/javascript', 'cache-control': 'no-store'})
        self.assertEqual(policy.diagnostics()['fulfilled'], 1)
        self.assertEqual(policy.diagnostics()['preflights'], 0)
        self.assertNotIn(CANARY, str(policy.diagnostics()))
        current.abort.assert_not_awaited()

    async def test_public_script_rejects_missing_or_wrong_mime_and_http_error(self):
        for status, mime in [(200, ''), (200, 'text/html'), (200, 'application/json'),
                             (401, 'text/javascript')]:
            policy = relay([Response(status, headers={'Content-Type': mime})])
            current = route(kind='script')
            await policy.handle(current)
            current.abort.assert_awaited_once()
            current.fulfill.assert_not_awaited()
            self.assertEqual(policy.diagnostics()['failures'], {'script_content_rejected': 1})

    async def test_cors_script_redirect_must_validate_every_response(self):
        for cors in ({}, {'Access-Control-Allow-Origin': ORIGIN, 'Access-Control-Allow-Credentials': 'true'}):
            policy = relay([Response(302, headers={'Location': '/next.js', **cors}), Response(body=b'void 0', headers={'Content-Type': 'application/javascript',
            'Access-Control-Allow-Origin': ORIGIN, 'Access-Control-Allow-Credentials': 'true'})])
            current = route(kind='script')
            current.request.all_headers.return_value.update({'sec-fetch-mode': 'cors', 'origin': ORIGIN})
            await policy.handle(current)
            if cors:
                self.assertEqual(len(policy._session.calls), 2)
                current.fulfill.assert_awaited_once()
            else:
                self.assertEqual(len(policy._session.calls), 1)
                current.abort.assert_awaited_once()
                self.assertEqual(policy.diagnostics()['failures'], {'response_cors_rejected': 1})

    async def test_anonymous_cors_scripts_accept_wildcard_or_exact_without_credentials(self):
        for allowed_origin in ('*', ORIGIN):
            with self.subTest(allowed_origin=allowed_origin):
                policy = relay([
                    Response(302, headers={'Location': '/second.js', 'Access-Control-Allow-Origin': allowed_origin}),
                    Response(307, headers={'Location': '/final.js', 'Access-Control-Allow-Origin': ORIGIN}),
                    Response(body=b'void 0', headers={'Content-Type': 'application/javascript',
                        'Access-Control-Allow-Origin': allowed_origin, 'Set-Cookie': 'ignored=private',
                        'Access-Control-Allow-Credentials': 'false'})])
                current = route(kind='script')
                current.request.all_headers.return_value.update({'sec-fetch-mode': 'cors', 'origin': ORIGIN,
                                                                  'authorization': CANARY})
                await policy.handle(current)
                self.assertEqual(len(policy._session.calls), 3)
                for method, url, sent in policy._session.calls:
                    self.assertEqual(method, 'GET')
                    self.assertEqual(sent['headers'], {'accept': '*/*', 'accept-encoding': 'identity', 'origin': ORIGIN})
                    self.assertIsNone(sent['data'])
                    self.assertNotIn(CANARY, str(sent))
                current.abort.assert_not_awaited()
                self.assertEqual(current.fulfill.call_args.kwargs['headers'], {
                    'content-type': 'application/javascript', 'cache-control': 'no-store',
                    'access-control-allow-origin': allowed_origin})

    async def test_anonymous_cors_rejects_malformed_multiple_and_mismatched_origins_at_each_hop(self):
        invalid = [[], [('Access-Control-Allow-Origin', '')], [('Access-Control-Allow-Origin', 'null')],
                   [('Access-Control-Allow-Origin', 'https://other.example.com')],
                   [('Access-Control-Allow-Origin', ORIGIN + ', *')],
                   [('Access-Control-Allow-Origin', ORIGIN + '/')],
                   [('Access-Control-Allow-Origin', '*'), ('Access-Control-Allow-Origin', '*')],
                   [('Access-Control-Allow-Origin', ORIGIN), ('Access-Control-Allow-Origin', '*')],
                   [('Access-Control-Allow-Origin', ORIGIN), ('Access-Control-Allow-Origin', ORIGIN)]]
        for invalid_headers in invalid:
            for bad_hop in range(3):
                with self.subTest(headers=invalid_headers, hop=bad_hop):
                    replies = []
                    for hop in range(3):
                        headers = (invalid_headers if hop == bad_hop else [('Access-Control-Allow-Origin', '*')])
                        headers = headers + ([('Location', '/next' + str(hop) + '.js')] if hop < 2
                                             else [('Content-Type', 'application/javascript')])
                        replies.append(Response(302 if hop < 2 else 200, b'void 0', headers))
                    policy = relay(replies)
                    current = route(kind='script')
                    current.request.all_headers.return_value.update({'sec-fetch-mode': 'cors', 'origin': ORIGIN})
                    await policy.handle(current)
                    self.assertEqual(len(policy._session.calls), bad_hop + 1)
                    current.abort.assert_awaited_once()
                    current.fulfill.assert_not_awaited()
                    self.assertEqual(policy.diagnostics()['failures'], {'response_cors_rejected': 1})

    async def test_cors_script_origin_signal_is_required_and_cannot_be_forged(self):
        for initiating in ({'origin': 'null'}, {'origin': ''},
                           {'origin': 'https://other.example.com'}, {'sec-fetch-mode': 'cors'}):
            with self.subTest(initiating=initiating):
                policy = relay([])
                current = route(kind='script')
                current.request.all_headers.return_value.update(initiating)
                await policy.handle(current)
                self.assertEqual(policy._session.calls, [])
                current.abort.assert_awaited_once()
                self.assertEqual(policy.diagnostics()['failures'], {'initiating_origin_rejected': 1})

    async def test_redirects_consume_budget_and_never_replay_post(self):
        policy = relay([Response(302, headers={'Location': '/next'}) for _ in range(4)])
        current = route(kind='script')
        await policy.handle(current)
        self.assertEqual(len(policy._session.calls), 4)
        current.abort.assert_awaited_once()
        policy = relay([Response(302, headers={'Location': '/next'})])
        policy.MAX_REQUESTS = 1
        current = route(kind='script')
        await policy.handle(current)
        self.assertEqual(len(policy._session.calls), 1)
        current.abort.assert_awaited_once()

    async def test_unknown_request_does_not_read_headers_or_body(self):
        policy = relay([])
        current = route(url='https://other.example.com/')
        self.assertFalse(await policy.handle(current))
        current.request.all_headers.assert_not_awaited()
        self.assertEqual(policy._session.calls, [])

    async def test_failed_preflight_never_sends_private_post(self):
        failures = [preflight(**{'Access-Control-Allow-Origin': '*'}),
                    preflight(**{'Access-Control-Allow-Origin': 'https://other.example.com'}),
                    preflight(**{'Access-Control-Allow-Credentials': 'false'}),
                    preflight(**{'Access-Control-Allow-Methods': 'GET'}),
                    preflight(**{'Access-Control-Allow-Headers': '*'}),
                    preflight(**{'Access-Control-Allow-Headers': 'content-type'}),
                    Response(302, headers={'Location': '/next'}), Response(403)]
        for response in failures:
            with self.subTest(status=response.status, headers=list(response.headers)):
                policy = relay([response])
                current = route(method='POST')
                await policy.handle(current)
                self.assertEqual(len(policy._session.calls), 1)
                method, _, sent = policy._session.calls[0]
                self.assertEqual(method, 'OPTIONS')
                self.assertIsNone(sent['data'])
                self.assertEqual(sent['headers'], {'Origin': ORIGIN, 'Access-Control-Request-Method': 'POST',
                    'Access-Control-Request-Headers': 'authorization, content-type'})
                self.assertNotIn(CANARY, str(sent))
                current.abort.assert_awaited_once()
                current.fulfill.assert_not_awaited()

    async def test_response_cors_and_initiating_origin_cannot_be_forged(self):
        for headers in ({}, {'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Credentials': 'true'},
                        {'Access-Control-Allow-Origin': ORIGIN}):
            policy = relay([Response(headers=headers)])
            current = route()
            await policy.handle(current)
            current.abort.assert_awaited_once()
            current.fulfill.assert_not_awaited()
        policy = relay([])
        current = route()
        current.request.all_headers.return_value['origin'] = 'https://other.example.com'
        await policy.handle(current)
        self.assertEqual(policy._session.calls, [])
        current.abort.assert_awaited_once()

    def test_preflight_safelist_is_bounded_and_excludes_only_automatic_headers(self):
        self.assertEqual(_preflight_headers({'cookie': CANARY, 'origin': ORIGIN, 'sec-fetch-mode': 'cors',
            'user-agent': 'fixture', 'accept': 'application/json', 'content-type': 'text/plain;charset=UTF-8'}), [])
        for headers in ({'authorization': CANARY}, {'content-type': 'application/json'}, {'x-fixture': 'ok'},
                        {'accept': 'a' * 129}, {'accept': 'invalid(value)'}, {'content-language': 'bad!'},
                        {'priority': 'u=1'}):
            self.assertEqual(_preflight_headers(headers), list(headers))

    async def test_body_total_budget_timeout_and_queued_close_fail_closed(self):
        policy = relay([Response(body=b'12345')])
        policy.MAX_BODY_BYTES = 4
        current = route()
        await policy.handle(current)
        current.abort.assert_awaited_once()
        current.fulfill.assert_not_awaited()
        policy = relay([Response(body=b'123')])
        policy.MAX_TOTAL_BYTES = 2
        current = route()
        await policy.handle(current)
        current.abort.assert_awaited_once()
        policy = relay([])
        policy.REQUEST_SECONDS = 0.01
        current = route()
        async def stuck():
            await asyncio.Event().wait()
        current.request.all_headers = stuck
        await policy.handle(current)
        current.abort.assert_awaited_once()
        self.assertEqual(policy._session.calls, [])
        policy = relay([])
        policy._semaphore = asyncio.Semaphore(0)
        current = route()
        task = asyncio.create_task(policy.handle(current))
        await asyncio.sleep(0)
        await policy.close()
        policy._semaphore.release()
        self.assertTrue(await task)
        current.abort.assert_awaited_once()


class AuthHostBrowserCookieTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_chromium_anonymous_module_cors_without_acac_and_redirect_checks(self):
        from playwright.async_api import async_playwright
        driver = await async_playwright().start()
        browser = None
        try:
            browser = await driver.chromium.launch(headless=True, chromium_sandbox=True)
            cases = [('*', '*', True), (ORIGIN, ORIGIN, True),
                     (None, '*', False), ('*', 'https://other.example.com', False)]
            for redirect_origin, final_origin, accepted in cases:
                with self.subTest(redirect_origin=redirect_origin, final_origin=final_origin):
                    context = await browser.new_context(service_workers='block')
                    await context.add_cookies([{'name': 'existing', 'value': CANARY,
                        'url': 'https://' + HOST, 'httpOnly': True, 'secure': True, 'sameSite': 'Lax'}])
                    redirect_headers = {'Location': '/module.js', 'Set-Cookie': 'intermediate=private; Secure'}
                    if redirect_origin is not None:
                        redirect_headers['Access-Control-Allow-Origin'] = redirect_origin
                    policy = relay([Response(302, headers=redirect_headers),
                        Response(body=b'window.fixtureLoaded=true; export {};', headers={
                            'Content-Type': 'application/javascript', 'Access-Control-Allow-Origin': final_origin,
                            'Set-Cookie': 'final=private; Secure'})])
                    modes = []
                    async def intercept(current):
                        if current.request.url == ORIGIN + '/':
                            await current.fulfill(status=200, content_type='text/html', body='<p>Fixture</p>')
                        else:
                            observed_headers = await current.request.all_headers()
                            modes.append((observed_headers.get('sec-fetch-mode'), observed_headers.get('origin')))
                            if not await policy.handle(current):
                                await current.abort()
                    await context.route('**/*', intercept)
                    page = await context.new_page()
                    await page.goto(ORIGIN + '/')
                    result = await page.evaluate("""url => new Promise(resolve => {
                        const script=document.createElement('script');script.type='module';
                        script.crossOrigin='anonymous';script.src=url;
                        script.onload=()=>resolve(window.fixtureLoaded===true);
                        script.onerror=()=>resolve(false);document.head.append(script);
                    })""", 'https://' + HOST + '/entry.js')
                    self.assertEqual(result, accepted)
                    self.assertEqual(len(modes), 1)
                    self.assertIn(modes[0][0], (None, 'cors'))
                    self.assertEqual(modes[0][1], ORIGIN)
                    for method, url, sent in policy._session.calls:
                        self.assertEqual(sent['headers'], {'accept': '*/*', 'accept-encoding': 'identity', 'origin': ORIGIN})
                        self.assertIsNone(sent['data'])
                        self.assertNotIn(CANARY, str(sent))
                    cookies = {cookie['name']: cookie['value'] for cookie in await context.cookies('https://' + HOST)}
                    self.assertEqual(cookies, {'existing': CANARY})
                    await policy.close()
                    await context.close()
        finally:
            if browser is not None:
                await browser.close()
            await driver.stop()

    async def test_real_chromium_public_script_ignores_redirect_and_final_cookies(self):
        from playwright.async_api import async_playwright
        driver = await async_playwright().start()
        browser = None
        try:
            browser = await driver.chromium.launch(headless=True, chromium_sandbox=True)
            context = await browser.new_context(service_workers='block')
            await context.add_cookies([{'name': 'existing', 'value': CANARY,
                'url': 'https://' + HOST, 'httpOnly': True, 'secure': True, 'sameSite': 'Lax'}])
            policy = relay([Response(307, headers={'Location': '/version.js',
                    'Set-Cookie': 'intermediate=private; Path=/; Secure; HttpOnly'}),
                Response(200, b'window.fixtureLoaded=true', {'Content-Type': 'application/javascript',
                    'Set-Cookie': 'final=private; Path=/; Secure; HttpOnly'})])
            observed_cookie = []
            async def intercept(current):
                if current.request.url == ORIGIN + '/':
                    await current.fulfill(status=200, content_type='text/html', body='<p>Fixture</p>')
                else:
                    observed_cookie.append('existing=' + CANARY in (await current.request.all_headers()).get('cookie', ''))
                    if not await policy.handle(current):
                        await current.abort()
            await context.route('**/*', intercept)
            page = await context.new_page()
            await page.goto(ORIGIN + '/')
            result = await page.evaluate("""url => new Promise(resolve => {
                const script=document.createElement('script');script.src=url;
                script.onload=()=>resolve(window.fixtureLoaded===true);
                script.onerror=()=>resolve(false);document.head.append(script);
            })""", 'https://' + HOST + '/latest.js')
            self.assertTrue(result)
            self.assertEqual(observed_cookie, [True])
            for method, url, sent in policy._session.calls:
                self.assertEqual(sent['headers'], {'accept': '*/*', 'accept-encoding': 'identity'})
                self.assertIsNone(sent['data'])
            cookies = {cookie['name']: cookie['value'] for cookie in await context.cookies('https://' + HOST)}
            self.assertEqual(cookies, {'existing': CANARY})
            await policy.close()
        finally:
            if browser is not None:
                await browser.close()
            await driver.stop()

    async def test_real_chromium_negative_cors_response_observation(self):
        from playwright.async_api import async_playwright
        driver = await async_playwright().start()
        browser = None
        try:
            browser = await driver.chromium.launch(headless=True, chromium_sandbox=True)
            for cors in ({}, {'Access-Control-Allow-Origin': 'https://other.example.com'},
                         {'Access-Control-Allow-Origin': ORIGIN}):
                context = await browser.new_context(service_workers='block')
                policy = relay([preflight(), Response(200, b'{"ok":true}', {'Content-Type': 'application/json', **cors})])
                observed = []
                async def intercept(current):
                    if current.request.url == ORIGIN + '/':
                        await current.fulfill(status=200, content_type='text/html', body='<p>Fixture</p>')
                    else:
                        observed.append((current.request.method, current.request.resource_type))
                        if not await policy.handle(current):
                            await current.abort()
                await context.route('**/*', intercept)
                page = await context.new_page()
                await page.goto(ORIGIN + '/')
                result = await page.evaluate("""async url => {
                    try {
                        const response=await fetch(url,{method:'POST',credentials:'include',
                            headers:{'Content-Type':'application/json','Authorization':'Bearer fixture'},body:'{}'});
                        return {accepted:true,status:response.status};
                    } catch {return {accepted:false};}
                }""", 'https://' + HOST + '/v1/sign_in')
                self.assertEqual(observed, [('POST', 'fetch')])
                self.assertEqual([call[0] for call in policy._session.calls], ['OPTIONS', 'POST'])
                self.assertEqual(result, {'accepted': False}, msg='CORS response not enforced by intercepted Chromium')
                await policy.close()
                await context.close()
        finally:
            if browser is not None:
                await browser.close()
            await driver.stop()

    async def test_real_chromium_json_authorization_cookie_and_multiple_set_cookie(self):
        from playwright.async_api import async_playwright
        driver = await async_playwright().start()
        browser = None
        try:
            browser = await driver.chromium.launch(headless=True, chromium_sandbox=True)
            context = await browser.new_context(service_workers='block')
            await context.add_cookies([{'name': 'existing', 'value': CANARY,
                'url': 'https://' + HOST, 'httpOnly': True, 'secure': True, 'sameSite': 'Lax'}])
            policy = relay([preflight(), Response(200, b'{"ok":true}', [
                ('Content-Type', 'application/json'), ('Access-Control-Allow-Origin', ORIGIN),
                ('Access-Control-Allow-Credentials', 'true'),
                ('Access-Control-Allow-Headers', 'content-type, authorization'),
                ('Access-Control-Allow-Methods', 'POST'),
                ('Set-Cookie', 'first=one; Path=/; Secure; HttpOnly; SameSite=Lax'),
                ('Set-Cookie', 'second=two; Path=/; Secure; HttpOnly; SameSite=Lax')])])
            observed_methods = []
            async def intercept(current):
                if current.request.url == ORIGIN + '/':
                    await current.fulfill(status=200, content_type='text/html', body='<p>Fixture</p>')
                else:
                    observed_methods.append((current.request.method, current.request.resource_type))
                    if not await policy.handle(current):
                        await current.abort()
            await context.route('**/*', intercept)
            page = await context.new_page()
            await page.goto(ORIGIN + '/')
            result = await page.evaluate("""async ({url,value}) => {
                const response=await fetch(url,{method:'POST',credentials:'include',
                    headers:{'Content-Type':'application/json','Authorization':'Bearer '+value},
                    body:JSON.stringify({fixture:value})});
                return {status:response.status,ok:(await response.json()).ok};
            }""", {'url': 'https://' + HOST + '/v1/sign_in', 'value': CANARY})
            self.assertEqual(result, {'status': 200, 'ok': True})
            # Chromium intercepts POST without OPTIONS; the relay must perform
            # its own credential-free preflight before forwarding that POST.
            self.assertEqual(observed_methods, [('POST', 'fetch')])
            self.assertEqual([call[0] for call in policy._session.calls], ['OPTIONS', 'POST'])
            sent = policy._session.calls[1][2]
            self.assertIn('existing=' + CANARY, sent['headers'].get('cookie', ''))
            self.assertEqual(sent['headers']['authorization'], 'Bearer ' + CANARY)
            self.assertEqual(sent['headers']['content-type'], 'application/json')
            self.assertIn(CANARY.encode(), sent['data'])
            cookies = {cookie['name']: cookie for cookie in await context.cookies('https://' + HOST)}
            self.assertEqual(cookies['first']['value'], 'one')
            self.assertEqual(cookies['second']['value'], 'two')
            self.assertTrue(cookies['first']['httpOnly'])
            self.assertTrue(cookies['second']['secure'])
            self.assertNotIn(CANARY, await page.content())
            await policy.close()
        finally:
            if browser is not None:
                await browser.close()
            await driver.stop()


if __name__ == '__main__':
    unittest.main()
