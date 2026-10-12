"""Real compression streams and local HTTP; no public network or credentials."""
import gzip
import os
import unittest
import zlib
from unittest.mock import patch

from aiohttp import ClientSession, DummyCookieJar, web
from aiohttp.test_utils import TestServer

from oak.website_resources import _decoded_chunks, brotli
from test_website_resources import CANARY, SCRIPT, STYLE, TIKTOK_ORIGIN, Response, ready, route


def encode(body, kind):
    if kind == 'gzip':
        return gzip.compress(body)
    if kind == 'deflate':
        return zlib.compress(body)
    if kind == 'raw-deflate':
        compressor = zlib.compressobj(wbits=-15)
        return compressor.compress(body) + compressor.flush()
    if kind == 'br':
        return brotli.compress(body, quality=1)
    return body


def response(body, kind):
    return Response(body=encode(body, kind), headers={
        'Content-Type': 'application/javascript',
        'Content-Encoding': 'deflate' if kind == 'raw-deflate' else kind,
        'Set-Cookie': CANARY, 'Authorization': CANARY})


class WebsiteResourceCompressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_codecs_decode_exactly_strip_headers_and_count_decoded_bytes(self):
        body = b'window.fixtureLoaded=true;' * 5000
        self.assertIsNotNone(brotli, 'Pinned Brotli dependency is required for CDN support')
        for kind in ('identity', 'gzip', 'deflate', 'raw-deflate', 'br'):
            with self.subTest(codec=kind):
                encoded = response(body, kind)
                policy = ready([encoded]); self.addAsyncCleanup(policy.close)
                request = route()
                await policy.handle(request)
                request.abort.assert_not_awaited()
                request.fulfill.assert_awaited_once()
                result = request.fulfill.call_args.kwargs
                self.assertEqual(result['body'], body)
                self.assertEqual(result['headers'], {'content-type': 'application/javascript',
                                                     'cache-control': 'no-store'})
                self.assertEqual(policy._bytes, len(body))
                self.assertEqual(policy._wire_bytes, len(encoded.body))
                self.assertNotIn(CANARY, str(result))
                self.assertNotIn(CANARY, str(policy._session.calls))

    async def test_decoded_bombs_rejected_without_fulfillment_or_completed_path(self):
        body = b'X' * (8 * 1024 * 1024)
        for kind in ('gzip', 'deflate', 'raw-deflate', 'br'):
            with self.subTest(codec=kind):
                encoded = response(body, kind)
                self.assertLess(len(encoded.body), 65536)
                policy = ready([encoded]); self.addAsyncCleanup(policy.close)
                request = route(); await policy.handle(request)
                request.abort.assert_awaited_once(); request.fulfill.assert_not_awaited()
                self.assertGreater(policy._bytes, policy.MAX_RESOURCE_BYTES)
                self.assertLessEqual(policy._bytes, policy.MAX_RESOURCE_BYTES + 131072)
                self.assertEqual(policy._anonymous_urls, set())

    async def test_total_decoded_quota_is_shared_across_compressed_responses(self):
        policy = ready([response(b'A' * 100, 'gzip'), response(b'B' * 100, 'br')])
        self.addAsyncCleanup(policy.close); policy.MAX_TOTAL_BYTES = 150
        first, second = route(), route(SCRIPT + '/next')
        await policy.handle(first); await policy.handle(second)
        first.fulfill.assert_awaited_once(); second.abort.assert_awaited_once()
        self.assertEqual(policy._bytes, 200)
        self.assertEqual(policy._anonymous_urls, {SCRIPT})

    async def test_chunked_wire_without_content_length_still_has_resource_limit(self):
        encoded = Response(body=b'A' * 101); encoded.content_length = None
        policy = ready([encoded]); self.addAsyncCleanup(policy.close)
        policy.MAX_RESOURCE_BYTES = 100
        request = route(); await policy.handle(request)
        request.abort.assert_awaited_once(); request.fulfill.assert_not_awaited()
        self.assertEqual(policy._bytes, 0)

    async def test_cumulative_wire_limit_cannot_be_hidden_by_small_decoded_payloads(self):
        encoded = response(b'x', 'gzip')
        policy = ready([encoded, response(b'x', 'gzip')]); self.addAsyncCleanup(policy.close)
        policy.MAX_TOTAL_BYTES = len(encoded.body) + 1
        first, second = route(), route(SCRIPT + '/next')
        await policy.handle(first); await policy.handle(second)
        first.fulfill.assert_awaited_once(); second.abort.assert_awaited_once()
        self.assertEqual(policy._bytes, 1)

    async def test_invalid_truncated_and_trailing_data_never_fulfills(self):
        for kind in ('gzip', 'deflate', 'br'):
            for data in (b'invalid compressed data', encode(b'public script', kind)[:-1],
                         encode(b'public script', kind) + b'trailing garbage'):
                with self.subTest(codec=kind, size=len(data)):
                    encoded = Response(body=data, headers={'Content-Type': 'application/javascript',
                                                          'Content-Encoding': kind})
                    policy = ready([encoded]); self.addAsyncCleanup(policy.close)
                    request = route(); await policy.handle(request)
                    request.abort.assert_awaited_once(); request.fulfill.assert_not_awaited()
                    self.assertEqual(policy._anonymous_urls, set())

    async def test_unknown_or_stacked_encodings_never_fulfill(self):
        for kind in ('zstd', 'gzip, br', 'x-gzip', 'compress'):
            policy = ready([Response(headers={'Content-Type': 'application/javascript',
                                              'Content-Encoding': kind})])
            self.addAsyncCleanup(policy.close)
            request = route(); await policy.handle(request)
            request.abort.assert_awaited_once(); request.fulfill.assert_not_awaited()

    async def test_missing_brotli_support_fails_closed(self):
        policy = ready([response(b'public', 'br')]); self.addAsyncCleanup(policy.close)
        request = route()
        with patch('oak.website_resources.brotli', None):
            await policy.handle(request)
        request.abort.assert_awaited_once(); request.fulfill.assert_not_awaited()

    async def test_head_with_encoding_has_no_decoded_body(self):
        policy = ready([Response(body=b'', headers={'Content-Type': 'application/javascript',
                                                   'Content-Encoding': 'gzip'})])
        self.addAsyncCleanup(policy.close)
        request = route(method='HEAD'); await policy.handle(request)
        request.fulfill.assert_awaited_once()
        self.assertEqual(request.fulfill.call_args.kwargs['body'], b'')

    async def test_gzip_members_decode_in_order_and_freeze_keeps_only_completed_path(self):
        encoded = Response(body=gzip.compress(b'first;') + gzip.compress(b'second;'),
                           headers={'Content-Type': 'application/javascript', 'Content-Encoding': 'gzip'})
        policy = ready([encoded, encoded]); self.addAsyncCleanup(policy.close)
        first = route(); await policy.handle(first)
        self.assertEqual(first.fulfill.call_args.kwargs['body'], b'first;second;')
        policy.freeze()
        denied = route(SCRIPT + '/new'); await policy.handle(denied)
        denied.abort.assert_awaited_once()
        second = route(); await policy.handle(second)
        second.fulfill.assert_awaited_once()
        self.assertEqual(len(policy._session.calls), 2)

    async def test_decoder_output_chunks_are_bounded_even_for_high_compression_ratio(self):
        body = b'Z' * (8 * 1024 * 1024)
        for kind in ('gzip', 'deflate', 'br'):
            with self.subTest(codec=kind):
                sizes = [len(chunk) for chunk in _decoded_chunks(encode(body, kind), kind)]
                self.assertEqual(sum(sizes), len(body))
                self.assertLessEqual(max(sizes), 131072)

    async def test_actual_aiohttp_stream_ignores_identity_request_but_still_decodes_safely(self):
        received = []
        async def endpoint(request):
            received.append(dict(request.headers))
            kind = request.match_info['kind']
            return web.Response(body=encode(b'window.localFixture=true;', kind), headers={
                'Content-Type': 'application/javascript', 'Content-Encoding': kind,
                'Set-Cookie': CANARY})
        app = web.Application(); app.router.add_get('/{kind}', endpoint)
        server = TestServer(app); await server.start_server(); self.addAsyncCleanup(server.close)
        session = ClientSession(auto_decompress=False, cookie_jar=DummyCookieJar(), trust_env=False,
                                headers={'Accept-Encoding': 'identity'})
        self.addAsyncCleanup(session.close)
        for kind in ('gzip', 'deflate', 'br'):
            with self.subTest(codec=kind):
                class LocalFixtureSession:
                    # Test-only local connection; production exact HTTPS pins unchanged.
                    def request(self, method, url, **kwargs):
                        return session.request(method, server.make_url('/' + kind), **kwargs)
                    async def close(self):
                        pass
                policy = ready([]); policy._session = LocalFixtureSession()
                self.addAsyncCleanup(policy.close)
                request = route(); await policy.handle(request)
                request.fulfill.assert_awaited_once()
                self.assertEqual(request.fulfill.call_args.kwargs['body'], b'window.localFixture=true;')
        self.assertEqual(len(received), 3)
        self.assertTrue(all(h['Accept-Encoding'] == 'identity' for h in received))
        self.assertTrue(all('Cookie' not in h and 'Authorization' not in h for h in received))

    @unittest.skipUnless(os.environ.get('OAK_PROVIDER_BROWSER_TESTS') == '1', 'opt-in intercepted Chromium')
    async def test_compressed_script_and_stylesheet_render_native_fields_in_chromium(self):
        from playwright.async_api import async_playwright
        script = b'document.body.innerHTML="<label>Account identifier<input type=email></label><button>Continue</button>";'
        style = b'input{height:47px}'
        policy = ready([]); self.addAsyncCleanup(policy.close)
        original = policy._session.request
        def fixture_response(method, url, **kwargs):
            encoded = response(script, 'br') if url == SCRIPT else response(style, 'gzip')
            if url == STYLE:
                encoded.headers['Content-Type'] = 'text/css'
            policy._session.responses.append(encoded)
            return original(method, url, **kwargs)
        policy._session.request = fixture_response
        async with async_playwright() as driver:
            browser = await driver.chromium.launch(headless=True, chromium_sandbox=True)
            try:
                context = await browser.new_context(service_workers='block')
                async def intercept(request_route):
                    if request_route.request.is_navigation_request():
                        await request_route.fulfill(content_type='text/html', body=
                            '<html><head><link rel="stylesheet" href="'+STYLE+'"></head><body>'
                            '<script src="'+SCRIPT+'"></script></body></html>')
                    else:
                        self.assertTrue(await policy.handle(request_route))
                await context.route('**/*', intercept)
                page = await context.new_page(); await page.goto(TIKTOK_ORIGIN)
                field = page.get_by_label('Account identifier', exact=True)
                await field.wait_for(timeout=5000)
                self.assertEqual(await field.get_attribute('type'), 'email')
                self.assertEqual(await field.evaluate('e=>getComputedStyle(e).height'), '47px')
                self.assertTrue(await page.get_by_role('button', name='Continue', exact=True).is_visible())
                self.assertEqual(policy._bytes, len(script) + len(style))
                self.assertEqual(policy._anonymous_urls, {SCRIPT, STYLE})
            finally:
                await browser.close()


if __name__ == '__main__':
    unittest.main()
