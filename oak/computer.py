"""Bounded actions on an explicitly selected local X11 desktop."""

import asyncio
import contextlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile


_SCREENSHOT = '''
import json, sys
from PIL import ImageGrab
image = ImageGrab.grab(xdisplay=sys.argv[1])
image.save(sys.argv[2], format="PNG")
print(json.dumps({"width": image.width, "height": image.height}))
'''
_MODIFIERS = '''
import ctypes as c, json, sys
class Mapping(c.Structure):
    _fields_ = [("count", c.c_int), ("codes", c.POINTER(c.c_ubyte))]
x = c.CDLL("libX11.so.6")
for name, result, args in (
    ("XOpenDisplay", c.c_void_p, [c.c_char_p]),
    ("XCloseDisplay", c.c_int, [c.c_void_p]),
    ("XGetModifierMapping", c.POINTER(Mapping), [c.c_void_p]),
    ("XFreeModifiermap", c.c_int, [c.POINTER(Mapping)]),
    ("XQueryKeymap", c.c_int, [c.c_void_p, c.POINTER(c.c_ubyte)]),
):
    fn = getattr(x, name); fn.restype = result; fn.argtypes = args
display = x.XOpenDisplay(sys.argv[1].encode())
if not display:
    raise RuntimeError("Cannot read desktop modifiers")
try:
    mapping = x.XGetModifierMapping(display)
    if not mapping:
        raise RuntimeError("Cannot read modifier mapping")
    try:
        keys = (c.c_ubyte * 32)()
        x.XQueryKeymap(display, keys)
        width = mapping.contents.count
        groups = [mapping.contents.codes[i * width:(i + 1) * width] for i in range(8)]
        first = [next((code for code in group if code), 0) for group in groups]
        print(json.dumps({"modifiers": [code for code in first if code],
                          "pressed": [code for code in range(256) if keys[code // 8] & (1 << (code % 8))]}))
    finally:
        x.XFreeModifiermap(mapping)
finally:
    x.XCloseDisplay(display)
'''
_BUTTONS = {'left': '1', 'middle': '2', 'right': '3'}
_SCROLL = {'up': '4', 'down': '5', 'left': '6', 'right': '7'}


