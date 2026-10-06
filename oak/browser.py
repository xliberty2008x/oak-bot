"""An isolated, persistent Playwright browser for Oak's explicit browser tools."""

import asyncio
from pathlib import Path
from urllib.parse import urlsplit
import uuid


class BrowserTools:
    def __init__(self, workspace):
        self.workspace = Path(workspace).expanduser().resolve()
        self.profile = self.workspace / 'browser-profile'
        self.artifacts = self.workspace / 'artifacts'
        self._playwright = None
        self._context = None
        self._page = None
        self._lock = asyncio.Lock()

    async def _start(self):
        if self._context is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError('Install Playwright and its Chromium browser to browse pages.') from None
        self.profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                str(self.profile), headless=True, viewport={'width': 1280, 'height': 800},
                accept_downloads=False)
            self._context.set_default_timeout(15000)
            self._page = self._context.pages[-1] if self._context.pages else await self._context.new_page()
        except Exception:
            await self._playwright.stop()
            self._playwright = None
            raise RuntimeError('Chromium could not start; install it with python -m playwright install chromium.') from None

    async def run(self, action='read', url=None, selector=None, text=None):
        if action not in {'open', 'read', 'click', 'type', 'screenshot'}:
            raise ValueError('Browser action must be open, read, click, type or screenshot.')
        if action == 'open':
            parsed = urlsplit(url or '')
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('Open requires an HTTP or HTTPS URL without embedded credentials.')
        if action in {'click', 'type'} and (not isinstance(selector, str) or not selector.strip() or len(selector) > 1000):
            raise ValueError('This browser action requires a CSS or Playwright selector.')
        if action == 'type' and (not isinstance(text, str) or len(text) > 20000):
            raise ValueError('Typed text must contain at most 20000 characters.')
        async with self._lock:
            await self._start()
            try:
                if self._page.is_closed():
                    self._page = await self._context.new_page()
                page = self._page
                if action == 'open':
                    await page.goto(url, wait_until='domcontentloaded', timeout=30000)
                elif action == 'click':
                    await page.locator(selector).click()
                    # Follow a newly opened tab without touching any external profile.
                    if self._context.pages and self._context.pages[-1] is not page:
                        page = self._page = self._context.pages[-1]
                    await page.wait_for_load_state('domcontentloaded', timeout=15000)
                elif action == 'type':
                    await page.locator(selector).fill(text)
                result = {'url': page.url, 'title': await page.title()}
                if action == 'screenshot':
                    output = self.artifacts / (uuid.uuid4().hex + '.png')
                    await page.screenshot(path=str(output), full_page=False)
                    result['path'] = str(output)
                else:
                    result['text'] = (await page.locator('body').inner_text())[:20000]
                    result['controls'] = await page.locator('a,button,input,textarea,select').evaluate_all(
                        "elements => elements.slice(0, 50).map((e, i) => ({"
                        "selector: ':is(a,button,input,textarea,select) >> nth=' + i,"
                        "tag: e.tagName.toLowerCase(), text: (e.innerText || e.getAttribute('aria-label') || '').slice(0, 160),"
                        "type: e.getAttribute('type'), placeholder: e.getAttribute('placeholder'),"
                        "href: e.tagName === 'A' ? e.href : undefined}))")
                return result
            except Exception:
                # Playwright errors include typed text, selectors and navigated URLs.
                raise RuntimeError('Browser action failed or timed out; read the page and retry the intended action.') from None

    async def close(self):
        async with self._lock:
            try:
                if self._context is not None:
                    await self._context.close()
            finally:
                self._context = self._page = None
                if self._playwright is not None:
                    await self._playwright.stop()
                    self._playwright = None
