"""Bounded ordinary-input requests. Authentication never uses these fields.

SQLite records consumption, not an exactly-once remote RPC acknowledgement.
After a restart an undispatched request expires; a dispatched result is uncertain.
"""

import asyncio
import hashlib
import json
import re
import time
import uuid

from .a2ui import CATALOG, VERSION, validate_messages


TTL = 300
SENSITIVE = re.compile(r'password|passcode|credential|secret|\botp\b|\b2fa\b|token|cookie|'
                       r'парол|секрет|токен|кукі|код.{0,20}(?:вход|вхід|підтвер|автентиф)|'
                       r'(?:verification|authentication|one.time)\s+code', re.I)
TEMPLATES = {
    'task_details': [('details', 'Деталі задачі', None)],
    'plan_details': [('topic', 'Тема плану', None),
                     ('pace', 'Темп', ['Спокійно', 'Зосереджено'])],
}


class RequestUnavailable(ValueError):
    pass


def bounded(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if len(encoded.encode()) > 16000:
        raise ValueError('Request is too large.')
    return encoded


def text(value, limit=500):
    if not isinstance(value, str) or not 0 < len(value.strip()) <= limit or SENSITIVE.search(value):
        raise ValueError('Only ordinary non-secret questions are supported.')
    return value


def form(fields):
    """Build only the existing Oak catalogue; never accept model components."""
    components = [{'id': 'root', 'component': 'Column', 'children': [f'f{i}' for i in range(len(fields))]}]
    for i, (key, label, options) in enumerate(fields):
        item = {'id': f'f{i}', 'component': 'ChoicePicker' if options else 'TextField',
                'label': label, 'value': {'path': '/answers/' + key}, 'required': True}
        if options:
            item['options'] = [{'label': value, 'value': value} for value in options]
        components.append(item)
    messages = [
        {'version': VERSION, 'createSurface': {'surfaceId': 'request', 'catalogId': CATALOG}},
        {'version': VERSION, 'updateComponents': {'surfaceId': 'request', 'components': components}},
    ]
    validate_messages(messages)
    return {'fields': [{'id': key, 'label': label, 'options': options} for key, label, options in fields],
            'components': components, 'catalogId': CATALOG, 'version': VERSION}


class InputRequests:
    def __init__(self, controller):
        self.controller, self.db = controller, controller.db
        epoch = getattr(controller.client, 'runtime_epoch', None)
        self.epoch = epoch if isinstance(epoch, str) else uuid.uuid4().hex
        self.waiters = {}
        self.broker = None
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS input_requests (
                id TEXT PRIMARY KEY, owner INTEGER NOT NULL, chat_id INTEGER NOT NULL,
                thread_id TEXT NOT NULL, turn_id TEXT NOT NULL, runtime_epoch TEXT NOT NULL,
                native_id TEXT NOT NULL, adapter TEXT NOT NULL, kind TEXT NOT NULL,
                form TEXT NOT NULL, expires REAL NOT NULL, outcome TEXT NOT NULL,
                delivery TEXT NOT NULL, response TEXT,
                UNIQUE(runtime_epoch,native_id));
            CREATE TABLE IF NOT EXISTS input_request_receipts (
                request_id TEXT NOT NULL, action_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                result TEXT NOT NULL, PRIMARY KEY(request_id,action_id));
        ''')
        with self.db:
            self.db.execute("UPDATE input_requests SET outcome='expired' WHERE outcome='pending'")
            self.db.execute("UPDATE input_requests SET delivery='uncertain' WHERE delivery='ready' OR "
                            "(delivery='sent' AND NOT EXISTS (SELECT 1 FROM turns WHERE "
                            "turns.turn_id=input_requests.turn_id AND turns.thread_id=input_requests.thread_id "
                            "AND turns.status IN ('completed','failed','interrupted')))")

    def _binding(self, metadata):
        c = self.controller
        chat = c.chat_for_thread(metadata.get('threadId'))
        native_id, turn = metadata.get('requestId'), metadata.get('turnId')
        if (chat is None or c.sessions.deleted(chat) or c.active.get(chat) != turn or not turn
                or type(native_id) not in (str, int)
                or metadata.get('runtimeEpoch', self.epoch) != self.epoch):
            raise RequestUnavailable('The original native request is unavailable.')
        owner = c.sessions.owner(chat)
        if owner is None:
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='web_conversations'").fetchone():
                row = self.db.execute('SELECT owner FROM web_conversations WHERE chat_id=?', (chat,)).fetchone()
                owner = row[0] if row else None
        if owner is None:
            raise RequestUnavailable('Unknown request owner.')
        return owner, chat, metadata['threadId'], turn, bounded(native_id)

    def _live(self, row):
        c = self.controller
        return (row['runtime_epoch'] == self.epoch and not c.sessions.deleted(row['chat_id'])
                and c.threads.get(row['chat_id']) == row['thread_id']
                and c.active.get(row['chat_id']) == row['turn_id']
                and row['id'] in self.waiters and not self.waiters[row['id']].done())

    def _response(self, row, outcome, values=None):
        if row['adapter'] == 'questions':
            response = {'answers': {field['id']: {'answers': [values[field['id']]] if values else []}
                                    for field in json.loads(row['form'])['fields']}}
        elif row['adapter'] == 'mcp':
            response = {'action': 'accept' if outcome == 'submitted' else 'decline',
                        'content': values if outcome == 'submitted' else None}
        else:
            response = {'outcome': outcome, 'values': values or {}}
            if row['kind'] == 'sign_in':
                provider = json.loads(row['form']).get('provider', 'instagram')
                simulated = provider == 'oak_synthetic'
                response = {'outcome': outcome, 'provider': provider,
                            'authenticated': simulated and outcome == 'authenticated',
                            'reason': 'synthetic_login_verified' if simulated and outcome == 'authenticated'
                                      else ('synthetic_login_' + outcome if simulated else 'trusted_channel_unavailable')}
                if simulated:
                    response['simulated'] = True
        return response

    def _terminal(self, row, outcome, values=None):
        response = self._response(row, outcome, values)
        with self.db:
            changed = self.db.execute("UPDATE input_requests SET outcome=?,response=?,delivery='ready' "
                                      "WHERE id=? AND outcome='pending'", (outcome, bounded(response), row['id'])).rowcount
        future = self.waiters.get(row['id'])
        if changed and future is not None and not future.done():
            future.set_result(response)
        if changed and self.broker:
            self.broker.revoke(row['id'])

    async def _ask(self, metadata, adapter, presentation, kind='ordinary'):
        chat = self.controller.chat_for_thread(metadata.get('threadId'))
        if chat is None:
            raise RequestUnavailable('The original native request is unavailable.')
        identifier = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        # turn/start registers active under this lock. A buffered native request
        # can arrive first; wait for registration, then bind without yielding.
        async with self.controller._lock(chat):
            owner, chat, thread, turn, native = self._binding(metadata)
            with self.db:
                # A repeated native request must never create another continuation.
                if self.db.execute('SELECT 1 FROM input_requests WHERE runtime_epoch=? AND native_id=?',
                                   (self.epoch, native)).fetchone():
                    raise RequestUnavailable('Native request has already been handled.')
                self.db.execute('INSERT INTO input_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)',
                                (identifier, owner, chat, thread, turn, self.epoch, native, adapter, kind,
                                 bounded(presentation), time.time() + TTL, 'pending', 'waiting'))
            self.waiters[identifier] = future
        try:
            await self.controller.emit(chat, {'type': 'CUSTOM', 'name': 'input_request',
                                              'value': {'id': identifier, 'kind': kind,
                                                        'summary': 'Oak потребує уточнення.' if kind == 'ordinary' else
                                                                   ('Тестовий запит входу Oak.' if presentation.get('simulated') else 'Потрібен вхід в Instagram.')}})
            try:
                return await asyncio.wait_for(asyncio.shield(future), TTL)
            except asyncio.TimeoutError:
                self._terminal(self.row(owner, chat, identifier), 'expired')
                return future.result()
        finally:
            self.waiters.pop(identifier, None)
            if not future.done():
                future.cancel()
            with self.db:
                self.db.execute("UPDATE input_requests SET outcome='cancelled',delivery='abandoned' "
                                "WHERE id=? AND outcome='pending'", (identifier,))
            if self.broker:
                self.broker.revoke(identifier)

    async def template(self, metadata, name):
        if name == 'synthetic_sign_in' and self.broker:
            # Explicit fixture only; not advertised as a production model tool.
            return await self._ask(metadata, 'tool', {'provider': 'oak_synthetic',
                                   'destination': 'synthetic loopback provider', 'capability': True,
                                   'simulated': True, 'handoff': 'trusted_user_required'}, 'sign_in')
        if name == 'instagram_sign_in':
            return await self._ask(metadata, 'tool', {'provider': 'instagram', 'destination': 'https://www.instagram.com',
                                   'capability': False, 'reason': 'trusted_channel_unavailable',
                                   'handoff': 'trusted_user_required'}, 'sign_in')
        if name not in TEMPLATES:
            raise ValueError('Unknown ordinary-input template.')
        return await self._ask(metadata, 'tool', form(TEMPLATES[name]))

    async def native(self, metadata):
        method = metadata.get('method')
        try:
            if method == 'item/tool/requestUserInput':
                questions = metadata.get('questions')
                if not isinstance(questions, list) or not 1 <= len(questions) <= 3:
                    raise ValueError('Unsupported questions.')
                fields = []
                for question in questions:
                    if not isinstance(question, dict) or question.get('isSecret'):
                        raise ValueError('Secret input is unsupported.')
                    key = question.get('id')
                    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}', key):
                        raise ValueError('Invalid question ID.')
                    choices = question.get('options')
                    if not isinstance(choices, list) or not 1 <= len(choices) <= 32:
                        # Free text only uses server-owned templates, never model-authored labels.
                        raise ValueError('Use a fixed ordinary-input template for free text.')
                    options = [text(choice.get('label'), 100) for choice in choices if isinstance(choice, dict)]
                    if len(options) != len(choices) or len(set(options)) != len(options):
                        raise ValueError('Invalid options.')
                    fields.append((key, text(question.get('question')), options))
                if len({f[0] for f in fields}) != len(fields):
                    raise ValueError('Duplicate questions.')
                return await self._ask(metadata, 'questions', form(fields))
            if method == 'mcpServer/elicitation/request' and metadata.get('mode') == 'form':
                schema = metadata.get('requestedSchema')
                if (not isinstance(schema, dict) or schema.get('type') != 'object'
                        or set(schema) - {'type', 'properties', 'required', 'additionalProperties'}
                        or schema.get('additionalProperties') is not False):
                    raise ValueError('Unsupported MCP schema.')
                properties = schema.get('properties')
                if not isinstance(properties, dict) or not 1 <= len(properties) <= 3 or set(schema.get('required', [])) != set(properties):
                    raise ValueError('Unsupported MCP fields.')
                fields = []
                for key, definition in properties.items():
                    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}', key) or SENSITIVE.search(key):
                        raise ValueError('Unsupported field.')
                    if (not isinstance(definition, dict) or definition.get('type') != 'string'
                            or set(definition) - {'type', 'enum', 'title', 'description'}):
                        raise ValueError('Unsupported field schema.')
                    choices = definition.get('enum')
                    if not isinstance(choices, list) or not 1 <= len(choices) <= 32:
                        raise ValueError('Free-text elicitation is unsupported.')
                    options = [text(value, 100) for value in choices]
                    if len(set(options)) != len(options):
                        raise ValueError('Duplicate choices.')
                    fields.append((key, text(definition.get('title', key)), options))
                return await self._ask(metadata, 'mcp', form(fields))
        except (ValueError, TypeError):
            pass  # Never echo, persist or publish rejected metadata or authentication URLs.
        return {'answers': {}} if method == 'item/tool/requestUserInput' else {'action': 'decline', 'content': None}

    def row(self, owner, chat, identifier):
        row = self.db.execute('SELECT * FROM input_requests WHERE id=? AND owner=? AND chat_id=?',
                              (identifier, owner, chat)).fetchone()
        if row is None:
            raise RequestUnavailable('Request not found.')
        return row

    def snapshot(self, owner, chat, identifier):
        row = self.row(owner, chat, identifier)
        if row['outcome'] == 'pending':
            if row['expires'] <= time.time():
                self._terminal(row, 'expired')
            elif not self._live(row):
                self.invalidate_turn(row['thread_id'], row['turn_id'])
            row = self.row(owner, chat, identifier)
        result = {'id': row['id'], 'kind': row['kind'], 'revision': 1, 'expires': row['expires'],
                  'outcome': row['outcome'], 'delivery': row['delivery'], 'form': json.loads(row['form'])}
        if row['kind'] == 'sign_in' and self.broker and result['form'].get('provider') == 'oak_synthetic':
            result['broker'] = self.broker.snapshot(identifier)
        return result

    async def decide(self, owner, chat, identifier, data):
        if not isinstance(data, dict) or data.get('decision') not in {'submit', 'cancel'}:
            raise ValueError('Invalid request decision.')
        allowed = {'requestId', 'revision', 'decision'} | ({'values'} if data['decision'] == 'submit' else set())
        if set(data) != allowed or type(data.get('revision')) is not int or data['revision'] != 1:
            raise ValueError('Invalid decision fields.')
        action_id = data.get('requestId')
        if not isinstance(action_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', action_id):
            raise ValueError('Invalid action ID.')
        async with self.controller._lock(chat):
            row = self.row(owner, chat, identifier)
            if row['kind'] == 'sign_in' and data['decision'] != 'cancel':
                raise ValueError('Website authentication is unavailable.')
            values = data.get('values')
            if data['decision'] == 'submit':
                fields = json.loads(row['form'])['fields']
                if not isinstance(values, dict) or set(values) != {f['id'] for f in fields}:
                    raise ValueError('Values differ from the offered form.')
                for field in fields:
                    value = text(values[field['id']], 2000)
                    if field['options'] and value not in field['options']:
                        raise ValueError('Invalid choice.')
            fingerprint = hashlib.sha256(bounded(data).encode()).hexdigest()
            receipt = self.db.execute('SELECT fingerprint,result FROM input_request_receipts WHERE request_id=? AND action_id=?',
                                      (identifier, action_id)).fetchone()
            if receipt:
                if receipt[0] != fingerprint:
                    raise RequestUnavailable('Action ID reused with different input.')
                return {**json.loads(receipt[1]), 'delivery': row['delivery']}
            if row['outcome'] != 'pending' or not self._live(row):
                raise RequestUnavailable('Request has ended or its native runtime is unavailable.')
            if row['expires'] <= time.time():
                self._terminal(row, 'expired')
                raise RequestUnavailable('Request expired.')
            outcome = 'submitted' if data['decision'] == 'submit' else 'cancelled'
            result = {'outcome': outcome, 'delivery': 'ready'}
            # No awaits inside this commit; receipt and consumption form one transaction.
            response = self._response(row, outcome, values)
            with self.db:
                self.db.execute("UPDATE input_requests SET outcome=?,response=?,delivery='ready' WHERE id=? AND outcome='pending'",
                                (outcome, bounded(response), identifier))
                self.db.execute('INSERT INTO input_request_receipts VALUES (?,?,?,?)',
                                (identifier, action_id, fingerprint, bounded(result)))
            self.waiters[identifier].set_result(response)
            if self.broker:
                self.broker.revoke(identifier)
            return result

    def delivered(self, epoch, native_id, status):
        if status not in {'sent', 'uncertain'}:
            raise ValueError('Invalid native delivery status.')
        with self.db:
            self.db.execute("UPDATE input_requests SET delivery=? WHERE runtime_epoch=? AND native_id=? AND delivery='ready'",
                            (status, epoch, bounded(native_id)))

    def invalidate_turn(self, thread, turn):
        rows = self.db.execute("SELECT id FROM input_requests WHERE thread_id=? AND turn_id=? AND outcome='pending'", (thread, turn)).fetchall()
        with self.db:
            self.db.execute("UPDATE input_requests SET outcome='cancelled',delivery='abandoned' WHERE thread_id=? AND turn_id=? AND outcome='pending'", (thread, turn))
        for row in rows:
            future = self.waiters.get(row['id'])
            if future is not None and not future.done():
                future.cancel()
            if self.broker:
                self.broker.revoke(row['id'])
