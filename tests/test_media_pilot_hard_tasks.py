"""Input isolation and independent geometry checks for the second pilot."""

import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from PIL import Image, ImageChops

from oak.media_pilot_hard_tasks import TASKS, materialize_task, quality_spec, reference_frame


def _decode(path, size):
    result = subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-filter_threads', '1', '-filter_complex_threads', '1',
                             '-threads', '1', '-i', str(path),
                             '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-threads', '1', 'pipe:1'],
                            check=True, capture_output=True, timeout=30)
    frame_bytes = size[0] * size[1] * 3
    return [Image.frombytes('RGB', tuple(size), result.stdout[i:i + frame_bytes])
            for i in range(0, len(result.stdout), frame_bytes)]


@unittest.skipUnless(shutil.which('ffmpeg'), 'synthetic repair inputs require local FFmpeg')
class HardTaskContracts(unittest.TestCase):
    def test_balanced_new_tasks_and_disclosed_runtime_contract(self):
        from oak.media_pilot_tasks import TASKS as FIRST_TASKS
        self.assertEqual(len(TASKS), 12)
        self.assertEqual(len({task['id'] for task in TASKS}), 12)
        self.assertFalse({task['id'] for task in TASKS} & {task['id'] for task in FIRST_TASKS})
        for split in ('dev', 'holdout'):
            tasks = [task for task in TASKS if task['split'] == split]
            self.assertEqual(len(tasks), 6)
            self.assertEqual(sum(task['kind'] == 'creation' for task in tasks), 3)
            self.assertEqual(sum(task['output_name'] == 'final.mp4' for task in tasks), 4)
            for task in tasks:
                self.assertIn('inputs/scene.json', task['brief'])
                self.assertNotIn('/home/', task['brief'])
                self.assertNotIn('http', task['brief'])

    def test_all_inputs_are_hashed_no_oracle_output_and_references_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            for public in TASKS:
                with self.subTest(task=public['id']):
                    workspace = Path(root) / public['id']
                    task = materialize_task(public['id'], workspace)
                    self.assertEqual(len(task['reference_paths']), 3 if public['kind'] == 'repair' else 2)
                    expected = {str(path) for path in (workspace / 'inputs').iterdir()}
                    self.assertEqual(set(task['source_hashes']), expected)
                    self.assertEqual(task['specification_path'], str(workspace / 'inputs' / 'scene.json'))
                    self.assertIn(task['specification_path'], task['source_hashes'])
                    self.assertTrue({str(path) for path in map(Path, task['reference_paths'])} <= expected)
                    for path, identity in task['source_hashes'].items():
                        self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), identity)
                    self.assertFalse((workspace / task['output_name']).exists())
                    self.assertEqual({path.name for path in workspace.iterdir()}, {'inputs'})
                    spec = quality_spec(public['id'], workspace)
                    contract = json.loads((workspace / 'inputs' / 'scene.json').read_text())
                    self.assertEqual(spec['contract'], contract)
                    self.assertEqual(spec['frame_count'], 36 if spec['duration'] else 1)
                    self.assertTrue(spec['regions'])
                    for region in spec['regions']:
                        left, top, right, bottom = region['box']
                        self.assertLess(left, right)
                        self.assertLess(top, bottom)
                        self.assertGreaterEqual(min(left, top), 0)
                        self.assertLessEqual(right, spec['size'][0])
                        self.assertLessEqual(bottom, spec['size'][1])
                    with Image.open(spec['assets']['scene']) as source:
                        self.assertEqual(list(source.size), spec['size'])
                    self.assertEqual(reference_frame(public['id'], workspace).size, tuple(spec['size']))

    def test_deterministic_sources_and_fresh_workspace_requirement(self):
        with tempfile.TemporaryDirectory() as root:
            first = materialize_task('dev-repair-owl-blink', Path(root) / 'first')
            second = materialize_task('dev-repair-owl-blink', Path(root) / 'second')
            self.assertEqual(first['brief'], second['brief'])
            self.assertEqual(sorted(first['source_hashes'].values()), sorted(second['source_hashes'].values()))
            with self.assertRaises(ValueError):
                materialize_task('dev-repair-owl-blink', Path(root) / 'first')
            with self.assertRaises(ValueError):
                materialize_task('no-such-task', Path(root) / 'not-created')
            self.assertFalse((Path(root) / 'not-created').exists())
            with self.assertRaises(ValueError):
                quality_spec('holdout-repair-fox-blink', Path(root) / 'first')
            with self.assertRaises(ValueError):
                reference_frame('dev-repair-owl-blink', Path(root) / 'first', 36)

    def test_heldout_geometry_sources_and_timing_are_new(self):
        with tempfile.TemporaryDirectory() as root:
            for family in ('blink', 'pendulum', 'occlusion', 'orbit', 'labels', 'archival'):
                specs = []
                for split in ('dev', 'holdout'):
                    public = next(task for task in TASKS if task['split'] == split and family in task['id']) if family not in {'labels', 'archival'} else next(
                        task for task in TASKS if task['split'] == split and ('collection' in task['id'] if family == 'labels' else 'archive' in task['id']))
                    workspace = Path(root) / public['id']
                    materialize_task(public['id'], workspace)
                    specs.append(quality_spec(public['id'], workspace))
                self.assertNotEqual(specs[0]['size'], specs[1]['size'])
                for key in ('scene', 'atlas'):
                    self.assertNotEqual(Path(specs[0]['assets'][key]).read_bytes(), Path(specs[1]['assets'][key]).read_bytes())
                if specs[0]['duration']:
                    self.assertNotEqual(specs[0]['contract']['animation'], specs[1]['contract']['animation'])


