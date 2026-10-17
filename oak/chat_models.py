"""Durable, single-use model choices bound to one conversation snapshot."""

import asyncio
import json
import re
import time
import uuid


UNAVAILABLE = 'Цей вибір уже недоступний. Відкрий нове меню командою /model.'
BUSY = 'Дочекайся завершення поточної роботи й відкрий /model знову.'
FAILED = 'Модель не змінено. Перевір доступність моделі та Турбо в панелі Oak, потім відкрий /model знову.'


def chat_models(controller):
    if not hasattr(controller, '_chat_models'):
        controller._chat_models = ChatModels(controller)
    return controller._chat_models


class ChatModels:
    def __init__(self, controller):
        self.controller, self.db = controller, controller.db
        self.db.execute('''CREATE TABLE IF NOT EXISTS chat_model_choices (
            token TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, snapshot TEXT NOT NULL,
            kind TEXT NOT NULL, model TEXT, page INTEGER, expires REAL NOT NULL,
            state TEXT NOT NULL, outcome TEXT)''')
        with self.db:
            self.db.execute("UPDATE chat_model_choices SET state='uncertain',outcome=? WHERE state='applying'", (UNAVAILABLE,))

    def _finish(self, token, state, outcome):
        with self.db:
            self.db.execute('UPDATE chat_model_choices SET state=?,outcome=? WHERE token=?', (state, outcome, token))
        return outcome

    async def open(self, scope, page=0):
        c = self.controller
        if c.sessions.deleted(scope):
            return UNAVAILABLE
        if c.model_busy(scope):
            return BUSY
        snapshot = c.model_snapshot(scope)
        try:
            models = await asyncio.wait_for(c.model_catalog(scope), 10)
        except asyncio.CancelledError:
            raise
        except Exception:
            return 'Перелік моделей зараз недоступний. Спробуй /model пізніше.'
        if snapshot['tier'] == 'fast':
            models = [model for model in models if model['turbo_available']]
        if not models:
            return 'Немає доступних моделей для поточних налаштувань. Перевір модель і Турбо в панелі Oak.'
        page = min(max(0, page), (len(models) - 1) // 20)
        async with c._lock(scope):
            if c.sessions.deleted(scope) or snapshot != c.model_snapshot(scope):
                return UNAVAILABLE
            if c.model_busy(scope):
                return BUSY
            encoded = json.dumps(snapshot, sort_keys=True)
            expires, rows = time.time() + 600, []
            with self.db:
                self.db.execute("UPDATE chat_model_choices SET state='stale',outcome=? WHERE chat_id=? AND state='pending'", (UNAVAILABLE, scope))

                def choice(label, kind, model=None, target_page=None):
                    token = uuid.uuid4().hex
                    self.db.execute('INSERT INTO chat_model_choices VALUES (?,?,?,?,?,?,?,\'pending\',NULL)',
                                    (token, scope, encoded, kind, model, target_page, expires))
                    rows.append([{'label': label, 'token': token}])

                for model in models[page * 20:(page + 1) * 20]:
                    label = ('✓ ' if model['model'] == snapshot['model'] else '') + ' '.join(model['display_name'].split())
                    choice(label if len(label) <= 60 else label[:59] + '…', 'model', model['model'])
                if page > 0:
                    choice('← Попередні моделі', 'page', target_page=page - 1)
                if (page + 1) * 20 < len(models):
                    choice('Наступні моделі →', 'page', target_page=page + 1)
            text = 'Обери модель для цієї розмови. Поточна позначена ✓. Вибір діє 10 хвилин.'
            if snapshot['tier'] == 'fast':
                text += ' Показано моделі, сумісні з увімкненим Турбо.'
        await c.emit(scope, {'type': 'CUSTOM', 'name': 'model_choices', 'value': {'text': text, 'rows': rows}})
        return None

    async def choose(self, scope, token):
        if not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{32}', token):
            return UNAVAILABLE
        c = self.controller
        async with c._lock(scope):
            if c.sessions.deleted(scope):
                return UNAVAILABLE
            row = self.db.execute('SELECT * FROM chat_model_choices WHERE token=? AND chat_id=?', (token, scope)).fetchone()
            if row is None:
                return UNAVAILABLE
            if row['state'] != 'pending':
                return row['outcome'] or 'Зміна вже обробляється. Перевір налаштування в панелі Oak.'
            if row['expires'] <= time.time() or json.loads(row['snapshot']) != c.model_snapshot(scope):
                return self._finish(token, 'stale', UNAVAILABLE)
            if c.model_busy(scope):
                return self._finish(token, 'failed', BUSY)
            if row['kind'] == 'model' and row['model'] == c.model_for(scope):
                return self._finish(token, 'done', 'Ця модель уже вибрана. Налаштування збережено.')
            self._finish(token, 'applying', None)
        try:
            if row['kind'] == 'page':
                error = await self.open(scope, row['page'])
                return self._finish(token, 'done' if error is None else 'failed', error or 'Показано іншу сторінку моделей.')
            await c.set_model(scope, row['model'], expected=json.loads(row['snapshot']))
        except asyncio.CancelledError:
            self._finish(token, 'uncertain', UNAVAILABLE)
            raise
        except Exception:
            return self._finish(token, 'failed', FAILED)
        return self._finish(token, 'done', 'Модель для цієї розмови оновлено.')
