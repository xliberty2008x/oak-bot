"""Recovery views preserve uncertain evidence across review, scopes and restart."""

import asyncio
import json
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from oak.controller import Controller
from oak.recovery import RecoveryStore


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.controller = Controller(AsyncMock(), ':memory:', '.', AsyncMock())
        self.addCleanup(self.controller.close)
        self.db = self.controller.db
        self.outbox = sqlite3.connect(':memory:')
        self.outbox.row_factory = sqlite3.Row
        self.addCleanup(self.outbox.close)
        self.outbox.executescript('''
            CREATE TABLE intake(update_id INTEGER PRIMARY KEY,chat_id INTEGER,payload TEXT,status TEXT);
            CREATE TABLE deliveries(id TEXT PRIMARY KEY,chat_id INTEGER,method TEXT,payload TEXT,status TEXT,receipt TEXT);
            CREATE TABLE replies(chat_id INTEGER,run_id TEXT,value TEXT,status TEXT);
        ''')
        self.controller.telegram = SimpleNamespace(db=self.outbox)
        self.store = RecoveryStore(self.controller)

    def uncertain_input(self, update_id=101, scope=7, text='Перевірити звіт', attachments='[]'):
        with self.db:
            self.db.execute('INSERT INTO inputs(update_id,chat_id,text,status,attachments) VALUES (?,?,?,\'uncertain\',?)',
                            (update_id, scope, text, attachments))

    def item(self, kind='input', scope=7):
        return next(item for item in self.store.inventory(7, scope)['items'] if item['kind'] == kind)

    def test_all_uncertain_categories_have_bounded_safe_projections(self):
        self.uncertain_input(text='a\n\t' * 100)
        self.uncertain_input(102, attachments='[{"path":"PRIVATE_PATH"}]')
        job = self.controller.scheduler.add(7, 'Скласти звіт', delay_seconds=60, mode='run')
        with self.db:
            self.db.execute("UPDATE jobs SET status='uncertain' WHERE id=?", (job,))
            self.db.execute("INSERT INTO turns VALUES ('turn','thread',7,'uncertain')")
            self.db.execute("INSERT INTO input_requests VALUES ('request',7,7,'thread','turn','epoch','native','private','sign_in',?,0,'answered','uncertain',?)",
                            ('PRIVATE_FORM', 'PRIVATE_RESPONSE'))
        with self.outbox:
            self.outbox.execute("INSERT INTO intake VALUES (1,7,'PRIVATE_INTAKE','uncertain')")
            self.outbox.execute("INSERT INTO deliveries VALUES ('delivery',7,'sendDocument','PRIVATE_PAYLOAD','uncertain','PRIVATE_RECEIPT')")
            self.outbox.execute("INSERT INTO replies VALUES (7,'reply','PRIVATE_REPLY','uncertain')")
        items = self.store.inventory(7, 7)['items']
        self.assertEqual({item['kind'] for item in items},
                         {'input', 'job', 'turn', 'native_request', 'telegram_intake', 'telegram_delivery', 'telegram_reply'})
        self.assertNotIn('PRIVATE_', json.dumps(items))
        self.assertTrue(all(len(item['preview'] or '') <= 80 for item in items))
        self.assertEqual(sum(item['can_retry'] for item in items), 2)
        self.controller.client.request.assert_not_called()

    def test_review_is_idempotent_and_does_not_claim_execution_succeeded(self):
        self.uncertain_input()
        identifier = self.item()['id']
        original = tuple(self.db.execute('SELECT * FROM inputs').fetchone())
        first = self.store.review(7, 7, identifier)
        second = RecoveryStore(self.controller).review(7, 7, identifier)
        self.assertEqual(first, second)
        self.assertTrue(second['reviewed'])
        self.assertEqual(second['state'], 'uncertain')
        self.assertEqual(tuple(self.db.execute('SELECT * FROM inputs').fetchone()), original)

    def test_owner_topic_and_deleted_scope_cannot_be_crossed(self):
        topic = self.controller.sessions.resolve(7, 42)
        self.uncertain_input(101, topic)
        self.uncertain_input(102, 8, 'OTHER_OWNER')
        item = self.item(scope=topic)
        self.assertEqual(self.store.inventory(7, 7)['items'], [])
        for owner, scope in ((8, topic), (7, 8), (7, 7)):
            with self.subTest(owner=owner, scope=scope), self.assertRaises(LookupError):
                self.store.review(owner, scope, item['id'])
        self.controller.sessions.delete(topic)
        with self.assertRaises(LookupError):
            self.store.inventory(7, topic)

    def test_web_conversation_is_owned_and_pagination_survives_stale_records(self):
        self.db.execute('CREATE TABLE web_conversations(id TEXT,owner INTEGER,chat_id INTEGER,title TEXT)')
        self.db.execute("INSERT INTO web_conversations VALUES ('web',7,-100,'Private')")
        for number in range(10):
            self.uncertain_input(100 + number, -100, str(number))
        first = self.store.inventory(7, -100, limit=3)
        with self.db:
            self.db.execute("UPDATE inputs SET status='failed' WHERE update_id=105")
        second = self.store.inventory(7, -100, before=first['next_cursor'], limit=3)
        self.assertEqual(len(first['items']), 3)
        self.assertEqual(len(second['items']), 3)
        self.assertFalse({item['id'] for item in first['items']} & {item['id'] for item in second['items']})
        for kwargs in ({'limit': 0}, {'limit': 51}, {'before': -1}, {'limit': True}, {'before': '1'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.store.inventory(7, -100, **kwargs)

    def test_changed_or_missing_source_cannot_be_reviewed(self):
        self.uncertain_input()
        identifier = self.item()['id']
        with self.db:
            self.db.execute("UPDATE inputs SET status='accepted'")
        with self.assertRaises(LookupError):
            self.store.review(7, 7, identifier)
        self.assertEqual(self.store.inventory(7, 7)['items'], [])

    async def test_retry_dispatches_fresh_owned_input_once_and_keeps_original(self):
        self.uncertain_input()
        original = tuple(self.db.execute('SELECT * FROM inputs').fetchone())
        self.controller.threads[7] = 'thread'
        self.controller.loaded.add('thread')
        self.controller.client.request.return_value = {'turn': {'id': 'new-turn'}}
        identifier = self.item()['id']
        operation = 'operation-12345678'
        first, repeated = await asyncio.gather(
            self.store.retry(7, 7, identifier, operation, confirmed=True),
            self.store.retry(7, 7, identifier, operation, confirmed=True))
        self.assertEqual(first['input_id'], repeated['input_id'])
        self.assertEqual(first['state'], 'accepted')
        self.assertEqual(tuple(self.db.execute('SELECT * FROM inputs WHERE update_id=101').fetchone()), original)
        fresh = self.db.execute('SELECT * FROM inputs WHERE update_id=?', (first['input_id'],)).fetchone()
        self.assertEqual((fresh['chat_id'], fresh['status'], fresh['attachments']), (7, 'accepted', '[]'))
        self.assertIn('Перевірити звіт', fresh['text'])
        self.assertIn('не дублюй уже виконані зовнішні дії', fresh['text'])
        self.controller.client.request.assert_awaited_once()
        self.assertEqual(self.controller.client.request.await_args.args[0], 'turn/start')
        self.assertFalse(self.item()['can_retry'])
        with self.assertRaises(RuntimeError):
            await self.store.retry(7, 7, identifier, 'another-operation-1', confirmed=True)

    async def test_retry_requires_confirmation_scope_and_idle_work(self):
        self.uncertain_input()
        identifier = self.item()['id']
        for confirmed in (False, 'true', 1):
            with self.assertRaises(ValueError):
                await self.store.retry(7, 7, identifier, 'operation-12345678', confirmed=confirmed)
        with self.assertRaises(LookupError):
            await self.store.retry(8, 7, identifier, 'operation-12345678', confirmed=True)
        self.controller.active[7] = 'busy'
        with self.assertRaises(RuntimeError):
            await self.store.retry(7, 7, identifier, 'operation-12345678', confirmed=True)
        self.assertEqual(self.db.execute('SELECT count(*) FROM owner_retries').fetchone()[0], 0)
        self.controller.client.request.assert_not_called()

    async def test_attachment_and_outgoing_delivery_are_never_replayed(self):
        self.uncertain_input(attachments='[{"path":"PRIVATE_PATH"}]')
        with self.outbox:
            self.outbox.execute("INSERT INTO deliveries VALUES ('delivery',7,'sendDocument','PRIVATE_PAYLOAD','uncertain',NULL)")
        for item in self.store.inventory(7, 7)['items']:
            self.assertFalse(item['can_retry'])
            with self.assertRaises(RuntimeError):
                await self.store.retry(7, 7, item['id'], 'operation-12345678', confirmed=True)
        self.controller.client.request.assert_not_called()
        self.assertEqual(self.outbox.execute('SELECT status FROM deliveries').fetchone()[0], 'uncertain')

    async def test_restart_does_not_redispatch_a_reserved_retry(self):
        self.uncertain_input()
        identifier = self.item()['id']
        with self.db:
            self.db.execute("INSERT INTO owner_retries VALUES (7,'operation-12345678',?,-909,'dispatching',0)", (identifier,))
        self.store = RecoveryStore(self.controller)
        result = await self.store.retry(7, 7, identifier, 'operation-12345678', confirmed=True)
        self.assertEqual(result['state'], 'uncertain')
        self.assertEqual(result['input_id'], '-909')
        self.assertEqual(self.db.execute('SELECT count(*) FROM inputs').fetchone()[0], 1)
        self.controller.client.request.assert_not_called()

    async def test_submit_race_or_uncertain_failure_does_not_replay(self):
        self.uncertain_input()
        identifier = self.item()['id']
        self.controller.submit = AsyncMock(return_value=False)
        first = await self.store.retry(7, 7, identifier, 'operation-12345678', confirmed=True)
        self.assertEqual(first['state'], 'not_started')
        self.assertTrue(self.controller.submit.await_args.kwargs['idle_only'])
        again = await self.store.retry(7, 7, identifier, 'operation-12345678', confirmed=True)
        self.assertEqual(first, again)
        self.controller.submit.assert_awaited_once()
        self.uncertain_input(102)
        other = next(item for item in self.store.inventory(7, 7)['items'] if item['id'] != identifier)
        self.controller.submit = AsyncMock(side_effect=ConnectionError('PRIVATE_ERROR'))
        result = await self.store.retry(7, 7, other['id'], 'second-operation-1', confirmed=True)
        self.assertEqual(result['state'], 'uncertain')
        self.assertNotIn('PRIVATE_ERROR', json.dumps(result))
        await self.store.retry(7, 7, other['id'], 'second-operation-1', confirmed=True)
        self.controller.submit.assert_awaited_once()

    async def test_operation_binding_and_deleted_topic_survive_retry_receipts(self):
        topic = self.controller.sessions.resolve(7, 42)
        self.uncertain_input(101, topic)
        self.uncertain_input(102, topic)
        items = self.store.inventory(7, topic)['items']
        self.controller.submit = AsyncMock(return_value=True)
        await self.store.retry(7, topic, items[0]['id'], 'operation-12345678', confirmed=True)
        with self.assertRaises(RuntimeError):
            await self.store.retry(7, topic, items[1]['id'], 'operation-12345678', confirmed=True)
        self.controller.sessions.delete(topic)
        with self.assertRaises(LookupError):
            await self.store.retry(7, topic, items[0]['id'], 'operation-12345678', confirmed=True)
        self.controller.submit.assert_awaited_once()

    async def test_scheduled_run_retry_preserves_quarantined_job_and_recurrence(self):
        run = self.controller.scheduler.add(7, 'Перевірити задачу', delay_seconds=60, interval_seconds=300, mode='run')
        reminder = self.controller.scheduler.add(7, 'Нагадати', delay_seconds=60)
        with self.db:
            self.db.execute("UPDATE jobs SET status='uncertain'")
        snapshot = [tuple(row) for row in self.db.execute('SELECT * FROM jobs')]
        items = self.store.inventory(7, 7)['items']
        self.controller.submit = AsyncMock(return_value=True)
        task = next(item for item in items if item['can_retry'])
        await self.store.retry(7, 7, task['id'], 'operation-12345678', confirmed=True)
        self.assertIn('Перевірити задачу', self.controller.submit.await_args.args[1])
        self.assertEqual([tuple(row) for row in self.db.execute('SELECT * FROM jobs')], snapshot)
        note = next(item for item in items if not item['can_retry'])
        with self.assertRaises(RuntimeError):
            await self.store.retry(7, 7, note['id'], 'second-operation-1', confirmed=True)


if __name__ == '__main__':
    unittest.main()
