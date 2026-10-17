"""Turn-scoped approval and input requests answered through the owner chat."""

import asyncio
from datetime import datetime
import json
import time
import uuid
from zoneinfo import ZoneInfo


class Interactions:
    def __init__(self, db, controller):
        self.db, self.controller = db, controller
        self.pending = {}
        config = getattr(controller, 'config', {})
        self.timeout = config.get('approval_timeout_seconds', 300) if isinstance(config, dict) else 300
        if type(self.timeout) is not int or not 60 <= self.timeout <= 3600:
            raise ValueError('approval_timeout_seconds must be an integer from 60 to 3600.')
        self.clock, self.sleep, self.wait_for = time.time, asyncio.sleep, asyncio.wait_for
        db.execute('''CREATE TABLE IF NOT EXISTS interactions (
            id TEXT PRIMARY KEY, chat_id INTEGER, thread_id TEXT, turn_id TEXT,
            kind TEXT, request TEXT, expires REAL, status TEXT)''')
        db.execute('CREATE TABLE IF NOT EXISTS interaction_results (id TEXT PRIMARY KEY, outcome TEXT NOT NULL)')
        with db:
            db.execute("UPDATE interactions SET status='expired' WHERE status='pending'")

    def snapshot(self, chat_id, request_id):
        """Bounded owner-channel projection; never expose stored request metadata."""
        row = self.db.execute('SELECT id,kind,expires,status FROM interactions WHERE id=? AND chat_id=?',
                              (request_id, chat_id)).fetchone()
        if row is None or row[1] != 'approval':
            raise ValueError('Запит недоступний.')
        outcome = self.db.execute('SELECT outcome FROM interaction_results WHERE id=?', (request_id,)).fetchone()
        state = outcome[0] if row[3] == 'answered' and outcome else row[3]
        if state == 'pending' and row[2] <= self.clock():
            state = 'expired'
        return {'id': row[0], 'state': state, 'expires': row[2], 'summary': 'Дозволити дію?'}

    async def _notice(self, chat_id, text, run_id):
        notice = getattr(self.controller, 'notice', None)
        if callable(notice):
            try:
                await notice(chat_id, text, run_id=run_id)
            except Exception:
                pass  # Notification failure cannot grant or repeat permission.

    async def _remind(self, chat_id, request_id, expires, future):
        await self.sleep(max(0, expires - 60 - self.clock()))
        if not future.done() and self.snapshot(chat_id, request_id)['state'] == 'pending':
            await self._notice(chat_id, 'Запит підтвердження завершиться через одну хвилину.',
                               'approval-reminder-' + request_id)

    async def ask(self, metadata, kind, message):
        chat_id = self.controller.chat_for_thread(metadata.get('threadId'))
        sessions = getattr(self.controller, 'sessions', None)
        if chat_id is None or (sessions is not None and sessions.deleted(chat_id)):
            return False if kind == 'approval' else ''
        request_id = uuid.uuid4().hex[:10]
        future = asyncio.get_running_loop().create_future()
        duration = self.timeout if kind == 'approval' else 300
        expires = self.clock() + duration
        if kind == 'approval':
            timezone = getattr(getattr(self.controller, 'scheduler', None), 'timezone', None)
            if not isinstance(timezone, ZoneInfo):
                timezone = ZoneInfo('Europe/Kyiv')
            deadline = datetime.fromtimestamp(expires, timezone).strftime('%d.%m.%Y %H:%M:%S %z')
            message += '\nВідповісти до ' + deadline + ' (' + timezone.key + ').'
        with self.db:
            self.db.execute('INSERT INTO interactions VALUES (?,?,?,?,?,?,?,?)',
                (request_id, chat_id, metadata.get('threadId'), metadata.get('turnId'),
                 kind, json.dumps(metadata, ensure_ascii=False), expires, 'pending'))
        self.pending[request_id] = future
        reminder = asyncio.create_task(self._remind(chat_id, request_id, expires, future)) if kind == 'approval' else None
        try:
            await self.controller.emit(chat_id, {'type': 'CUSTOM', 'name':
                'approval_request' if kind == 'approval' else 'user_input_request',
                'value': {'id': request_id, 'summary': message, 'question': message}})
            return await self.wait_for(future, duration)
        except asyncio.TimeoutError:
            with self.db:
                self.db.execute("UPDATE interactions SET status='expired' WHERE id=?", (request_id,))
            if kind == 'approval':
                await self._notice(chat_id, 'Час підтвердження минув. Дію не дозволено; надішли новий запит, якщо вона ще потрібна.',
                                   'approval-expired-' + request_id)
            return False if kind == 'approval' else ''
        finally:
            if reminder is not None:
                reminder.cancel()
                await asyncio.gather(reminder, return_exceptions=True)
            self.pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            with self.db:
                self.db.execute("UPDATE interactions SET status='cancelled' WHERE id=? AND status='pending'",
                                (request_id,))

    def resolve(self, chat_id, request_id, value, kind):
        row = self.db.execute('SELECT chat_id,kind,expires,status FROM interactions WHERE id=?', (request_id,)).fetchone()
        future = self.pending.get(request_id)
        if (not row or row[0] != chat_id or row[1] != kind or row[2] <= self.clock()
                or row[3] != 'pending' or future is None or future.done()):
            raise ValueError('Цей запит недоступний, уже оброблений або прострочений.')
        if (kind == 'approval' and type(value) is not bool) or (kind == 'input' and not isinstance(value, str)):
            raise ValueError('Неправильний формат відповіді.')
        with self.db:
            self.db.execute("UPDATE interactions SET status='answered' WHERE id=?", (request_id,))
            if kind == 'approval':
                self.db.execute('INSERT OR REPLACE INTO interaction_results VALUES (?,?)',
                                (request_id, 'accepted' if value else 'declined'))
        if not future.done():
            future.set_result(value)

    def cancel_turn(self, thread_id, turn_id):
        rows = self.db.execute("SELECT id,kind FROM interactions WHERE thread_id=? AND turn_id=? AND status='pending'",
                               (thread_id, turn_id)).fetchall()
        for request_id, kind in rows:
            future = self.pending.get(request_id)
            if future and not future.done():
                future.set_result(False if kind == 'approval' else '')
        with self.db:
            self.db.execute("UPDATE interactions SET status='cancelled' WHERE thread_id=? AND turn_id=? AND status='pending'",
                            (thread_id, turn_id))

    async def approval(self, metadata):
        method = metadata['method']
        summary = str(metadata.get('command') or metadata.get('reason') or metadata.get('message') or method)
        if method == 'item/permissions/requestApproval':
            summary += '\n' + json.dumps(metadata.get('permissions', {}), ensure_ascii=False)
        accepted = await self.ask(metadata, 'approval', 'Дозволити дію?\n' + str(summary)[:1800])
        if method == 'item/permissions/requestApproval':
            return {'permissions': metadata.get('permissions', {}) if accepted else {}, 'scope': 'turn'}
        if method == 'mcpServer/elicitation/request':
            return {'action': 'accept' if accepted else 'decline', 'content': None}
        return {'decision': 'accept' if accepted else 'decline'}

    async def user_input(self, metadata):
        requests = getattr(self.controller, 'requests', None)
        if requests is None:
            return {'answers': {}} if metadata.get('method') == 'item/tool/requestUserInput' else {'action': 'decline', 'content': None}
        return await requests.native(metadata)
