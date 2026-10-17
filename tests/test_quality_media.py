"""Actual decoded evidence and snapshot integrity, independent of model verdicts."""

import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from oak.executor_worker import FileTransfers, OPERATIONS
from oak.quality_media import codec_environment, inspect_media, EVIDENCE_BYTES, _local_motion_regions, _sheet


HAS_IMAGING = all(importlib.util.find_spec(name) for name in ('PIL', 'numpy'))


@unittest.skipUnless(HAS_IMAGING, 'Provisioned Pillow and numpy are required')
class QualityMediaTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.files = FileTransfers({'workspace': str(self.root), 'runtime_home': str(self.root / 'runtime')})
        self.addCleanup(self.files.close_all)

    def inspect(self, path):
        return inspect_media(self.files, str(path), hashlib.sha256(path.read_bytes()).hexdigest())

    def image(self, name='picture.bin'):
        from PIL import Image
        path = self.root / name
        Image.new('RGB', (100, 60), (20, 70, 110)).save(path, format='PNG')
        return path

    def test_image_detected_from_bytes_and_actual_pixels_returned(self):
        from PIL import Image
        path = self.image()
        result = self.inspect(path)
        self.assertEqual(result['type'], 'image')
        self.assertEqual(result['decode_status'], 'complete')
        self.assertEqual(result['dimensions'], {'width': 100, 'height': 60})
        self.assertEqual(result['size'], path.stat().st_size)
        self.assertEqual(len(result['evidence']), 1)
        with Image.open(io.BytesIO(base64.b64decode(result['evidence'][0]['image_url'].split(',', 1)[1]))) as decoded:
            self.assertEqual(decoded.size, (100, 60))
            self.assertLess(sum(abs(a-b) for a, b in zip(decoded.getpixel((30, 20)), (20, 70, 110))), 8)

    def test_changed_bytes_rejected_before_decoder(self):
        path = self.image()
        old_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        path.write_bytes(b'changed')
        with patch('oak.quality_media._image') as decode:
            with self.assertRaises(ValueError):
                inspect_media(self.files, str(path), old_hash)
            decode.assert_not_called()
        self.assertEqual(self.files.handles, {})

    def test_expected_hash_required_and_no_symlinks(self):
        path = self.image()
        for value in (None, '', True, 'x' * 64):
            with self.assertRaises(ValueError):
                inspect_media(self.files, str(path), value)
        link = self.root / 'link.png'
        link.symlink_to(path)
        with self.assertRaises(OSError):
            self.inspect(link)

    def test_renamed_text_is_unsupported_and_malformed_media_fails(self):
        path = self.root / 'movie.mp4'
        path.write_bytes(b'this is not a video')
        result = self.inspect(path)
        self.assertEqual(result['type'], 'unsupported')
        self.assertEqual(result['evidence'], [])
        path.write_bytes(b'\x89PNG\r\n\x1a\ncorrupt')
        result = self.inspect(path)
        self.assertEqual(result['decode_status'], 'failed')
        self.assertEqual(result['evidence'], [])

    def test_codec_environment_cannot_inherit_workspace_library_path(self):
        with patch.dict('os.environ', {'LD_LIBRARY_PATH': '/tmp/untrusted', 'LD_PRELOAD': '/tmp/inject.so'}):
            env = codec_environment()
        self.assertNotIn('/tmp', env['LD_LIBRARY_PATH'])
        self.assertNotIn('LD_PRELOAD', env)
        self.assertIn('quality.inspect', OPERATIONS)

    def test_all_frames_contribute_and_brief_local_motion_is_selected(self):
        import numpy as np
        if not Path('/usr/bin/ffmpeg').is_file():
            self.skipTest('Distro ffmpeg is unavailable')
        path = self.root / 'animation.data'
        frames = []
        for index in range(50):
            frame = np.full((96, 128, 3), 200, dtype=np.uint8)
            # A large moving region must not hide a tiny two-frame change
            # between uniform samples (e.g. a blink amid moving hair).
            frame[45:90, 5:110] = 50 + (index % 10) * 10
            if index in (25, 26):
                frame[22:24, 51:55] = (0, 0, 0)
            frames.append(frame.tobytes())
        subprocess.run(['/usr/bin/ffmpeg', '-nostdin', '-v', 'error', '-f', 'rawvideo',
                        '-pix_fmt', 'rgb24', '-s', '128x96', '-r', '25', '-i', 'pipe:0',
                        '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-f', 'mp4', str(path)],
                       input=b''.join(frames), check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, env=codec_environment())
        result = self.inspect(path)
        self.assertEqual(result['type'], 'video')
        self.assertEqual(result['decode_status'], 'complete')
        self.assertEqual(result['decoded_frames'], 50)
        self.assertAlmostEqual(result['duration'], 2, places=2)
        self.assertTrue(set((25, 26)) & set(result['sampled_frame_indices']))
        self.assertGreater(result['max_local_frame_change'], 100)
        self.assertTrue(any('changing region' in item['label'] for item in result['evidence']))
        self.assertLess(len(json.dumps(result)), EVIDENCE_BYTES + 100_000)
        self.assertEqual(self.files.handles, {})

    def test_decoder_failure_does_not_leave_positive_coverage(self):
        path = self.root / 'video'
        path.write_bytes(b'\x00\x00\x00\x20ftyp' + b'\x00' * 80)
        with patch('oak.quality_media._run', side_effect=subprocess.TimeoutExpired('ffprobe', 20)):
            result = self.inspect(path)
        self.assertEqual(result['type'], 'video')
        self.assertEqual(result['decode_status'], 'failed')
        self.assertEqual(result['evidence'], [])
        self.assertEqual(self.files.handles, {})

    def test_localized_transition_detail_survives_broad_lower_amplitude_motion(self):
        import numpy as np
        from PIL import Image
        before = np.full((600, 800, 3), 100, dtype=np.uint8)
        after = before.copy()
        after[250:550, 100:700] = 110  # Broad drifting region.
        after[105:125, 480:500] = 200  # Small brief transition elsewhere.
        frames = {0: Image.fromarray(before), 10: Image.fromarray(before), 11: Image.fromarray(after)}
        regions = _local_motion_regions(frames, [11])
        self.assertTrue(regions)
        left, top, right, bottom = regions[0]['bounds']
        self.assertTrue(left <= 490 < right and top <= 115 < bottom)
        self.assertLess((right - left) * (bottom - top), 800 * 600 * .04)
        self.assertGreater(regions[0]['score'], 80)

    def test_detail_contact_sheet_actually_magnifies_small_regions(self):
        from PIL import Image
        frame = Image.new('RGB', (20, 20), (200, 20, 40))
        result = _sheet([frame], ['Localized transition'], cell_width=500)
        self.assertEqual(result.getpixel((10, 10)), (200, 20, 40))
        self.assertEqual(result.getpixel((490, 490)), (200, 20, 40))


if __name__ == '__main__':
    unittest.main()
