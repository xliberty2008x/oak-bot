import asyncio
import re
import unittest

from oak.computer import ComputerTools


class KeyboardDesktop(ComputerTools):
    """Model an X11 client that retains its keymap until explicitly notified."""

    def __init__(self):
        super().__init__('.', ':92')
        self.server = {'8': None, '93': None}
        self.client = self.server.copy()
        self.focus = 17
        self.notifications = []
        self.output = ''
        self.released = []
        self.block_typing = False
        self.typing_started = asyncio.Event()
        self.fail_sync = None

    async def _modifiers(self):
        return {'modifiers': [50], 'pressed': []}

    async def _sync_keymap(self, window, notify=True):
        self.notifications.append((window, notify))
        if self.fail_sync == notify:
            self.fail_sync = None
            raise RuntimeError('Synthetic client synchronization failure')
        if notify:
            self.client = self.server.copy()

    async def _exec(self, *args, data=None):
        if args == ('xdotool', 'getwindowfocus'):
            return str(self.focus).encode()
        if args == ('xmodmap', '-pke'):
            return ''.join(f'keycode {code} =\n' for code, value in self.server.items()
                           if value is None).encode()
        if args == ('xmodmap', '-'):
            for line in data.decode().splitlines():
                code, symbols = re.fullmatch(r'keycode (\d+) =(.*)', line).groups()
                self.server[code] = chr(int(symbols.split()[0][1:], 16)) if symbols.strip() else None
        elif args[:2] == ('xdotool', 'type'):
            self.typing_started.set()
            if self.block_typing:
                await asyncio.Future()
            self.output += ''.join(char for char in data.decode()
                                   if char.isascii() or char in self.client.values())
        elif args[:2] == ('xdotool', 'keyup'):
            self.released.extend(args[3:])
        elif args == ('xdotool', 'key', '--', 'Return'):
            self.output += '\n'
            self.focus = 29
        else:
            raise AssertionError(args)
        return b''


class UnicodeKeyboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_unicode_reaches_cached_client_and_both_maps_are_restored(self):
        desktop = KeyboardDesktop()
        await desktop._type('AпіB')
        self.assertEqual(desktop.output, 'AпіB')
        self.assertEqual(desktop.server, {'8': None, '93': None})
        self.assertEqual(desktop.client, desktop.server)
        self.assertEqual(desktop.released, ['008', '093'])

    async def test_cancellation_restores_original_client_after_focus_changes(self):
        desktop = KeyboardDesktop()
        desktop.block_typing = True
        task = asyncio.create_task(desktop._type('п'))
        await desktop.typing_started.wait()
        desktop.focus = 29
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(desktop.server, {'8': None, '93': None})
        self.assertEqual(desktop.client, desktop.server)
        self.assertTrue(all(window == 17 for window, _ in desktop.notifications))

    async def test_drain_failure_still_restores_server_and_client(self):
        desktop = KeyboardDesktop()
        desktop.fail_sync = False
        with self.assertRaisesRegex(RuntimeError, 'synchronization failure'):
            await desktop._type('п')
        self.assertEqual(desktop.server, {'8': None, '93': None})
        self.assertEqual(desktop.client, desktop.server)

    async def test_notification_failure_cleans_up_without_typing(self):
        desktop = KeyboardDesktop()
        desktop.fail_sync = True
        with self.assertRaisesRegex(RuntimeError, 'synchronization failure'):
            await desktop._type('п')
        self.assertEqual(desktop.output, '')
        self.assertEqual(desktop.server, {'8': None, '93': None})
        self.assertEqual(desktop.client, desktop.server)

    async def test_multiline_unicode_uses_current_focus_for_each_chunk(self):
        desktop = KeyboardDesktop()
        await desktop._type('п\nі')
        self.assertEqual(desktop.output, 'п\nі')
        self.assertEqual([window for window, notify in desktop.notifications if notify],
                         [17, 17, 29, 29])

    async def test_ascii_preserves_existing_keyboard_path(self):
        desktop = KeyboardDesktop()
        await desktop._type('plain text')
        self.assertEqual(desktop.output, 'plain text')
        self.assertEqual(desktop.notifications, [])


if __name__ == '__main__':
    unittest.main()
