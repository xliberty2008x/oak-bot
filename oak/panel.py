"""Small, redacted views of Oak's existing control and runtime capabilities."""

import asyncio
import hashlib
import ipaddress
import re
import time
from urllib.parse import parse_qsl, urlsplit

from aiohttp import web

from .controller import MODEL
from .runtime import RpcError
from .telegram import TelegramError


def identifier(kind, value):
    return hashlib.sha256((kind + ':' + value).encode()).hexdigest()[:24]


def label(value, fallback):
    # Runtime presentation names are text, never URLs, paths or config dumps.
    if not isinstance(value, str) or not value.strip() or len(value) > 120:
        return fallback
    if any(ord(c) < 32 for c in value) or '://' in value or '/' in value or '\\' in value:
        return fallback
    return value.strip()


def https_url(value, native=False):
    if not isinstance(value, str) or not 0 < len(value) <= 8192 or any(c.isspace() or ord(c) < 32 for c in value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.port not in (None, 443):
            return None
        if native and url.hostname != 'chatgpt.com':
            return None
        if url.fragment or any(k.lower() in {'access_token', 'refresh_token', 'id_token', 'api_key', 'token'}
                               for k, _ in parse_qsl(url.query)):
            return None
        return value
    except ValueError:
        return None


class ControlPanel:
    def __init__(self, gateway):
        self.gateway = gateway
        self.controller = gateway.controller
        self.db = gateway.db
        self.db.execute('''CREATE TABLE IF NOT EXISTS panel_oauth (
            owner INTEGER, server TEXT, state TEXT, expires REAL,
            PRIMARY KEY(owner,server))''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS panel_topic_operations (
            owner INTEGER, operation TEXT, kind TEXT, name TEXT, session TEXT, state TEXT,
            PRIMARY KEY(owner,operation))''')
        self._topic_capabilities = (0, None)
        # A process restart cannot establish whether a dispatched login finished.
        with self.db:
            self.db.execute("UPDATE panel_oauth SET state='uncertain' WHERE state IN ('requested','pending')")
            self.db.execute("UPDATE panel_topic_operations SET state='uncertain' WHERE state='requested'")

    async def topic_capabilities(self):
        checked, identity = self._topic_capabilities
        if checked > time.monotonic() - 60:
            return identity
        telegram = getattr(self.controller, 'telegram', None)
        identity = None
        if telegram is not None:
            try:
                result = await telegram._api('getMe', {}, timeout=6, rate_limit_attempts=1)
                if isinstance(result, dict) and result.get('is_bot') is True:
                    identity = {'topics_enabled': result.get('has_topics_enabled') is True,
                                'users_can_create_topics': result.get('allows_users_to_create_topics') is True}
            except TelegramError:
                pass
        self._topic_capabilities = (time.monotonic(), identity)
        return identity

    def session_rows(self, owner):
        rows = []
        for session in self.controller.sessions.list(owner):
            scope = session['scope_id']
            state = self.summary(scope)
            rows.append({k: session[k] for k in ('id', 'topic_id', 'name', 'closed')} |
                        state['session'] | {'memory_count': state['memory']['count'],
                        'task_count': self.db.execute("SELECT count(*) FROM jobs WHERE chat_id=? AND status IN ('pending','running','uncertain')", (scope,)).fetchone()[0]})
        return rows

    async def sessions(self, owner):
        capabilities = await self.topic_capabilities()
        return {**(capabilities or {'topics_enabled': None, 'users_can_create_topics': None}),
                'sessions': self.session_rows(owner), 'inventory': 'observed'}

    async def topic_action(self, owner, data, session_id=None):
        name, operation = data.get('name'), data.get('operation_id')
        if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 128
                or any(ord(c) < 32 or ord(c) == 127 for c in name)
                or not isinstance(operation, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', operation)):
            raise web.HTTPBadRequest(text='Потрібні назва теми (1–128 символів) та ідентифікатор дії.')
        name = name.strip()
        kind = 'rename' if session_id is not None else 'create'
        scope = self.controller.sessions.lookup(owner, session_id) if session_id is not None else None
        if session_id is not None and (scope is None or scope == owner):
            raise web.HTTPNotFound(text='Тему не знайдено.')
        capabilities = await self.topic_capabilities()
        existing = self.db.execute('SELECT * FROM panel_topic_operations WHERE owner=? AND operation=?', (owner, operation)).fetchone()
        if existing:
            if existing['kind'] != kind or existing['name'] != name or (kind == 'rename' and existing['session'] != session_id):
                raise web.HTTPConflict(text='Ідентифікатор уже використано для іншої дії.')
            if existing['state'] == 'created':
                row = next((s for s in self.session_rows(owner) if s['id'] == existing['session']), None)
                return {'session': row, 'state': 'created'}
            raise web.HTTPConflict(text='Стан попередньої дії не підтверджено. Перевір теми в Telegram перед новою спробою.')
        if not capabilities or capabilities['topics_enabled'] is not True:
            raise web.HTTPConflict(text='Threaded mode не підтверджено. Перевір налаштування @BotFather та онови панель.')
        # A lost response must not create a second topic, including after reload.
        pending = self.db.execute("SELECT 1 FROM panel_topic_operations WHERE owner=? AND kind=? AND state IN ('requested','uncertain') AND (name=? OR (kind='rename' AND session=?))",
                                  (owner, kind, name, session_id)).fetchone()
        if pending:
            raise web.HTTPConflict(text='Подібна дія вже очікує перевірки в Telegram; повторний запит не надіслано.')
        with self.db:
            self.db.execute('INSERT INTO panel_topic_operations VALUES (?,?,?,?,?,?)',
                            (owner, operation, kind, name, session_id, 'requested'))
        telegram = self.controller.telegram
        try:
            if kind == 'create':
                result = await telegram._api('createForumTopic', {'chat_id': owner, 'name': name}, timeout=10, rate_limit_attempts=1)
                topic_id = result.get('message_thread_id') if isinstance(result, dict) else None
                if type(topic_id) is not int or topic_id <= 1:
                    raise TelegramError('createForumTopic')
                self.controller.sessions.resolve(owner, topic_id, name=name)
                session_id = 'topic:' + str(topic_id)
            else:
                destination = self.controller.sessions.destination(scope)
                result = await telegram._api('editForumTopic', {**destination, 'name': name}, timeout=10, rate_limit_attempts=1)
                if result is not True:
                    raise TelegramError('editForumTopic')
                self.controller.sessions.resolve(owner, destination['message_thread_id'], name=name)
        except (TelegramError, asyncio.CancelledError) as exc:
            state = 'failed' if isinstance(exc, TelegramError) and not exc.uncertain else 'uncertain'
            with self.db:
                self.db.execute('UPDATE panel_topic_operations SET state=? WHERE owner=? AND operation=?', (state, owner, operation))
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise web.HTTPServiceUnavailable(text='Результат дії не підтверджено. Перевір теми в Telegram; автоматичного повтору немає.') from None
        with self.db:
            self.db.execute("UPDATE panel_topic_operations SET state='created',session=? WHERE owner=? AND operation=?", (session_id, owner, operation))
        return {'session': next(s for s in self.session_rows(owner) if s['id'] == session_id), 'state': 'created'}

    def summary(self, owner):
        c = self.controller
        thread = c.threads.get(owner)
        turn = self.db.execute('SELECT status FROM turns WHERE chat_id=? AND thread_id=? ORDER BY rowid DESC LIMIT 1',
                               (owner, thread)).fetchone()
        waiting = self.db.execute("SELECT count(*) FROM interactions WHERE chat_id=? AND status='pending' AND expires>?",
                                  (owner, time.time())).fetchone()[0]
        username = c.config.get('telegram_username')
        telegram_url = 'https://t.me/' + username.lstrip('@') if isinstance(username, str) and re.fullmatch(r'@?[A-Za-z0-9_]{5,32}', username) else None
        return {
            'name': 'Oak', 'telegram_url': telegram_url,
            'session': {'initialized': bool(thread), 'active': owner in c.active,
                'awaiting_confirmation': waiting, 'turn_status': self.turn_status(turn[0]) if turn else None,
                'uncertain_inputs': self.db.execute("SELECT count(*) FROM inputs WHERE chat_id=? AND status='uncertain'", (owner,)).fetchone()[0]},
            'memory': {'count': self.db.execute('SELECT count(*) FROM memory_notes WHERE chat_id=?', (owner,)).fetchone()[0]},
            'computer': c.tools.computer_status(owner) if c.tools else None,
            'settings': {'model': MODEL, 'auth': 'chatgpt' if getattr(c.client, '_authenticated', False) is True else 'unavailable',
                'timezone': str(c.scheduler.timezone), 'sandbox': c.config.get('sandbox', 'workspace-write'),
                'approval_policy': c.config.get('runtime_config', {}).get('approval_policy', 'on-request'),
                'transport': self.gateway.config.get('transport', 'sse')},
        }

    @staticmethod
    def turn_status(status):
        return status if status in {'inProgress', 'completed', 'failed', 'interrupted', 'uncertain'} else None

    def tasks(self, owner, task_id=None):
        query = '''SELECT j.id,j.due,j.interval,j.mode,j.status,t.status AS turn_status,j.turn_id
                   FROM jobs j LEFT JOIN turns t ON t.turn_id=j.turn_id WHERE j.chat_id=?'''
        args = [owner]
        if task_id is not None:
            query += ' AND j.id=?'
            args.append(task_id)
        query += " ORDER BY j.status IN ('pending','running','uncertain') DESC,j.due DESC LIMIT 100"
        rows = self.db.execute(query, args).fetchall()
        tasks = [{k: row[k] for k in ('id', 'due', 'interval', 'mode', 'status')} |
                 {'turn_status': self.turn_status(row['turn_status'])} for row in rows]
        if task_id is None:
            return {'tasks': tasks, 'timezone': str(self.controller.scheduler.timezone)}
        if not rows:
            raise web.HTTPNotFound(text='Задачу не знайдено.')
        # The panel keeps only lifecycle events; never relay message or tool content.
        timeline = []
        if rows[0]['turn_id']:
            records = self.db.execute('''SELECT json_extract(event,'$.type'),json_extract(event,'$.timestamp'),
                json_extract(event,'$.status') FROM ui_events WHERE chat_id=? AND run_id=?
                AND json_extract(event,'$.type') IN ('RUN_STARTED','RUN_FINISHED','RUN_ERROR')
                ORDER BY sequence LIMIT 50''', (owner, rows[0]['turn_id']))
            for kind, timestamp, status in records:
                timeline.append({'type': kind, 'timestamp': timestamp if type(timestamp) in (int, float) else None,
                                 'status': self.turn_status(status)})
        return {'task': tasks[0], 'timeline': timeline}

    def thread_params(self, owner):
        thread = self.controller.threads.get(owner)
        return {'threadId': thread} if thread and thread in self.controller.loaded else {}

    async def rpc(self, method, params):
        try:
            result = await asyncio.wait_for(self.controller.client.request(method, params), 8)
            return result if isinstance(result, dict) else None
        except (RpcError, ConnectionError, OSError, asyncio.TimeoutError):
            return None  # Do not send runtime errors, paths or account data to a browser.

    async def servers(self, owner):
        params = {**self.thread_params(owner), 'detail': 'toolsAndAuthOnly', 'limit': 100}
        rows, cursor = [], None
        for _ in range(10):
            result = await self.rpc('mcpServerStatus/list', {**params, **({'cursor': cursor} if cursor else {})})
            if not result or not isinstance(result.get('data'), list):
                return None
            rows.extend(row for row in result['data'] if isinstance(row, dict) and isinstance(row.get('name'), str))
            next_cursor = result.get('nextCursor')
            if not next_cursor:
                return rows
            if next_cursor == cursor:
                return None
            cursor = next_cursor
        return None

    def callback_ready(self, request, owner):
        callback = self.controller.config.get('runtime_config', {}).get('mcp_oauth_callback_url')
        if callback:
            url = https_url(callback)
            if not url:
                return False
            host = urlsplit(url).hostname
            if host == 'localhost' or host.endswith('.localhost'):
                return False
            try:
                if not ipaddress.ip_address(host).is_global:
                    return False
            except ValueError:
                pass
            port = self.controller.config.get('runtime_config', {}).get('mcp_oauth_callback_port')
            return (type(port) is int and 1024 <= port <= 65535
                    and self.gateway.config.get('oauth_callback_ready') is True)
        # Loopback redirects work only in a browser on the runtime machine.
        host = urlsplit('http://' + request.host).hostname
        public_host = urlsplit(self.gateway.public_url).hostname
        return (self.gateway.config.get('oauth_loopback_ready') is True
                and public_host in {None, 'localhost', '127.0.0.1', '::1'}
                and host in {'localhost', '127.0.0.1', '::1'}
                and request.remote in {'127.0.0.1', '::1'}
                and not any(h in request.headers for h in ('Forwarded', 'X-Forwarded-Host', 'X-Forwarded-For'))
                and self.gateway.session_source(request) == 'browser')

    def valid_redirect(self, request, owner, authorization_url):
        redirects = [v for k, v in parse_qsl(urlsplit(authorization_url).query) if k == 'redirect_uri']
        if len(redirects) != 1:
            return False
        target = urlsplit(redirects[0])
        callback = self.controller.config.get('runtime_config', {}).get('mcp_oauth_callback_url')
        if callback:
            if not https_url(redirects[0]):
                return False
            expected = urlsplit(callback)
            base = expected.path.rstrip('/')
            suffix = target.path[len(base) + 1:] if target.path.startswith(base + '/') else ''
            return (target.netloc == expected.netloc and target.query == expected.query
                    and (target.path == expected.path or bool(re.fullmatch(r'[A-Za-z0-9_-]{1,128}', suffix))))
        return (self.callback_ready(request, owner) and target.scheme == 'http'
                and target.hostname in {'localhost', '127.0.0.1', '::1'}
                and not target.username and not target.password and not target.fragment)

    def oauth_state(self, owner, server):
        row = self.db.execute('SELECT state,expires FROM panel_oauth WHERE owner=? AND server=?', (owner, server)).fetchone()
        return ('expired' if row['expires'] <= time.time() else row['state']) if row else None

    async def inventory(self, request, owner, scope=None):
        scope = owner if scope is None else scope
        params = self.thread_params(scope)
        apps, plugins, servers = await asyncio.gather(
            self.rpc('app/installed', {**params, 'forceRefresh': True}),
            self.rpc('plugin/installed', {'cwds': [self.controller.cwd]}), self.servers(scope))
        app_items, plugin_items, server_items = [], [], []
        installed = apps.get('apps') if apps else None
        metadata = {}
        if isinstance(installed, list):
            app_ids = [a['id'] for a in installed if isinstance(a, dict) and isinstance(a.get('id'), str)]
            for start in range(0, len(app_ids), 100):
                detail = await self.rpc('app/read', {**params, 'appIds': app_ids[start:start + 100], 'includeTools': False})
                if detail and isinstance(detail.get('apps'), list):
                    metadata.update({a['id']: a for a in detail['apps'] if isinstance(a, dict) and isinstance(a.get('id'), str)})
            for app in installed:
                if not isinstance(app, dict) or not isinstance(app.get('id'), str):
                    continue
                info = metadata.get(app['id'], {})
                app_items.append({'id': identifier('app', app['id']), 'name': label(info.get('name') or app.get('runtimeName'), 'Інтеграція'),
                    'enabled': app.get('enabled') is True, 'callable': app.get('callable') is True,
                    'manage_available': bool(https_url(info.get('installUrl'), native=True))})
        marketplaces = plugins.get('marketplaces') if plugins else None
        if isinstance(marketplaces, list):
            for marketplace in marketplaces:
                for plugin in marketplace.get('plugins', []):
                    if not isinstance(plugin, dict) or plugin.get('installed') is not True:
                        continue
                    name = plugin.get('name') or ''
                    interface = plugin.get('interface') or {}
                    reason = plugin.get('disabledReason')
                    if plugin.get('availability') == 'DISABLED_BY_ADMIN':
                        reason = 'disabled_by_admin'
                    plugin_items.append({'id': identifier('plugin', str(plugin.get('id', name))),
                        'name': label(interface.get('displayName') or name, 'Плагін'), 'enabled': plugin.get('enabled') is True,
                        'auth_policy': plugin.get('authPolicy') if plugin.get('authPolicy') in {'ON_INSTALL', 'ON_USE'} else 'unknown',
                        'status': reason if reason in {'disabled_by_admin', 'plan_not_eligible', 'required_app_unavailable', 'unknown'}
                                  else ('enabled' if plugin.get('enabled') is True else 'disabled')})
        if servers is not None:
            for server in servers:
                auth = server.get('authStatus')
                state = server.get('runtimeStatus')
                login = auth == 'notLoggedIn' or (auth == 'oAuth' and state in {'authenticationRequired', 'failed'})
                ready = self.callback_ready(request, owner)
                tools = server.get('tools')
                server_items.append({'id': identifier('server', server['name']), 'name': label(server['name'], 'MCP сервер'),
                    'auth_status': auth if auth in {'unknown', 'unsupported', 'notLoggedIn', 'bearerToken', 'oAuth'} else 'unknown',
                    'runtime_status': state if state in {'notStarted', 'starting', 'connected', 'authenticationRequired', 'failed', 'cancelled', 'disabled'} else None,
                    'tool_count': len(tools) if isinstance(tools, (dict, list)) and not server.get('toolsError') else None,
                    'oauth_available': login and ready, 'oauth_reason': '' if login and ready else
                        ('Для входу з телефона потрібні зовнішній callback, сталий порт і підтвердження власником його готовності на сервері Oak.' if login else 'Runtime не повідомляє про потребу OAuth входу.'),
                    'login_state': self.oauth_state(owner, server['name'])})
        return {'scope': 'telegram' if params else 'runtime',
            'apps': {'available': isinstance(installed, list), 'items': app_items},
            'plugins': {'available': isinstance(marketplaces, list), 'complete': not bool((plugins or {}).get('marketplaceLoadErrors')), 'items': plugin_items},
            'servers': {'available': servers is not None, 'items': server_items}}

    async def manage(self, owner, app_id, scope=None):
        params = self.thread_params(owner if scope is None else scope)
        result = await self.rpc('app/installed', params)
        if result is None:
            raise web.HTTPServiceUnavailable(text='Стан інтеграцій недоступний.')
        app = next((a for a in result.get('apps', []) if identifier('app', a['id']) == app_id), None)
        if not app:
            raise web.HTTPNotFound(text='Інтеграцію не знайдено.')
        detail = await self.rpc('app/read', {**params, 'appIds': [app['id']], 'includeTools': False})
        info = next((a for a in (detail or {}).get('apps', []) if a.get('id') == app['id']), {})
        url = https_url(info.get('installUrl'), native=True)
        if not url:
            raise web.HTTPConflict(text='Runtime не надав підтримуване посилання на налаштування.')
        return {'url': url}

    async def login(self, request, owner, server_id, scope=None):
        scope = owner if scope is None else scope
        rows = await self.servers(scope)
        if rows is None:
            raise web.HTTPServiceUnavailable(text='Стан серверів недоступний.')
        server = next((s for s in rows if identifier('server', s['name']) == server_id), None)
        if not server:
            raise web.HTTPNotFound(text='Сервер не знайдено.')
        if not self.callback_ready(request, owner):
            raise web.HTTPConflict(text='Спочатку налаштуйте зовнішній OAuth callback або відкрийте панель локально на сервері.')
        if not (server.get('authStatus') == 'notLoggedIn' or (server.get('authStatus') == 'oAuth' and server.get('runtimeStatus') in {'authenticationRequired', 'failed'})):
            raise web.HTTPConflict(text='Runtime не повідомляє про потребу OAuth входу.')
        name = server['name']
        # Do not replay an uncertain external login after timeout, disconnect or restart.
        with self.db:
            existing = self.db.execute('SELECT 1 FROM panel_oauth WHERE server=? AND expires>?', (name, time.time())).fetchone()
            if existing:
                raise web.HTTPConflict(text='Вхід уже запитано. Перевірте стан; автоматично повторювати запит не будемо.')
            self.db.execute('INSERT OR REPLACE INTO panel_oauth VALUES (?,?,?,?)', (owner, name, 'requested', time.time() + 600))
        try:
            result = await asyncio.wait_for(self.controller.client.request('mcpServer/oauth/login',
                {**self.thread_params(scope), 'name': name, 'timeoutSecs': 300}), 12)
            url = https_url(result.get('authorizationUrl'))
            if not url:
                raise ValueError('Unsupported OAuth URL')
            # An external browser must be able to reach the exact callback supplied by the runtime.
            if not self.valid_redirect(request, owner, url):
                raise ValueError('Unsupported callback returned')
        except asyncio.CancelledError:
            with self.db:
                self.db.execute("UPDATE panel_oauth SET state='uncertain' WHERE owner=? AND server=?", (owner, name))
            raise
        except (RpcError, ConnectionError, OSError, asyncio.TimeoutError, ValueError, TypeError):
            with self.db:
                self.db.execute("UPDATE panel_oauth SET state='uncertain' WHERE owner=? AND server=?", (owner, name))
            raise web.HTTPServiceUnavailable(text='Стан OAuth запиту невідомий. Перевірте нативні налаштування перед повторним входом.') from None
        with self.db:
            self.db.execute("UPDATE panel_oauth SET state='pending' WHERE owner=? AND server=?", (owner, name))
        return {'url': url, 'state': 'pending'}
