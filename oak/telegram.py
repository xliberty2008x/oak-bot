"""Private-chat Telegram transport; Agent execution stays in the controller."""

import asyncio
from collections import deque
from dataclasses import asdict, dataclass, field
import json
import math
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .media import ToolUnavailable
from .rendering import render_markdown

MAX_DOWNLOAD = 20 * 1024 * 1024
MAX_UPLOAD = 50 * 1024 * 1024
CONTROL_COMMANDS = {'/start', '/help', '/status', '/web', '/remember', '/memory', '/remind', '/tasks',
                    '/cancel', '/image', '/preview', '/speak', '/research', '/browse'}


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
    def __init__(self, controller, token: str, allowed_user_ids: set[int], state_dir: Path):
        if not token or not allowed_user_ids or any(type(n) is not int or n <= 0 for n in allowed_user_ids):
            raise ValueError('Telegram requires a token and explicit positive numeric user IDs.')
        self.controller = controller
        self.controller.telegram = self
        self.sessions = controller.sessions
        self._token = token
        self.allowed_user_ids = set(allowed_user_ids)
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.offset_path = self.state_dir / 'telegram-offset.json'
        self.offset = 0
        if self.offset_path.exists():
            try:
                self.offset = json.loads(self.offset_path.read_text())['offset']
                if type(self.offset) is not int or self.offset < 0:
                    raise ValueError
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
        ''')
        os.chmod(self.state_dir / 'telegram-outbox.sqlite3', 0o600)
        with self.db:
            self.db.execute("UPDATE deliveries SET status='uncertain' WHERE status='sending'")
            self.db.execute("UPDATE intake SET status='uncertain' WHERE status='processing'")
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
                reply.error = 'Попередню відповідь перервано перезапуском.'
            self._replies.setdefault(row['chat_id'], deque()).append(reply)

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
        upload = payload.pop('_upload')
        path = self._artifact_file(upload['path'])
        boundary = 'oak-' + uuid.uuid4().hex
        body = bytearray()
        for key, value in payload.items():
            text = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{text}\r\n'.encode())
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
        field_name = {'sendPhoto': 'photo', 'sendVideo': 'video', 'sendAudio': 'audio',
                      'sendVoice': 'voice'}.get(method, 'document')
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
        body.extend(content)
        body.extend(f'\r\n--{boundary}--\r\n'.encode())
        return bytes(body), 'multipart/form-data; boundary=' + boundary

    def _request_sync(self, method, payload, timeout=40):
        data, content_type = (self._multipart(payload, method) if '_upload' in payload
                              else (json.dumps(payload).encode(), 'application/json'))
        request = urllib.request.Request(
            f'https://api.telegram.org/bot{self._token}/{method}',
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
            if code == 400 and any(phrase in description for phrase in (
                    'message thread not found', 'message thread is not found',
                    'message_thread_id_invalid', 'message_thread_invalid', 'message_thread_not_found',
                    'topic_id_invalid', 'topic_deleted', 'topic_not_found', 'topic not found', 'topic was deleted')):
                reason = 'topic_missing'
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
                json.dump({'offset': offset}, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, self.offset_path)
            self.offset = offset
        finally:
            if os.path.exists(name):
                os.unlink(name)

    async def emit(self, chat_id: int, event: dict):
        if self._destination(chat_id) is not None:
            run_id = event.get('runId')
            if event.get('type') == 'RUN_STARTED' and run_id:
                self._activity_add(chat_id, 'run:' + run_id)
            # Persist before returning so a completed model reply survives a
            # crash even when the rendering worker has not reached it yet.
            self._apply(chat_id, dict(event))
            self.events.put_nowait(None)
            if event.get('type') in ('RUN_FINISHED', 'RUN_ERROR'):
                await self._activity_remove(chat_id, 'run:' + run_id if run_id else None)

    def _download_sync(self, remote_path, destination):
        parsed = PurePosixPath(remote_path)
        if (parsed.is_absolute() or any(part in {'.', '..'} for part in remote_path.split('/'))
                or not re.fullmatch(r'[A-Za-z0-9_./-]+', remote_path)):
            raise ValueError('Telegram returned an invalid attachment path.')
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if destination.parent.is_symlink() or destination.parent.resolve() != destination.parent:
            raise ValueError('Attachment inbox must not be a symlink.')
        fd, temporary = tempfile.mkstemp(prefix='.download-', dir=destination.parent)
        try:
            request = urllib.request.Request(f'https://api.telegram.org/file/bot{self._token}/{remote_path}')
            with os.fdopen(fd, 'wb') as output:
                with urllib.request.build_opener(_NoRedirect()).open(request, timeout=40) as source:
                    total = 0
                    while chunk := source.read(65536):
                        total += len(chunk)
                        if total > MAX_DOWNLOAD:
                            raise ValueError('Attachment exceeds the 20 MiB limit.')
                        output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
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
        if item.get('file_size', 0) > MAX_DOWNLOAD:
            raise ValueError('Файл завеликий: максимум 20 MiB.')
        info = await self._api('getFile', {'file_id': item['file_id']})
        if info.get('file_size', 0) > MAX_DOWNLOAD:
            raise ValueError('Файл завеликий: максимум 20 MiB.')
        destination = self.state_dir.resolve() / 'inbox' / f'{update_id}-{name}'
        await asyncio.to_thread(self._download_sync, info['file_path'], destination)
        return [{'path': str(destination), 'mime': mime, 'name': name}]

    @staticmethod
    def _owner(message, sender, allowed):
        chat = message.get('chat') or {}
        user_id = sender.get('id')
        return (chat.get('type') == 'private' and type(user_id) is int
                and user_id in allowed and chat.get('id') == user_id and not sender.get('is_bot'))

    async def _decision(self, chat_id, text):
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
            await self._notice(chat_id, 'Запит недоступний, уже оброблений або завершився.')
        else:
            await self._notice(chat_id, result if isinstance(result, str) and result else 'Відповідь прийнято.')

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

    async def handle_update(self, update, *, queued=False):
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
                if match and not self.sessions.deleted(scope):
                    await self._decision(scope, f'/{match[1]} {match[2]}')
                try:
                    await self._api('answerCallbackQuery', {'callback_query_id': callback['id']})
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
            text = message.get('text') or message.get('caption') or ''
            command = self._command(message)
            if command in {'/approve', '/deny', '/answer'}:
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
            else:
                # submit checkpoints input before it returns. On uncertain
                # acceptance propagate the failure without advancing the cursor.
                try:
                    try:
                        attachments = await self._attachments(message, update_id)
                    except ValueError:
                        await self._notice(chat_id, 'Не вдалося прийняти файл. Надішли зображення, аудіо або документ до 20 MiB.')
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
        if not queued:
            self._checkpoint(update_id + 1)

    async def _ingest(self, update):
        update_id = update.get('update_id')
        if type(update_id) is not int or update_id < self.offset:
            return
        message = update.get('message') or update.get('edited_message') or {}
        if (update.get('callback_query') or self._command(message) in
                {'/stop', '/new', '/approve', '/deny', '/answer', '/status', '/help', '/start', '/web'}
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
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO intake VALUES (?,?,?,'pending')",
                            (update_id, chat_id, json.dumps(update)))
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
            with self.db:
                self.db.execute("UPDATE intake SET status='processing' WHERE update_id=?", (row['update_id'],))
            task = asyncio.create_task(self.handle_update(json.loads(row['payload']), queued=True))
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
                await self._activity_remove(chat_id, 'input:' + str(row['update_id']))
            with self.db:
                self.db.execute("UPDATE intake SET status=? WHERE update_id=? AND status='processing'",
                                (status, row['update_id']))

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

    def _apply(self, chat_id, event):
        kind = event.get('type', '')
        if kind == 'CUSTOM':
            name, value = event.get('name'), event.get('value') or {}
            if name == 'artifact':
                path_value = value.get('path')
                if path_value is None:
                    path_value = self.controller.bus.artifact_path(chat_id, value['id'])
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
                    'reply_markup': {'inline_keyboard': [[{'text': 'Відкрити Oak', 'web_app': {'url': url}}]]}})
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

    async def _render_delivery(self, row):
        with self.db:
            claimed = self.db.execute("UPDATE deliveries SET status='sending' WHERE id=? AND status='pending'", (row['id'],))
        if claimed.rowcount != 1:
            return
        try:
            result = await self._deliver(row['method'], json.loads(row['payload']))
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

    async def _render(self):
        while True:
            while not self.events.empty():
                self.events.get_nowait()
            for row in self._deliveries():
                if time.monotonic() - self._last_send.get(row['chat_id'], 0) >= 1:
                    await self._render_delivery(row)
            pending = self._pending()
            for chat_id, reply, chunks in pending:
                if time.monotonic() - self._last_send.get(chat_id, 0) >= 1:
                    await self._render_one(chat_id, reply, chunks)
            pending = self._pending()
            pending_chats = [chat_id for chat_id, _, _ in pending] + [row['chat_id'] for row in self._deliveries()]
            delay = min((max(0, 1 - (time.monotonic() - self._last_send.get(chat_id, 0)))
                         for chat_id in pending_chats), default=None)
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
