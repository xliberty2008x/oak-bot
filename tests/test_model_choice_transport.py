import asyncio
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import test_telegram_ux as fixtures
from oak.events import validate_event


class ModelChoiceTransportTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.TelegramUXTests.setUp
    finish = fixtures.TelegramUXTests.finish

    def event(self):
        return {'type': 'CUSTOM', 'name': 'model_choices', 'threadId': 'thread', 'runId': 'notice-menu',
                'value': {'text': 'Обери модель', 'rows': [[{'label': 'Модель', 'token': 'a' * 32}]]}}

    async def test_menu_is_durable_and_callback_respects_owner_and_topic(self):
        event = self.event()
        validate_event(event)
        await self.g.emit(7, event)
        await self.g.emit(7, event)
        self.assertEqual(len(self.g._deliveries()), 1)
        await self.g._render_delivery(self.g._deliveries()[0])
        self.assertEqual(self.g._deliver.call_args.args[1]['reply_markup']['inline_keyboard'][0][0]['callback_data'], 'model:' + 'a' * 32)
        choose = AsyncMock(return_value='Модель вибрано.')
        module = SimpleNamespace(chat_models=lambda c: SimpleNamespace(choose=choose))
        update = {'update_id': 1, 'callback_query': {'id': 'cb', 'data': 'model:' + 'a' * 32,
            'from': {'id': 8}, 'message': {'chat': {'id': 7, 'type': 'private'}, 'message_thread_id': 12}}}
        with patch.dict(sys.modules, {'oak.chat_models': module}):
            await self.g.handle_update(update)
            choose.assert_not_awaited()
            update['update_id'] = 2
            update['callback_query']['from']['id'] = 7
            await self.g.handle_update(update)
        choose.assert_awaited_once_with(self.c.sessions.resolve(7, 12), 'a' * 32)
        self.assertEqual(self.g._api.call_args.args[1]['text'], 'Модель вибрано.')

    def test_menu_limits_reject_arbitrary_callback_and_duplicate_tokens(self):
        for rows in ([], [[{'label': 'Bad', 'token': 'approve:secret'}]],
                     [[{'label': 'X', 'token': 'a' * 32}]] * 2,
                     [[{'label': 'X', 'token': 'a' * 32, 'url': 'https://invalid.test'}]]):
            event = self.event()
            event['value']['rows'] = rows
            with self.assertRaises(ValueError):
                validate_event(event)
