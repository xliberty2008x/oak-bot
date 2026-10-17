"""Atomic schedule edits with scoped revisions and durable owner action receipts.

The HTTP caller authenticates the owner and resolves the selected scope first.
These methods recheck scope liveness and bind every job/action to that scope.
"""

from datetime import datetime, timezone
import hashlib
import json
import math
import re
import time
import uuid


class JobControls:
    def __init__(self, controller):
        self.controller, self.db = controller, controller.db
        if 'revision' not in {row[1] for row in self.db.execute('PRAGMA table_info(jobs)')}:
            self.db.execute('ALTER TABLE jobs ADD COLUMN revision INTEGER NOT NULL DEFAULT 1')
        self.db.execute('''CREATE TABLE IF NOT EXISTS owner_job_operations (
            chat_id INTEGER NOT NULL, operation_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
            result TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(chat_id,operation_id))''')
        self.db.commit()

    def _scope(self, scope):
        if type(scope) is not int or scope == 0 or self.controller.sessions.deleted(scope):
            raise LookupError('Розмову не знайдено.')
        if self.controller.sessions.owner(scope) is not None:
            return
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='web_conversations'").fetchone():
            if self.db.execute('SELECT 1 FROM web_conversations WHERE chat_id=?', (scope,)).fetchone():
                return
        raise LookupError('Розмову не знайдено.')

    def _operation(self, scope, operation_id, payload):
        self._scope(scope)
        if not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', operation_id):
            raise ValueError('Некоректний ідентифікатор дії.')
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                                 allow_nan=False).encode()).hexdigest()
        receipt = self.db.execute('SELECT fingerprint,result FROM owner_job_operations WHERE chat_id=? AND operation_id=?',
                                  (scope, operation_id)).fetchone()
        if receipt and receipt['fingerprint'] != fingerprint:
            raise RuntimeError('Ідентифікатор дії вже використано з іншими даними.')
        return fingerprint, json.loads(receipt['result']) if receipt else None

    def _record(self, scope, operation_id, fingerprint, identifier):
        result = dict(self.db.execute('SELECT id,revision,status,due FROM jobs WHERE id=? AND chat_id=?',
                                      (identifier, scope)).fetchone())
        self.db.execute('INSERT INTO owner_job_operations VALUES (?,?,?,?,?)',
                        (scope, operation_id, fingerprint, json.dumps(result), time.time()))
        return result

    def _time(self, at):
        if not isinstance(at, str) or not 1 <= len(at) <= 128:
            raise ValueError('Укажи дату й час у форматі ISO.')
        try:
            moment = datetime.fromisoformat(at)
            if moment.tzinfo is None:
                zone = self.controller.scheduler.timezone
                choices = set()
                for fold in (0, 1):
                    local = moment.replace(tzinfo=zone, fold=fold)
                    if local.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == moment:
                        choices.add(local.timestamp())
                if len(choices) != 1:
                    raise ValueError('Час неоднозначний або не існує. Укажи ISO-час із явним UTC-зсувом.')
                due = choices.pop()
            else:
                due = moment.timestamp()
            if not math.isfinite(due):
                raise ValueError('Час поза підтримуваним діапазоном.')
            return due
        except (OverflowError, OSError):
            raise ValueError('Час поза підтримуваним діапазоном.') from None

    def create_owned(self, scope, operation_id, prompt, at, interval_seconds=None, mode='remind'):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000 or mode not in ('remind', 'run'):
            raise ValueError('Укажи текст задачі від 1 до 16000 символів і підтримуваний тип.')
        if interval_seconds is not None:
            try:
                valid_interval = type(interval_seconds) in (int, float) and math.isfinite(interval_seconds) and interval_seconds >= 60
            except OverflowError:
                valid_interval = False
            if not valid_interval:
                raise ValueError('Інтервал повторення має бути не менше 60 секунд.')
        with self.db:
            fingerprint, receipt = self._operation(scope, operation_id,
                {'kind': 'create', 'prompt': prompt, 'at': at, 'interval_seconds': interval_seconds, 'mode': mode})
            if receipt is not None:
                return receipt
            due = self._time(at)
            identifier = uuid.uuid4().hex[:12]
            # A direct insert keeps the job and its receipt in the same SQLite
            # transaction; Scheduler.add commits independently and cannot do so.
            self.db.execute('INSERT INTO jobs(id,chat_id,prompt,due,interval,mode) VALUES (?,?,?,?,?,?)',
                            (identifier, scope, prompt.strip(), due, interval_seconds, mode))
            return self._record(scope, operation_id, fingerprint, identifier)

    def _editable(self, scope, identifier, expected_revision):
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 128:
            raise LookupError('Задачу не знайдено.')
        if type(expected_revision) is not int or not 1 <= expected_revision < 2**63:
            raise ValueError('Потрібна поточна версія задачі.')
        job = self.db.execute('SELECT id,revision,status,due FROM jobs WHERE id=? AND chat_id=?',
                              (identifier, scope)).fetchone()
        if job is None:
            raise LookupError('Задачу не знайдено.')
        if job['revision'] != expected_revision:
            raise RuntimeError('Задача вже змінилася. Онови розклад перед новою дією.')
        if job['status'] not in {'pending', 'paused'}:
            raise RuntimeError('Можна змінювати лише задачі, що очікують або призупинені.')
        return job

    def reschedule_owned(self, scope, identifier, operation_id, expected_revision, at):
        with self.db:
            fingerprint, receipt = self._operation(scope, operation_id,
                {'kind': 'reschedule', 'id': identifier, 'expected_revision': expected_revision, 'at': at})
            if receipt is not None:
                return receipt
            self._editable(scope, identifier, expected_revision)
            due = self._time(at)
            updated = self.db.execute("UPDATE jobs SET due=?,revision=revision+1 WHERE id=? AND chat_id=? "
                                      "AND revision=? AND status IN ('pending','paused')",
                                      (due, identifier, scope, expected_revision))
            if updated.rowcount != 1:
                raise RuntimeError('Задача вже змінилася. Онови розклад перед новою дією.')
            return self._record(scope, operation_id, fingerprint, identifier)
