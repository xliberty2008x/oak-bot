"""Human-only, ephemeral synthetic sign-in broker.

The human server and Unix control server are distinct applications. No real
provider is enabled by this module: a second process is not an isolation proof.
Only the fixture adapter can create a browser, and it cannot navigate externally.
"""

import asyncio
from dataclasses import dataclass, field
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit

from aiohttp import web


TICKET_SECONDS = 30
IDLE_SECONDS = 120
HEARTBEAT_SECONDS = 10
LOGIN_SECONDS = 300
TERMINAL = {'authenticated', 'cancelled', 'expired', 'failed'}


def origin(value, *, loopback=False):
    parsed = urlsplit(value)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username
            or parsed.password or parsed.path not in {'', '/'} or parsed.query or parsed.fragment):
        raise ValueError('Invalid broker origin.')
    if parsed.scheme != 'https' and not (loopback and parsed.hostname == '127.0.0.1'):
        raise ValueError('HTTPS is required.')
    if not re.fullmatch(r'[A-Za-z0-9.-]+(?::[0-9]{1,5})?', parsed.netloc) or not 1 <= (parsed.port or 443) <= 65535:
        raise ValueError('Invalid broker authority.')
    return parsed.scheme + '://' + parsed.netloc


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Binding:
    request_id: str
    owner: int
    chat_id: int
    thread_id: str
    turn_id: str
    runtime_epoch: str
    panel_hash: str
    expires: float
    provider: str

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError('Invalid sign-in binding.')
        if (not re.fullmatch(r'[a-f0-9]{32}', str(value['request_id']))
                or type(value['owner']) is not int or type(value['chat_id']) is not int
                or not all(isinstance(value[k], str) and 0 < len(value[k]) <= 200
                           for k in ('thread_id', 'turn_id', 'runtime_epoch'))
                or not re.fullmatch(r'[a-f0-9]{64}', str(value['panel_hash']))
                or type(value['expires']) not in (int, float)
                or not time.time() < value['expires'] <= time.time() + LOGIN_SECONDS + 1
                or value['provider'] != 'oak_synthetic'):
            raise ValueError('Synthetic provider binding required.')
        return cls(**value)


