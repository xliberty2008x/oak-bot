"""A durable event stream with conversation and run-scoped subscribers."""

import asyncio
from contextlib import asynccontextmanager
import json
import mimetypes
from pathlib import Path
import time
import uuid


class EventBus:
    def __init__(self, controller):
        self.controller = controller
        self.db = controller.db
        self.sinks = []
        self.subscribers = {}
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS ui_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER,
                run_id TEXT, event TEXT);
            CREATE INDEX IF NOT EXISTS ui_events_chat ON ui_events(chat_id,sequence);
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY, chat_id INTEGER, path TEXT, name TEXT, mime TEXT);
        ''')
        self.db.commit()

    def add_sink(self, sink):
        self.sinks.append(sink)

    def artifact_path(self, chat_id, identifier):
        row = self.db.execute('SELECT path FROM artifacts WHERE id=? AND chat_id=?', (identifier, chat_id)).fetchone()
        if not row:
            raise ValueError('Unknown artifact.')
        return self.controller.tools.file(row[0])

    async def emit(self, chat_id, event):
        from .events import validate_event
        event = dict(event)
        event.setdefault('threadId', self.controller.threads.get(chat_id) or 'conversation-' + str(chat_id))
        event.setdefault('runId', self.controller.active.get(chat_id) or 'notice-' + uuid.uuid4().hex)
        event.setdefault('timestamp', int(time.time() * 1000))
        if event.get('type') == 'CUSTOM' and event.get('name') == 'artifact':
            value = dict(event['value'])
            if value.get('path'):
                path = self.controller.tools.file(value['path'])
                identifier = uuid.uuid4().hex
                mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
                with self.db:
                    self.db.execute('INSERT INTO artifacts VALUES (?,?,?,?,?)', (identifier, chat_id, str(path), path.name, mime))
                event['value'] = {'id': identifier, 'name': path.name, 'mime': mime,
                                  'caption': value.get('caption', ''), 'url': '/api/artifacts/' + identifier}
        validate_event(event)
        with self.db:
            cursor = self.db.execute('INSERT INTO ui_events(chat_id,run_id,event) VALUES (?,?,?)',
                                     (chat_id, event['runId'], json.dumps(event, ensure_ascii=False)))
        sequence = cursor.lastrowid
        envelope = {'sequence': sequence, 'event': event}
        # Delivery sinks persist their own outgoing state; UI subscribers never
        # replace or mutate a shared engine sink.
        for sink in self.sinks:
            await sink(chat_id, event)
        for key, queue in tuple(self.subscribers.items()):
            subscribed_chat, run_id, _ = key
            if chat_id == subscribed_chat and (run_id is None or run_id == event['runId']):
                if queue.full():
                    queue.get_nowait()
                    queue.put_nowait(None)  # Client resumes from its durable cursor.
                else:
                    queue.put_nowait(envelope)

    def replay(self, chat_id, after=0, run_id=None, limit=500):
        query = 'SELECT sequence,event FROM ui_events WHERE chat_id=? AND sequence>?'
        args = [chat_id, after]
        if run_id:
            query += ' AND run_id=?'
            args.append(run_id)
        query += ' ORDER BY sequence LIMIT ?'
        args.append(min(max(int(limit), 1), 500))
        return [{'sequence': row[0], 'event': json.loads(row[1])} for row in self.db.execute(query, args)]

    @asynccontextmanager
    async def subscribe(self, chat_id, run_id=None):
        key = (chat_id, run_id, uuid.uuid4().hex)
        queue = asyncio.Queue(maxsize=512)
        self.subscribers[key] = queue
        try:
            yield queue
        finally:
            self.subscribers.pop(key, None)
