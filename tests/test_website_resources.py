import asyncio
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from oak.website_resources import (StaticWebsiteResources, TIKTOK_ORIGIN, STATIC_HOSTS,
                                   _PinnedResolver, validate_static_resources)


SCRIPT_HOST, MIXED_HOST = STATIC_HOSTS
SCRIPT = 'https://' + SCRIPT_HOST + '/assets/login.js'
STYLE = 'https://' + MIXED_HOST + '/assets/login.css'
PUBLIC_IP = '93.184.216.34'
CANARY = 'synthetic-private-resource-canary'


class Response:
    def __init__(self, status=200, body=b'console.log("fixture")', headers=None):
        self.status, self.body = status, body
        self.headers = headers or {'Content-Type': 'application/javascript'}
        self.content_length = len(body)
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


def route(url=SCRIPT, method='GET', kind='script', body=None, navigation=False):
    request = SimpleNamespace(url=url, method=method, resource_type=kind, post_data_buffer=body,
                              headers={'Cookie': CANARY, 'Authorization': CANARY, 'Referer': CANARY},
                              is_navigation_request=lambda: navigation)
    return SimpleNamespace(request=request, fulfill=AsyncMock(), abort=AsyncMock())


def ready(responses):
    policy = StaticWebsiteResources(TIKTOK_ORIGIN)
    policy._pins = {host: PUBLIC_IP for host in STATIC_HOSTS}
    policy._session = Session(responses)
    return policy


class StaticPolicyConfigurationTests(unittest.TestCase):
    def test_empty_is_inactive_and_explicit_mapping_is_copied_and_normalized(self):
        self.assertEqual(validate_static_resources({}), {})
        source = {'https://login.example.com': {'assets.example.com': ['stylesheet', 'script']}}
        validated = validate_static_resources(source)
        self.assertEqual(validated, {'https://login.example.com': {'assets.example.com': ['script', 'stylesheet']}})
        source['https://login.example.com']['assets.example.com'].append('font')
        self.assertEqual(validated['https://login.example.com']['assets.example.com'], ['script', 'stylesheet'])

    def test_origins_hosts_types_and_counts_are_strict(self):
        invalid = [None, [], True, {'https://login.example.com': {}},
                   {'https://login.example.com': {'assets.example.com': []}}]
        for origin in ('http://login.example.com', 'https://login.example.com/',
                       'https://login.example.com:443', 'https://USER:pass@login.example.com',
                       'https://login.example.com?x=1', 'https://login.example.com#x',
                       'https://LOGIN.example.com', 'https://*.example.com',
                       'https://127.0.0.1', 'https://127.1', 'https://[::1]',
                       'https://localhost', 'https://app.localhost', 'https://app.local',
                       'https://xn--example.com', 'https://exämple.com'):
            invalid.append({origin: {'assets.example.com': ['script']}})
        for host in ('assets.example.com:443', 'https://assets.example.com', '*.example.com',
                     'assets.example.com/', 'Assets.example.com', 'assets.example.com.',
                     '127.0.0.1', '93.184.216.34', '0x7f.1', '1.0x1', 'localhost',
                     'xn--example.com', 'exämple.com', 'assets..example.com',
                     'login.example.com', 'a' * 64 + '.example.com'):
            invalid.append({'https://login.example.com': {host: ['script']}})
        for types in (None, 'script', {'script': True}, [], ['script', 'script'], ['image'],
                      ['xhr'], ['fetch'], ['document'], ['websocket'], ['script', True], [[]]):
            invalid.append({'https://login.example.com': {'assets.example.com': types}})
        invalid += [
            {f'https://site{i}.example.com': {'assets.example.com': ['script']} for i in range(17)},
            {'https://login.example.com': {f'cdn{i}.example.com': ['script'] for i in range(9)}},
            {f'https://site{i}.example.com': {f'cdn{i}-{j}.example.com': ['script'] for j in range(8)}
             for i in range(5)},
            {f'https://site{i}.example.com': {
                f'cdn{j}.' + 'a' * 60 + '.' + 'b' * 60 + '.example.com': ['script'] for j in range(8)}
             for i in range(16)}]
        for value in invalid:
            with self.subTest(policy=value), self.assertRaisesRegex(ValueError, '^Public website resource unavailable.$'):
                validate_static_resources(value)

    def test_legacy_merge_only_accepts_identical_duplicate_grants_and_stays_bounded(self):
        legacy = {host: sorted(kinds) for host, kinds in STATIC_HOSTS.items()}
        self.assertEqual(validate_static_resources({}, tiktok=True), {TIKTOK_ORIGIN: legacy})
        custom = {TIKTOK_ORIGIN: {SCRIPT_HOST: ['script'], 'explicit.example.com': ['stylesheet']}}
        merged = validate_static_resources(custom, tiktok=True)
        self.assertEqual(merged[TIKTOK_ORIGIN], {**legacy, 'explicit.example.com': ['stylesheet']})
        for types in (['script', 'stylesheet'], ['font']):
            with self.assertRaises(ValueError):
                validate_static_resources({TIKTOK_ORIGIN: {SCRIPT_HOST: types}}, tiktok=True)
        with self.assertRaises(ValueError):
            validate_static_resources({TIKTOK_ORIGIN: {f'cdn{i}.example.com': ['script'] for i in range(7)}}, tiktok=True)
        with self.assertRaises(ValueError):
            validate_static_resources({f'https://site{i}.example.com': {'assets.example.com': ['script']}
                                       for i in range(16)}, tiktok=True)


class WebsiteResourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_exact_hosts_keep_96_request_cap_and_safe_diagnostics(self):
        policy = ready([Response(body=b'public') for _ in range(96)])
        for index in range(96):
            request = route(SCRIPT + f'/{index}')
            await policy.handle(request)
            request.fulfill.assert_awaited_once()
        extra = route(SCRIPT + '/' + CANARY)
        await policy.handle(extra)
        extra.abort.assert_awaited_once()
        self.assertEqual(len(policy._session.calls), 96)
        snapshot = policy.diagnostics()
        self.assertEqual(snapshot, {'requests': 96, 'max_requests': 96, 'bytes': 576,
            'hosts': 2, 'completed_urls': 96, 'frozen': False, 'failures': {'request_quota': 1}})
        snapshot['failures']['request_quota'] = 123
        self.assertEqual(policy.diagnostics()['failures'], {'request_quota': 1})
        self.assertNotIn(CANARY, str(policy.diagnostics()))
        self.assertNotIn(SCRIPT_HOST, str(policy.diagnostics()))
        await policy.close()

    async def test_custom_policy_resolves_only_its_copied_exact_hosts(self):
        hosts = {'assets.example.com': ['script'], 'styles.example.com': ['stylesheet']}
        resolver = AsyncMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', (PUBLIC_IP, 443))])
        policy = StaticWebsiteResources('https://login.example.com', hosts=hosts, resolver=resolver)
        hosts['new.example.com'] = ['script']
        hosts['assets.example.com'].append('stylesheet')
        session = Session([])
        with patch('oak.website_resources.TCPConnector'), patch('oak.website_resources.ClientSession', return_value=session):
            await asyncio.gather(policy.prepare(), policy.prepare())
        self.assertEqual({call.args[0] for call in resolver.call_args_list}, {'assets.example.com', 'styles.example.com'})
        self.assertEqual(resolver.await_count, 2)
        denied = route('https://assets.example.com/file.css', kind='stylesheet')
        await policy.handle(denied)
        denied.abort.assert_awaited_once()
        self.assertFalse(await policy.handle(route('https://new.example.com/file.js')))
        self.assertEqual(session.calls, [])
        await policy.close()

    async def test_custom_policy_cors_is_bound_to_its_origin_and_remains_cookie_blind(self):
        origin = 'https://login.example.com'
        for allow_origin in (origin, '*', TIKTOK_ORIGIN, 'https://other.example.com'):
            with self.subTest(cors=allow_origin):
                policy = StaticWebsiteResources(origin, hosts={'assets.example.com': ['script']})
                policy._pins = {'assets.example.com': PUBLIC_IP}
                policy._session = Session([Response(headers={'Content-Type': 'application/javascript',
                    'Access-Control-Allow-Origin': allow_origin, 'Set-Cookie': CANARY,
                    'Access-Control-Allow-Credentials': 'true'})])
                request = route('https://assets.example.com/file.js')
                await policy.handle(request)
                headers = request.fulfill.call_args.kwargs['headers']
                self.assertEqual(headers.get('access-control-allow-origin'), allow_origin if allow_origin in {origin, '*'} else None)
                self.assertNotIn(CANARY, str(request.fulfill.call_args))
                self.assertEqual(policy._session.calls[0][2], {'allow_redirects': False, 'ssl': True})
                await policy.close()

    async def test_custom_policy_preserves_type_redirect_url_and_post_input_path_limits(self):
        policy = StaticWebsiteResources('https://login.example.com', hosts={
            'assets.example.com': ['script'], 'styles.example.com': ['stylesheet']})
        policy._pins = {host: PUBLIC_IP for host in policy._hosts}
        policy._session = Session([Response(), Response(302, headers={'Location': 'https://styles.example.com/next.js'})])
        initial = 'https://assets.example.com/file.js'
        await policy.handle(route(initial))
        redirected = route('https://assets.example.com/redirect.js')
        await policy.handle(redirected)
        redirected.abort.assert_awaited_once()
        self.assertEqual(len(policy._session.calls), 2)
        policy.freeze()
        for request in (route('https://assets.example.com/' + CANARY), route(initial + '?token=' + CANARY),
                        route(initial, method='POST'), route(initial, kind='xhr'),
                        route(initial, body=b'private')):
            await policy.handle(request)
            request.abort.assert_awaited_once()
        self.assertFalse(await policy.handle(route(initial, navigation=True)))
        self.assertFalse(await policy.handle(route('https://assets.example.com.evil.test/file.js')))
        self.assertEqual(len(policy._session.calls), 2)
        self.assertNotIn(CANARY, str(policy._session.calls))
        await policy.close()

    async def test_only_exact_authorized_origin_can_activate_policy(self):
        for origin in ('https://example.com', 'https://tiktok.com', 'http://www.tiktok.com',
                       'https://www.tiktok.com:443', 'https://www.tiktok.com.evil.test'):
            resolver = AsyncMock()
            policy = StaticWebsiteResources(origin, resolver=resolver)
            await policy.prepare()
            self.assertFalse(policy.active)
            self.assertFalse(await policy.handle(route()))
            resolver.assert_not_awaited()
            self.assertIsNone(policy._session)

    async def test_private_dns_answer_mixed_answers_and_empty_answers_rejected(self):
        for addresses in ([], ['127.0.0.1'], ['10.0.0.1'], ['::1'], ['169.254.169.254'],
                          [PUBLIC_IP, '192.168.1.1'], ['::ffff:127.0.0.1'], ['not-an-address']):
            records = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443)) for address in addresses]
            policy = StaticWebsiteResources(TIKTOK_ORIGIN, resolver=AsyncMock(return_value=records))
            with patch('oak.website_resources.ClientSession') as session:
                with self.assertRaisesRegex(ValueError, '^Public website resource unavailable.$'):
                    await policy.prepare()
                session.assert_not_called()
            self.assertEqual(policy._pins, {})

    async def test_prepare_pins_once_and_uses_independent_credential_free_session(self):
        records = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (PUBLIC_IP, 443))]
        resolver = AsyncMock(return_value=records)
        policy = StaticWebsiteResources(TIKTOK_ORIGIN, resolver=resolver)
        fake = Session([])
        with patch('oak.website_resources.TCPConnector') as connector, \
                patch('oak.website_resources.ClientSession', return_value=fake) as session:
            await policy.prepare()
            await policy.prepare()
            self.assertEqual(resolver.await_count, 2)
            config = session.call_args.kwargs
            self.assertEqual(type(config['cookie_jar']).__name__, 'DummyCookieJar')
            self.assertFalse(config['trust_env'])
            self.assertFalse(config['auto_decompress'])
            self.assertEqual(config['headers'], {'Accept': '*/*', 'Accept-Encoding': 'identity'})
            self.assertTrue(connector.call_args.kwargs['ssl'])
            self.assertFalse(connector.call_args.kwargs['use_dns_cache'])
            pin = connector.call_args.kwargs['resolver']
            self.assertEqual((await pin.resolve(SCRIPT_HOST, 443))[0]['host'], PUBLIC_IP)
            # Later DNS rebinding never influences a prepared connection target.
            resolver.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
            self.assertEqual((await pin.resolve(SCRIPT_HOST, 443))[0]['host'], PUBLIC_IP)
            self.assertEqual(resolver.await_count, 2)
        await policy.close()
        fake.close.assert_awaited_once()
        self.assertEqual(policy._pins, {})
        self.assertIsNone(policy._session)

    async def test_pinned_resolver_rejects_unknown_hosts_ports_and_nonpublic_pins(self):
        resolver = _PinnedResolver({SCRIPT_HOST: PUBLIC_IP, MIXED_HOST: '127.0.0.1'})
        for host, port in ((SCRIPT_HOST, 80), ('localhost', 443), (MIXED_HOST, 443)):
            with self.assertRaises(ValueError):
                await resolver.resolve(host, port)

    async def test_rejects_unsafe_methods_bodies_types_and_urls_before_http(self):
        cases = [route(method='POST'), route(body=b'x'), route(method='PUT'),
                 route(kind='xhr'), route(kind='fetch'), route(kind='websocket'),
                 route(kind='ping'), route(kind='image'), route(kind='stylesheet'),
                 route(url=SCRIPT + '?token=' + CANARY), route(url=SCRIPT + '?'),
                 route(url=SCRIPT + '#fragment'), route(url=SCRIPT.replace('https:', 'http:')),
                 route(url=SCRIPT.replace(SCRIPT_HOST, SCRIPT_HOST + ':444')),
                 route(url=SCRIPT.replace(SCRIPT_HOST, 'user:password@' + SCRIPT_HOST))]
        policy = ready([])
        for request in cases:
            with self.subTest(url=request.request.url, kind=request.request.resource_type):
                self.assertTrue(await policy.handle(request))
                request.abort.assert_awaited_once()
                request.fulfill.assert_not_awaited()
        self.assertEqual(policy._session.calls, [])

    async def test_unlisted_hosts_and_document_navigation_stay_under_original_policy(self):
        policy = ready([])
        for request in (route(url='https://evil.test/script.js'), route(navigation=True),
                        route(url='https://' + SCRIPT_HOST + '.evil.test/script.js')):
            self.assertFalse(await policy.handle(request))
            request.abort.assert_not_awaited()
            request.fulfill.assert_not_awaited()
        self.assertEqual(policy._session.calls, [])

    async def test_browser_secrets_and_upstream_cookies_or_redirect_headers_never_propagate(self):
        response = Response(headers={'Content-Type': 'application/javascript; charset=utf-8',
                                     'Set-Cookie': CANARY, 'Location': 'https://evil.test/' + CANARY,
                                     'Authorization': CANARY, 'Access-Control-Allow-Credentials': 'true',
                                     'Access-Control-Allow-Origin': TIKTOK_ORIGIN})
        policy = ready([response])
        request = route()
        self.assertTrue(await policy.handle(request))
        self.assertEqual(policy._session.calls, [('GET', SCRIPT, {'allow_redirects': False, 'ssl': True})])
        fulfilled = request.fulfill.call_args.kwargs
        self.assertNotIn(CANARY, str(fulfilled))
        self.assertEqual(fulfilled['headers'], {'content-type': 'application/javascript',
                         'cache-control': 'no-store', 'access-control-allow-origin': TIKTOK_ORIGIN})
        self.assertEqual(fulfilled['body'], response.body)
        request.abort.assert_not_awaited()

    async def test_allowed_redirect_is_revalidated_and_follows_only_pinned_hosts(self):
        destination = 'https://' + MIXED_HOST + '/assets/next.js'
        policy = ready([Response(302, headers={'Location': destination}), Response()])
        request = route()
        await policy.handle(request)
        self.assertEqual([call[1] for call in policy._session.calls], [SCRIPT, destination])
        self.assertTrue(all(call[2] == {'allow_redirects': False, 'ssl': True} for call in policy._session.calls))
        request.fulfill.assert_awaited_once()

    async def test_denied_redirect_never_requests_target(self):
        for location in ('https://127.0.0.1/internal', 'http://' + SCRIPT_HOST + '/file.js',
                         'https://evil.test/file.js', 'https://' + MIXED_HOST + ':444/file.js',
                         '/file.js?token=' + CANARY, '//user:pass@' + MIXED_HOST + '/file.js',
                         '\nhttps://' + MIXED_HOST + '/file.js'):
            policy = ready([Response(302, headers={'Location': location})])
            request = route()
            await policy.handle(request)
            self.assertEqual(len(policy._session.calls), 1)
            request.abort.assert_awaited_once()
            request.fulfill.assert_not_awaited()

    async def test_redirect_cannot_change_host_resource_type_permission(self):
        policy = ready([Response(302, headers={'Location': 'https://' + SCRIPT_HOST + '/file.css'})])
        request = route(STYLE, kind='stylesheet')
        await policy.handle(request)
        self.assertEqual(len(policy._session.calls), 1)
        request.abort.assert_awaited_once()

    async def test_redirect_count_content_type_and_compression_bounds(self):
        looping = ready([Response(302, headers={'Location': '/again.js'}) for _ in range(4)])
        request = route()
        await looping.handle(request)
        self.assertEqual(len(looping._session.calls), 4)
        request.abort.assert_awaited_once()
        for response in (Response(404), Response(headers={'Content-Type': 'text/html'}),
                         Response(headers={'Content-Type': 'application/javascript', 'Content-Encoding': 'gzip'}),
                         Response(headers={'Content-Type': 'application/javascript', 'Content-Encoding': 'unknown'})):
            request = route()
            await ready([response]).handle(request)
            request.abort.assert_awaited_once()
            request.fulfill.assert_not_awaited()

    async def test_resource_and_total_byte_limits_and_call_quota_are_enforced(self):
        policy = ready([Response(body=b'12345')])
        policy.MAX_RESOURCE_BYTES = 4
        request = route()
        await policy.handle(request)
        request.abort.assert_awaited_once()
        policy = ready([Response(body=b'123'), Response(body=b'123')])
        policy.MAX_TOTAL_BYTES = 5
        first, second = route(), route()
        await policy.handle(first)
        await policy.handle(second)
        first.fulfill.assert_awaited_once()
        second.abort.assert_awaited_once()
        policy = ready([Response()])
        policy.MAX_REQUESTS = 1
        await policy.handle(route())
        second = route()
        await policy.handle(second)
        self.assertEqual(len(policy._session.calls), 1)
        second.abort.assert_awaited_once()

    async def test_timeout_aborts_without_retry(self):
        policy = ready([])
        policy.REQUEST_SECONDS = 0.01
        async def wait(*args):
            await asyncio.sleep(1)
        policy._fetch = AsyncMock(side_effect=wait)
        request = route()
        await policy.handle(request)
        request.abort.assert_awaited_once()
        policy._fetch.assert_awaited_once()

    async def test_close_prevents_further_fetches(self):
        policy = ready([])
        session = policy._session
        await policy.close()
        await policy.close()
        request = route()
        await policy.handle(request)
        request.abort.assert_awaited_once()
        self.assertEqual(session.calls, [])
        session.close.assert_awaited_once()

    async def test_freeze_preserves_completed_anonymous_paths_and_rejects_new_paths(self):
        destination = 'https://' + MIXED_HOST + '/assets/next.js'
        policy = ready([Response(302, headers={'Location': destination}), Response(), Response()])
        await policy.handle(route())
        self.assertEqual(policy._anonymous_urls, {SCRIPT, destination})
        policy.freeze()
        previous = route(destination)
        await policy.handle(previous)
        previous.fulfill.assert_awaited_once()
        leaked = route('https://' + MIXED_HOST + '/' + CANARY + '.js')
        await policy.handle(leaked)
        leaked.abort.assert_awaited_once()
        self.assertEqual(len(policy._session.calls), 3)
        self.assertNotIn(CANARY, str(policy._session.calls))

    async def test_freeze_during_download_cannot_approve_unseen_redirect_path(self):
        policy = ready([Response(302, headers={'Location': '/new.js'})])
        original = policy._session.request
        def freeze_on_response(*args, **kwargs):
            response = original(*args, **kwargs)
            policy.freeze()
            return response
        policy._session.request = freeze_on_response
        request = route()
        await policy.handle(request)
        request.abort.assert_awaited_once()
        self.assertEqual(len(policy._session.calls), 1)
        self.assertEqual(policy._anonymous_urls, set())

    async def test_failed_download_does_not_approve_its_path_before_freeze(self):
        policy = ready([Response(404)])
        await policy.handle(route())
        self.assertEqual(policy._anonymous_urls, set())
        policy.freeze()
        request = route()
        await policy.handle(request)
        request.abort.assert_awaited_once()
        self.assertEqual(len(policy._session.calls), 1)

    async def test_close_retains_session_until_cleanup_succeeds(self):
        for failure in (RuntimeError('synthetic close failure'), asyncio.CancelledError()):
            policy = ready([])
            session = policy._session
            session.close.side_effect = [failure, None]
            with self.assertRaises(type(failure)):
                await policy.close()
            self.assertIs(policy._session, session)
            await policy.close()
            self.assertIsNone(policy._session)
            self.assertEqual(session.close.await_count, 2)

    async def test_close_while_dns_is_resolving_never_opens_a_session(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def resolve(*args, **kwargs):
            entered.set()
            await release.wait()
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (PUBLIC_IP, 443))]
        policy = StaticWebsiteResources(TIKTOK_ORIGIN, resolver=resolve)
        with patch('oak.website_resources.ClientSession') as session:
            pending = asyncio.create_task(policy.prepare())
            await entered.wait()
            await policy.close()
            release.set()
            with self.assertRaises(ValueError):
                await pending
            session.assert_not_called()
        self.assertEqual(policy._pins, {})


if __name__ == '__main__':
    unittest.main()