@dataclass
class Lease:
    binding: Binding
    identifier: str = field(default_factory=lambda: secrets.token_hex(16))
    ticket_hash: str = ''
    ticket_deadline: float = 0
    human_hash: str = ''
    idle_deadline: float = 0
    heartbeat_deadline: float = 0
    state: str = 'waiting'
    browser: object = None
    cleanup: str = 'waiting'
    close_task: object = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SyntheticBrowser:
    """A real, nonpersistent Chromium context restricted to one fake provider."""

    def __init__(self, provider_origin):
        self.origin = origin(provider_origin, loopback=True)
        if urlsplit(self.origin).hostname != '127.0.0.1':
            raise ValueError('The test provider must be loopback.')
        self.driver = self.browser = self.context = self.page = None
        self.epoch = 0

    async def start(self):
        from playwright.async_api import async_playwright
        self.driver = await async_playwright().start()
        self.browser = await self.driver.chromium.launch(headless=True, args=[
            '--disable-breakpad', '--disable-crash-reporter', '--disable-sync',
            '--disable-save-password-bubble'])
        self.context = await self.browser.new_context(viewport={'width': 390, 'height': 530},
                                                     accept_downloads=False)
        async def route(request):
            parsed = urlsplit(request.request.url)
            if (parsed.scheme + '://' + parsed.netloc == self.origin
                    and parsed.path in {'/login', '/verify', '/account'}):
                await request.continue_()
            else:
                await request.abort()
        await self.context.route('**/*', route)
        self.page = await self.context.new_page()
        self.page.on('framenavigated', lambda frame: self._navigate(frame))
        self.context.on('page', lambda page: asyncio.create_task(page.close()) if page != self.page else None)
        await self.page.goto(self.origin + '/login', wait_until='domcontentloaded')

    def _navigate(self, frame):
        if self.page and frame == self.page.main_frame:
            self.epoch += 1

    def checked(self):
        parsed = urlsplit(self.page.url)
        if (parsed.scheme + '://' + parsed.netloc != self.origin
                or parsed.path not in {'/login', '/verify', '/account'}):
            raise ValueError('Provider navigation rejected.')

    async def frame(self):
        self.checked()
        epoch = self.epoch
        image = await self.page.screenshot(type='png')  # bytes only, human channel only
        self.checked()
        if epoch != self.epoch:
            raise ValueError('Navigation changed.')
        return epoch, image

    async def event(self, value):
        self.checked()
        if type(value.get('epoch')) is not int or value['epoch'] != self.epoch:
            raise ValueError('Stale input frame.')
        kind = value.get('kind')
        safe = await self.page.evaluate('''() => [...document.forms].every(f =>
            new URL(f.action).origin === location.origin &&
            ['/login','/verify'].includes(new URL(f.action).pathname)) &&
            document.querySelectorAll('iframe').length === 0''')
        self.checked()
        if not safe or value['epoch'] != self.epoch:
            raise ValueError('Provider form rejected.')
        if kind == 'click' and set(value) == {'kind', 'epoch', 'x', 'y'}:
            if not all(type(value[k]) in (int, float) for k in ('x', 'y')) or not (0 <= value['x'] < 390 and 0 <= value['y'] < 530):
                raise ValueError('Invalid pointer.')
            # Input frames/forms are checked inside the browser immediately before
            # the action, and only fixed fixture destinations are permitted.
            await self.page.mouse.click(value['x'], value['y'])
        elif kind == 'text' and set(value) == {'kind', 'epoch', 'text'}:
            if not isinstance(value['text'], str) or not 0 < len(value['text']) <= 256:
                raise ValueError('Invalid human input.')
            # This is never a model tool and never accepts selectors or script.
            await self.page.keyboard.insert_text(value['text'])
        elif kind == 'key' and set(value) == {'kind', 'epoch', 'key'}:
            if value['key'] not in {'Tab', 'Enter', 'Backspace', 'ArrowLeft', 'ArrowRight'}:
                raise ValueError('Invalid human key.')
            await self.page.keyboard.press(value['key'])
        else:
            raise ValueError('Invalid human event.')
        self.checked()

    async def verified(self):
        self.checked()
        return (urlsplit(self.page.url).path == '/account'
                and await self.page.locator('[data-oak-synthetic-authenticated]').count() == 1)

    async def close(self):
        # Retain a failed underlying handle; retrying an empty adapter must not
        # turn an uncertain destruction into a false confirmation.
        if self.browser:
            await self.browser.close()
            self.page = self.context = self.browser = None
        if self.driver:
            await self.driver.stop()
            self.driver = None


