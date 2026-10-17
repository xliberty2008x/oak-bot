"""Durable owner decisions on delivered reminder occurrences."""

import time
import uuid


class ReminderActions:
    def __init__(self, scheduler):
        self.scheduler, self.db = scheduler, scheduler.db
        self.db.execute('''CREATE TABLE IF NOT EXISTS reminder_occurrences (
            token TEXT PRIMARY KEY, job_id TEXT NOT NULL, chat_id INTEGER NOT NULL,
            due REAL NOT NULL, prompt TEXT NOT NULL, expires REAL NOT NULL,
            state TEXT NOT NULL, action TEXT, new_job TEXT,
            UNIQUE(job_id,due))''')
        self.db.commit()

    async def publish(self, job_id, chat_id, prompt, due):
        c = self.scheduler.controller
        if not callable(getattr(c, 'emit', None)) or getattr(c, 'sessions', None) is None:
            await c.notice(chat_id, 'Нагадування: ' + prompt, run_id='job-' + job_id + '-' + str(due))
            return
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO reminder_occurrences VALUES (?,?,?,?,?,?,'pending',NULL,NULL)",
                            (uuid.uuid4().hex[:16], job_id, chat_id, due, prompt, time.time() + 7 * 86400))
        token = self.db.execute('SELECT token FROM reminder_occurrences WHERE job_id=? AND due=?', (job_id, due)).fetchone()[0]
        await c.emit(chat_id, {'type': 'CUSTOM', 'name': 'reminder',
                              'value': {'id': token, 'text': 'Нагадування: ' + prompt}})

    def snapshot(self, chat_id, token):
        row = self.db.execute('SELECT state,expires,new_job FROM reminder_occurrences WHERE token=? AND chat_id=?', (token, chat_id)).fetchone()
        if row is None:
            raise ValueError('Нагадування недоступне.')
        state = 'expired' if row[0] == 'pending' and row[1] <= time.time() else row[0]
        return {'state': state, 'job': row[2]}

    def decide(self, chat_id, token, action):
        if action not in {'600', '3600', 'done'}:
            raise ValueError('Невідома дія.')
        row = self.db.execute('SELECT o.job_id,o.prompt,o.expires,o.state,o.action,o.new_job,j.mode '
                              'FROM reminder_occurrences o JOIN jobs j ON j.id=o.job_id AND j.chat_id=o.chat_id '
                              'WHERE o.token=? AND o.chat_id=?', (token, chat_id)).fetchone()
        if row is None or row[6] != 'remind':
            raise ValueError('Нагадування недоступне.')
        if row[3] != 'pending':
            if row[4] == action:
                return {'state': row[3], 'job': row[5]}
            raise ValueError('Для цього нагадування вже вибрано дію.')
        if row[2] <= time.time():
            raise ValueError('Кнопки цього нагадування прострочені.')
        job = uuid.uuid4().hex[:12] if action != 'done' else None
        state = 'snoozed' if job else 'done'
        # Receipt and new reminder commit together; never modify the recurring parent.
        with self.db:
            if job:
                self.db.execute("INSERT INTO jobs(id,chat_id,prompt,due,interval,mode) VALUES (?,?,?,?,NULL,'remind')",
                                (job, chat_id, row[1], time.time() + int(action)))
            self.db.execute('UPDATE reminder_occurrences SET state=?,action=?,new_job=? WHERE token=?', (state, action, job, token))
        return {'state': state, 'job': job}
