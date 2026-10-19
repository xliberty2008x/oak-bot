"""Real local media checks for the bounded synthetic pilot tasks."""

import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from oak.media_pilot_tasks import TASKS, evaluate_artifact, materialize_task


class SyntheticTaskContracts(unittest.TestCase):
    def test_balanced_portable_task_set(self):
        self.assertEqual(len(TASKS), 12)
        self.assertEqual(len({task['id'] for task in TASKS}), 12)
        for split in ('dev', 'holdout'):
            tasks = [task for task in TASKS if task['split'] == split]
            self.assertEqual(len(tasks), 6)
            self.assertEqual(sum(task['kind'] == 'creation' for task in tasks), 3)
            self.assertEqual(sum(task['kind'] == 'repair' for task in tasks), 3)
            for task in tasks:
                self.assertNotIn('/home/', task['brief'])
                self.assertNotIn('http', task['brief'])
                self.assertIn(task['output_name'], ('final.png', 'final.mp4'))

    def test_materialization_is_deterministic_and_contains_no_oracle(self):
        with tempfile.TemporaryDirectory() as root:
            first = materialize_task('dev-repair-workshop', Path(root) / 'one')
            second = materialize_task('dev-repair-workshop', Path(root) / 'two')
            self.assertEqual(first['brief'], second['brief'])
            self.assertEqual(sorted(first['source_hashes'].values()), sorted(second['source_hashes'].values()))
            self.assertEqual(len(first['reference_paths']), 3)
            for path in first['reference_paths']:
                self.assertTrue(Path(path).is_absolute())
                self.assertEqual(first['source_hashes'][path], hashlib.sha256(Path(path).read_bytes()).hexdigest())
                self.assertEqual(Path(path).suffix, '.png')
            self.assertFalse((Path(root) / 'one' / first['output_name']).exists())
            with self.assertRaises(ValueError):
                materialize_task('dev-repair-workshop', Path(root) / 'one')
            self.assertEqual(set(path.name for path in (Path(root) / 'one').iterdir()), {'inputs'})

    def test_unknown_task_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / 'absent'
            with self.assertRaises(ValueError):
                materialize_task('not-a-task', workspace)
            self.assertFalse(workspace.exists())

    def test_holdout_uses_different_sources_and_geometries(self):
        with tempfile.TemporaryDirectory() as root:
            dev = materialize_task('dev-create-market', Path(root) / 'dev')
            held = materialize_task('holdout-create-science', Path(root) / 'held')
            dev_scene = next(path for path in dev['reference_paths'] if path.endswith('scene.png'))
            held_scene = next(path for path in held['reference_paths'] if path.endswith('scene.png'))
            self.assertNotEqual(dev['source_hashes'][dev_scene], held['source_hashes'][held_scene])
            with Image.open(dev_scene) as first, Image.open(held_scene) as second:
                self.assertNotEqual(first.size, second.size)
            self.assertNotEqual(dev['brief'], held['brief'])


class ImageStructuralChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name)
        self.output = self.workspace / 'final.png'

    def image(self):
        image = Image.new('RGB', (960, 640), '#f7f1e4')
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((100, 100, 860, 540), radius=40, fill='#176e7b')
        return image

    def check(self):
        return evaluate_artifact('dev-create-market', self.output, self.workspace)

    def test_structural_pass_explicitly_does_not_assert_semantics(self):
        # A shape has adequate structure, but is not the requested event poster.
        # The independent reviewer must still reject that semantic failure.
        self.image().save(self.output)
        result = self.check()
        self.assertEqual(result['status'], 'pass')
        self.assertTrue(result['structural_only'])
        self.assertIn('text accuracy', result['limitation'])

    def test_wrong_dimensions_clipping_transparency_and_blank_fail(self):
        cases = {}
        cases['dimensions'] = self.image().resize((640, 640))
        clipped = self.image()
        ImageDraw.Draw(clipped).rectangle((0, 0, 180, 120), fill='black')
        cases['clear_gutter'] = clipped
        transparent = self.image().convert('RGBA')
        transparent.putalpha(0)
        cases['opaque'] = transparent
        cases['nonblank_composition'] = Image.new('RGB', (960, 640), '#f7f1e4')
        for criterion, image in cases.items():
            with self.subTest(criterion=criterion):
                image.save(self.output)
                result = self.check()
                self.assertEqual(result['status'], 'fail')
                self.assertIn(criterion, [item['criterion'] for item in result['checks'] if item['status'] == 'fail'])

    def test_real_format_and_decoding_are_checked(self):
        self.image().save(self.output, format='JPEG')
        self.assertEqual(self.check()['status'], 'fail')
        self.output.write_bytes(b'not an image')
        self.assertEqual(self.check()['status'], 'fail')

    def test_missing_outside_and_symlink_artifacts_fail(self):
        self.assertEqual(self.check()['status'], 'fail')
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / 'external.png'
            self.image().save(external)
            self.assertEqual(evaluate_artifact('dev-create-market', external, self.workspace)['status'], 'fail')
            self.output.symlink_to(external)
            self.assertEqual(self.check()['status'], 'fail')


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'requires actual local FFmpeg')
class VideoStructuralChecks(unittest.TestCase):
    def test_every_task_materializes_only_complete_synthetic_inputs(self):
        with tempfile.TemporaryDirectory() as root:
            for spec in TASKS:
                with self.subTest(task=spec['id']):
                    workspace = Path(root) / spec['id']
                    task = materialize_task(spec['id'], workspace)
                    self.assertEqual(len(task['reference_paths']), 3 if spec['kind'] == 'repair' else 2)
                    for path in task['reference_paths']:
                        self.assertTrue(Path(path).is_file())
                        self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), task['source_hashes'][path])
                    if spec['kind'] == 'repair':
                        seed = next(path for path in task['reference_paths'] if 'broken-seed.' in path)
                        self.assertEqual(evaluate_artifact(spec['id'], seed, workspace)['status'], 'fail')

    def test_repair_video_seed_decodes_but_fails_motion(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            task = materialize_task('dev-repair-comet', workspace)
            seed = next(path for path in task['reference_paths'] if path.endswith('.mp4'))
            result = evaluate_artifact(task['task_id'], seed, workspace)
            statuses = {item['criterion']: item['status'] for item in result['checks']}
            self.assertEqual(statuses['decoded_frames'], 'pass')
            self.assertEqual(statuses['dimensions'], 'pass')
            self.assertEqual(statuses['temporal_change'], 'fail')
            self.assertEqual(result['status'], 'fail')

    def test_actual_motion_and_exact_video_requirements(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            for index in range(36):
                frame = Image.new('RGB', (640, 360), '#e8f5ff')
                draw = ImageDraw.Draw(frame)
                x = 30 + 14 * index
                draw.ellipse((x, 140, x + 60, 200), fill='#d4543c')
                frame.save(workspace / f'frame-{index:03d}.png')
            output = workspace / 'final.mp4'
            subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-framerate', '12',
                            '-i', str(workspace / 'frame-%03d.png'), '-c:v', 'libx264',
                            '-threads', '1', '-pix_fmt', 'yuv420p', str(output)],
                           check=True, capture_output=True, timeout=30)
            self.assertEqual(evaluate_artifact('dev-create-kite', output, workspace)['status'], 'pass')
            mismatch = evaluate_artifact('holdout-create-trail', output, workspace)
            self.assertEqual(mismatch['status'], 'fail')
            failed = {item['criterion'] for item in mismatch['checks'] if item['status'] == 'fail'}
            self.assertTrue({'dimensions', 'duration', 'frame_rate'} <= failed)
            with patch('oak.media_pilot_tasks.shutil.which', return_value=None):
                result = evaluate_artifact('dev-create-kite', output, workspace)
            self.assertEqual(result['status'], 'unverified')


if __name__ == '__main__':
    unittest.main()