class ComputerTools:
    def __init__(self, workspace, display):
        if not isinstance(display, str) or not re.fullmatch(r':[0-9]{1,5}(?:\.[0-9]{1,3})?', display):
            raise ValueError('Computer display must be a local X11 address, such as :2.')
        self.workspace = Path(workspace).expanduser().resolve()
        self.display = display
        self._lock = asyncio.Lock()

    async def _exec(self, *args, data=None):
        process = await asyncio.create_subprocess_exec(
            *args, env={**os.environ, 'DISPLAY': self.display, 'LC_ALL': 'C.UTF-8'},
            stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            output, _ = await asyncio.wait_for(process.communicate(data), timeout=10)
        except BaseException as exc:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            await process.wait()
            if isinstance(exc, TimeoutError):
                raise RuntimeError('Desktop operation timed out.') from None
            raise
        if process.returncode:
            raise RuntimeError('Desktop operation failed on the configured X11 display.')
        return output

    async def _geometry(self):
        geometry = (await self._exec('xdotool', 'getdisplaygeometry')).split()
        if len(geometry) != 2:
            raise RuntimeError('Cannot determine desktop dimensions.')
        width, height = map(int, geometry)
        if width < 1 or height < 1:
            raise RuntimeError('Desktop dimensions must be positive.')
        return width, height

    async def _modifiers(self):
        return json.loads(await self._exec(sys.executable, '-c', _MODIFIERS, self.display))

    async def _release_typing_modifiers(self, before):
        after = await self._modifiers()
        newly_held = (set(after['pressed']) - set(before['pressed'])) & set(before['modifiers'])
        if newly_held:
            await self._exec('xdotool', 'keyup', '--', *(str(code) for code in sorted(newly_held)))

    async def _await_cleanup(self, operation):
        cleanup = asyncio.create_task(operation)
        cancelled = False
        # Keep the desktop lock until cleanup finishes, even if cancellation
        # arrives (or repeats) while a key-release/finally block runs.
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled = True
        cleanup.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _key(self, chord):
        try:
            await self._exec('xdotool', 'key', '--', chord)
        except BaseException:
            with contextlib.suppress(Exception):
                await self._await_cleanup(self._exec('xdotool', 'keyup', '--', chord))
            raise

    async def _clear_borrowed_keys(self, codes):
        try:
            await self._exec('xdotool', 'keyup', '--', *(str(code) for code in codes))
        finally:
            # Clients must consume the final key events before these mappings vanish.
            await asyncio.sleep(0.3)
            await self._exec('xmodmap', '-', data=''.join(f'keycode {code} =\n' for code in codes).encode())

    async def _type(self, text):
        before = await self._modifiers()
        try:
            for index, line in enumerate(re.split(r'\r\n|\r|\n', text)):
                if index:
                    await self._key('Return')
                while line:
                    if line.isascii():
                        await self._exec('xdotool', 'type', '--delay', '0', '--file', '-', data=line.encode())
                        break
                    table = (await self._exec('xmodmap', '-pke')).decode()
                    spare = re.findall(r'^keycode\s+(\d+)\s*=\s*$', table, re.MULTILINE)
                    if not spare:
                        raise RuntimeError('The X11 keymap has no empty keycodes for Unicode typing.')
                    bindings = {}
                    length = 0
                    for char in line:
                        if not char.isascii() and char not in bindings:
                            if len(bindings) == len(spare):
                                break
                            bindings[char] = spare[len(bindings)]
                        length += 1
                    run, line = line[:length], line[length:]
                    mapping = ''.join(f'keycode {code} = U{ord(char):04X} U{ord(char):04X}\n'
                                      for char, code in bindings.items()).encode()
                    try:
                        await self._exec('xmodmap', '-', data=mapping)
                        # xdotool's per-character temporary mapping races client caches.
                        await asyncio.sleep(0.3)
                        await self._exec('xdotool', 'type', '--delay', '0', '--file', '-', data=run.encode())
                    finally:
                        await self._await_cleanup(self._clear_borrowed_keys(list(bindings.values())))
        except BaseException:
            with contextlib.suppress(Exception):
                await self._await_cleanup(self._release_typing_modifiers(before))
            raise

    async def status(self):
        from PIL import features
        if not features.check_feature('xcb'):
            raise RuntimeError('Pillow requires X11/XCB support for desktop screenshots.')
        async with self._lock:
            width, height = await self._geometry()
        return {'display': self.display, 'width': width, 'height': height, 'available': True}

    async def _screenshot(self):
        directory = self.workspace / 'artifacts'
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not directory.resolve().is_relative_to(self.workspace):
            raise ValueError('Desktop screenshots must remain inside the workspace.')
        fd, name = tempfile.mkstemp(prefix='desktop-', suffix='.png', dir=directory)
        os.close(fd)  # mkstemp creates private mode 0600; the image writer preserves it.
        path = Path(name)
        try:
            output = await self._exec(sys.executable, '-c', _SCREENSHOT, self.display, str(path))
            if path.stat().st_size >= 8 * 1024 * 1024:
                raise RuntimeError('Desktop screenshot exceeds the 8 MiB limit.')
            size = json.loads(output)
            return {'path': str(path), 'width': size['width'], 'height': size['height'],
                    'display': self.display}
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    async def run(self, action='screenshot', x=None, y=None, end_x=None, end_y=None,
                  button='left', clicks=1, text=None, key=None, direction='down',
                  steps=3, seconds=0.5):
        if action not in {'screenshot', 'move', 'click', 'drag', 'scroll', 'type', 'key', 'wait'}:
            raise ValueError('Unknown desktop action.')
        if action in {'click', 'drag'} and button not in _BUTTONS:
            raise ValueError('Mouse button must be left, middle or right.')
        if action == 'click' and (type(clicks) is not int or clicks not in {1, 2}):
            raise ValueError('Click count must be 1 or 2.')
        if action == 'scroll' and (direction not in _SCROLL or type(steps) is not int or not 1 <= steps <= 20):
            raise ValueError('Scroll requires a direction and 1–20 steps.')
        if action == 'type' and (not isinstance(text, str) or not 1 <= len(text) <= 10000 or '\x00' in text):
            raise ValueError('Text must contain 1–10000 characters without NUL.')
        if action == 'key' and (not isinstance(key, str) or len(key) > 100
                                or not re.fullmatch(r'[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+){0,5}', key)):
            raise ValueError('Use one key or chord, such as Return or ctrl+a.')
        if action == 'wait' and (type(seconds) not in {int, float} or not math.isfinite(seconds)
                                 or not 0 <= seconds <= 2):
            raise ValueError('Desktop wait must be between 0 and 2 seconds.')

        async with self._lock:
            width, height = await self._geometry()

            def point(px, py):
                if type(px) is not int or type(py) is not int or not (0 <= px < width and 0 <= py < height):
                    raise ValueError('Coordinates must be integer pixels inside the configured desktop.')
                return str(px), str(py)

            target = None
            if action in {'move', 'drag'} or action in {'click', 'scroll'} and (x is not None or y is not None):
                target = point(x, y)
            end = point(end_x, end_y) if action == 'drag' else None
            if target is not None:
                await self._exec('xdotool', 'mousemove', *target)
            if action == 'click':
                await self._exec('xdotool', 'click', '--repeat', str(clicks), '--delay', '100', _BUTTONS[button])
            elif action == 'drag':
                # Release only the button this operation attempted to hold,
                # including a cancellation while mousedown was in flight.
                try:
                    await self._exec('xdotool', 'mousedown', _BUTTONS[button])
                    await self._exec('xdotool', 'mousemove', *end)
                    await self._exec('xdotool', 'mouseup', _BUTTONS[button])
                except BaseException:
                    with contextlib.suppress(Exception):
                        await asyncio.shield(self._exec('xdotool', 'mouseup', _BUTTONS[button]))
                    raise
            elif action == 'scroll':
                await self._exec('xdotool', 'click', '--repeat', str(steps), '--delay', '60', _SCROLL[direction])
            elif action == 'type':
                await self._type(text)
            elif action == 'key':
                chord = '+'.join('super' if part.lower() == 'meta' else part for part in key.split('+'))
                await self._key(chord)
            elif action == 'wait':
                await asyncio.sleep(seconds)
            if action not in {'screenshot', 'wait'}:
                await asyncio.sleep(0.2)
            return await self._screenshot()
