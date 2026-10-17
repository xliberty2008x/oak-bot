import tempfile
import unittest
from unittest.mock import AsyncMock

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
