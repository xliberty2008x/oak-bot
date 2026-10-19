"""Optional improvement monitoring sees references, never private message bodies."""

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock

from oak.sessions import SessionStore
from oak.telegram import TelegramError, TelegramGateway


class FixtureController:
    def __init__(self, db):
        self.sessions = SessionStore(db)
        self.events = []
        self.submit = AsyncMock()
        self.fail_monitoring = False

    def record_improvement_event(self, chat_id, kind, metadata, **identity):
        self.events.append((chat_id, kind, metadata, identity))
        if self.fail_monitoring:
            raise RuntimeError('Synthetic monitoring failure.')


class TelegramImprovementEventsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        self.controller = FixtureController(db)
        self.gateway = TelegramGateway(self.controller, 'fake-token', {7}, Path(self.temporary.name))
        self.addCleanup(self.gateway.db.close)
        self.gateway._api = AsyncMock(return_value={'message_id': 41})
        self.gateway._attachments = AsyncMock(return_value=[])
        self.gateway._bind_reaction = lambda *args, **kwargs: None
        artifact = Path(self.temporary.name) / 'artifacts/private-name.png'
        artifact.parent.mkdir()
        artifact.write_bytes(b'Synthetic artifact bytes')
        self.gateway._queue_delivery(7, 'sendPhoto', {'chat_id': 7, 'caption': 'PRIVATE_CAPTION_CANARY',
            '_upload': {'path': str(artifact), 'mime': 'image/png'}}, 'fixture-artifact')

    async def deliver(self):
        await self.gateway._render_delivery(self.gateway._deliveries()[0])

    async def test_confirmed_artifact_records_only_reference_metadata_once(self):
        row = self.gateway._deliveries()[0]
        await self.deliver()
        await self.gateway._render_delivery(row)  # Reusing a stale pending row cannot resend.
        self.gateway._api.assert_awaited_once()
        self.assertEqual(self.controller.events, [(7, 'artifact_delivery',
            {'delivery_status': 'confirmed', 'transport': 'telegram', 'message_id': 41},
            {'request_id': 'telegram-delivery:fixture-artifact'})])
        self.assertNotIn('PRIVATE_CAPTION_CANARY', json.dumps(self.controller.events))
        self.assertNotIn('private-name', json.dumps(self.controller.events))

    async def test_monitor_failure_cannot_fail_confirmed_delivery_or_trigger_resend(self):
        self.controller.fail_monitoring = True
        await self.deliver()
        self.assertEqual(self.gateway._deliveries(), [])
        self.assertEqual(self.gateway.db.execute('SELECT status FROM deliveries').fetchone()[0], 'delivered')
        self.gateway._api.assert_awaited_once()

    async def test_ambiguous_transport_does_not_claim_a_confirmed_delivery(self):
        self.gateway._api.side_effect = TelegramError('sendPhoto')
        with self.assertRaises(TelegramError):
            await self.deliver()
        self.assertEqual(self.controller.events, [])
        self.assertEqual(self.gateway.db.execute('SELECT status FROM deliveries').fetchone()[0], 'uncertain')

    async def test_reply_feedback_is_scoped_association_without_sentiment_or_body(self):
        await self.deliver()
        self.controller.events.clear()
        update = {'update_id': 5, 'message': {'message_id': 50, 'chat': {'id': 7, 'type': 'private'},
            'from': {'id': 7}, 'text': 'PRIVATE_FEEDBACK_CANARY', 'reply_to_message': {'message_id': 41}}}
        await self.gateway.handle_update(update)
        self.assertEqual(self.controller.events, [(7, 'owner_feedback',
            {'transport': 'telegram', 'reply_to_message_id': 41,
             'delivery_reference': 'telegram-delivery:fixture-artifact'},
            {'request_id': 'telegram-update:5'})])
        self.assertNotIn('PRIVATE_FEEDBACK_CANARY', json.dumps(self.controller.events))
        self.controller.submit.assert_awaited_once_with(7, 'PRIVATE_FEEDBACK_CANARY', 5)

    async def test_unrelated_or_wrong_scope_reply_is_not_bound_to_an_artifact(self):
        await self.deliver()
        self.controller.events.clear()
        for identifier, reply_to in ((5, 999), (6, 41)):
            if identifier == 6:
                with self.gateway.db:
                    self.gateway.db.execute('UPDATE deliveries SET chat_id=8')
            await self.gateway.handle_update({'update_id': identifier, 'message': {
                'chat': {'id': 7, 'type': 'private'}, 'from': {'id': 7},
                'text': 'Synthetic ordinary reply.', 'reply_to_message': {'message_id': reply_to}}})
        self.assertEqual(self.controller.events, [])


if __name__ == '__main__':
    unittest.main()
