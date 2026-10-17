"""Chat choices preserve catalogue validation, idle-only settings and scope."""

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from oak.chat_models import BUSY, FAILED, UNAVAILABLE, ChatModels, chat_models
from oak.controller import Controller


class ChatModelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client, self.emit = AsyncMock(), AsyncMock()
        self.controller = Controller(self.client, ':memory:', '.', self.emit)
        self.addCleanup(self.controller.close)
        self.catalog = [dict(model='gpt-6.1-sol', display_name='Default', effort='low', efforts=['low'], turbo_available=True),
                        dict(model='synthetic-model', display_name='Synthetic', effort='medium', efforts=['medium'], turbo_available=True)]
        self.controller.model_catalog = AsyncMock(side_effect=lambda scope: [dict(row) for row in self.catalog])
        self.models = chat_models(self.controller)

    async def token(self, scope=7, label='Synthetic'):
        self.assertIsNone(await self.models.open(scope))
        return next(row[0]['token'] for row in self.emit.await_args.args[1]['value']['rows'] if row[0]['label'] == label)

    async def test_model_choice_is_owned_single_use_and_preserves_default_elsewhere(self):
        topic = self.controller.sessions.resolve(7, 42)
        self.controller.threads[topic] = 'existing-thread'
        token = await self.token(topic)
        self.assertEqual(await self.models.choose(8, token), UNAVAILABLE)
        first, duplicate = await asyncio.gather(self.models.choose(topic, token), self.models.choose(topic, token))
        self.assertIn('оновлено', first)
        self.assertIsInstance(duplicate, str)
        self.assertEqual(await self.models.choose(topic, token), first)
        self.assertEqual(self.controller.model_settings(topic), {'model': 'synthetic-model', 'effort': 'medium'})
        self.assertEqual(self.controller.model_for(7), 'gpt-6.1-sol')
        self.assertEqual(self.controller.threads[topic], 'existing-thread')
        self.assertEqual(self.controller.model_catalog.await_count, 2)  # menu + one validation
        self.client.request.assert_not_called()

    async def test_same_model_has_no_setting_mutation_or_extra_catalogue_request(self):
        token = await self.token(label='✓ Default')
        result = await self.models.choose(7, token)
        self.assertIn('уже вибрана', result)
        self.assertEqual(self.controller.db.execute('SELECT count(*) FROM conversation_models').fetchone()[0], 0)
        self.assertEqual(self.controller.model_catalog.await_count, 1)

    async def test_real_telegram_ingest_opens_model_menu_without_self_busy_intake(self):
        from oak.telegram import TelegramGateway

        with tempfile.TemporaryDirectory() as folder:
            gateway = TelegramGateway(self.controller, 'synthetic-token', {7}, Path(folder))
            self.addCleanup(gateway.db.close)
            gateway._api = AsyncMock(return_value=True)
            await gateway._ingest({'update_id': 100, 'message': {'message_id': 10,
                'chat': {'id': 7, 'type': 'private'}, 'from': {'id': 7}, 'text': '/model',
                'entities': [{'type': 'bot_command', 'offset': 0, 'length': 6}]}})
            self.assertEqual(gateway.db.execute('SELECT count(*) FROM intake').fetchone()[0], 0)
            self.assertEqual(gateway.offset, 101)
            self.controller.model_catalog.assert_awaited_once_with(7)
            self.assertEqual(self.emit.await_args.args[1]['name'], 'model_choices')
            self.assertEqual(len(self.emit.await_args.args[1]['value']['rows']), 2)
            self.assertEqual(self.controller.db.execute('SELECT count(*) FROM inputs').fetchone()[0], 0)
            self.assertEqual(self.controller.model_for(7), 'gpt-6.1-sol')
            self.client.request.assert_not_called()

    async def test_stale_expired_active_and_deleted_choices_cannot_apply(self):
        token = await self.token()
        self.controller.threads[7] = 'changed-thread'
        self.assertEqual(await self.models.choose(7, token), UNAVAILABLE)
        token = await self.token()
        self.controller.db.execute('UPDATE chat_model_choices SET expires=0 WHERE token=?', (token,))
        self.assertEqual(await self.models.choose(7, token), UNAVAILABLE)
        token = await self.token()
        self.controller.active[7] = 'active'
        self.assertEqual(await self.models.choose(7, token), BUSY)
        self.assertEqual(await self.models.open(7), BUSY)
        self.controller.active.clear()
        topic = self.controller.sessions.resolve(7, 42)
        token = await self.token(topic)
        self.controller.sessions.delete(topic)
        self.assertEqual(await self.models.choose(topic, token), UNAVAILABLE)
        self.assertEqual(self.controller.model_for(7), 'gpt-6.1-sol')

    async def test_snapshot_guard_catches_changes_during_catalogue_validation(self):
        token = await self.token()
        async def changed_catalog(scope):
            self.controller.threads[scope] = 'new-thread'
            return self.catalog
        self.controller.model_catalog.side_effect = changed_catalog
        self.assertEqual(await self.models.choose(7, token), FAILED)
        self.assertEqual(self.controller.model_for(7), 'gpt-6.1-sol')

    async def test_removed_model_and_turbo_incompatibility_keep_current_settings(self):
        token = await self.token()
        self.catalog.pop()
        self.assertEqual(await self.models.choose(7, token), FAILED)
        self.catalog.append(dict(model='synthetic-model', display_name='Synthetic', effort='medium', efforts=['medium'], turbo_available=False))
        with self.controller.db:
            self.controller.db.execute("INSERT INTO conversation_models VALUES (7,'gpt-6.1-sol','low','fast')")
        await self.models.open(7)
        rows = self.emit.await_args.args[1]['value']['rows']
        self.assertEqual([row[0]['label'] for row in rows], ['✓ Default'])
        self.assertEqual(self.controller.service_tier_for(7), 'fast')
        self.assertEqual(self.controller.model_for(7), 'gpt-6.1-sol')

    async def test_unavailable_catalogue_does_not_change_model_or_leak_provider_errors(self):
        self.controller.model_catalog.side_effect = RuntimeError('PRIVATE_PROVIDER_ERROR')
        result = await self.models.open(7)
        self.assertIn('недоступний', result)
        self.assertNotIn('PRIVATE_', result)
        self.emit.assert_not_called()
        self.assertEqual(self.controller.model_for(7), 'gpt-6.1-sol')

    async def test_pagination_tokens_are_bounded_and_invalidate_old_choices(self):
        self.catalog = [dict(model='choice-' + str(i), display_name='Model ' + str(i), effort='low', efforts=['low'], turbo_available=False)
                        for i in range(45)]
        await self.models.open(7)
        first = self.emit.await_args.args[1]['value']['rows']
        self.assertEqual(len(first), 21)
        self.assertEqual(await self.models.choose(7, first[-1][0]['token']), 'Показано іншу сторінку моделей.')
        second = self.emit.await_args.args[1]['value']['rows']
        self.assertEqual(len(second), 22)
        self.assertEqual(second[0][0]['label'], 'Model 20')
        self.assertEqual(await self.models.choose(7, first[0][0]['token']), UNAVAILABLE)
        self.assertTrue(all(len(row) == 1 and len(row[0]['token']) == 32 and len(row[0]['label']) <= 60 for row in second))

    async def test_restart_does_not_repeat_a_choice_with_unknown_outcome(self):
        token = await self.token()
        with self.controller.db:
            self.controller.db.execute("UPDATE chat_model_choices SET state='applying' WHERE token=?", (token,))
        reopened = ChatModels(self.controller)
        self.assertEqual(await reopened.choose(7, token), UNAVAILABLE)
        self.assertEqual(self.controller.model_catalog.await_count, 1)

    async def test_model_command_uses_catalogue_menu_without_starting_a_turn(self):
        self.assertIsNone(await self.controller.command(7, '/model', 100))
        self.assertEqual(self.emit.await_args.args[1]['name'], 'model_choices')
        self.assertEqual(self.controller.active, {})
        self.client.request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
