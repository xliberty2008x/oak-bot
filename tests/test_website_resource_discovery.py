"""Anonymous dependency discovery; local fake transports, never owner data."""
import asyncio
import socket
import unittest
from unittest.mock import AsyncMock, patch

from oak.website_resources import AnonymousWebsiteResources, CONTENT_TYPES
from test_website_resources import Response, Session, route, PUBLIC_IP, CANARY

ORIGIN = 'https://login.example.com'
SCRIPT = 'https://assets.example.com/bootstrap.js'
STYLE = 'https://styles.example.com/login.css'


def records(*addresses):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443)) for address in addresses]


def ready(responses, resolver=None):
    value = AnonymousWebsiteResources(ORIGIN, resolver=resolver or AsyncMock(return_value=records(PUBLIC_IP)))
    value._session = Session(responses)
    return value


class AnonymousDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiple_documents_share_512_requests_and_stop_exactly_at_quota(self):
        policy = ready([Response(body=b'public') for _ in range(512)])
        # Reuse one lease across two document dependency sets, both beyond the
        # legacy 96-request limit that exhausted the real homepage/login flow.
        for document in ('homepage', 'signin'):
            for index in range(256):
                request = route(f'https://assets.example.com/{document}/{index}.js')
                await policy.handle(request)
                request.fulfill.assert_awaited_once()
                request.abort.assert_not_awaited()
        rejected = [route(f'https://assets.example.com/extra/{index}.js') for index in range(3)]
        await asyncio.gather(*(policy.handle(request) for request in rejected))
        for request in rejected:
            request.abort.assert_awaited_once()
            request.fulfill.assert_not_awaited()
        self.assertEqual(len(policy._session.calls), 512)
        self.assertEqual(policy.diagnostics(), {
            'requests': 512, 'max_requests': 512, 'bytes': 512 * 6, 'hosts': 1,
            'completed_urls': 512, 'frozen': False, 'failures': {'request_quota': 3}})
        self.assertEqual(policy._resolver.await_count, 1)
        await policy.close()

    async def test_larger_request_budget_keeps_total_bytes_and_freeze_boundaries(self):
        policy = ready([Response(body=b'123'), Response(body=b'123'), Response(body=b'1')])
        policy.MAX_TOTAL_BYTES = 5
        first, second = route(SCRIPT), route(STYLE, kind='stylesheet')
        # Use an otherwise valid stylesheet so the shared byte budget rejects it.
        policy._session.responses[1].headers = {'Content-Type': 'text/css'}
        await policy.handle(first)
        await policy.handle(second)
        first.fulfill.assert_awaited_once()
        second.abort.assert_awaited_once()
        self.assertEqual(policy._anonymous_urls, {SCRIPT})
        self.assertEqual(policy.diagnostics()['failures'], {'request_quota': 0})
        policy.freeze()
        unseen = route('https://new.example.com/' + CANARY + '.js')
        await policy.handle(unseen)
        unseen.abort.assert_awaited_once()
        self.assertEqual(len(policy._session.calls), 2)
        self.assertEqual(policy._resolver.await_count, 2)
        self.assertNotIn(CANARY, str(policy.diagnostics()))
        await policy.close()

    async def test_discovered_dependencies_use_pinned_anonymous_transport(self):
        resolver = AsyncMock(return_value=records(PUBLIC_IP))
        policy = AnonymousWebsiteResources(ORIGIN, resolver=resolver)
        session = Session([Response(headers={'Content-Type': 'application/javascript',
            'Set-Cookie': CANARY, 'Authorization': CANARY, 'Access-Control-Allow-Credentials': 'true',
            'Access-Control-Allow-Origin': ORIGIN})])
        with patch('oak.website_resources.TCPConnector') as connector, \
                patch('oak.website_resources.ClientSession', return_value=session) as factory:
            await policy.prepare()
            pin_resolver = connector.call_args.kwargs['resolver']
            with self.assertRaises(ValueError):
                await pin_resolver.resolve('assets.example.com', 443)
            request = route(SCRIPT)
            self.assertTrue(await policy.handle(request))
            self.assertEqual((await pin_resolver.resolve('assets.example.com', 443))[0]['host'], PUBLIC_IP)
            kwargs = factory.call_args.kwargs
            self.assertEqual(type(kwargs['cookie_jar']).__name__, 'DummyCookieJar')
            self.assertFalse(kwargs['trust_env'])
            self.assertFalse(kwargs['auto_decompress'])
            self.assertEqual(kwargs['headers'], {'Accept': '*/*', 'Accept-Encoding': 'identity'})
            self.assertEqual(kwargs['skip_auto_headers'], {'User-Agent', 'Content-Type'})
            self.assertFalse(connector.call_args.kwargs['use_dns_cache'])
        self.assertEqual(session.calls, [('GET', SCRIPT, {'allow_redirects': False, 'ssl': True})])
        self.assertNotIn(CANARY, str(session.calls) + str(request.fulfill.call_args))
        request.abort.assert_not_awaited()
        self.assertEqual(policy._anonymous_urls, {SCRIPT})
        await policy.close()

    async def test_nonstatic_requests_and_navigation_cannot_discover_or_fetch(self):
        resolver = AsyncMock()
        policy = ready([], resolver)
        for kind in ('xhr', 'fetch', 'document', 'websocket', 'ping', 'image', 'media', 'other'):
            request = route(SCRIPT, kind=kind)
            self.assertFalse(await policy.handle(request))
            request.abort.assert_not_awaited()
            request.fulfill.assert_not_awaited()
        self.assertFalse(await policy.handle(route(SCRIPT, navigation=True)))
        resolver.assert_not_awaited()
        self.assertEqual(policy._session.calls, [])
        await policy.close()

    async def test_unsafe_static_urls_methods_and_bodies_never_resolve(self):
        resolver = AsyncMock()
        policy = ready([], resolver)
        cases = [route(SCRIPT, method='POST'), route(SCRIPT, body=b'private'),
                 route(SCRIPT + '?'), route(SCRIPT + '?token=' + CANARY), route(SCRIPT + '#fragment')]
        cases += [route(url) for url in ('http://assets.example.com/a.js',
            'https://127.0.0.1/a.js', 'https://93.184.216.34/a.js', 'https://localhost/a.js',
            'https://app.local/a.js', 'https://assets.example.com:8443/a.js',
            'https://user:private@assets.example.com/a.js', 'https://assets.example.com\\evil/a.js',
            'https://assets.example.com/\nfile.js', 'https://xn--example.com/a.js')]
        for request in cases:
            self.assertTrue(await policy.handle(request))
            request.abort.assert_awaited_once()
            request.fulfill.assert_not_awaited()
        resolver.assert_not_awaited()
        self.assertEqual(policy._session.calls, [])
        await policy.close()

    async def test_mixed_private_empty_dns_and_repeated_failure_are_denied(self):
        for answers in ([], ['127.0.0.1'], ['10.0.0.1'], ['::1'], [PUBLIC_IP, '192.168.1.1'],
                        ['169.254.169.254'], ['::ffff:127.0.0.1'], ['not-an-ip']):
            with self.subTest(answers=answers):
                resolver = AsyncMock(return_value=records(*answers))
                policy = ready([], resolver)
                for _ in range(2):
                    request = route(SCRIPT)
                    await policy.handle(request)
                    request.abort.assert_awaited_once()
                self.assertEqual(resolver.await_count, 1)
                self.assertEqual(policy._pins, {})
                self.assertEqual(policy._session.calls, [])
                await policy.close()

    async def test_duplicate_concurrent_discovery_resolves_once_and_pins_forever(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def resolve(*args, **kwargs):
            entered.set()
            await release.wait()
            return records(PUBLIC_IP)
        resolver = AsyncMock(side_effect=resolve)
        policy = ready([Response(), Response(), Response()], resolver)
        first, second = route(SCRIPT), route(SCRIPT)
        tasks = [asyncio.create_task(policy.handle(first)), asyncio.create_task(policy.handle(second))]
        await entered.wait()
        release.set()
        await asyncio.gather(*tasks)
        first.fulfill.assert_awaited_once(); second.fulfill.assert_awaited_once()
        self.assertEqual(resolver.await_count, 1)
        resolver.side_effect = None
        resolver.return_value = records('127.0.0.1')
        await policy.handle(route(SCRIPT))
        self.assertEqual(resolver.await_count, 1)
        self.assertEqual(policy._pins, {'assets.example.com': PUBLIC_IP})
        await policy.close()

    async def test_host_attempt_budget_includes_failed_dns_and_redirect_hosts(self):
        resolver = AsyncMock(side_effect=[records('127.0.0.1'), records(PUBLIC_IP)])
        policy = ready([Response(302, headers={'Location': 'https://third.example.com/new.js'})], resolver)
        policy.MAX_DISCOVERED_HOSTS = 2
        await policy.handle(route('https://bad.example.com/file.js'))
        second = route(SCRIPT)
        await policy.handle(second)
        second.abort.assert_awaited_once()
        self.assertEqual(resolver.await_count, 2)
        self.assertEqual(policy._host_attempts, {'bad.example.com', 'assets.example.com'})
        self.assertEqual(len(policy._session.calls), 1)
        await policy.close()

    async def test_concurrent_distinct_hosts_cannot_overrun_budget(self):
        resolver = AsyncMock(return_value=records(PUBLIC_IP))
        policy = ready([Response(), Response()], resolver)
        policy.MAX_DISCOVERED_HOSTS = 2
        requests = [route(f'https://cdn{i}.example.com/file.js') for i in range(8)]
        await asyncio.gather(*(policy.handle(request) for request in requests))
        self.assertEqual(resolver.await_count, 2)
        self.assertEqual(sum(request.fulfill.await_count for request in requests), 2)
        self.assertEqual(sum(request.abort.await_count for request in requests), 6)
        await policy.close()

    async def test_public_redirect_discovery_is_bounded_and_rechecked(self):
        destination = 'https://second.example.com/next.js'
        resolver = AsyncMock(return_value=records(PUBLIC_IP))
        policy = ready([Response(302, headers={'Location': destination}), Response()], resolver)
        request = route(SCRIPT)
        await policy.handle(request)
        request.fulfill.assert_awaited_once()
        self.assertEqual({call.args[0] for call in resolver.call_args_list}, {'assets.example.com', 'second.example.com'})
        self.assertEqual(policy._anonymous_urls, {SCRIPT, destination})
        self.assertEqual([v[1] for v in policy._session.calls], [SCRIPT, destination])
        await policy.close()
        policy = ready([Response(302, headers={'Location': 'https://private.example.com/next.js'})],
                       AsyncMock(side_effect=[records(PUBLIC_IP), records('10.0.0.1')]))
        request = route(SCRIPT)
        await policy.handle(request)
        request.abort.assert_awaited_once()
        self.assertEqual(len(policy._session.calls), 1)
        await policy.close()

    async def test_freeze_allows_only_completed_exact_urls_without_new_dns(self):
        resolver = AsyncMock(return_value=records(PUBLIC_IP))
        policy = ready([Response(), Response()], resolver)
        await policy.handle(route(SCRIPT))
        policy.freeze()
        existing = route(SCRIPT)
        await policy.handle(existing)
        existing.fulfill.assert_awaited_once()
        for url in ('https://assets.example.com/' + CANARY + '.js',
                    'https://new.example.com/file.js', SCRIPT + '?token=' + CANARY):
            request = route(url)
            await policy.handle(request)
            request.abort.assert_awaited_once()
        self.assertEqual(resolver.await_count, 1)
        self.assertEqual(len(policy._session.calls), 2)
        self.assertNotIn(CANARY, str(policy._session.calls))
        await policy.close()

    async def test_freeze_or_close_during_dns_cannot_install_pin_or_fetch(self):
        for closing in (False, True):
            with self.subTest(closing=closing):
                entered, release = asyncio.Event(), asyncio.Event()
                async def resolve(*args, **kwargs):
                    entered.set()
                    await release.wait()
                    return records(PUBLIC_IP)
                policy = ready([], resolve)
                session = policy._session
                request = route(SCRIPT)
                pending = asyncio.create_task(policy.handle(request))
                await entered.wait()
                if closing: await policy.close()
                else: policy.freeze()
                release.set()
                await pending
                request.abort.assert_awaited_once()
                self.assertEqual(policy._pins, {})
                self.assertEqual(session.calls, [])
                await policy.close()

    async def test_freeze_during_body_discards_late_completion_without_approving_path(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class Slow(Response):
            async def iter_chunked(self, size):
                entered.set()
                await release.wait()
                yield b'public-script'
        policy = ready([Slow()])
        request = route(SCRIPT)
        pending = asyncio.create_task(policy.handle(request))
        await entered.wait()
        policy.freeze()
        release.set()
        await pending
        request.abort.assert_awaited_once()
        request.fulfill.assert_not_awaited()
        self.assertEqual(policy._anonymous_urls, set())
        self.assertEqual(len(policy._session.calls), 1)
        await policy.close()

    async def test_freeze_during_redirect_cannot_discover_next_host(self):
        resolver = AsyncMock(return_value=records(PUBLIC_IP))
        policy = ready([Response(302, headers={'Location': 'https://next.example.com/file.js'})], resolver)
        original = policy._session.request
        def freeze_on_response(*args, **kwargs):
            result = original(*args, **kwargs)
            policy.freeze()
            return result
        policy._session.request = freeze_on_response
        request = route(SCRIPT)
        await policy.handle(request)
        request.abort.assert_awaited_once()
        self.assertEqual(resolver.await_count, 1)
        self.assertEqual(len(policy._session.calls), 1)
        self.assertEqual(policy._anonymous_urls, set())
        await policy.close()

    async def test_discovery_keeps_mime_compression_bytes_requests_and_redirect_limits(self):
        bad = (Response(headers={'Content-Type': 'text/html'}),
               Response(headers={'Content-Type': 'application/javascript', 'Content-Encoding': 'gzip'}),
               Response(body=b'12345'))
        for response in bad:
            policy = ready([response]); policy.MAX_RESOURCE_BYTES = 4
            request = route(SCRIPT)
            await policy.handle(request)
            request.abort.assert_awaited_once()
            self.assertEqual(policy._anonymous_urls, set())
            await policy.close()
        policy = ready([Response(), Response()]); policy.MAX_REQUESTS = 1
        await policy.handle(route(SCRIPT))
        request = route(SCRIPT)
        await policy.handle(request)
        request.abort.assert_awaited_once()
        self.assertEqual(len(policy._session.calls), 1)
        await policy.close()
        policy = ready([Response(302, headers={'Location': '/again.js'}) for _ in range(4)])
        request = route(SCRIPT)
        await policy.handle(request)
        request.abort.assert_awaited_once()
        self.assertEqual(len(policy._session.calls), 4)
        await policy.close()

    async def test_dns_timeout_consumes_slot_without_replay(self):
        async def resolve(*args, **kwargs):
            await asyncio.sleep(1)
        resolver = AsyncMock(side_effect=resolve)
        policy = ready([], resolver); policy.DNS_SECONDS = .01
        for _ in range(2):
            request = route(SCRIPT)
            await policy.handle(request)
            request.abort.assert_awaited_once()
        self.assertEqual(resolver.await_count, 1)
        self.assertEqual(policy._session.calls, [])
        await policy.close()
