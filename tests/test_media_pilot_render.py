"""Real local sandbox checks; no model or gateway is started."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from oak.media_pilot_render import render_python


class MediaPilotRenderTests(unittest.TestCase):
    def test_absent_sandbox_fails_closed_and_source_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory, patch(
                'oak.media_pilot_render.shutil.which', return_value=None):
            result = render_python(Path(directory), "raise AssertionError('must not run')")
            self.assertFalse(result['ok'])
            self.assertEqual(result['error'], 'render_sandbox_unavailable')
            self.assertEqual(list(Path(directory).iterdir()), [])
        result = render_python(Path('/'), 'x' * 65537)
        self.assertEqual(result['error'], 'invalid_render_request')

    @unittest.skipUnless(shutil.which('bwrap'), 'bubblewrap not installed')
    def test_real_sandbox_hides_host_siblings_environment_and_network(self):
        with tempfile.TemporaryDirectory(prefix='oak-pilot-isolation-') as directory:
            root = Path(directory)
            workspace = root / 'workspace'
            workspace.mkdir()
            canary = root / 'private-oracle.txt'
            canary.write_text('PRIVATE_RENDER_CANARY')
            source = f'''import json, os, pathlib, socket
assert not pathlib.Path({str(canary)!r}).exists()
assert not pathlib.Path('/home/ubuntu').exists()
assert 'OAK_PILOT_PRIVATE_CANARY' not in os.environ
try:
    socket.create_connection(('1.1.1.1', 443), timeout=1)
except OSError:
    pass
else:
    raise AssertionError('network available')
try:
    pathlib.Path('/usr/oak-pilot-write-probe').write_text('bad')
except OSError:
    pass
else:
    raise AssertionError('system roots writable')
pathlib.Path('result.txt').write_text('safe')
pathlib.Path('symlink.txt').symlink_to({str(canary)!r})
print(json.dumps({{'isolated': True, 'cwd': str(pathlib.Path.cwd())}}))
'''
            with patch.dict(os.environ, {'OAK_PILOT_PRIVATE_CANARY': 'not_for_worker'}):
                result = render_python(workspace, source)
            self.assertTrue(result['ok'], result)
            self.assertEqual(json.loads(result['stdout']), {'isolated': True, 'cwd': '/workspace'})
            self.assertEqual(result['files'], ['result.txt'])
            self.assertEqual(canary.read_text(), 'PRIVATE_RENDER_CANARY')

    @unittest.skipUnless(shutil.which('bwrap'), 'bubblewrap not installed')
    def test_source_inputs_are_read_only_and_outputs_remain_writable(self):
        with tempfile.TemporaryDirectory(prefix='oak-pilot-inputs-') as directory:
            workspace = Path(directory)
            inputs = workspace / 'inputs'
            inputs.mkdir()
            source = inputs / 'source.txt'
            source.write_text('immutable input')
            code = '''import pathlib
source = pathlib.Path('inputs/source.txt')
assert source.read_text() == 'immutable input'
try:
    source.write_text('mutated')
except OSError:
    pass
else:
    raise AssertionError('source writable')
pathlib.Path('output.txt').write_text('rendered')
'''
            result = render_python(workspace, code)
            self.assertTrue(result['ok'], result)
            self.assertEqual(source.read_text(), 'immutable input')
            self.assertEqual((workspace / 'output.txt').read_text(), 'rendered')
            source.unlink()
            inputs.rmdir()
            inputs.symlink_to(workspace, target_is_directory=True)
            self.assertEqual(render_python(workspace, "print('must not run')")['error'],
                             'render_sandbox_unavailable')

    @unittest.skipUnless(shutil.which('bwrap'), 'bubblewrap not installed')
    def test_timeout_and_output_are_bounded(self):
        with tempfile.TemporaryDirectory(prefix='oak-pilot-timeout-') as directory:
            workspace = Path(directory)
            output = render_python(workspace, "print('x' * 50000)")
            self.assertTrue(output['ok'], output)
            self.assertLessEqual(len(output['stdout']), 12000)
            result = render_python(workspace, 'import time; time.sleep(10)', timeout=0.3)
            self.assertFalse(result['ok'])
            self.assertEqual(result['error'], 'render_timeout')
            self.assertLess(result['elapsed_seconds'], 3)

    @unittest.skipUnless(shutil.which('bwrap') and importlib.util.find_spec('PIL'),
                         'bubblewrap and Pillow-enabled interpreter required')
    def test_pillow_font_and_ffmpeg_run_inside_sandbox(self):
        with tempfile.TemporaryDirectory(prefix='oak-pilot-media-') as directory:
            source = '''from PIL import Image, ImageDraw, ImageFont
import subprocess
image = Image.new('RGB', (320, 180), 'navy')
font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 20)
ImageDraw.Draw(image).text((10, 40), 'Oak Україна', font=font, fill='white')
image.save('card.png')
subprocess.run(['/usr/bin/ffmpeg', '-v', 'error', '-threads', '1', '-loop', '1',
                '-i', 'card.png', '-t', '0.2', '-vf', 'format=yuv420p',
                '-threads', '1', 'preview.mp4'], check=True)
print('LOCAL_MEDIA_OK')
'''
            result = render_python(Path(directory), source)
            self.assertTrue(result['ok'], result)
            self.assertEqual(result['files'], ['card.png', 'preview.mp4'])
            self.assertIn('LOCAL_MEDIA_OK', result['stdout'])


if __name__ == '__main__':
    unittest.main()
