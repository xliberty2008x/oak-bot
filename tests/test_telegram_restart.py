"""Durable turn evidence governs reply recovery; no Telegram or model calls."""

import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from oak.sessions import SessionStore
from oak.telegram import TelegramGateway


class ReplyRestartTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE turns(turn_id TEXT,thread_id TEXT,chat_id INTEGER,status TEXT)')
        self.controller = SimpleNamespace(sessions=SessionStore(db))
        self.gateway = self.open_gateway()

    def open_gateway(self):
        gateway = TelegramGateway(self.controller, 'fake-token', {7, 8}, Path(self.temporary.name))
        self.addCleanup(gateway.db.close)
        gateway._api = AsyncMock(return_value={'message_id': 41})
        return gateway

    async def interrupted_snapshot(self, *, text=None, turn_chat=7, status='completed'):
        self.gateway._apply(7, {'type': 'RUN_STARTED', 'runId': 'fixture-turn'})
        if text is not None:
            self.gateway._apply(7, {'type': 'TEXT_MESSAGE_CONTENT', 'runId': 'fixture-turn', 'delta': text})
        await self.gateway._render_one(*self.gateway._pending()[0])
        with self.controller.sessions.db:
            self.controller.sessions.db.execute('INSERT INTO turns VALUES(?,?,?,?)',
                ('fixture-turn', 'fixture-thread', turn_chat, status))
        raw = json.loads(self.gateway.db.execute('SELECT value FROM replies').fetchone()[0])
        self.assertFalse(raw['terminal'])
        self.assertFalse(raw['sending'])
        self.assertEqual((len(raw['message_ids']), len(raw['sent'])), (1, 1))
        return raw

    async def test_completed_empty_snapshot_retains_acknowledged_message_without_rewriting_it(self):
        raw = await self.interrupted_snapshot()
        self.assertEqual(raw['parts'], {})  # Exact live failure metadata shape.
        restarted = self.open_gateway()
        self.assertEqual(restarted._pending(), [])
        restarted._api.assert_not_awaited()
        status, saved = restarted.db.execute('SELECT status,value FROM replies').fetchone()
        saved = json.loads(saved)
        self.assertEqual(status, 'delivered')
        self.assertTrue(saved['terminal'])
        self.assertEqual(saved['error'], '')
        self.assertEqual(saved['message_ids'], raw['message_ids'])
        self.assertEqual(saved['sent'], raw['sent'])
        self.assertEqual(saved['parts'], {})
        again = self.open_gateway()
        self.assertEqual(again._pending(), [])
        again._api.assert_not_awaited()

    async def test_completed_delivered_text_settles_without_an_external_edit_or_send(self):
        await self.interrupted_snapshot(text='Synthetic completed result')
        restarted = self.open_gateway()
        self.assertEqual(restarted._pending(), [])
        restarted._api.assert_not_awaited()
        status, raw = restarted.db.execute('SELECT status,value FROM replies').fetchone()
        self.assertEqual(status, 'delivered')
        self.assertEqual(json.loads(raw)['parts'], {'text': 'Synthetic completed result'})
        self.assertEqual(json.loads(raw)['error'], '')

    async def test_other_scope_completion_does_not_complete_an_unfinished_reply(self):
        await self.interrupted_snapshot(turn_chat=8)
        restarted = self.open_gateway()
        reply = restarted._pending()[0][1]
        self.assertIn('перервано перезапуском', reply.error)
        restarted._api.assert_not_awaited()

    async def test_noncompleted_turn_retains_restart_interruption(self):
        await self.interrupted_snapshot(status='uncertain')
        restarted = self.open_gateway()
        reply = restarted._pending()[0][1]
        self.assertIn('перервано перезапуском', reply.error)
        restarted._api.assert_not_awaited()

    async def test_completed_turn_cannot_authorize_replaying_an_ambiguous_send(self):
        await self.interrupted_snapshot()
        row = self.gateway.db.execute('SELECT value FROM replies').fetchone()
        value = json.loads(row[0])
        value['sending'] = True
        with self.gateway.db:
            self.gateway.db.execute('UPDATE replies SET value=?', (json.dumps(value),))
        restarted = self.open_gateway()
        self.assertEqual(restarted._pending(), [])
        self.assertEqual(restarted.db.execute('SELECT status FROM replies').fetchone()[0], 'uncertain')
        restarted._api.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
