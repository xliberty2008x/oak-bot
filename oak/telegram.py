"""Private-chat Telegram transport; Agent execution stays in the controller."""

import asyncio
from collections import deque
from dataclasses import asdict, dataclass, field
import json
import ipaddress
import math
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .a2ui_telegram import card_blocks
from .commands import command_names
from .media import ToolUnavailable
from .rendering import render_markdown

MAX_UPLOAD = 50 * 1024 * 1024
VOICE_DOWNLOAD_LIMIT = 20 * 1024 * 1024
VOICE_CACHE_LIMIT = 128 * 1024 * 1024
VOICE_ORPHAN_AGE = 24 * 3600
CARD = 'oakA2UICard'  # Outbox-only method; resolved to a rich send or an in-place edit.
CONTROL_COMMANDS = command_names() - {'/stop', '/new', '/approve', '/deny', '/answer'}


class TelegramError(RuntimeError):
    """Never include request URLs, tokens, or response bodies in diagnostics."""

    def __init__(self, method, code=0, *, reason=''):
        self.code = code
        self.reason = reason
        detail = " Another poller or a webhook may own this bot." if code == 409 else ""
        super().__init__(f"Telegram {method} failed (code {code or 'network/protocol'}).{detail}")

    @property
    def uncertain(self):
        return self.code == 0 or self.code >= 500


def split_text(text, limit=4096):
    """Bound UTF-16 units too, so astral emoji cannot exceed Telegram's limit."""
    chunks, current, units = [], [], 0
    for char in text:
        size = 2 if ord(char) > 0xFFFF else 1
        if units + size > limit:
            chunks.append(''.join(current))
            current, units = [], 0
        current.append(char)
        units += size
    if current:
        chunks.append(''.join(current))
    return chunks


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass
class _Reply:
    run_id: str
    parts: dict = field(default_factory=dict)
    message_ids: list = field(default_factory=list)
    sent: list = field(default_factory=list)
    terminal: bool = False
    error: str = ''
    sending: bool = False
    classic: bool = False

    def text(self):
        text = '\n\n'.join(value for value in self.parts.values() if value)
        if self.error:
            text = (text + '\n\n' if text else '') + self.error
        return text or ('Готово.' if self.terminal else 'Працюю…')


