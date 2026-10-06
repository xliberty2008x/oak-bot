"""Owner-authenticated Oak control panel and compatibility event endpoints."""

import asyncio
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import time
from urllib.parse import parse_qsl, urlsplit
from urllib.request import urlopen
import uuid

from aiohttp import web


def telegram_owner(init_data, token, allowed, now=None):
    if not isinstance(init_data, str) or len(init_data) > 16000:
        raise ValueError('Invalid Telegram authentication.')
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError('Duplicate authentication fields.')
    supplied = fields.pop('hash', '')
    payload = '\n'.join(k + '=' + v for k, v in sorted(fields.items()))
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    actual = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(actual, supplied):
        raise ValueError('Invalid Telegram authentication.')
    now = time.time() if now is None else now
    if not -60 <= now - int(fields.get('auth_date', 0)) <= 3600:
        raise ValueError('Telegram authentication expired.')
    owner = json.loads(fields.get('user', '{}')).get('id')
    if type(owner) is not int or owner not in allowed:
        raise ValueError('This owner is not allowed.')
    return owner


class WebGateway:
    def __init__(self, controller, bus, token, allowed, config):
        self.controller, self.bus, self.token = controller, bus, token
        self.allowed = set(allowed)
        self.config = config
        self.public_url = config.get('public_url') or ''
        self.db = controller.db
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS web_sessions (
                hash TEXT PRIMARY KEY, owner INTEGER, expires REAL);
            CREATE TABLE IF NOT EXISTS web_conversations (
                id TEXT PRIMARY KEY, owner INTEGER, chat_id INTEGER UNIQUE, title TEXT);
        ''')
        self.db.commit()
        if 'source' not in {r[1] for r in self.db.execute('PRAGMA table_info(web_sessions)')}:
            self.db.execute("ALTER TABLE web_sessions ADD COLUMN source TEXT NOT NULL DEFAULT 'telegram'")
            self.db.commit()
        from .panel import ControlPanel
        self.controls = ControlPanel(self)
        self.keys_file = Path(config.get('access_key_file') or Path(controller.config['state_dir']) / 'web-access-keys.json').expanduser().resolve()
        if not self.keys_file.exists():
            self.keys_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.keys_file.write_text(json.dumps({str(owner): secrets.token_urlsafe(32) for owner in allowed}))
            self.keys_file.chmod(0o600)
        if self.keys_file.stat().st_mode & 0o077:
            raise ValueError('Web access keys must be private (chmod 600).')
        self.keys = json.loads(self.keys_file.read_text())
        self.failures = {}
        self.runner = None
        self.sdk_file = Path(controller.config['state_dir']) / 'telegram-web-app.js'
        self.app = web.Application(client_max_size=1024 * 1024, middlewares=[self.guard])
        self.app.add_routes([
            web.get('/', self.static), web.get('/app.js', self.static), web.get('/style.css', self.static),
            web.get('/telegram-web-app.js', self.sdk),
            web.post('/api/session', self.session), web.get('/api/bootstrap', self.bootstrap),
            web.get('/api/panel', self.panel), web.get('/api/tasks', self.tasks),
            web.post('/api/computer', self.computer),
            web.get('/api/tasks/{id}', self.task), web.post('/api/tasks/{id}/cancel', self.cancel_task),
            web.get('/api/integrations', self.integrations),
            web.post('/api/integrations/apps/{id}/manage', self.manage_app),
            web.post('/api/integrations/servers/{id}/oauth', self.oauth),
            web.get('/api/conversations', self.conversations), web.post('/api/conversations', self.conversations),
            web.get('/api/events', self.events), web.get('/api/stream', self.stream),
            web.post('/api/input', self.input), web.post('/api/stop', self.stop),
            web.post('/api/decision', self.decision), web.post('/api/render', self.render),
            web.get('/api/artifacts/{id}', self.artifact),
        ])

    @web.middleware
    async def guard(self, request, handler):
        origin = request.headers.get('Origin')
        expected_hosts = {request.host, urlsplit(self.public_url).netloc}
        if origin and (urlsplit(origin).scheme not in ('http', 'https') or urlsplit(origin).netloc not in expected_hosts):
            raise web.HTTPForbidden(text='Untrusted origin.')
        try:
            response = await handler(request)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise web.HTTPBadRequest(text='Invalid request.') from None
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = ("default-src 'self'; script-src 'self' https://telegram.org; "
            "connect-src 'self'; img-src 'self'; media-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors https://web.telegram.org https://*.telegram.org")
        return response

    def owner(self, request):
        authorization = request.headers.get('Authorization', '')
        cookie = authorization[7:] if authorization.startswith('Bearer ') else request.cookies.get('oak_session', '')
        row = self.db.execute('SELECT owner,expires FROM web_sessions WHERE hash=?',
                              (hashlib.sha256(cookie.encode()).hexdigest(),)).fetchone()
        if not row or row[1] < time.time() or row[0] not in self.allowed:
            raise web.HTTPUnauthorized(text='Sign in to Oak.')
        return row[0]

    def conversation(self, request, owner, identifier=None):
        identifier = identifier or request.query.get('conversation', 'telegram')
        if identifier == 'telegram':
            return owner
        row = self.db.execute('SELECT chat_id FROM web_conversations WHERE id=? AND owner=?', (identifier, owner)).fetchone()
        if not row:
            raise web.HTTPNotFound(text='Conversation not found.')
        return row[0]

    def session_source(self, request):
        authorization = request.headers.get('Authorization', '')
        cookie = authorization[7:] if authorization.startswith('Bearer ') else request.cookies.get('oak_session', '')
        row = self.db.execute('SELECT source FROM web_sessions WHERE hash=?',
                              (hashlib.sha256(cookie.encode()).hexdigest(),)).fetchone()
        return row[0] if row else None

    async def static(self, request):
        name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[request.path]
        return web.FileResponse(Path(__file__).parent / 'web' / name)

    async def sdk(self, request):
        if not self.sdk_file.exists():
            def download():
                with urlopen('https://telegram.org/js/telegram-web-app.js', timeout=8) as response:
                    data = response.read(512 * 1024 + 1)
                if not 0 < len(data) <= 512 * 1024:
                    raise ValueError('SDK size exceeds the limit.')
                pending = self.sdk_file.with_name(self.sdk_file.name + '.' + uuid.uuid4().hex + '.tmp')
                try:
                    pending.write_bytes(data)
                    pending.chmod(0o600)
                    pending.replace(self.sdk_file)
                finally:
                    pending.unlink(missing_ok=True)
            try:
                await asyncio.to_thread(download)
            except Exception:
                raise web.HTTPServiceUnavailable(text='Telegram SDK is temporarily unavailable.') from None
        return web.FileResponse(self.sdk_file, headers={'Content-Type': 'application/javascript'})

    async def session(self, request):
        data = await request.json()
        peer = request.remote or 'unknown'
        attempts, since = self.failures.get(peer, (0, time.monotonic()))
        if time.monotonic() - since > 60:
            attempts, since = 0, time.monotonic()
        if attempts >= 20:
            raise web.HTTPTooManyRequests(text='Wait before trying again.')
        try:
            if data.get('initData'):
                owner = telegram_owner(data['initData'], self.token, self.allowed)
            else:
                supplied = str(data.get('key', ''))
                owner = next((int(k) for k, v in self.keys.items()
                              if int(k) in self.allowed and hmac.compare_digest(str(v), supplied)), None)
                if owner is None:
                    raise ValueError('Invalid access key.')
        except (ValueError, TypeError):
            self.failures[peer] = (attempts + 1, since)
            raise web.HTTPUnauthorized(text='Sign in through Telegram or use your private access key.') from None
        self.failures.pop(peer, None)
        cookie = secrets.token_urlsafe(32)
        with self.db:
            self.db.execute('DELETE FROM web_sessions WHERE expires<?', (time.time(),))
            self.db.execute('INSERT INTO web_sessions(hash,owner,expires,source) VALUES (?,?,?,?)',
                (hashlib.sha256(cookie.encode()).hexdigest(), owner, time.time() + 3600, 'telegram' if data.get('initData') else 'browser'))
        response = web.json_response({'ok': True, 'access_token': cookie})
        secure = request.secure or (self.public_url.startswith('https://') and request.host == urlsplit(self.public_url).netloc)
        response.set_cookie('oak_session', cookie, max_age=3600, httponly=True, secure=secure, samesite='None' if secure else 'Strict')
        return response

    async def bootstrap(self, request):
        self.owner(request)
        return web.json_response({'transport': self.config.get('transport', 'sse'), 'name': 'Oak'})

    async def panel(self, request):
        return web.json_response(self.controls.summary(self.owner(request)))

    async def computer(self, request):
        owner = await self.confirmed_owner(request)
        data = await request.json()
        if type(data.get('enabled')) is not bool:
            raise web.HTTPBadRequest(text='Увімкнення потребує логічного значення.')
        if self.controller.tools is None:
            raise web.HTTPServiceUnavailable(text='Керування комп’ютером недоступне.')
        return web.json_response(await self.controller.tools.set_computer_enabled(owner, data['enabled']))

    async def tasks(self, request):
        return web.json_response(self.controls.tasks(self.owner(request)))

    async def task(self, request):
        return web.json_response(self.controls.tasks(self.owner(request), request.match_info['id']))

    async def confirmed_owner(self, request):
        owner = self.owner(request)
        data = await request.json()
        if not isinstance(data, dict) or data.get('confirmed') is not True:
            raise web.HTTPBadRequest(text='Потрібне явне підтвердження власника.')
        return owner

    async def cancel_task(self, request):
        owner = await self.confirmed_owner(request)
        task = self.controls.tasks(owner, request.match_info['id'])['task']
        cancelled = self.controller.scheduler.cancel(owner, task['id'])
        return web.json_response({'cancelled': cancelled, 'active_turn_unchanged': True})

    async def integrations(self, request):
        return web.json_response(await self.controls.inventory(request, self.owner(request)))

    async def manage_app(self, request):
        owner = await self.confirmed_owner(request)
        return web.json_response(await self.controls.manage(owner, request.match_info['id']))

    async def oauth(self, request):
        owner = await self.confirmed_owner(request)
        return web.json_response(await self.controls.login(request, owner, request.match_info['id']))

    async def conversations(self, request):
        owner = self.owner(request)
        if request.method == 'POST':
            data = await request.json()
            title = str(data.get('title') or 'Нова розмова').strip()[:100]
            identifier = uuid.uuid4().hex
            chat = -secrets.randbelow(2**60) - 1
            with self.db:
                self.db.execute('INSERT INTO web_conversations VALUES (?,?,?,?)', (identifier, owner, chat, title))
            return web.json_response({'id': identifier, 'title': title})
        rows = self.db.execute('SELECT id,title FROM web_conversations WHERE owner=? ORDER BY rowid', (owner,))
        return web.json_response([{'id': 'telegram', 'title': 'Telegram'}, *[{'id': r[0], 'title': r[1]} for r in rows]])

    async def events(self, request):
        owner = self.owner(request)
        chat = self.conversation(request, owner)
        after = max(0, int(request.query.get('after', '0')))
        wait = min(20, max(0, float(request.query.get('wait', '0'))))
        run = request.query.get('runId')
        async with self.bus.subscribe(chat, run) as queue:
            records = self.bus.replay(chat, after, run)
            if not records and wait:
                try:
                    await asyncio.wait_for(queue.get(), wait)
                except asyncio.TimeoutError:
                    pass
                records = self.bus.replay(chat, after, run)
        from .events import to_agui_event
        return web.json_response([{'sequence': r['sequence'], 'event': to_agui_event(r['event'])} for r in records])

    async def stream(self, request):
        owner = self.owner(request)
        chat = self.conversation(request, owner)
        after = max(0, int(request.headers.get('Last-Event-ID') or request.query.get('after', '0')))
        run = request.query.get('runId')
        from .events import to_agui_event
        response = web.StreamResponse(headers={'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
        await response.prepare(request)
        try:
            async with self.bus.subscribe(chat, run) as queue:
                while True:
                    records = self.bus.replay(chat, after, run)
                    for item in records:
                        await response.write(('id: ' + str(item['sequence']) + '\ndata: ' + json.dumps(to_agui_event(item['event']), ensure_ascii=False) + '\n\n').encode())
                        after = item['sequence']
                    if len(records) == 500:
                        continue
                    try:
                        await asyncio.wait_for(queue.get(), 15)
                    except asyncio.TimeoutError:
                        await response.write(b': keepalive\n\n')
                    self.owner(request)
        except (ConnectionError, web.HTTPException):
            pass
        return response

    async def input(self, request):
        owner = self.owner(request)
        data = await request.json()
        chat = self.conversation(request, owner, data.get('conversation'))
        text = data.get('text')
        if not isinstance(text, str) or not 0 < len(text.strip()) <= 32000:
            raise ValueError('Input must contain 1–32000 characters.')
        # A stable client request ID deduplicates an explicitly repeated POST.
        identifier = data.get('requestId')
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 100:
            raise ValueError('Missing input requestId.')
        digest = hashlib.sha256((str(chat) + ':' + identifier).encode()).digest()
        update = -int.from_bytes(digest[:7], 'big') - 1
        await self.controller.submit(chat, text, update)
        return web.json_response({'accepted': True, 'requestId': identifier})

    async def stop(self, request):
        owner = self.owner(request)
        data = await request.json()
        chat = self.conversation(request, owner, data.get('conversation'))
        return web.json_response({'status': await self.controller.stop(chat)})

    async def decision(self, request):
        owner = self.owner(request)
        data = await request.json()
        chat = self.conversation(request, owner, data.get('conversation'))
        if data.get('kind') == 'approval':
            if type(data.get('accepted')) is not bool:
                raise ValueError('Approval requires a boolean.')
            result = await self.controller.approve(chat, data['id'], data['accepted'])
        else:
            result = await self.controller.answer(chat, data['id'], str(data.get('text', ''))[:16000])
        return web.json_response({'message': result})

    async def render(self, request):
        self.owner(request)
        from .rendering import parse_markdown
        data = await request.json()
        if not isinstance(data.get('text'), str):
            raise ValueError('Text is required.')
        return web.json_response(parse_markdown(data['text']))

    async def artifact(self, request):
        owner = self.owner(request)
        identifier = request.match_info['id']
        row = self.db.execute('SELECT chat_id,mime FROM artifacts WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise web.HTTPNotFound()
        chat = row[0]
        if chat != owner and not self.db.execute('SELECT 1 FROM web_conversations WHERE chat_id=? AND owner=?', (chat, owner)).fetchone():
            raise web.HTTPNotFound()
        path = self.bus.artifact_path(chat, identifier)
        response = web.FileResponse(path, headers={'Content-Type': row[1]})
        response.headers['Content-Disposition'] = "attachment; filename*=UTF-8''" + __import__('urllib.parse', fromlist=['quote']).quote(path.name)
        return response

    async def start(self):
        self.runner = web.AppRunner(self.app, access_log=None, shutdown_timeout=2)
        await self.runner.setup()
        await web.TCPSite(self.runner, self.config.get('host', '127.0.0.1'), int(self.config.get('port', 8765))).start()

    async def close(self):
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
