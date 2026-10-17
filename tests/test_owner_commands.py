import tempfile
import unittest
from unittest.mock import AsyncMock
import time

from oak.controller import Controller
from oak.commands import command_names


class OwnerCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.c = Controller(AsyncMock(), ':memory:', self.folder.name, AsyncMock())
        self.addCleanup(self.c.close)

    async def test_forget_is_scoped_and_removes_fts_without_starting_work(self):
        topic = self.c.sessions.resolve(7, 42)
        note = self.c.memory.remember(topic, 'copper kestrel')
        other = self.c.memory.remember(7, 'copper kestrel')
        self.assertIn('/forget', command_names())
        self.assertIn('не знайдено', await self.c.command(7, '/forget ' + note, 1))
        self.assertEqual(len(self.c.memory.search(topic, 'kestrel')), 1)
        self.assertIn('видалено', await self.c.command(topic, '/forget ' + note, 2))
        self.assertEqual(self.c.memory.search(topic, 'kestrel'), [])
        self.assertEqual(self.c.memory.search(7, 'kestrel')[0]['id'], other)
        self.assertIn('не знайдено', await self.c.command(topic, '/forget ' + note, 3))
        for argument in ['', '*', note + ' extra']:
            self.assertIn('Формат', await self.c.command(topic, '/forget ' + argument, 4))
        self.c.client.request.assert_not_awaited()

    async def test_status_reads_model_waiting_schedule_and_memory_without_mutation(self):
        topic = self.c.sessions.resolve(7, 42)
        c = self.c
        c.threads[topic] = 'topic-thread'
        c.memory.remember(topic, 'PRIVATE-MEMORY-CANARY')
        c.memory.remember(7, 'another note')
        c.scheduler.add(topic, 'PRIVATE-JOB-CANARY', at='2027-01-01T09:00:00+02:00')
        c.scheduler.add(7, 'other job', delay_seconds=0)
        with c.db:
            c.db.execute('INSERT INTO conversation_models VALUES (?,?,?,?)', (topic, 'synthetic-model', 'high', 'fast'))
            c.db.execute('INSERT INTO turns VALUES (?,?,?,?)', ('turn', 'topic-thread', topic, 'uncertain'))
            for identifier, scope, expiry in [('own', topic, time.time()+600), ('foreign', 7, time.time()+600), ('expired', topic, 0)]:
                c.db.execute('INSERT INTO interactions VALUES (?,?,?,?,?,?,?,?)',
                             (identifier, scope, 'thread', 'turn', 'approval', '{}', expiry, 'pending'))
        snapshot = list(c.db.iterdump())
        text = await c.command(topic, '/status', 5)
        for value in ['synthetic-model', 'high', 'Турбо: Увімкнено', 'Очікують відповіді: 1', '01.01.2027 09:00 +0200', 'Нотаток у цій розмові: 1', 'потребує перевірки']:
            self.assertIn(value, text)
        self.assertNotIn('PRIVATE-', text)
        self.assertEqual(list(c.db.iterdump()), snapshot)
        c.client.request.assert_not_awaited()
        c.active[topic] = 'current'
        self.assertIn('Є активна задача', await c.command(topic, '/status', 6))
