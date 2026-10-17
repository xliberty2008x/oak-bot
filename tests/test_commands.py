import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from oak.commands import command_names, help_text, menu_commands, register_commands
from oak.controller import Controller
from oak.telegram import CONTROL_COMMANDS, TelegramError


class CommandsTests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_is_owner_scoped_and_partial_failure_is_cosmetic(self):
        gateway = SimpleNamespace(_api=AsyncMock(side_effect=[TelegramError('setMyCommands', 400), True]))
        with patch('builtins.print') as diagnostic:
            self.assertEqual(await register_commands(gateway, {8, 7}), 1)
        calls = gateway._api.await_args_list
        self.assertEqual([c.args[1]['scope'] for c in calls], [{'type': 'chat', 'chat_id': 7}, {'type': 'chat', 'chat_id': 8}])
        self.assertNotIn('web', {r['command'] for r in calls[0].args[1]['commands']})
        self.assertNotIn('7', diagnostic.call_args.args[0])
        self.assertEqual(CONTROL_COMMANDS | {'/stop', '/new', '/approve', '/deny', '/answer'}, command_names())

    async def test_help_and_menu_cover_supported_commands_with_telegram_limits(self):
        rows = menu_commands()
        self.assertLessEqual(len(rows), 100)
        self.assertEqual(len(rows), len({r['command'] for r in rows}))
        for row in rows:
            self.assertRegex(row['command'], r'^[a-z0-9_]{1,32}$')
            self.assertTrue(1 <= len(row['description']) <= 256)
            self.assertIn('/' + row['command'], help_text())
        self.assertNotIn('/web', help_text(False))

    async def test_start_has_onboarding_and_scoped_app_link_without_starting_a_turn(self):
        with tempfile.TemporaryDirectory() as temporary:
            c = Controller(AsyncMock(), ':memory:', Path(temporary), AsyncMock())
            self.addCleanup(c.close)
            c.web = SimpleNamespace(public_url='https://oak.example')
            topic = c.sessions.resolve(7, 12)
            text = await c.command(topic, '/start', 1)
            self.assertIn('/help', text)
            self.assertIn('голосове', text)
            self.assertNotEqual(text, await c.command(topic, '/help', 2))
            event = c.emit.await_args.args[1]
            self.assertEqual(event['name'], 'web_app_link')
            self.assertIn('topic%3A12', event['value']['url'])
            c.client.request.assert_not_awaited()
            c.web = None
            c.emit.reset_mock()
            await c.command(topic, '/start', 3)
            c.emit.assert_not_awaited()
