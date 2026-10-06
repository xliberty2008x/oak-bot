"""Durable one-time and recurring reminders or agent tasks."""

import asyncio
from datetime import datetime
import math
import time
import uuid
from zoneinfo import ZoneInfo


class Scheduler:
    def __init__(self, db, controller, timezone='Europe/Kyiv'):
        self.db, self.controller = db, controller
        self.timezone = ZoneInfo(timezone)
        db.execute('''CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, prompt TEXT NOT NULL,
            due REAL NOT NULL, interval REAL, mode TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending')''')
        if 'turn_id' not in {row[1] for row in db.execute('PRAGMA table_info(jobs)')}:
            db.execute('ALTER TABLE jobs ADD COLUMN turn_id TEXT')
        db.commit()

    def add(self, chat_id, prompt, delay_seconds=None, at=None, interval_seconds=None, mode='remind'):
        if not prompt.strip() or len(prompt) > 16000 or mode not in ('remind', 'run'):
            raise ValueError('Invalid scheduled task.')
        if at:
            moment = datetime.fromisoformat(at)
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=self.timezone)
            due = moment.timestamp()
        elif delay_seconds is not None and float(delay_seconds) >= 0:
            due = time.time() + float(delay_seconds)
        else:
            raise ValueError('Set at or nonnegative delay_seconds.')
        interval = float(interval_seconds) if interval_seconds is not None else None
        if not math.isfinite(due):
            raise ValueError('Scheduled time must be finite.')
        if interval is not None and (not math.isfinite(interval) or interval < 60):
            raise ValueError('Recurring tasks require an interval of at least 60 seconds.')
        job_id = uuid.uuid4().hex[:12]
        with self.db:
            self.db.execute('INSERT INTO jobs(id,chat_id,prompt,due,interval,mode) VALUES (?,?,?,?,?,?)',
                            (job_id, chat_id, prompt.strip(), due, interval, mode))
        return job_id

    def list(self, chat_id):
        rows = self.db.execute("SELECT id,prompt,due,interval,mode,status FROM jobs WHERE chat_id=? "
                               "AND status IN ('pending','running','uncertain') ORDER BY due", (chat_id,))
        return [dict(zip(('id', 'prompt', 'due', 'interval', 'mode', 'status'), row)) for row in rows]

    def cancel(self, chat_id, job_id):
        with self.db:
            return bool(self.db.execute("UPDATE jobs SET status='cancelled' WHERE id=? AND chat_id=? "
                                        "AND status IN ('pending','running','uncertain')", (job_id, chat_id)).rowcount)

    def _finish(self, job_id, interval):
        with self.db:
            if interval is not None:
                self.db.execute("UPDATE jobs SET status='pending',due=?,turn_id=NULL "
                                "WHERE id=? AND status='running'", (time.time() + interval, job_id))
            else:
                self.db.execute("UPDATE jobs SET status='done' WHERE id=? AND status='running'", (job_id,))

    async def _settle_runs(self):
        rows = self.db.execute("SELECT j.id,j.chat_id,j.interval,t.status FROM jobs j "
                               "JOIN turns t ON t.turn_id=j.turn_id "
                               "WHERE j.status='running' AND j.mode='run'").fetchall()
        for job_id, chat_id, interval, status in rows:
            if status == 'completed':
                self._finish(job_id, interval)
            elif status in ('failed', 'interrupted', 'uncertain'):
                with self.db:
                    self.db.execute("UPDATE jobs SET status='uncertain' WHERE id=? AND status='running'", (job_id,))
                await self.controller.notice(chat_id, 'Задача ' + job_id +
                    ' не завершилася успішно. Перевір її стан; автоматично повторювати не буду.')

    async def recover(self):
        await self._settle_runs()
        rows = self.db.execute("SELECT id,chat_id FROM jobs WHERE status='running'").fetchall()
        with self.db:
            self.db.execute("UPDATE jobs SET status='uncertain' WHERE status='running'")
        for job_id, chat_id in rows:
            await self.controller.notice(chat_id, 'Задача ' + job_id +
                                         ' була перервана. Перевір її стан; автоматично повторювати не буду.')

    async def tick(self):
        await self._settle_runs()
        rows = self.db.execute("SELECT id,chat_id,prompt,interval,mode,due FROM jobs "
                               "WHERE status='pending' AND due<=? ORDER BY due LIMIT 20", (time.time(),)).fetchall()
        for job_id, chat_id, prompt, interval, mode, due in rows:
            # A background task waits for an idle chat rather than steering user work.
            if mode == 'run' and chat_id in self.controller.active:
                continue
            with self.db:
                claimed = self.db.execute("UPDATE jobs SET status='running' WHERE id=? AND status='pending'",
                                          (job_id,)).rowcount
            if not claimed:
                continue
            try:
                if mode == 'remind':
                    await self.controller.notice(chat_id, 'Нагадування: ' + prompt,
                                                 run_id='job-' + job_id + '-' + str(due))
                    self._finish(job_id, interval)
                else:
                    synthetic_id = -int(uuid.uuid4().hex[:15], 16)
                    accepted = await self.controller.submit(chat_id, '[Запланована задача]\n' + prompt,
                                                            synthetic_id, idle_only=True)
                    if not accepted:
                        with self.db:
                            self.db.execute("UPDATE jobs SET status='pending' WHERE id=? AND status='running'", (job_id,))
                        continue
                    turn_id = self.controller.active.get(chat_id)
                    if not turn_id:
                        raise RuntimeError('Scheduled turn acknowledgement is missing.')
                    with self.db:
                        self.db.execute("UPDATE jobs SET turn_id=? WHERE id=? AND status='running'", (turn_id, job_id))
            except Exception:
                with self.db:
                    self.db.execute("UPDATE jobs SET status='uncertain' WHERE id=? AND status='running'", (job_id,))
                await self.controller.notice(chat_id, 'Не вдалося підтвердити виконання задачі ' + job_id + '.')

    async def run(self):
        await self.recover()
        while True:
            await self.tick()
            await asyncio.sleep(1)