class Broker:
    def __init__(self, human_origin, parent_origin, provider_origin, *, browser_factory=SyntheticBrowser):
        self.human_origin = origin(human_origin, loopback=True)
        self.parent_origin = origin(parent_origin, loopback=True)
        if self.human_origin == self.parent_origin:
            raise ValueError('Broker must use a different origin.')
        self.provider_origin = origin(provider_origin, loopback=True)
        if urlsplit(self.provider_origin).hostname != '127.0.0.1':
            raise ValueError('Only the synthetic loopback provider is implemented.')
        self.browser_factory = browser_factory
        self.leases = {}
        self.sweeper = None
        self.human = web.Application(client_max_size=4096, middlewares=[self.guard])
        self.human.add_routes([web.get('/', self.page), web.get('/human.js', self.script),
                              web.get('/human.css', self.style), web.post('/api/attach', self.attach),
                              web.post('/api/frame', self.frame), web.post('/api/event', self.event),
                              web.post('/api/cancel', self.cancel_human)])
        self.control = web.Application(client_max_size=4096, middlewares=[self.control_guard])
        self.control.add_routes([web.post('/create', self.create), web.post('/status', self.control_status),
                                web.post('/cancel', self.cancel_control)])

    @web.middleware
    async def guard(self, request, handler):
        if request.method == 'POST' and request.headers.get('Origin') != self.human_origin:
            raise web.HTTPForbidden(text='Human origin required.')
        try:
            response = await handler(request)
        except web.HTTPException as error:
            response = web.Response(status=error.status, text='Human request unavailable.')
        except Exception:
            response = web.Response(status=409, text='Human request unavailable.')
        response.headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
                                 'X-Content-Type-Options': 'nosniff',
                                 'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors " + self.parent_origin + ' https://web.telegram.org https://*.telegram.org'})
        return response

    @web.middleware
    async def control_guard(self, request, handler):
        # This app is bound ONLY to a private Unix socket; never to a TCP site.
        # The human app has no route to these handlers.
        try:
            return await handler(request)
        except Exception:
            return web.json_response({'error': 'Broker control unavailable.'}, status=409)

    def expire(self, lease):
        now = time.time()
        return now >= lease.binding.expires or (lease.state == 'active' and
                (now >= lease.idle_deadline or (lease.heartbeat_deadline and now >= lease.heartbeat_deadline)))

    def public_status(self, lease):
        return {'session_id': lease.identifier, 'request_id': lease.binding.request_id,
                'provider': 'oak_synthetic', 'simulated': True, 'state': lease.state,
                'runtime_epoch': lease.binding.runtime_epoch, 'cleanup': lease.cleanup}

    async def finish(self, lease, state):
        if lease.state in TERMINAL and lease.browser is None:
            return
        if lease.state not in TERMINAL:
            lease.state = state
        lease.ticket_hash = lease.human_hash = ''
        if lease.browser:
            try:
                if lease.close_task is None or (lease.close_task.done() and
                        (lease.close_task.cancelled() or lease.close_task.exception() is not None)):
                    lease.close_task = asyncio.create_task(lease.browser.close())
                    lease.close_task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
                await asyncio.wait_for(asyncio.shield(lease.close_task), 5)
                lease.browser = None
                lease.cleanup = 'confirmed'
            except asyncio.CancelledError:
                lease.state = 'failed'
                lease.cleanup = 'uncertain'
                raise
            except Exception:
                lease.state = 'failed'
                lease.cleanup = 'uncertain'  # retain resource for another close attempt
        else:
            lease.cleanup = 'confirmed'
        if lease.state == 'authenticated' and time.time() >= lease.binding.expires:
            lease.state = 'expired'

    async def operation(self, lease, function):
        deadlines = [lease.binding.expires, lease.idle_deadline]
        if lease.heartbeat_deadline:
            deadlines.append(lease.heartbeat_deadline)
        remaining = min(deadlines) - time.time()
        if remaining <= 0:
            await self.finish(lease, 'expired')
            raise ValueError('Lease expired.')
        try:
            value = await asyncio.wait_for(function(), remaining)
        except asyncio.TimeoutError:
            await self.finish(lease, 'expired')
            raise ValueError('Lease expired.') from None
        if self.expire(lease):
            await self.finish(lease, 'expired')
            raise ValueError('Lease expired.')
        return value

    async def create(self, request):
        binding = Binding.parse(await request.json())
        if binding.request_id in self.leases:
            raise ValueError('Already attempted.')
        ticket = secrets.token_urlsafe(32)
        lease = Lease(binding, ticket_hash=digest(ticket), ticket_deadline=min(time.time() + TICKET_SECONDS, binding.expires))
        self.leases[binding.request_id] = lease
        return web.json_response({**self.public_status(lease), 'origin': self.human_origin,
                                  'ticket': ticket, 'expires': lease.ticket_deadline})

    async def attach(self, request):
        value = await request.json()
        if not isinstance(value, dict) or set(value) != {'request_id', 'ticket'}:
            raise ValueError('Invalid attachment.')
        lease = self.leases.get(value['request_id'])
        if not lease or not isinstance(value['ticket'], str) or len(value['ticket']) > 100:
            raise ValueError('Invalid attachment.')
        async with lease.lock:
            if (lease.state != 'waiting' or self.expire(lease) or time.time() >= lease.ticket_deadline
                    or not hmac.compare_digest(digest(value['ticket']), lease.ticket_hash)):
                raise ValueError('Invalid attachment.')
            lease.ticket_hash = ''  # consume BEFORE creating the browser
            lease.state = 'active'
            lease.idle_deadline = time.time() + IDLE_SECONDS
            lease.browser = self.browser_factory(self.provider_origin)
            try:
                await self.operation(lease, lease.browser.start)
            except Exception:
                await self.finish(lease, 'failed')
                raise ValueError('Browser unavailable.') from None
            human = secrets.token_urlsafe(32)
            lease.human_hash = digest(human)
            lease.heartbeat_deadline = time.time() + HEARTBEAT_SECONDS
            return web.json_response({'lease': human, 'provider': 'oak_synthetic', 'simulated': True})

    async def authorized(self, request):
        value = await request.json()
        if not isinstance(value, dict) or not isinstance(value.get('request_id'), str):
            raise ValueError('Invalid lease.')
        lease = self.leases.get(value['request_id'])
        supplied = request.headers.get('Authorization', '')
        if (not lease or not supplied.startswith('Bearer ') or not lease.human_hash
                or not hmac.compare_digest(digest(supplied[7:]), lease.human_hash)):
            raise ValueError('Invalid lease.')
        return lease, value

    async def frame(self, request):
        lease, value = await self.authorized(request)
        if set(value) != {'request_id'}:
            raise ValueError('Invalid frame request.')
        async with lease.lock:
            if self.expire(lease):
                await self.finish(lease, 'expired')
            if lease.state != 'active':
                raise ValueError('Lease ended.')
            if await self.operation(lease, lease.browser.verified):
                await self.finish(lease, 'authenticated')
                return web.json_response({'state': lease.state, 'simulated': True})
            epoch, image = await self.operation(lease, lease.browser.frame)
            lease.heartbeat_deadline = time.time() + HEARTBEAT_SECONDS
            return web.Response(body=image, content_type='image/png', headers={'X-Oak-Navigation-Epoch': str(epoch),
                    'X-Oak-Verified-Origin': self.provider_origin})

    async def event(self, request):
        lease, value = await self.authorized(request)
        if set(value) != {'request_id', 'event'} or not isinstance(value['event'], dict):
            raise ValueError('Invalid event.')
        async with lease.lock:
            if self.expire(lease):
                await self.finish(lease, 'expired')
            if lease.state != 'active':
                raise ValueError('Lease ended.')
            try:
                await self.operation(lease, lambda: lease.browser.event(value['event']))
                lease.idle_deadline = time.time() + IDLE_SECONDS
            except Exception:
                await self.finish(lease, 'failed')
                raise ValueError('Human input rejected.') from None
            return web.json_response({'accepted': True})

    async def cancel_human(self, request):
        lease, value = await self.authorized(request)
        if set(value) != {'request_id'}:
            raise ValueError('Invalid cancellation.')
        async with lease.lock:
            await self.finish(lease, 'cancelled')
        return web.json_response({'state': lease.state})

    async def lookup(self, request):
        value = await request.json()
        if not isinstance(value, dict) or set(value) != {'request_id', 'session_id', 'runtime_epoch'}:
            raise ValueError('Invalid control scope.')
        lease = self.leases.get(value['request_id'])
        if not lease or lease.identifier != value['session_id'] or lease.binding.runtime_epoch != value['runtime_epoch']:
            raise ValueError('Invalid control scope.')
        return lease

    async def control_status(self, request):
        lease = await self.lookup(request)
        async with lease.lock:
            if self.expire(lease):
                await self.finish(lease, 'expired')
            return web.json_response(self.public_status(lease))

    async def cancel_control(self, request):
        lease = await self.lookup(request)
        async with lease.lock:
            await self.finish(lease, 'cancelled')
            return web.json_response(self.public_status(lease))

    async def page(self, request):
        # No model-controlled text or inline executable script is interpolated.
        html = (Path(__file__).parent / 'broker_web' / 'index.html').read_text()
        return web.Response(text=html.replace('PARENT_ORIGIN', self.parent_origin), content_type='text/html')

    async def script(self, request):
        return web.FileResponse(Path(__file__).parent / 'broker_web' / 'human.js')

    async def style(self, request):
        return web.FileResponse(Path(__file__).parent / 'broker_web' / 'human.css')

    async def sweep(self):
        while True:
            await asyncio.sleep(1)
            for identifier, lease in tuple(self.leases.items()):
                async with lease.lock:
                    if self.expire(lease):
                        await self.finish(lease, 'expired')
                    if lease.state in TERMINAL and lease.browser is None and time.time() > lease.binding.expires + 30:
                        self.leases.pop(identifier, None)

    async def close(self):
        if self.sweeper:
            self.sweeper.cancel()
            await asyncio.gather(self.sweeper, return_exceptions=True)
        for identifier, lease in tuple(self.leases.items()):
            async with lease.lock:
                await self.finish(lease, 'cancelled')
                if lease.browser is None and lease.cleanup == 'confirmed':
                    self.leases.pop(identifier, None)
        return not self.leases