@unittest.skipUnless(shutil.which('ffmpeg'), 'synthetic repair inputs require local FFmpeg')
class IndependentLayerGeometry(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)

    def make(self, task_id):
        materialize_task(task_id, self.workspace)
        return quality_spec(task_id, self.workspace)

    def test_blink_closes_only_public_eye_rectangles_and_source_returns(self):
        task_id = 'dev-repair-owl-blink'
        spec = self.make(task_id)
        original = Image.open(spec['assets']['scene']).convert('RGB')
        closed = reference_frame(task_id, self.workspace, 10)
        atlas = Image.open(spec['assets']['atlas']).convert('RGB')
        expected = original.copy()
        for eye in spec['contract']['animation']['eyes']:
            expected.paste(atlas.crop(eye['closed_atlas_box']), tuple(eye['box'][:2]))
        self.assertIsNone(ImageChops.difference(closed, expected).getbbox())
        self.assertIsNone(ImageChops.difference(reference_frame(task_id, self.workspace, 0), original).getbbox())
        self.assertIsNone(ImageChops.difference(reference_frame(task_id, self.workspace, 35), original).getbbox())
        for eye in spec['contract']['animation']['eyes']:
            self.assertIsNotNone(ImageChops.difference(closed.crop(eye['box']), original.crop(eye['box'])).getbbox())
        # Every intermediate aperture keeps the original pupil in place instead
        # of exposing disconnected bands of a closed-eye seam.
        animation = spec['contract']['animation']
        self.assertEqual(animation['type'], 'blink-local-curved-aperture')
        for n in (7, 8, 9, 11, 12, 13, 24, 25, 26, 28, 29, 30):
            intermediate = reference_frame(task_id, self.workspace, n)
            for eye in animation['eyes']:
                x0, y0, x1, y1 = eye['box']
                self.assertEqual(intermediate.getpixel((x0 + 40, y0 + 27)),
                                 original.getpixel((x0 + 40, y0 + 27)))
                # Skin above/below the curved opening uses the supplied fill,
                # while no unrelated face or print pixel changes.
                self.assertEqual(intermediate.getpixel((x0 + 40, y0 + 4)),
                                 atlas.getpixel((40, 84)))
            protected = ImageChops.difference(intermediate, original)
            for eye in animation['eyes']:
                protected.paste((0, 0, 0), tuple(eye['box']))
            self.assertIsNone(protected.getbbox())
        frames = _decode(self.workspace / 'inputs' / 'broken-seed.mp4', spec['size'])
        self.assertEqual(len(frames), 36)
        # Broken seed actually shifts protected print rather than merely omitting a blink.
        footer = spec['regions'][-1]['box']
        self.assertIsNotNone(ImageChops.difference(frames[0].crop(footer), frames[3].crop(footer)).getbbox())

    def test_pendulum_zero_angle_pivots_from_disclosed_source(self):
        task_id = 'dev-create-woven-pendulum'
        spec = self.make(task_id)
        animation = spec['contract']['animation']
        expected = Image.open(spec['assets']['scene']).convert('RGBA')
        sprite = Image.open(spec['assets']['atlas']).convert('RGBA').crop((0, 0, 112, 120))
        sprite = sprite.resize((196, 210), Image.Resampling.LANCZOS)
        expected.alpha_composite(sprite, (animation['pivot'][0] - 98, animation['pivot'][1]))
        # A full-canvas intermediate alpha layer can round RGB by one level.
        difference = ImageChops.difference(expected.convert('RGB'), reference_frame(task_id, self.workspace, 0))
        self.assertLessEqual(max(high for low, high in difference.getextrema()), 1)
        self.assertIsNotNone(ImageChops.difference(reference_frame(task_id, self.workspace, 0), reference_frame(task_id, self.workspace, 9)).getbbox())

    def test_occlusion_order_keeps_every_opaque_foreground_pixel(self):
        task_id = 'dev-create-koi-occlusion'
        spec = self.make(task_id)
        foreground = Image.open(spec['assets']['foreground']).convert('RGBA')
        mask = foreground.getchannel('A')
        for n in (0, 9, 18, 27, 35):
            frame = reference_frame(task_id, self.workspace, n)
            diff = ImageChops.difference(frame, foreground.convert('RGB'))
            self.assertIsNone(ImageChops.multiply(diff, mask.convert('RGB')).getbbox())
        # At an actual crossing, pasting a subject last changes visible foreground.
        n = 9
        frame = reference_frame(task_id, self.workspace, n).convert('RGBA')
        animation = spec['contract']['animation']
        u = n / 35
        x = round(animation['start'][0] + (animation['end'][0] - animation['start'][0]) * u)
        y = round(animation['start'][1] + animation['bob_amplitude'] * math.sin(2 * math.pi * u))
        sprite = Image.open(spec['assets']['atlas']).convert('RGBA').crop(animation['atlas_box'])
        wrong = frame.copy()
        wrong.alpha_composite(sprite, (x, y))
        self.assertIsNotNone(ImageChops.difference(wrong.convert('RGB'), frame.convert('RGB')).getbbox())

    def test_orbit_seed_repeats_between_checkpoints_but_contract_moves(self):
        task_id = 'dev-repair-copper-orbit'
        spec = self.make(task_id)
        frames = _decode(self.workspace / 'inputs' / 'broken-seed.mp4', spec['size'])
        self.assertEqual(len(frames), 36)
        # Lossy video can perturb repeated pixels slightly; their difference is bounded.
        from PIL import ImageStat
        difference = ImageStat.Stat(ImageChops.difference(frames[3], frames[5])).mean
        self.assertLess(max(difference), 1)
        self.assertIsNotNone(ImageChops.difference(reference_frame(task_id, self.workspace, 3), reference_frame(task_id, self.workspace, 5)).getbbox())
        animation = spec['contract']['animation']
        source = Image.open(spec['assets']['scene']).convert('RGBA')
        sprite = Image.open(spec['assets']['atlas']).convert('RGBA').crop(animation['atlas_box'])
        x = animation['center'][0] - animation['radii'][0] - 28
        y = animation['center'][1] - 30
        source.alpha_composite(sprite, (x, y))
        self.assertIsNone(ImageChops.difference(source.convert('RGB'), reference_frame(task_id, self.workspace, 0)).getbbox())

    def test_label_alpha_retains_paper_and_exact_supplied_glyphs(self):
        task_id = 'dev-create-linen-collection'
        spec = self.make(task_id)
        expected = Image.open(spec['assets']['scene']).convert('RGBA')
        atlas = Image.open(spec['assets']['atlas']).convert('RGBA')
        for layer in spec['contract']['layers']:
            source = (atlas.crop(layer['crop']) if layer['asset'] == 'atlas' else
                      Image.open(spec['assets'][layer['asset']]).convert('RGBA'))
            expected.paste(source, tuple(layer['position']), source)
        actual = reference_frame(task_id, self.workspace)
        self.assertIsNone(ImageChops.difference(actual, expected.convert('RGB')).getbbox())
        # Transparent label corners retain the source paper byte-for-byte.
        scene = Image.open(spec['assets']['scene']).convert('RGB')
        label = spec['contract']['layers'][1]
        x, y = label['position']
        self.assertEqual(actual.getpixel((x, y)), scene.getpixel((x, y)))


if __name__ == '__main__':
    unittest.main()