class TelegramGateway:
    def __init__(self, controller, token: str, allowed_user_ids: set[int], state_dir: Path,
                 *, api_url='https://api.telegram.org', local_directory=None):
        if not token or not allowed_user_ids or any(type(n) is not int or n <= 0 for n in allowed_user_ids):
            raise ValueError('Telegram requires a token and explicit positive numeric user IDs.')
        parsed = urllib.parse.urlsplit(api_url)
        try:
            local = ipaddress.ip_address(parsed.hostname or '').is_loopback
            port = parsed.port
        except ValueError:
            local, port = False, None
        if (parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.path not in ('', '/')
                or not ((api_url.rstrip('/') == 'https://api.telegram.org')
                        or (local and parsed.scheme in ('http', 'https') and port is not None))):
            raise ValueError('Telegram API must use the official HTTPS endpoint or an explicit loopback endpoint.')
        self.api_url = api_url.rstrip('/')
        self.local_directory = None
        if local:
            if local_directory is None:
                raise ValueError('A local Telegram API requires its shared file directory.')
            root = Path(local_directory).absolute()
            if root.resolve(strict=True) != root or not root.is_dir():
                raise ValueError('The local Telegram directory must be a real directory without symlinks.')
            self.local_directory = root
        elif local_directory is not None:
            raise ValueError('A local Telegram directory requires a loopback API endpoint.')
        self.controller = controller
        self.controller.telegram = self
        self.sessions = controller.sessions
        self._token = token
        self.allowed_user_ids = set(allowed_user_ids)
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        config = getattr(controller, 'config', None)
        self.discard_voice_audio = isinstance(config, dict) and config.get('telegram_discard_voice_audio') is True
        self.voice_directory = self.state_dir.resolve() / 'voice-inbox'
        self._voice_downloads = set()
        self._active_voice_files = set()
        self.offset_path = self.state_dir / 'telegram-offset.json'
        self.offset = 0
        if self.offset_path.exists():
            try:
                checkpoint = json.loads(self.offset_path.read_text())
                self.offset = checkpoint['offset']
                if type(self.offset) is not int or self.offset < 0:
                    raise ValueError
                # Cloud and local Bot API servers have independent update IDs.
                # Legacy checkpoints belong to the original cloud endpoint.
                if checkpoint.get('api_url', 'https://api.telegram.org') != self.api_url:
                    self.offset = 0
            except (ValueError, KeyError, TypeError):
                raise RuntimeError('Invalid Telegram offset file; inspect it before restarting.') from None
        self.events = asyncio.Queue()
        self._replies = {}
        self._active = {}
        self._last_send = {}
        self._delivery_locks = {}
        self._rich_supported = True  # Capability cache for this API endpoint.
        self._running = False
        self._activity = {}
        self._typing_tasks = {}
        self._intake_tasks = {}
        self._intake_workers = {}
        self._worker_group = None
        self._intake_wake = {chat_id: asyncio.Event() for chat_id in self.allowed_user_ids}
        db_path = self.state_dir / 'telegram-outbox.sqlite3'
        fd = os.open(db_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('''CREATE TABLE IF NOT EXISTS input_reactions (
            update_id INTEGER PRIMARY KEY, chat_id INTEGER, message_id INTEGER,
            run_id TEXT, received TEXT NOT NULL, completed TEXT NOT NULL)''')
        with self.db:
            self.db.execute("UPDATE input_reactions SET received='failed' WHERE received='sending'")
            self.db.execute("UPDATE input_reactions SET completed='failed' WHERE completed='sending'")
        self.db.execute('CREATE TABLE IF NOT EXISTS activity_stops (token TEXT PRIMARY KEY, outcome TEXT NOT NULL)')
        self.db.execute('''CREATE TABLE IF NOT EXISTS activity_cards (
            chat_id INTEGER, run_id TEXT, token TEXT UNIQUE, started REAL,
            tools TEXT NOT NULL, ended TEXT NOT NULL, state TEXT NOT NULL,
            message_id INTEGER, sent TEXT, delivery TEXT NOT NULL,
            PRIMARY KEY(chat_id,run_id))''')
        with self.db:
            self.db.execute("UPDATE activity_cards SET state='stale' WHERE state='running'")
            self.db.execute("UPDATE activity_cards SET delivery='uncertain' WHERE delivery='sending'")
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS replies (
                chat_id INTEGER, run_id TEXT, value TEXT, status TEXT NOT NULL,
                PRIMARY KEY(chat_id,run_id));
            CREATE TABLE IF NOT EXISTS deliveries (
                id TEXT PRIMARY KEY, chat_id INTEGER, method TEXT, payload TEXT,
                status TEXT NOT NULL, receipt TEXT);
            CREATE TABLE IF NOT EXISTS intake (
                update_id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL,
                payload TEXT NOT NULL, status TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS telegram_albums (
                chat_id INTEGER, group_id TEXT, deadline REAL, state TEXT NOT NULL,
                PRIMARY KEY(chat_id,group_id));
            CREATE TABLE IF NOT EXISTS telegram_album_members (
                update_id INTEGER PRIMARY KEY, chat_id INTEGER, group_id TEXT);
        ''')
        os.chmod(self.state_dir / 'telegram-outbox.sqlite3', 0o600)
        with self.db:
            self.db.execute("UPDATE deliveries SET status='uncertain' WHERE status='sending'")
            self.db.execute("UPDATE intake SET status='uncertain' WHERE status='processing'")
            self.db.execute("UPDATE telegram_albums SET state='uncertain' WHERE state='processing'")
            # Observe topic metadata from older accepted updates without moving
            # completed work. Only undispatched inputs can be safely rerouted.
            for row in self.db.execute("SELECT update_id,chat_id,payload,status FROM intake WHERE status IN ('pending','done') ORDER BY update_id").fetchall():
                update = json.loads(row['payload'])
                message = update.get('message') or update.get('edited_message') or {}
                if (row['chat_id'] == (message.get('chat') or {}).get('id')
                        and self._owner(message, message.get('from') or {}, self.allowed_user_ids)):
                    scope = self._message_scope(message)
                    if row['status'] == 'pending':
                        self.db.execute('UPDATE intake SET chat_id=? WHERE update_id=?', (scope, row['update_id']))
        for row in self.db.execute("SELECT * FROM replies WHERE status='pending' ORDER BY rowid").fetchall():
            if self._destination(row['chat_id']) is None:
                continue
            reply = _Reply(**json.loads(row['value']))
            if reply.sending:
                self._save_reply(row['chat_id'], reply, 'uncertain')
                continue
            # Restore delivery, never rerun model work from a Telegram snapshot.
            if not reply.terminal:
                reply.terminal = True
                if self._completed_turn(row['chat_id'], reply.run_id):
                    if (not any(reply.parts.values()) and not reply.error and reply.message_ids
                            and len(reply.message_ids) == len(reply.sent)
                            and all(type(value) is int and value > 0 for value in reply.message_ids)):
                        # The turn completed and these messages were acknowledged.
                        # An empty stale text snapshot cannot justify rewriting them.
                        self._save_reply(row['chat_id'], reply, 'delivered')
                        continue
                else:
                    reply.error = 'Попередню відповідь перервано перезапуском.'
            self._replies.setdefault(row['chat_id'], deque()).append(reply)

    def _completed_turn(self, chat_id, turn_id):
        db = self.sessions.db
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='turns'").fetchone():
            return False
        return db.execute("SELECT 1 FROM turns WHERE chat_id=? AND turn_id=? AND status='completed'",
                          (chat_id, turn_id)).fetchone() is not None

    def _record_improvement_event(self, chat_id, kind, metadata, *, request_id=None):
        recorder = getattr(type(self.controller), 'record_improvement_event', None)
        if callable(recorder):
            try:
                recorder(self.controller, chat_id, kind, metadata, request_id=request_id)
            except Exception:
                pass  # Monitoring failure cannot replay confirmed transport or block owner input.

    def _improvement_control(self, chat_id, text):
        parts = text.strip().split()
        action = parts[1].lower() if len(parts) == 2 else 'status' if len(parts) == 1 else None
        if action not in {'status', 'stop', 'resume'}:
            return 'Використай /improve status, /improve stop або /improve resume.'
        control = getattr(type(self.controller), 'improvement_control', None)
        if not callable(control):
            return 'Самопокращення не увімкнено для цієї розмови.'
        try:
            value = control(self.controller, chat_id, action)
        except Exception:
            return 'Стан самопокращення зараз недоступний.'
        if not isinstance(value, dict) or value.get('enabled') is not True:
            return 'Самопокращення не увімкнено для цієї розмови.'
        phase = str(value.get('phase', ''))
        if value.get('paused') is True:
            state = 'призупинено'
        elif phase in {'running', 'searching', 'search', 'evaluating', 'evaluation', 'frozen', 'candidate'}:
            state = 'працює'
        elif phase == 'blocked':
            state = 'потрібна перевірка оператором'
        elif phase == 'stopped':
            state = 'вичерпано бюджет циклів' if value.get('reason') == 'cycle_budget_exhausted' else 'цикли зупинено'
        elif phase in {'completed', 'complete'}:
            state = 'цикл завершено'
        else:
            state = 'очікує готових дозволених задач'
        lines = ['Самопокращення: ' + state + '.']
        if action == 'stop' and value.get('paused') is True:
            lines.append('Нові цикли зупинено. Поточна основна задача продовжується.')
        elif action == 'resume' and value.get('paused') is False:
            lines.append('Дозволено продовжувати цикли в межах наявного бюджету.')
        def count(key):
            result = value.get(key, 0)
            return result if type(result) is int and result >= 0 else 0
        lines.append(f'Випадки: {count("cases")}; дозволено для покращення: {count("approved_cases")}.')
        lines.append(f'Без готового сценарію: {count("awaiting_adapter")}; очікують дозволу: {count("awaiting_approval")}.')
        decision = value.get('last_decision')
        if isinstance(decision, dict) and type(decision.get('accepted')) is bool:
            classification = decision.get('classification')
            if classification == 'infrastructure_abort':
                lines.append('Останній цикл: якість не оцінювали через технічний збій.')
            elif classification == 'search_no_candidate':
                lines.append('Останній цикл: кандидат не сформований; якість не оцінювали.')
            elif decision.get('evaluated') is False:
                lines.append('Останній цикл: якість не оцінювали.')
            else:
                lines.append('Останній кандидат: ' + ('прийнято.' if decision['accepted'] else 'відхилено.'))
        version = value.get('active_version')
        if isinstance(version, str) and re.fullmatch('[a-f0-9]{64}', version):
            lines.append('Активна версія: ' + version[:12] + '.')
        else:
            lines.append('Активна версія: поточні процедури Oak.')
        return '\n'.join(lines)

    def _destination(self, scope):
        destination = self.sessions.destination(scope)
        return destination if destination and destination['chat_id'] in self.allowed_user_ids else None

    def _native_payload(self, method, payload):
        destination = self._destination(payload['chat_id'])
        if destination is None:
            raise ValueError('Telegram destination is unavailable.')
        payload = dict(payload)
        payload['chat_id'] = destination['chat_id']
        if method.startswith('send') and 'message_thread_id' in destination:
            payload['message_thread_id'] = destination['message_thread_id']
        else:
            payload.pop('message_thread_id', None)
        return payload

    def _message_scope(self, message):
        name = None
        for kind in ('forum_topic_created', 'forum_topic_edited'):
            if kind in message:
                value = message[kind].get('name')
                if isinstance(value, str):
                    name = ' '.join(''.join(c if ord(c) >= 32 and ord(c) != 127 else ' ' for c in value).split())[:128] or None
        closed = True if 'forum_topic_closed' in message else False if 'forum_topic_reopened' in message else None
        return self.sessions.resolve(message['chat']['id'], message.get('message_thread_id'), name=name, closed=closed)

    @staticmethod
    def _topic_service(message):
        return any(kind in message for kind in ('forum_topic_created', 'forum_topic_edited',
                   'forum_topic_closed', 'forum_topic_reopened', 'general_forum_topic_hidden', 'general_forum_topic_unhidden'))

    def _ensure_intake_worker(self, scope):
        self._intake_wake.setdefault(scope, asyncio.Event())
        worker = self._intake_workers.get(scope)
        if self._worker_group is not None and (worker is None or worker.done()):
            self._intake_workers[scope] = self._worker_group.create_task(self._intake_worker(scope))

    def _save_reply(self, chat_id, reply, status='pending'):
        if self.sessions.deleted(chat_id):
            status = 'cancelled'
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO replies VALUES (?,?,?,?)',
                            (chat_id, reply.run_id, json.dumps(asdict(reply)), status))

    def _queue_delivery(self, chat_id, method, payload, identifier=None):
        if self.sessions.deleted(chat_id):
            return
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO deliveries VALUES (?,?,?,?,?,NULL)',
                            (identifier or uuid.uuid4().hex, chat_id, method,
                             json.dumps(payload), 'pending'))

    def _artifact_file(self, value):
        path = Path(value).absolute()
        files = getattr(getattr(self.controller, 'tools', None), 'files', None)
        if files:
            if not files.stored(path):
                raise ValueError('An immutable artifact snapshot is required.')
            info = path.stat()
            if info.st_nlink != 1 or not 0 < info.st_size <= MAX_UPLOAD:
                raise ValueError('Artifact exceeds the upload limit.')
            return path
        resolved = path.resolve(strict=True)
        roots = [(self.state_dir / 'artifacts').resolve()]
        cwd = getattr(self.controller, 'cwd', None)
        if isinstance(cwd, (str, os.PathLike)):
            roots.append(Path(cwd).resolve())
        if path != resolved or not any(resolved.is_relative_to(root) for root in roots):
            raise ValueError('Artifact is outside the configured workspace/artifact directory.')
        blocked = {'.git', '.ssh', '.config', '.codex', 'secrets', 'credentials', 'browser-profile'}
        name = resolved.name.lower()
        if (set(part.lower() for part in resolved.parts) & blocked
                or name.startswith('.env') or name in {'auth.json', 'config.json'}
                or any(word in name for word in ('token', 'credential', 'secret'))
                or resolved.suffix.lower() in {'.pem', '.key', '.sqlite3', '.db'}):
            raise ValueError('Credential/configuration files cannot be uploaded as artifacts.')
        info = resolved.stat()
        if not resolved.is_file() or info.st_nlink != 1 or not 0 < info.st_size <= MAX_UPLOAD:
            raise ValueError('Artifact must be a regular file of at most 50 MiB.')
        return resolved

    def _multipart(self, payload, method='sendDocument'):
        payload = dict(payload)
        uploads = payload.pop('_uploads', None)
        if uploads is None:
            field_name = {'sendPhoto': 'photo', 'sendVideo': 'video', 'sendAudio': 'audio',
                          'sendVoice': 'voice'}.get(method, 'document')
            uploads = [{**payload.pop('_upload'), 'name': field_name}]
        boundary = 'oak-' + uuid.uuid4().hex
        body = bytearray()
        for key, value in payload.items():
            text = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{text}\r\n'.encode())
        for upload in uploads:
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', upload['name']):
                raise ValueError('Invalid upload field.')
            path = self._artifact_file(upload['path'])
            filename = re.sub(r'[^A-Za-z0-9._-]', '_', path.name)[:120] or 'artifact'
            mime = upload.get('mime') or mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            if not re.fullmatch(r'[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+', mime):
                mime = 'application/octet-stream'
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as file:
                info = os.fstat(file.fileno())
                if info.st_nlink != 1 or not 0 < info.st_size <= MAX_UPLOAD:
                    raise ValueError('Artifact changed before upload.')
                content = file.read(MAX_UPLOAD + 1)
            if len(content) > MAX_UPLOAD:
                raise ValueError('Artifact exceeds upload limit.')
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{upload["name"]}"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
            body.extend(content)
            body.extend(b'\r\n')
        body.extend(f'--{boundary}--\r\n'.encode())
        return bytes(body), 'multipart/form-data; boundary=' + boundary

    def _request_sync(self, method, payload, timeout=40):
        data, content_type = (self._multipart(payload, method) if '_upload' in payload or '_uploads' in payload
                              else (json.dumps(payload).encode(), 'application/json'))
        request = urllib.request.Request(
            f'{self.api_url}/bot{self._token}/{method}',
            data=data, headers={'Content-Type': content_type}, method='POST')
        try:
            try:
                with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
                    body = response.read()
            except urllib.error.HTTPError as exc:
                body = exc.read()
            result = json.loads(body)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (OSError, ValueError, urllib.error.URLError):
            raise TelegramError(method) from None

    async def _api(self, method, payload, *, timeout=40, rate_limit_attempts=3):
        waited = 0
        for attempt in range(rate_limit_attempts):
            response = await asyncio.to_thread(self._request_sync, method, payload, timeout)
            if not isinstance(response, dict):
                raise TelegramError(method)
            if response.get('ok') is True:
                return response.get('result')
            code = response.get('error_code', 0)
            if response.get('ok') is not False or type(code) is not int:
                raise TelegramError(method)
            if code == 429:
                try:
                    delay = max(1, float((response.get('parameters') or {}).get('retry_after', 1)))
                except (ValueError, TypeError):
                    raise TelegramError(method, code) from None
                if math.isfinite(delay) and waited + delay <= 30 and attempt + 1 < rate_limit_attempts:
                    waited += delay
                    await asyncio.sleep(delay)
                    continue
                raise TelegramError(method, code)
            description = str(response.get('description') or '').lower()
            # An edit retry can observe the desired text already installed.
            if method == 'editMessageText' and code == 400 and 'message is not modified' in description:
                return True
            reason = ''
            if method == 'getFile' and code == 400 and 'file is too big' in description:
                reason = 'file_too_big'
            elif code == 400 and any(phrase in description for phrase in (
                    'message thread not found', 'message thread is not found',
                    'message_thread_id_invalid', 'message_thread_invalid', 'message_thread_not_found',
                    'topic_id_invalid', 'topic_deleted', 'topic_not_found', 'topic not found', 'topic was deleted')):
                reason = 'topic_missing'
            elif method == 'editMessageText' and code == 400 and 'message to edit not found' in description:
                reason = 'message_missing'
            elif code == 404 and (description in ('not found', 'method not found')
                                or 'unknown method' in description or 'method not found' in description):
                reason = 'unknown_method'
            elif code == 400 and any(phrase in description for phrase in (
                    "can't parse", 'cannot parse', 'unsupported start tag', 'unsupported tag',
                    'rich_message', 'rich message', 'message text is empty', 'message is too long',
                    'too many entities', 'wrong entity')):
                reason = 'format'
            raise TelegramError(method, code, reason=reason)

    def _activity_add(self, chat_id, key):
        self._activity.setdefault(chat_id, set()).add(key)
        task = self._typing_tasks.get(chat_id)
        if self._running and (task is None or task.done()):
            self._typing_tasks[chat_id] = asyncio.create_task(self._typing(chat_id))

    async def _activity_remove(self, chat_id, key=None):
        active = self._activity.get(chat_id, set())
        active.clear() if key is None else active.discard(key)
        if active:
            return
        self._activity.pop(chat_id, None)
        task = self._typing_tasks.pop(chat_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise

    async def _typing(self, chat_id):
        deadline = time.monotonic()
        while self._activity.get(chat_id):
            try:
                await asyncio.wait_for(self._api('sendChatAction', self._native_payload('sendChatAction', {'chat_id': chat_id, 'action': 'typing'}),
                                                timeout=2, rate_limit_attempts=1), timeout=2)
            except Exception:
                pass  # Activity feedback must never fail the actual task.
            deadline += 4
            now = time.monotonic()
            if deadline <= now:
                deadline += (int((now - deadline) // 4) + 1) * 4
            await asyncio.sleep(max(0, deadline - time.monotonic()))

    async def _deliver(self, method, payload):
        chat_id = payload['chat_id']
        if self.sessions.deleted(chat_id):
            raise TelegramError(method, 400, reason='topic_missing')
        payload = self._native_payload(method, payload)
        owner = payload['chat_id']
        async with self._delivery_locks.setdefault(owner, asyncio.Lock()):
            delay = 1 - (time.monotonic() - self._last_send.get(owner, 0))
            if delay > 0:
                await asyncio.sleep(delay)
            for attempt in range(3):
                if self.sessions.deleted(chat_id):
                    raise TelegramError(method, 400, reason='topic_missing')
                try:
                    result = await self._api(method, payload)
                    break
                except TelegramError as exc:
                    if exc.reason == 'topic_missing' and chat_id < 0:
                        await self.controller.retire_topic(chat_id)
                    if method != 'editMessageText' or exc.code not in (0, 500, 502, 503, 504) or attempt == 2:
                        raise
                    await asyncio.sleep(2)
            self._last_send[chat_id] = time.monotonic()
            self._last_send[owner] = self._last_send[chat_id]
            return result

    def _checkpoint(self, offset):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.telegram-offset-', dir=self.state_dir)
        try:
            with os.fdopen(fd, 'w') as file:
                json.dump({'offset': offset, 'api_url': self.api_url}, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, self.offset_path)
            self.offset = offset
        finally:
            if os.path.exists(name):
                os.unlink(name)

    async def emit(self, chat_id: int, event: dict):
        if self._destination(chat_id) is not None:
            event = dict(event)
            if event.get('type') == 'CUSTOM' and event.get('name') == 'artifact':
                value = dict(event['value'])
                if value.get('path') is None:
                    value['path'] = str(await self.controller.bus.artifact_path(chat_id, value['id']))
                else:
                    files = getattr(getattr(self.controller, 'tools', None), 'files', None)
                    if files:
                        value['path'] = str(await files.snapshot(value['path']))
                event['value'] = value
            run_id = event.get('runId')
            if event.get('type') == 'RUN_STARTED' and run_id:
                self._activity_add(chat_id, 'run:' + run_id)
            # Persist before returning so a completed model reply survives a
            # crash even when the rendering worker has not reached it yet.
            self._apply(chat_id, dict(event))
            self._reaction_terminal(chat_id, event)
            self.events.put_nowait(None)
            if event.get('type') in ('RUN_FINISHED', 'RUN_ERROR'):
                await self._activity_remove(chat_id, 'run:' + run_id if run_id else None)

    def _local_attachment(self, remote_path):
        """Open only regular files beneath the explicitly shared Bot API root."""
        path = Path(remote_path)
        if self.local_directory is None or not path.is_relative_to(self.local_directory):
            raise ValueError('Telegram attachment is outside its shared directory.')
        parts = path.relative_to(self.local_directory).parts
        if not parts or self.local_directory.resolve(strict=True) != self.local_directory:
            raise ValueError('Telegram attachment directory is invalid.')
        directory = os.open(self.local_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            source = os.fdopen(fd, 'rb')
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                source.close()
                raise ValueError('Telegram attachment must be a regular file.')
            return source
        finally:
            os.close(directory)

    def _voice_files(self):
        root = self.voice_directory
        if root.is_symlink() or root.resolve() != root:
            raise ValueError('Invalid voice inbox.')
        root.mkdir(mode=0o700, exist_ok=True)
        for path in root.iterdir():
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if (stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
                    and not info.st_mode & 0o077):
                yield path, info

    def _remove_voice(self, path):
        path = Path(path)
        if (path.parent != self.voice_directory or self.voice_directory.is_symlink()
                or self.voice_directory.resolve() != self.voice_directory):
            return False
        try:
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077):
                return False
            path.unlink()
            return True
        except FileNotFoundError:
            return False

    def _pending_voice_paths(self):
        # Keep raw input needed by the controller's existing crash recovery.
        db = getattr(self.controller, 'db', None)
        if not isinstance(db, sqlite3.Connection):
            raise ValueError('Input state unavailable.')
        protected = set()
        for row in db.execute("SELECT attachments FROM inputs WHERE status IN ('pending','dispatching')"):
            items = json.loads(row[0])
            if not isinstance(items, list):
                raise ValueError('Invalid input state.')
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get('path'), str):
                    raise ValueError('Invalid input state.')
                protected.add(item['path'])
        return protected

    def _cleanup_voice_cache(self, now=None):
        if not self.discard_voice_audio:
            return 0
        try:
            protected = self._pending_voice_paths() | {str(path) for path in self._active_voice_files}
            cutoff = (time.time() if now is None else now) - VOICE_ORPHAN_AGE
            removed = 0
            for path, info in self._voice_files():
                owned_name = (re.fullmatch(r'[0-9]+-[0-9a-f]{32}\.ogg', path.name)
                              or re.fullmatch(r'\.download-[A-Za-z0-9_-]+', path.name))
                if owned_name and info.st_mtime < cutoff and str(path) not in protected:
                    removed += self._remove_voice(path)
            return removed
        except (OSError, ValueError, sqlite3.Error, TypeError, KeyError):
            return 0  # Unreadable recovery state must never justify deletion.

    async def _voice_cleanup_loop(self):
        while True:
            self._cleanup_voice_cache()
            await asyncio.sleep(3600)

    def _release_voice_attachments(self, attachments):
        voices = [Path(item['path']) for item in attachments if item.get('ephemeral_voice') is True]
        if not voices:
            return
        try:
            pending = self._pending_voice_paths()
        except (OSError, ValueError, sqlite3.Error, TypeError, KeyError):
            pending = {str(path) for path in voices}
        for path in voices:
            self._active_voice_files.discard(path)
            if str(path) not in pending:
                try:
                    self._remove_voice(path)
                except OSError:
                    pass  # The bounded orphan sweep can retry failed cleanup.

    def _download_sync(self, remote_path, destination, *, limit=None, cancel=None):
        if (not isinstance(remote_path, str) or not remote_path
                or any(part in {'.', '..'} for part in remote_path.split('/'))):
            raise ValueError('Telegram returned an invalid attachment path.')
        local = PurePosixPath(remote_path).is_absolute()
        if (local and self.local_directory is None) or (not local and not re.fullmatch(r'[A-Za-z0-9_./-]+', remote_path)):
            raise ValueError('Telegram returned an invalid attachment path.')
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if destination.parent.is_symlink() or destination.parent.resolve() != destination.parent:
            raise ValueError('Attachment inbox must not be a symlink.')
        fd, temporary = tempfile.mkstemp(prefix='.download-', dir=destination.parent)
        deadline = time.monotonic() + 120 if limit is not None else None
        try:
            with os.fdopen(fd, 'wb') as output:
                if cancel is not None and cancel.is_set():
                    raise ValueError('Voice download cancelled.')
                if local:
                    source = self._local_attachment(remote_path)
                else:
                    request = urllib.request.Request(f'{self.api_url}/file/bot{self._token}/{remote_path}')
                    source = urllib.request.build_opener(_NoRedirect()).open(request, timeout=20 if limit else 120)
                with source:
                    size = 0
                    while chunk := source.read(65536):
                        if ((cancel is not None and cancel.is_set())
                                or (deadline is not None and time.monotonic() > deadline)):
                            raise ValueError('Voice download stopped.')
                        size += len(chunk)
                        if limit is not None and size > limit:
                            raise ValueError('Voice message exceeds the download limit.')
                        output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if cancel is not None and cancel.is_set():
                raise ValueError('Voice download cancelled.')
            os.replace(temporary, destination)
        except (OSError, urllib.error.URLError):
            raise TelegramError('download') from None
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    async def _attachments(self, message, update_id):
        photo = message.get('photo')
        document = message.get('document')
        if photo:
            item = max(photo, key=lambda value: value.get('width', 0) * value.get('height', 0))
            name, mime = 'photo.jpg', 'image/jpeg'
        elif document:
            item = document
            name = re.sub(r'[^\w. -]', '_', Path(item.get('file_name') or 'document').name)[:100]
            mime = item.get('mime_type') or mimetypes.guess_type(name)[0] or 'application/octet-stream'
        elif message.get('voice') or message.get('audio'):
            item = message.get('voice') or message['audio']
            name = re.sub(r'[^\w. -]', '_', Path(item.get('file_name') or 'voice.ogg').name)[:100]
            mime = item.get('mime_type') or mimetypes.guess_type(name)[0] or 'audio/ogg'
        elif message.get('video') or message.get('video_note') or message.get('animation'):
            item = message.get('video') or message.get('video_note') or message['animation']
            name = re.sub(r'[^\w. -]', '_', Path(item.get('file_name') or 'video.mp4').name)[:100]
            mime = item.get('mime_type') or mimetypes.guess_type(name)[0] or 'video/mp4'
        else:
            return []
        ephemeral = self.discard_voice_audio and item is message.get('voice')
        if ephemeral:
            if type(item.get('file_size')) is int and item['file_size'] > VOICE_DOWNLOAD_LIMIT:
                raise ValueError('Voice message exceeds the download limit.')
            self._cleanup_voice_cache()
            used = sum(info.st_size for _, info in self._voice_files())
            if used + (len(self._voice_downloads) + 1) * VOICE_DOWNLOAD_LIMIT > VOICE_CACHE_LIMIT:
                raise ValueError('Voice inbox is full.')
            destination = self.voice_directory / f'{update_id}-{uuid.uuid4().hex}.ogg'
            self._voice_downloads.add(destination)
            self._active_voice_files.add(destination)
            cancelled = threading.Event()
            async def download_voice():
                info = await self._api('getFile', {'file_id': item['file_id']},
                                       timeout=40)
                if cancelled.is_set():
                    return
                await asyncio.to_thread(self._download_sync, info['file_path'], destination,
                                        limit=VOICE_DOWNLOAD_LIMIT, cancel=cancelled)
            download = asyncio.create_task(download_voice())
            try:
                await asyncio.shield(download)
            except asyncio.CancelledError:
                cancelled.set()
                # to_thread can finish after cancellation. Its unique result
                # is not given to the controller and must be removed then.
                def finished(task):
                    self._voice_downloads.discard(destination)
                    self._active_voice_files.discard(destination)
                    try:
                        task.result()
                    except BaseException:
                        pass
                    try:
                        self._remove_voice(destination)
                    except OSError:
                        pass
                download.add_done_callback(finished)
                raise
            except BaseException:
                self._active_voice_files.discard(destination)
                try:
                    self._remove_voice(destination)
                except OSError:
                    pass
                raise
            finally:
                if download.done():
                    self._voice_downloads.discard(destination)
            return [{'path': str(destination), 'mime': 'audio/ogg', 'name': 'voice.ogg',
                     'ephemeral_voice': True}]
        info = await self._api('getFile', {'file_id': item['file_id']},
                               timeout=3600 if self.local_directory else 40)
        destination = self.state_dir.resolve() / 'inbox' / f'{update_id}-{name}'
        await asyncio.to_thread(self._download_sync, info['file_path'], destination)
        return [{'path': str(destination), 'mime': mime, 'name': name}]

    @staticmethod
    def _owner(message, sender, allowed):
        chat = message.get('chat') or {}
        user_id = sender.get('id')
        return (chat.get('type') == 'private' and type(user_id) is int
                and user_id in allowed and chat.get('id') == user_id and not sender.get('is_bot'))

    async def _decision(self, chat_id, text, *, notify=True):
        parts = text.split(maxsplit=2)
        command = parts[0].split('@', 1)[0]
        if len(parts) < 2 or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', parts[1]):
            await self._notice(chat_id, 'Вкажи ідентифікатор запиту після команди.')
            return
        try:
            if command == '/answer':
                if len(parts) != 3:
                    await self._notice(chat_id, 'Формат: /answer ID відповідь')
                    return
                result = await self.controller.answer(chat_id, parts[1], parts[2])
            else:
                result = await self.controller.approve(chat_id, parts[1], command == '/approve')
        except (ValueError, RuntimeError):
            outcome = 'Запит недоступний, уже оброблений або завершився.'
        else:
            outcome = result if isinstance(result, str) and result else 'Відповідь прийнято.'
        self.events.put_nowait(None)
        if notify:
            await self._notice(chat_id, outcome)
        return outcome

    async def _notice(self, chat_id, text):
        if callable(getattr(type(self.controller), 'notice', None)):
            await self.controller.notice(chat_id, text)
            return
        run_id = 'notice-' + uuid.uuid4().hex
        for event in (
            {'type': 'RUN_STARTED', 'runId': run_id},
            {'type': 'TEXT_MESSAGE_CONTENT', 'runId': run_id, 'messageId': run_id, 'delta': text},
            {'type': 'RUN_FINISHED', 'runId': run_id},
        ):
            await self.emit(chat_id, event)

    @staticmethod
    def _command(message):
        text = message.get('text') or ''
        return text.strip().split()[0].split('@', 1)[0] if text.strip() else ''

    async def _cancel_intake(self, chat_id):
        processing = [row[0] for row in self.db.execute(
            "SELECT update_id FROM intake WHERE chat_id=? AND status='processing'", (chat_id,))]
        with self.db:
            count = self.db.execute("UPDATE intake SET status='cancelled' WHERE chat_id=? "
                                    "AND status IN ('pending','processing')", (chat_id,)).rowcount
        task = self._intake_tasks.get(chat_id)
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
        uncertain = [update_id for update_id in processing if self.controller.db.execute(
            "SELECT 1 FROM inputs WHERE update_id=? AND status IN ('dispatching','uncertain')",
            (update_id,)).fetchone()]
        if uncertain:
            with self.db:
                self.db.executemany("UPDATE intake SET status='uncertain' WHERE update_id=?",
                                    [(update_id,) for update_id in uncertain])
            return 'uncertain'
        return 'cancelled' if count else 'idle'

    async def discard_topic(self, chat_id):
        """Stop local delivery after a topic has been durably tombstoned."""
        await self._cancel_intake(chat_id)
        await self._activity_remove(chat_id)
        self._replies.pop(chat_id, None)
        self._active.pop(chat_id, None)
        with self.db:
            self.db.execute("UPDATE replies SET status='cancelled' WHERE chat_id=? AND status='pending'", (chat_id,))
            self.db.execute("UPDATE deliveries SET status='cancelled' WHERE chat_id=? AND status IN ('pending','sending')", (chat_id,))
        self.events.put_nowait(None)

    async def handle_update(self, update, *, queued=False, album=None):
        update_id = update.get('update_id')
        if type(update_id) is not int or (not queued and update_id < self.offset):
            return
        callback = update.get('callback_query')
        if callback:
            message = callback.get('message') or {}
            if self._owner(message, callback.get('from') or {}, self.allowed_user_ids):
                data = callback.get('data', '')
                match = re.fullmatch(r'(approve|deny):([A-Za-z0-9_.:-]{1,56})', data)
                scope = self._message_scope(message)
                outcome = 'Запит недоступний.'
                stop = re.fullmatch(r'stop:([a-f0-9]{16})', data)
                reminder = re.fullmatch(r'remind:([a-f0-9]{16}):(600|3600|done)', data)
                cancel_job = re.fullmatch(r'cancel-job:([a-f0-9]{12})', data)
                model_choice = re.fullmatch(r'model:([a-f0-9]{32})', data)
                if model_choice and not self.sessions.deleted(scope):
                    from .chat_models import chat_models
                    outcome = await chat_models(self.controller).choose(scope, model_choice[1])
                elif cancel_job and not self.sessions.deleted(scope):
                    outcome = self._cancel_job(scope, cancel_job[1])
                    await self._settle_task_controls(scope, message.get('message_id'))
                elif reminder and not self.sessions.deleted(scope):
                    try:
                        result = self.controller.scheduler.reminders.decide(scope, reminder[1], reminder[2])
                        outcome = 'Нагадаю пізніше.' if result['state'] == 'snoozed' else 'Нагадування завершено.'
                        self.events.put_nowait(None)
                    except ValueError as exc:
                        outcome = str(exc)
                elif stop and not self.sessions.deleted(scope):
                    outcome = await self._stop_callback(scope, stop[1])
                elif match and not self.sessions.deleted(scope):
                    outcome = await self._decision(scope, f'/{match[1]} {match[2]}', notify=False)
                try:
                    await self._api('answerCallbackQuery', {'callback_query_id': callback['id'], 'text': outcome or 'Запит недоступний.'})
                except TelegramError as exc:
                    if exc.code != 400:  # An expired callback needs no replay.
                        raise
            if not queued:
                self._checkpoint(update_id + 1)
            return
        message = update.get('message') or update.get('edited_message') or {}
        chat, sender = message.get('chat') or {}, message.get('from') or {}
        chat_id, user_id = chat.get('id'), sender.get('id')
        if self._owner(message, sender, self.allowed_user_ids):
            chat_id = self._message_scope(message)
            if self.sessions.deleted(chat_id) or self._topic_service(message):
                if not queued:
                    self._checkpoint(update_id + 1)
                return
            if update.get('edited_message'):
                self._queue_delivery(chat_id, 'sendMessage', {'chat_id': chat_id,
                    'text': 'Редагування не запускає задачу повторно. Надішли виправлення новим повідомленням.'},
                    'edited-input:' + str(update_id))
                self.events.put_nowait(None)
                if not queued:
                    self._checkpoint(update_id + 1)
                return
            text = message.get('text') or message.get('caption') or ''
            reply_to = (message.get('reply_to_message') or {}).get('message_id')
            if type(reply_to) is int and reply_to > 0:
                previous = self.db.execute("SELECT id FROM deliveries WHERE chat_id=? AND status='delivered' "
                    "AND json_extract(receipt,'$.message_id')=? AND json_type(payload,'$._upload')='object' "
                    "ORDER BY rowid DESC LIMIT 1", (chat_id, reply_to)).fetchone()
                if previous:
                    self._record_improvement_event(chat_id, 'owner_feedback',
                        {'transport': 'telegram', 'reply_to_message_id': reply_to,
                         'delivery_reference': 'telegram-delivery:' + previous['id']},
                        request_id='telegram-update:' + str(update_id))
            command = self._command(message)
            if command == '/improve':
                await self._notice(chat_id, self._improvement_control(chat_id, text))
            elif command in {'/approve', '/deny', '/answer'}:
                if message.get('forward_origin') or message.get('forward_from'):
                    await self._notice(chat_id, 'Підтвердження потрібно написати особисто, без пересилання.')
                else:
                    await self._decision(chat_id, text.strip())
            elif command == '/stop':
                intake_status = await self._cancel_intake(chat_id)
                try:
                    result = await self.controller.stop(chat_id)
                finally:
                    await self._activity_remove(chat_id)
                if intake_status == 'uncertain':
                    notice = ('Не вдалося підтвердити запуск або зупинку задачі у рушії. '
                              'Локальне очікування скасовано; стан збережено, автоматично повторювати не буду.')
                elif result == 'requested':
                    notice = 'Запит на зупинку прийнято.'
                elif intake_status == 'cancelled':
                    notice = 'Очікування та підготовку запитів скасовано.'
                else:
                    notice = 'Зараз немає активної задачі для зупинки.'
                await self._notice(chat_id, notice)
            elif command == '/new':
                try:
                    if self._intake_tasks.get(chat_id) or self.db.execute(
                            "SELECT 1 FROM intake WHERE chat_id=? AND status='pending' LIMIT 1", (chat_id,)).fetchone():
                        raise RuntimeError('Input is still being prepared.')
                    await self.controller.new(chat_id)
                except RuntimeError:
                    await self._notice(chat_id, 'Спочатку зупини поточну задачу командою /stop.')
                else:
                    await self._notice(chat_id, 'Наступне повідомлення почне нову розмову.')
            elif command in CONTROL_COMMANDS:
                try:
                    result = await self.controller.command(chat_id, text, update_id)
                except (ValueError, OverflowError):
                    result = 'Не вдалося виконати команду. Перевір її аргументи; /help — список команд.'
                if isinstance(result, str) and result:
                    await self._notice(chat_id, result)
                if command == '/tasks':
                    self._task_controls(chat_id)
                elif command == '/cancel':
                    await self._settle_task_controls(chat_id)
            else:
                # submit checkpoints input before it returns. On uncertain
                # acceptance propagate the failure without advancing the cursor.
                attachments = []
                try:
                    try:
                        sources = album or [update]
                        captions = []
                        for source in sources:
                            original = source.get('message') or {}
                            attachments.extend(await self._attachments(original, source['update_id']))
                            caption = original.get('text') or original.get('caption') or ''
                            if caption.strip() and caption not in captions:
                                captions.append(caption)
                        if album:
                            text = '\n\n'.join(captions)
                    except (ValueError, TelegramError) as exc:
                        if isinstance(exc, TelegramError) and exc.reason != 'file_too_big':
                            raise
                        text = ('Хмарний Telegram Bot API не віддає боту цей великий файл. Потрібне локальне підключення Telegram.'
                                if isinstance(exc, TelegramError) else
                                'Не вдалося прийняти файл. Спробуй надіслати його повторно як документ.')
                        await self._notice(chat_id, text)
                        if not queued:
                            self._checkpoint(update_id + 1)
                        return
                    if attachments:
                        await self.controller.submit(chat_id, text or 'Переглянь прикріплений файл.',
                                                     update_id, attachments=attachments)
                    elif text.strip():
                        await self.controller.submit(chat_id, text, update_id)
                    else:
                        await self._notice(chat_id, 'Надішли текст, фото, голосове повідомлення або документ.')
                    self._bind_reaction(chat_id, update_id)
                    for source in album or []:
                        self._bind_reaction(chat_id, source['update_id'], accepted_update=update_id)
                except (ValueError, ToolUnavailable):
                    await self._notice(chat_id, 'Не вдалося опрацювати вкладення. Перевір формат файлу та доступність обробки медіа.')
                    if queued:
                        raise
                    self._checkpoint(update_id + 1)
                    return
                except Exception:
                    try:
                        identifier = f'submit-error:{update_id}'
                        self._queue_delivery(chat_id, 'sendMessage', {
                            'chat_id': chat_id,
                            'text': 'Не вдалося підтвердити прийняття запиту. Стан збережено для перевірки; автоматично повторювати не буду.'}, identifier)
                        row = self.db.execute('SELECT * FROM deliveries WHERE id=?', (identifier,)).fetchone()
                        await self._render_delivery(row)
                    except TelegramError:
                        pass
                    raise
                finally:
                    self._release_voice_attachments(attachments)
        if not queued:
            self._checkpoint(update_id + 1)

    async def _ingest(self, update):
        update_id = update.get('update_id')
        if type(update_id) is not int or update_id < self.offset:
            return
        message = update.get('message') or update.get('edited_message') or {}
        if (update.get('callback_query') or update.get('edited_message') or self._command(message) in
                {'/stop', '/new', '/approve', '/deny', '/answer', '/status', '/help', '/start', '/web', '/model'}
                or self._topic_service(message)
                or not self._owner(message, message.get('from') or {}, self.allowed_user_ids)):
            await self.handle_update(update)
            return
        chat_id = self._message_scope(message)
        if self.sessions.deleted(chat_id):
            self._checkpoint(update_id + 1)
            return
        # This durable handoff acknowledges receipt without waiting for file
        # download/transcription. Each chat still dispatches its inputs in order.
        group = message.get('media_group_id')
        if isinstance(group, str) and 0 < len(group) <= 128:
            album = self.db.execute('SELECT deadline,state FROM telegram_albums WHERE chat_id=? AND group_id=?', (chat_id, group)).fetchone()
            count = self.db.execute('SELECT count(*) FROM telegram_album_members WHERE chat_id=? AND group_id=?', (chat_id, group)).fetchone()[0]
            if album and (album['state'] != 'collecting' or time.time() > album['deadline'] or count >= 10):
                self._queue_delivery(chat_id, 'sendMessage', {'chat_id': chat_id,
                    'text': 'Цей альбом уже прийнято. Надішли пізнє вкладення окремим повідомленням.'}, 'late-album:' + str(update_id))
                self._checkpoint(update_id + 1)
                self.events.put_nowait(None)
                return
        else:
            group = None
        with self.db:
            if group is not None:
                self.db.execute("INSERT OR IGNORE INTO telegram_albums VALUES (?,?,?,'collecting')", (chat_id, group, time.time() + 0.75))
                self.db.execute('INSERT OR IGNORE INTO telegram_album_members VALUES (?,?,?)', (update_id, chat_id, group))
            self.db.execute("INSERT OR IGNORE INTO intake VALUES (?,?,?,'pending')",
                            (update_id, chat_id, json.dumps(update)))
            config = getattr(self.controller, 'config', {})
            enabled = config.get('telegram_reactions', True) if isinstance(config, dict) else True
            if enabled and type(message.get('message_id')) is int:
                self.db.execute("INSERT OR IGNORE INTO input_reactions VALUES (?,?,?,NULL,'pending','waiting')",
                                (update_id, chat_id, message['message_id']))
        self._checkpoint(update_id + 1)
        self._ensure_intake_worker(chat_id)
        self._activity_add(chat_id, 'input:' + str(update_id))
        self._intake_wake[chat_id].set()

    async def _intake_worker(self, chat_id):
        self._intake_wake.setdefault(chat_id, asyncio.Event())
        while True:
            row = self.db.execute("SELECT update_id,payload FROM intake WHERE chat_id=? "
                                  "AND status='pending' ORDER BY update_id LIMIT 1", (chat_id,)).fetchone()
            if row is None:
                self._intake_wake[chat_id].clear()
                await self._intake_wake[chat_id].wait()
                continue
            member = self.db.execute('SELECT group_id FROM telegram_album_members WHERE update_id=?', (row['update_id'],)).fetchone()
            rows = [row]
            if member:
                group = member['group_id']
                album = self.db.execute('SELECT deadline,state FROM telegram_albums WHERE chat_id=? AND group_id=?', (chat_id, group)).fetchone()
                if album['state'] == 'collecting':
                    await asyncio.sleep(max(0, min(0.75, album['deadline'] - time.time())))
                rows = self.db.execute("SELECT i.update_id,i.payload FROM intake i JOIN telegram_album_members m USING(update_id) "
                                       "WHERE m.chat_id=? AND m.group_id=? AND i.status='pending' ORDER BY i.update_id", (chat_id, group)).fetchall()
                if not rows:
                    continue
                if album['state'] != 'collecting':
                    with self.db:
                        self.db.executemany("UPDATE intake SET status='uncertain' WHERE update_id=?", [(item['update_id'],) for item in rows])
                    continue
            with self.db:
                self.db.executemany("UPDATE intake SET status='processing' WHERE update_id=?", [(item['update_id'],) for item in rows])
                if member:
                    self.db.execute("UPDATE telegram_albums SET state='processing' WHERE chat_id=? AND group_id=?", (chat_id, group))
            updates = [json.loads(item['payload']) for item in rows]
            task = asyncio.create_task(self.handle_update(updates[0], queued=True, album=updates if member else None))
            self._intake_tasks[chat_id] = task
            self._activity_add(chat_id, 'input:' + str(row['update_id']))
            try:
                await task
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise  # Restart will quarantine the interrupted dispatch.
                status = 'cancelled'
            except (ValueError, ToolUnavailable):
                status = 'failed'
            except Exception:
                status = 'uncertain'
                self._queue_delivery(chat_id, 'sendMessage', {'chat_id': chat_id,
                    'text': 'Не вдалося підтвердити прийняття запиту. Стан збережено для перевірки; автоматично повторювати не буду.'},
                    f"submit-error:{row['update_id']}")
                self.events.put_nowait(None)
            else:
                status = 'done'
                active = getattr(self.controller, 'active', {})
                if isinstance(active, dict) and active.get(chat_id):
                    # Dispatch acknowledgement may precede RUN_STARTED.
                    self._activity_add(chat_id, 'run:' + active[chat_id])
            finally:
                self._intake_tasks.pop(chat_id, None)
                for item in rows:
                    await self._activity_remove(chat_id, 'input:' + str(item['update_id']))
            with self.db:
                self.db.executemany("UPDATE intake SET status=? WHERE update_id=? AND status='processing'",
                                    [(status, item['update_id']) for item in rows])
                if member:
                    self.db.execute('UPDATE telegram_albums SET state=? WHERE chat_id=? AND group_id=?', (status, chat_id, group))

    async def _poll(self):
        while True:
            try:
                updates = await self._api('getUpdates', {
                    'offset': self.offset, 'timeout': 25,
                    'allowed_updates': ['message', 'edited_message', 'callback_query']})
            except TelegramError as exc:
                if exc.code in (0, 500, 502, 503, 504):
                    await asyncio.sleep(2)
                    continue
                raise
            if not isinstance(updates, list):
                raise TelegramError('getUpdates')
            for update in updates:
                await self._ingest(update)

    def _bind_reaction(self, chat_id, update_id, *, accepted_update=None):
        db = getattr(self.controller, 'db', None)
        if not isinstance(db, sqlite3.Connection) or not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='input_runs'").fetchone():
            return
        row = db.execute('SELECT r.turn_id,t.status FROM input_runs r LEFT JOIN turns t ON t.turn_id=r.turn_id '
                         'WHERE r.update_id=? AND r.chat_id=?', (accepted_update if accepted_update is not None else update_id, chat_id)).fetchone()
        if row:
            with self.db:
                self.db.execute("UPDATE input_reactions SET run_id=?,completed=? WHERE update_id=? AND completed='waiting'",
                                (row[0], 'pending' if row[1] == 'completed' else 'waiting', update_id))

    def _reaction_terminal(self, chat_id, event):
        if event.get('type') not in {'RUN_FINISHED', 'RUN_ERROR'}:
            return
        success = (event['type'] == 'RUN_FINISHED' and event.get('status', 'completed') == 'completed'
                   and event.get('outcome') != 'interrupted')
        with self.db:
            self.db.execute("UPDATE input_reactions SET completed=? WHERE chat_id=? AND run_id=? AND completed='waiting'",
                            ('pending' if success else 'failed', chat_id, event.get('runId')))

    async def _render_reactions(self):
        rows = self.db.execute("SELECT * FROM input_reactions WHERE received='pending' OR completed='pending' LIMIT 10").fetchall()
        for row in rows:
            if self.sessions.deleted(row['chat_id']):
                continue
            column = 'completed' if row['completed'] == 'pending' else 'received'
            with self.db:
                self.db.execute('UPDATE input_reactions SET ' + column + "='sending' WHERE update_id=?", (row['update_id'],))
                if column == 'completed':
                    self.db.execute("UPDATE input_reactions SET received='done' WHERE update_id=?", (row['update_id'],))
            try:
                payload = self._native_payload('setMessageReaction', {'chat_id': row['chat_id'],
                    'message_id': row['message_id'], 'reaction': [{'type': 'emoji', 'emoji': '👍' if column == 'completed' else '👀'}]})
                await asyncio.wait_for(self._api('setMessageReaction', payload, timeout=2, rate_limit_attempts=1), 2)
                outcome = 'done'
            except Exception:
                outcome = 'failed'
            with self.db:
                self.db.execute('UPDATE input_reactions SET ' + column + '=? WHERE update_id=?', (outcome, row['update_id']))

    def _activity_event(self, chat_id, event):
        from .activity import tool_label
        run_id, kind = event.get('runId'), event.get('type')
        if not isinstance(run_id, str) or run_id.startswith(('notice-', 'job-', 'approval-')):
            return
        row = self.db.execute('SELECT * FROM activity_cards WHERE chat_id=? AND run_id=?', (chat_id, run_id)).fetchone()
        if kind == 'RUN_STARTED' and row is None:
            with self.db:
                self.db.execute("INSERT INTO activity_cards VALUES (?,?,?,?,?,?,'running',NULL,NULL,'pending')",
                                (chat_id, run_id, uuid.uuid4().hex[:16], time.time(), '{}', '[]'))
            return
        if row is None or row['state'] != 'running':
            return
        tools, ended = json.loads(row['tools']), json.loads(row['ended'])
        call = event.get('toolCallId')
        state = row['state']
        if kind == 'TOOL_CALL_START' and isinstance(call, str) and call not in ended and len(tools) < 128:
            tools[call] = tool_label(event.get('toolCallName'))
        elif kind in {'TOOL_CALL_RESULT', 'STEP_FINISHED'} and isinstance(call, str):
            tools.pop(call, None)
            if call not in ended and len(ended) < 512:
                ended.append(call)
        elif kind in {'RUN_FINISHED', 'RUN_ERROR'}:
            state = ('failed' if kind == 'RUN_ERROR' else 'interrupted'
                     if event.get('status') == 'interrupted' or event.get('outcome') == 'interrupted' else 'completed')
            tools.clear()
        else:
            return  # TOOL_CALL_END closes arguments, not execution.
        with self.db:
            self.db.execute('UPDATE activity_cards SET tools=?,ended=?,state=? WHERE chat_id=? AND run_id=?',
                            (json.dumps(tools), json.dumps(ended), state, chat_id, run_id))

    async def _stop_callback(self, chat_id, token):
        row = self.db.execute('SELECT run_id,state FROM activity_cards WHERE token=? AND chat_id=?', (token, chat_id)).fetchone()
        if row is None:
            return 'Запит недоступний.'
        receipt = self.db.execute('SELECT outcome FROM activity_stops WHERE token=?', (token,)).fetchone()
        if receipt:
            outcome = receipt[0]
        elif row['state'] != 'running':
            outcome = 'not_active'
        else:
            with self.db:
                self.db.execute("INSERT INTO activity_stops VALUES (?,'dispatching')", (token,))
            try:
                outcome = await self.controller.stop(chat_id, expected_turn=row['run_id'])
            except Exception:
                outcome = 'uncertain'
            with self.db:
                self.db.execute('UPDATE activity_stops SET outcome=? WHERE token=?', (outcome, token))
            self.events.put_nowait(None)
        return {'requested': 'Запит на зупинку прийнято.', 'idle': 'Задача вже завершилася.',
                'not_active': 'Ця задача вже завершилася.'}.get(outcome,
                'Результат зупинки невідомий. Перевір стан; повторно не надсилаю.')

    def _activity_payload(self, row):
        labels = {'completed': 'Обробку завершено.', 'failed': 'Задача завершилася з помилкою.',
                  'interrupted': 'Задачу зупинено.', 'stale': 'Стан задачі після перезапуску потребує перевірки.'}
        if row['state'] == 'running':
            activity = list(dict.fromkeys(json.loads(row['tools']).values()))
            text = ' · '.join(activity[:3]) or 'Працюю…'
            seconds = max(0, int(time.time() - row['started']))
            if seconds >= 30:
                text += ' · ' + str(seconds // 60) + ' хв ' + str(seconds % 60 // 10 * 10) + ' с'
        else:
            text = labels.get(row['state'], 'Стан задачі недоступний.')
        controls = []
        if row['state'] == 'running' and not self.db.execute('SELECT 1 FROM activity_stops WHERE token=?', (row['token'],)).fetchone():
            controls = [[{'text': '⏹ Зупинити', 'callback_data': 'stop:' + row['token']}]]
        return {'chat_id': row['chat_id'], 'text': text, 'reply_markup': {'inline_keyboard': controls}}

    async def _render_activity(self):
        rows = self.db.execute("SELECT * FROM activity_cards WHERE delivery IN ('pending','delivered')").fetchall()
        for row in rows:
            if self.sessions.deleted(row['chat_id']):
                continue
            if time.monotonic() - self._last_send.get(self.sessions.owner(row['chat_id']), 0) < 1:
                continue
            payload = self._activity_payload(row)
            serialized = json.dumps(payload)
            if row['sent'] == serialized:
                continue
            editing = type(row['message_id']) is int
            if not editing and row['state'] != 'running':
                with self.db:
                    self.db.execute("UPDATE activity_cards SET delivery='abandoned' WHERE token=?", (row['token'],))
                continue
            if editing:
                payload['message_id'] = row['message_id']
            else:
                with self.db:
                    self.db.execute("UPDATE activity_cards SET delivery='sending' WHERE token=?", (row['token'],))
            try:
                result = await self._deliver('editMessageText' if editing else 'sendMessage', payload)
                message_id = row['message_id'] if editing else result.get('message_id') if isinstance(result, dict) else None
                if type(message_id) is not int:
                    raise TelegramError('sendMessage')
            except TelegramError:
                with self.db:
                    self.db.execute("UPDATE activity_cards SET delivery=? WHERE token=?",
                                    ('failed' if editing else 'uncertain', row['token']))
                continue  # Cosmetic failure must not interrupt the run or resend its card.
            with self.db:
                self.db.execute("UPDATE activity_cards SET delivery=?,message_id=?,sent=? WHERE token=?",
                                ('delivered' if row['state'] == 'running' else 'settled', message_id, serialized, row['token']))

    def _apply(self, chat_id, event):
        self._activity_event(chat_id, event)
        kind = event.get('type', '')
        if kind == 'CUSTOM':
            name, value = event.get('name'), event.get('value') or {}
            if name == 'artifact':
                path_value = value.get('path')
                if path_value is None:
                    raise ValueError('Artifact was not prepared for delivery.')
                path = self._artifact_file(path_value)
                caption = str(value.get('caption') or path.name)
                mime = value.get('mime') or mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
                method = {'image/png': 'sendPhoto', 'image/jpeg': 'sendPhoto',
                          'video/mp4': 'sendVideo', 'audio/mpeg': 'sendAudio',
                          'audio/mp3': 'sendAudio', 'audio/wav': 'sendAudio',
                          'audio/x-wav': 'sendAudio', 'audio/ogg': 'sendVoice'}.get(mime, 'sendDocument')
                if method == 'sendPhoto' and path.stat().st_size > 10 * 1024 * 1024:
                    method = 'sendDocument'
                self._queue_delivery(chat_id, method, {
                    'chat_id': chat_id, 'caption': split_text(caption, 1024)[0],
                    '_upload': {'path': str(path), 'mime': mime}})
            elif name == 'model_choices':
                from .events import validate_model_choices
                value = validate_model_choices(value)
                rows = [[{'text': choice['label'], 'callback_data': 'model:' + choice['token']} for choice in row]
                        for row in value['rows']]
                self._queue_delivery(chat_id, 'sendMessage', {'chat_id': chat_id, 'text': value['text'],
                    'reply_markup': {'inline_keyboard': rows}}, f'{chat_id}:model-choices:{value["rows"][0][0]["token"]}')
            elif name == 'reminder':
                identifier = str(value.get('id', ''))
                if not re.fullmatch(r'[a-f0-9]{16}', identifier):
                    raise ValueError('Invalid reminder occurrence.')
                chunks = split_text(str(value.get('text', 'Нагадування.')), 3800)
                self._queue_delivery(chat_id, 'sendMessage', {'chat_id': chat_id,
                    'text': chunks[0], 'reply_markup': {'inline_keyboard': [[
                        {'text': '+10 хв', 'callback_data': f'remind:{identifier}:600'},
                        {'text': '+1 год', 'callback_data': f'remind:{identifier}:3600'},
                        {'text': 'Готово', 'callback_data': f'remind:{identifier}:done'}]]}}, f'{chat_id}:reminder:{identifier}')
                for index, chunk in enumerate(chunks[1:], 1):
                    self._queue_delivery(chat_id, 'sendMessage', {'chat_id': chat_id, 'text': chunk},
                                         f'{chat_id}:reminder-part:{identifier}:{index}')
            elif name == 'progress':
                from .activity import UNSUPPORTED_INPUT_MESSAGE
                if value.get('message') == UNSUPPORTED_INPUT_MESSAGE:
                    self._queue_delivery(chat_id, 'sendMessage', {'chat_id': chat_id, 'text': UNSUPPORTED_INPUT_MESSAGE},
                                         f'{chat_id}:unsupported-input:{event.get("runId")}')
            elif name == 'input_request':
                identifier = str(value.get('id', ''))
                if not re.fullmatch(r'[a-f0-9]{32}', identifier):
                    raise ValueError('Invalid contextual request.')
                target = self.controller.web_app_url(chat_id, request=identifier)
                payload = {'chat_id': chat_id, 'text': str(value['summary']) + '\nЗапит очікує відповідь у Mini App Oak.'}
                if target:
                    payload['reply_markup'] = {'inline_keyboard': [[{'text': 'Відкрити запит', 'web_app': {'url': target}}]]}
                self._queue_delivery(chat_id, 'sendMessage', payload, f'{chat_id}:input_request:{identifier}')
            elif name in {'approval_request', 'user_input_request'}:
                identifier = str(value['id'])
                text = str(value.get('summary' if name == 'approval_request' else 'question') or 'Потрібна відповідь.')
                if name == 'approval_request':
                    text += f'\n\n/approve {identifier}\n/deny {identifier}'
                else:
                    text += f'\n\n/answer {identifier} твоя відповідь'
                payload = {'chat_id': chat_id, 'text': split_text(text)[0]}
                if name == 'approval_request' and re.fullmatch(r'[A-Za-z0-9_.:-]{1,56}', identifier):
                    payload['reply_markup'] = {'inline_keyboard': [[
                        {'text': 'Підтвердити', 'callback_data': 'approve:' + identifier},
                        {'text': 'Відхилити', 'callback_data': 'deny:' + identifier}]]}
                self._queue_delivery(chat_id, 'sendMessage', payload, f'{chat_id}:{name}:{identifier}')
            elif name == 'web_app_link':
                url = str(value.get('url', ''))
                parsed = urllib.parse.urlsplit(url)
                if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                        or any(ord(char) < 33 for char in url)):
                    raise ValueError('Web App requires a valid public HTTPS URL.')
                self._queue_delivery(chat_id, 'sendMessage', {'chat_id': chat_id,
                    'text': str(value.get('label') or 'Відкрити Oak'),
                    'reply_markup': {'inline_keyboard': [[{'text': str(value.get('button_label') or 'Відкрити Oak'),
                                                          'web_app': {'url': url}}]]}})
            elif name == 'a2ui_card':
                url = value.get('url')
                if url is not None:
                    parsed = urllib.parse.urlsplit(url)
                    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                            or any(ord(char) < 33 for char in url)):
                        raise ValueError('Web App requires a valid public HTTPS URL.')
                surface = f'{event.get("threadId")}:{value["surfaceId"]}'
                identifier = (f'{chat_id}:a2ui:{surface}:{value["revision"]}' if 'revision' in value
                              else f'{chat_id}:a2ui:{surface}:deleted:{uuid.uuid4().hex}')
                self._queue_delivery(chat_id, CARD, {'chat_id': chat_id, 'thread': event.get('threadId'), **value}, identifier)
            return
        if kind not in {'RUN_STARTED', 'TEXT_MESSAGE_START', 'TEXT_MESSAGE_CONTENT', 'TEXT_MESSAGE_END', 'RUN_FINISHED', 'RUN_ERROR'}:
            return
        run_id = event.get('runId')
        reply = self._active.get(chat_id)
        if kind == 'RUN_STARTED' or reply is None or (run_id and run_id != reply.run_id):
            # Explicit run ids also preserve a previous run's still-pending final.
            queue = self._replies.setdefault(chat_id, deque())
            reply = next((item for item in queue if item.run_id == run_id), None)
            if reply is None:
                reply = _Reply(run_id or uuid.uuid4().hex)
                queue.append(reply)
            self._active[chat_id] = reply
        message_id = event.get('messageId', 'text')
        if kind == 'TEXT_MESSAGE_START':
            reply.parts.setdefault(message_id, '')
        elif kind == 'TEXT_MESSAGE_CONTENT':
            reply.parts[message_id] = reply.parts.get(message_id, '') + event.get('delta', '')
        elif kind == 'TEXT_MESSAGE_END' and 'text' in event:
            reply.parts[message_id] = event['text']
        elif kind == 'RUN_FINISHED':
            if event.get('status') == 'interrupted' or event.get('outcome') == 'interrupted':
                reply.error = 'Зупинено.'
            reply.terminal = True
        elif kind == 'RUN_ERROR':
            reply.error = event.get('message') or 'Не вдалося завершити задачу.'
            reply.terminal = True
        self._save_reply(chat_id, reply)

    def _chunks(self, reply):
        key = (reply.text(), self._rich_supported and not reply.classic)
        cached = getattr(reply, '_render_cache', None)
        if cached is None or cached[0] != key:
            chunks = [{'html': chunk.html, 'plain': chunk.plain, 'rich': chunk.rich}
                      for chunk in render_markdown(key[0], rich=key[1])]
            reply._render_cache = (key, chunks)
        return reply._render_cache[1]

    def _pending(self):
        pending = []
        for chat_id, queue in list(self._replies.items()):
            if self.sessions.deleted(chat_id):
                self._replies.pop(chat_id, None)
                self._active.pop(chat_id, None)
                continue
            # A notice should not wait for a long-running model response to end.
            for reply in list(queue):
                chunks = self._chunks(reply)
                if reply.terminal and reply.sent == chunks:
                    queue.remove(reply)
                    self._save_reply(chat_id, reply, 'delivered')
                    if self._active.get(chat_id) is reply:
                        self._active.pop(chat_id, None)
                elif reply.sent != chunks:
                    pending.append((chat_id, reply, chunks))
                    break
            if not queue:
                self._replies.pop(chat_id, None)
        return pending

    async def _render_one(self, chat_id, reply, chunks):
        if self.sessions.deleted(chat_id):
            return
        if len(reply.message_ids) > len(chunks):
            try:
                await self._deliver('deleteMessage', {
                    'chat_id': chat_id, 'message_id': reply.message_ids[-1]})
            except TelegramError as exc:
                if exc.reason == 'topic_missing' and self.sessions.deleted(chat_id):
                    return
                raise
            reply.message_ids.pop()
            reply.sent.pop()
            self._save_reply(chat_id, reply)
            return
        for index, chunk in enumerate(chunks):
            if index < len(reply.sent) and reply.sent[index] == chunk:
                continue
            payload = {'chat_id': chat_id}
            if chunk['rich']:
                payload['rich_message'] = {'html': chunk['html'], 'skip_entity_detection': True}
            else:
                payload.update(text=chunk['html'], parse_mode='HTML', link_preview_options={'is_disabled': True})
            editing = index < len(reply.message_ids)
            if editing:
                payload['message_id'] = reply.message_ids[index]
            else:
                # A crash/network loss after starting a send can be ambiguous;
                # persist that fact before sending and do not replay it blindly.
                reply.sending = True
                self._save_reply(chat_id, reply)
            method = 'editMessageText' if editing else ('sendRichMessage' if chunk['rich'] else 'sendMessage')
            try:
                result = await self._deliver(method, payload)
                if not editing and (not isinstance(result, dict) or type(result.get('message_id')) is not int):
                    raise TelegramError(method)
                if editing and result is not True and (not isinstance(result, dict) or type(result.get('message_id')) is not int):
                    raise TelegramError(method)
            except TelegramError as exc:
                if exc.reason == 'topic_missing' and self.sessions.deleted(chat_id):
                    self._save_reply(chat_id, reply, 'cancelled')
                    return
                if chunk['rich'] and exc.reason in ('unknown_method', 'format'):
                    if exc.reason == 'unknown_method':
                        self._rich_supported = False
                    reply.classic = True
                    reply.sending = False
                    self._save_reply(chat_id, reply)
                    # The API explicitly rejected this operation. An edit keeps
                    # the same message ID; an initial rejection created no ID.
                    return await self._render_one(chat_id, reply, self._chunks(reply))
                self._save_reply(chat_id, reply, 'uncertain' if exc.uncertain else 'failed')
                raise
            if editing:
                reply.sent[index] = chunk
            else:
                if not isinstance(result, dict) or type(result.get('message_id')) is not int:
                    raise TelegramError(method)
                reply.message_ids.append(result['message_id'])
                reply.sent.append(chunk)
                reply.sending = False
            self._save_reply(chat_id, reply)
            return  # At most one send/edit per chat per second.

    def _deliveries(self):
        return [row for row in self.db.execute("SELECT * FROM deliveries WHERE status='pending' ORDER BY rowid")
                if self._destination(row['chat_id']) is not None]

    def _card_message(self, chat_id, surface, thread):
        for row in self.db.execute(f"SELECT receipt FROM deliveries WHERE chat_id=? AND method='{CARD}' "
                                   "AND status='delivered' ORDER BY rowid DESC", (chat_id,)):
            receipt = json.loads(row['receipt'] or '{}')
            if receipt.get('surface') == surface and receipt.get('thread') == thread:
                return receipt.get('message_id')
        return None

    def _card_ambiguous(self, chat_id, surface, thread, before):
        # A send may have reached Telegram without returning its message ID.
        # Later revisions must not turn that unresolved send into a duplicate.
        for row in self.db.execute(f"SELECT payload,receipt FROM deliveries WHERE chat_id=? AND method='{CARD}' "
                                   "AND status IN ('sending','uncertain') AND rowid<?", (chat_id, before)):
            data = json.loads(row['payload'])
            receipt = json.loads(row['receipt'] or '{}')
            if (data['surfaceId'] == surface and data.get('thread') == thread
                    and type(receipt.get('message_id')) is not int):
                return True
        return False

    async def _render_card(self, row):
        with self.db:
            claimed = self.db.execute("UPDATE deliveries SET status='sending' WHERE id=? AND status='pending'", (row['id'],))
        if claimed.rowcount != 1:
            return
        data = json.loads(row['payload'])
        chat_id, surface, thread = data['chat_id'], data['surfaceId'], data.get('thread')
        receipt = {'surface': surface, 'thread': thread}

        def finish(status, value=None):
            with self.db:
                self.db.execute('UPDATE deliveries SET status=?,receipt=? WHERE id=?',
                                (status, json.dumps(value) if value else None, row['id']))

        # Updates and deletion tombstones share the same revision sequence.
        # Even a no-op deletion must fence off delayed older updates.
        order = self.db.execute('SELECT rowid FROM deliveries WHERE id=?', (row['id'],)).fetchone()[0]
        for other in self.db.execute(f"SELECT rowid,payload,status FROM deliveries WHERE chat_id=? AND method='{CARD}' "
                                     "AND id<>? AND status IN ('pending','delivered','superseded')", (chat_id, row['id'])):
            value = json.loads(other['payload'])
            if value['surfaceId'] != surface or value.get('thread') != thread:
                continue
            revision, other_revision = data.get('revision'), value.get('revision')
            if type(revision) is int and type(other_revision) is int:
                newer = other_revision >= revision
            else:
                # Old persisted, unversioned deletions can only be ordered by
                # their original outbox position; new events always have one.
                newer = other['rowid'] > order
            if newer:
                return finish('superseded')
        if self._card_ambiguous(chat_id, surface, thread, order):
            return finish('uncertain', receipt)
        previous = self._card_message(chat_id, surface, thread)
        uploads, markup = [], {}
        if data.get('deleted'):
            if previous is None:
                return finish('superseded')
            text = 'Картку закрито.'
            blocks = [{'type': 'paragraph', 'text': {'type': 'italic', 'text': text}}]
        else:
            # The bus resolves/snapshots artifacts asynchronously. Resolve each
            # identifier before passing the synchronous native-block compiler.
            attachments = {}
            for item in data['state']['components'].values():
                if item['component'] != 'Image' or item['artifact'] in attachments:
                    continue
                artifact = item['artifact']
                try:
                    path = self._artifact_file(await self.controller.bus.artifact_path(chat_id, artifact))
                except (ValueError, OSError):
                    attachments[artifact] = None
                    continue
                uploads.append({'name': 'card' + str(len(uploads)), 'path': str(path),
                                'mime': mimetypes.guess_type(path.name)[0] or 'image/png'})
                attachments[artifact] = uploads[-1]['name']

            text = split_text(data['label'])[0]
            blocks = card_blocks(data['state'], attachments.get) or [{'type': 'paragraph', 'text': data['label']}]
            if data.get('url'):
                markup = {'reply_markup': {'inline_keyboard': [[{'text': data['button_label'], 'web_app': {'url': data['url']}}]]}}
        rich = {'rich_message': {'blocks': blocks, 'skip_entity_detection': True}, **markup,
                **({'_uploads': uploads} if uploads else {})}
        attempts = [rich, {'text': text, **markup}] if self._rich_supported else [{'text': text, **markup}]
        index = 0
        while index < len(attempts):
            body = attempts[index]
            payload = {'chat_id': chat_id, **body}
            if previous is not None:
                payload['message_id'] = previous
            method = 'editMessageText' if previous is not None else 'sendRichMessage' if 'rich_message' in body else 'sendMessage'
            # Retain a known edit target across a crash/network ambiguity; a
            # replacement send deliberately clears it before dispatch.
            with self.db:
                self.db.execute('UPDATE deliveries SET receipt=? WHERE id=?',
                                (json.dumps({**receipt, **({'message_id': previous} if previous is not None else {})}), row['id']))
            try:
                result = await self._deliver(method, payload)
            except TelegramError as exc:
                if 'rich_message' in body and exc.reason in ('unknown_method', 'format') and index + 1 < len(attempts):
                    if exc.reason == 'unknown_method':
                        self._rich_supported = False
                    index += 1
                    continue
                if previous is not None and exc.reason == 'message_missing' and not data.get('deleted'):
                    previous = None  # The owner removed the old card; publish the current one afresh.
                    continue
                deleted = self.sessions.deleted(chat_id)
                return finish('cancelled' if deleted else 'uncertain' if exc.uncertain else 'failed',
                              {**receipt, **({'message_id': previous} if previous is not None else {})})
            except ValueError:
                return finish('failed')
            message_id = result.get('message_id') if isinstance(result, dict) else previous if result is True else None
            if type(message_id) is not int:
                return finish('uncertain', {**receipt, **({'message_id': previous} if previous is not None else {})})
            return finish('delivered', {**receipt, 'message_id': message_id})

    def _approval_payload(self, row, payload):
        prefix = str(row['chat_id']) + ':approval_request:'
        if not row['id'].startswith(prefix):
            return payload, None
        interactions = getattr(self.controller, 'interactions', None)
        if not callable(getattr(type(interactions), 'snapshot', None)):
            return payload, None
        try:
            state = interactions.snapshot(row['chat_id'], row['id'][len(prefix):])['state']
        except ValueError:
            state = 'expired'
        labels = {'accepted': '✅ Дозволено', 'declined': '❌ Відхилено',
                  'answered': 'Відповідь прийнято', 'expired': '⌛ Прострочено', 'cancelled': 'Скасовано'}
        if state not in labels:
            return payload, state
        text = payload.get('text', '').split('\n\n/approve ', 1)[0]
        return {**payload, 'text': split_text(text + '\n\n' + labels[state])[0],
                'reply_markup': {'inline_keyboard': []}}, state

    async def _settle_approvals(self):
        # Receipts are authority for message identity. Never resend an uncertain card.
        rows = self.db.execute("SELECT * FROM deliveries WHERE method='sendMessage' AND status='delivered' "
                               "AND id LIKE '%:approval_request:%' AND "
                               "coalesce(json_extract(receipt,'$.approval_state'),'pending')='pending'").fetchall()
        for row in rows:
            if self.sessions.deleted(row['chat_id']):
                continue
            payload, state = self._approval_payload(row, json.loads(row['payload']))
            receipt = json.loads(row['receipt'] or '{}')
            if state in (None, 'pending') or receipt.get('approval_state') == state:
                continue
            if type(receipt.get('message_id')) is not int:
                continue
            try:
                await self._deliver('editMessageText', {**payload, 'message_id': receipt['message_id']})
                receipt['approval_edit'] = 'delivered'
            except TelegramError:
                receipt['approval_edit'] = 'failed'
            # A cosmetic failure never undoes or repeats the native decision.
            receipt['approval_state'] = state
            with self.db:
                self.db.execute('UPDATE deliveries SET receipt=? WHERE id=?', (json.dumps(receipt), row['id']))

    def _task_controls(self, chat_id):
        scheduler = getattr(self.controller, 'scheduler', None)
        if not callable(getattr(type(scheduler), 'list', None)):
            return
        jobs = scheduler.list(chat_id)
        if not jobs:
            return
        rows = [[{'text': 'Скасувати: ' + scheduler.label(job['prompt'])[:40],
                  'callback_data': 'cancel-job:' + job['id']}] for job in jobs[:20]]
        self._queue_delivery(chat_id, 'sendMessage', {'chat_id': chat_id,
            'text': 'Керування розкладом' + (' · перші 20 задач; решта доступна через /cancel ID.' if len(jobs) > 20 else ''),
            'reply_markup': {'inline_keyboard': rows}}, 'task-controls:' + uuid.uuid4().hex)
        self.events.put_nowait(None)

    def _cancel_job(self, chat_id, identifier):
        row = self.controller.db.execute('SELECT status FROM jobs WHERE id=? AND chat_id=?', (identifier, chat_id)).fetchone()
        if row is None:
            return 'Задача недоступна.'
        if row[0] == 'cancelled':
            return 'Задачу вже скасовано.'
        if not self.controller.scheduler.cancel(chat_id, identifier):
            return 'Ця задача вже завершилася.'
        return ('Наступні запуски скасовано. Поточне виконання зупини через /stop.'
                if row[0] == 'running' else 'Задачу скасовано.')

    def _task_payload(self, row, payload):
        if not row['id'].startswith('task-controls:'):
            return payload
        rows = []
        for buttons in payload.get('reply_markup', {}).get('inline_keyboard', []):
            if not buttons:
                continue
            identifier = buttons[0].get('callback_data', '').removeprefix('cancel-job:')
            job = self.controller.db.execute('SELECT 1 FROM jobs WHERE id=? AND chat_id=? '
                "AND status IN ('pending','paused','running','uncertain')", (identifier, row['chat_id'])).fetchone()
            if job:
                rows.append(buttons)
        return {**payload, 'text': payload['text'] if rows else 'Немає задач для скасування.',
                'reply_markup': {'inline_keyboard': rows}}

    async def _settle_task_controls(self, chat_id, message_id=None):
        rows = self.db.execute("SELECT * FROM deliveries WHERE chat_id=? AND id LIKE 'task-controls:%' "
            "AND status='delivered' ORDER BY rowid DESC LIMIT 20", (chat_id,)).fetchall()
        for row in rows:
            receipt = json.loads(row['receipt'] or '{}')
            target = receipt.get('message_id')
            if type(target) is not int or (message_id is not None and message_id != target):
                continue
            payload = self._task_payload(row, json.loads(row['payload']))
            try:
                await self._deliver('editMessageText', {**payload, 'message_id': target})
            except TelegramError:
                pass
            break

    def _reminder_payload(self, row, payload):
        prefix = str(row['chat_id']) + ':reminder:'
        if not row['id'].startswith(prefix):
            return payload, None
        actions = getattr(getattr(self.controller, 'scheduler', None), 'reminders', None)
        if not callable(getattr(type(actions), 'snapshot', None)):
            return payload, None
        try:
            state = actions.snapshot(row['chat_id'], row['id'][len(prefix):])['state']
        except ValueError:
            state = 'expired'
        labels = {'snoozed': 'Нагадаю пізніше.', 'done': 'Нагадування завершено.', 'expired': 'Кнопки нагадування прострочені.'}
        if state not in labels:
            return payload, state
        return {**payload, 'text': split_text(payload['text'], 3800)[0] + '\n\n' + labels[state],
                'reply_markup': {'inline_keyboard': []}}, state

    async def _settle_reminders(self):
        rows = self.db.execute("SELECT * FROM deliveries WHERE status='delivered' AND id LIKE '%:reminder:%' "
                               "AND coalesce(json_extract(receipt,'$.reminder_state'),'pending')='pending'").fetchall()
        for row in rows:
            if self.sessions.deleted(row['chat_id']):
                continue
            payload, state = self._reminder_payload(row, json.loads(row['payload']))
            receipt = json.loads(row['receipt'] or '{}')
            if state in (None, 'pending') or type(receipt.get('message_id')) is not int:
                continue
            try:
                await self._deliver('editMessageText', {**payload, 'message_id': receipt['message_id']})
            except TelegramError:
                pass
            receipt['reminder_state'] = state
            with self.db:
                self.db.execute('UPDATE deliveries SET receipt=? WHERE id=?', (json.dumps(receipt), row['id']))

    async def _render_delivery(self, row):
        if row['method'] == CARD:
            return await self._render_card(row)
        payload, approval_state = self._approval_payload(row, json.loads(row['payload']))
        payload, reminder_state = self._reminder_payload(row, payload)
        payload = self._task_payload(row, payload)
        files = getattr(getattr(self.controller, 'tools', None), 'files', None)
        if files and '_upload' in payload and not files.stored(payload['_upload']['path']):
            payload['_upload']['path'] = str(await files.snapshot(payload['_upload']['path']))
            with self.db:
                self.db.execute("UPDATE deliveries SET payload=? WHERE id=? AND status='pending'",
                                (json.dumps(payload), row['id']))
        with self.db:
            claimed = self.db.execute("UPDATE deliveries SET status='sending' WHERE id=? AND status='pending'", (row['id'],))
        if claimed.rowcount != 1:
            return
        try:
            result = await self._deliver(row['method'], payload)
            if not isinstance(result, dict) or type(result.get('message_id')) is not int:
                raise TelegramError(row['method'])
        except Exception as exc:
            deleted = self.sessions.deleted(row['chat_id'])
            status = 'cancelled' if deleted else 'uncertain' if isinstance(exc, TelegramError) and exc.uncertain else 'failed'
            with self.db:
                self.db.execute('UPDATE deliveries SET status=? WHERE id=?', (status, row['id']))
            if deleted and isinstance(exc, TelegramError) and exc.reason == 'topic_missing':
                return
            raise
        receipt = {'message_id': result['message_id']}
        if approval_state is not None:
            receipt['approval_state'] = approval_state
        if reminder_state is not None:
            receipt['reminder_state'] = reminder_state
        for kind in ('document', 'audio', 'voice', 'video', 'photo'):
            media = result.get(kind)
            if isinstance(media, list) and media:
                media = media[-1]
            if isinstance(media, dict):
                receipt['file_id'] = media.get('file_id')
                break
        with self.db:
            self.db.execute("UPDATE deliveries SET status='delivered',receipt=? WHERE id=?",
                            (json.dumps(receipt), row['id']))
        if '_upload' in payload:
            self._record_improvement_event(row['chat_id'], 'artifact_delivery',
                {'delivery_status': 'confirmed', 'transport': 'telegram', 'message_id': result['message_id']},
                request_id='telegram-delivery:' + row['id'])

    async def _render(self):
        while True:
            while not self.events.empty():
                self.events.get_nowait()
            for row in self._deliveries():
                if time.monotonic() - self._last_send.get(row['chat_id'], 0) >= 1:
                    await self._render_delivery(row)
            await self._settle_approvals()
            await self._settle_reminders()
            pending = self._pending()
            for chat_id, reply, chunks in pending:
                if time.monotonic() - self._last_send.get(chat_id, 0) >= 1:
                    await self._render_one(chat_id, reply, chunks)
            await self._render_activity()
            await self._render_reactions()
            pending = self._pending()
            pending_chats = [chat_id for chat_id, _, _ in pending] + [row['chat_id'] for row in self._deliveries()]
            delay = min((max(0, 1 - (time.monotonic() - self._last_send.get(chat_id, 0)))
                         for chat_id in pending_chats), default=1)
            try:
                await asyncio.wait_for(self.events.get(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def run(self):
        self._running = True
        for chat_id, keys in list(self._activity.items()):
            if keys:
                self._activity_add(chat_id, next(iter(keys)))
        try:
            async with asyncio.TaskGroup() as tasks:
                self._worker_group = tasks
                tasks.create_task(self._poll())
                tasks.create_task(self._render())
                if self.discard_voice_audio:
                    tasks.create_task(self._voice_cleanup_loop())
                scopes = set(self._intake_wake) | {row[0] for row in self.db.execute(
                    "SELECT DISTINCT chat_id FROM intake WHERE status='pending'")}
                for chat_id in scopes:
                    if self._destination(chat_id) is not None:
                        self._ensure_intake_worker(chat_id)
        finally:
            self._worker_group = None
            self._intake_workers.clear()
            self._running = False
            workers = list(self._typing_tasks.values())
            self._typing_tasks.clear()
            self._activity.clear()
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
