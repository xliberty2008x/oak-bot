"""Portable perceptual task contracts and source recoverability checks."""

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from PIL import Image, ImageChops, ImageStat

from oak.media_pilot_perceptual_tasks import (
    TASKS, _spec, authored_example_frames, materialize_task, quality_spec,
)


@unittest.skipUnless(shutil.which('ffmpeg'), 'local synthetic repair inputs require FFmpeg')
class PerceptualTaskContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        cls.tasks = {public['id']: materialize_task(public['id'], cls.root / public['id'])
                     for public in TASKS}

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def facts(self, task_id):
        return quality_spec(task_id, self.root / task_id)

    def test_twelve_fresh_balanced_tasks(self):
        from oak.media_pilot_tasks import TASKS as FIRST
        from oak.media_pilot_hard_tasks import TASKS as SECOND
        self.assertEqual(len(TASKS), 12)
        identities = {task['id'] for task in TASKS}
        self.assertEqual(len(identities), 12)
        self.assertFalse(identities & {task['id'] for task in (*FIRST, *SECOND)})
        for split in ('dev', 'holdout'):
            tasks = [task for task in TASKS if task['split'] == split]
            self.assertEqual(len(tasks), 6)
            self.assertEqual(sum(task['kind'] == 'creation' for task in tasks), 3)
            self.assertEqual(sum(task['output_name'] == 'final.mp4' for task in tasks), 4)
            self.assertEqual(sorted(self.facts(task['id'])['family'] for task in tasks),
                             ['blink', 'blink', 'occlusion', 'occlusion', 'restoration', 'restoration'])

    def test_hashes_reference_roles_and_complete_original_references(self):
        for task_id, task in self.tasks.items():
            with self.subTest(task=task_id):
                workspace = self.root / task_id
                source_files = {str(path) for path in (workspace / 'inputs').iterdir()}
                self.assertEqual(set(task['source_hashes']), source_files)
                self.assertEqual(set(task['reference_roles']), set(task['reference_paths']))
                self.assertLessEqual(len(task['reference_paths']), 3)
                self.assertIn(task['specification_path'], source_files)
                for path, digest in task['source_hashes'].items():
                    self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), digest)
                for role in task['reference_roles'].values():
                    self.assertTrue(0 < len(role) <= 80)
                facts = self.facts(task_id)
                if facts['family'] == 'restoration':
                    self.assertEqual({Path(path).name for path in task['reference_paths']},
                                     {'scene.png', 'donor.png', 'guide.png'})
                self.assertFalse((workspace / task['output_name']).exists())
                self.assertEqual({path.name for path in workspace.iterdir()}, {'inputs'})
                with Image.open(facts['assets']['scene']) as source:
                    self.assertEqual(list(source.size), facts['size'])

    def test_public_manifest_separates_visual_intent_from_mechanical_recipe(self):
        forbidden_keys = {'eyes', 'masks', 'outer', 'iris', 'pupil', 'authoring', 'clean_original',
                          'foreground', 'crop_box', 'donor_offset', 'position', 'angle'}
        for task_id, task in self.tasks.items():
            contract = json.loads(Path(task['specification_path']).read_text())
            self.assertLess(Path(task['specification_path']).stat().st_size, 65536)
            manifest = contract['review_manifest']
            self.assertEqual(set(manifest), {'visual', 'mechanical'})
            self.assertEqual(set(manifest['visual']), {'brief', 'specification'})
            visual = json.dumps(manifest['visual'])
            for mechanical_key in ('geometry_tolerance_px', 'closure_coverage', 'blink_windows',
                                   'channel_noise_deadband', 'max_closure_step', 'motion_mae',
                                   'round(', 'sin(', 'MAE'):
                self.assertNotIn(mechanical_key, visual)
            self.assertEqual(manifest['mechanical']['acceptance'], contract['acceptance'])
            def check_keys(value):
                if isinstance(value, dict):
                    self.assertFalse(set(value) & forbidden_keys)
                    for child in value.values():
                        check_keys(child)
                elif isinstance(value, list):
                    for child in value:
                        check_keys(child)
            check_keys(contract)
            filenames = {Path(path).name for path in task['source_hashes']}
            self.assertFalse(filenames & {'foreground.png', 'iris.png', 'pupil.png', 'mask.png',
                                         'clean-original.png', 'gold.png'})

    def test_semantic_masks_private_and_nested(self):
        for public in TASKS:
            facts = self.facts(public['id'])
            size = tuple(facts['size'])
            if facts['family'] == 'blink':
                for eye in facts['properties']['eyes']:
                    for name in ('outer', 'iris', 'pupil'):
                        self.assertEqual(eye[name].mode, 'L')
                        self.assertEqual(eye[name].size, size)
                        self.assertIsNotNone(eye[name].getbbox())
                    self.assertIsNone(ImageChops.subtract(eye['pupil'], eye['iris']).getbbox())
                    # Masks may have antialiased fringes; all substantial iris
                    # pixels still lie inside the authorized eye region.
                    outside = ImageChops.multiply(eye['iris'], ImageChops.invert(eye['outer']))
                    self.assertLessEqual(max(outside.getextrema()), 1)
                    self.assertTrue(eye['lid_colors'])
            elif facts['family'] == 'occlusion':
                self.assertEqual(facts['masks']['foreground'].size, size)
                self.assertEqual(facts['properties']['clean_background'].size, size)
                self.assertEqual(len(facts['properties']['motion']), 36)
            else:
                self.assertIsNone(ImageChops.multiply(facts['masks']['damage'], facts['masks']['protected']).getbbox())
                self.assertTrue(all(item['mask'].getbbox() for item in facts['properties']['detail_masks']))

    def test_donor_rotation_crop_translation_is_exact_and_covers_every_stain(self):
        for public in TASKS:
            if self.facts(public['id'])['family'] != 'restoration':
                continue
            facts = self.facts(public['id'])
            seed = _spec(public['id'])[-1]
            w, h = facts['size']
            crop_box = (70, 78, w - 77, h - 75)
            crop_w, crop_h = crop_box[2] - crop_box[0], crop_box[3] - crop_box[1]
            dx, dy = 31 + seed % 11, 23 + seed % 7
            with Image.open(facts['assets']['donor']) as image:
                crop = image.convert('RGB').crop((dx, dy, dx + crop_h, dy + crop_w))
            crop = crop.transpose(Image.Transpose.ROTATE_270 if seed % 2 else Image.Transpose.ROTATE_90)
            clean = facts['properties']['clean_original']
            self.assertIsNone(ImageChops.difference(crop, clean.crop(crop_box)).getbbox())
            coverage = Image.new('L', (w, h))
            coverage.paste(255, crop_box)
            self.assertIsNone(ImageChops.multiply(facts['masks']['guide'], ImageChops.invert(coverage)).getbbox())
            with Image.open(facts['assets']['scene']) as image:
                original = image.convert('RGB')
            unchanged = ImageChops.multiply(ImageChops.difference(clean, original), facts['masks']['protected'].convert('RGB'))
            self.assertIsNone(unchanged.getbbox())
            # Three independently positioned visual landmarks are undamaged.
            for x, y in ((97, 104), (w - 117, 123), (123, h - 114)):
                box = (x - 17, y - 17, x + 30, y + 30)
                self.assertIsNone(ImageChops.difference(clean.crop(box), original.crop(box)).getbbox())

    def test_multiple_authored_blink_curves_are_distinct_and_source_local(self):
        for public in TASKS:
            facts = self.facts(public['id'])
            if facts['family'] != 'blink':
                continue
            with Image.open(facts['assets']['scene']) as image:
                source = image.convert('RGB')
            allowed = Image.new('L', source.size)
            for eye in facts['properties']['eyes']:
                allowed = ImageChops.lighter(allowed, eye['outer'])
            protected = ImageChops.invert(allowed)
            samples = {}
            for curve in ('triangle', 'cosine'):
                frames = list(authored_example_frames(public['id'], self.root / public['id'], curve=curve))
                self.assertEqual(len(frames), 36)
                self.assertIsNone(ImageChops.difference(frames[0], source).getbbox())
                self.assertIsNone(ImageChops.difference(frames[-1], source).getbbox())
                for frame in frames:
                    outside = ImageChops.multiply(ImageChops.difference(frame, source), protected.convert('RGB'))
                    self.assertLessEqual(max(ImageStat.Stat(outside).mean), .02)
                samples[curve] = frames[facts['properties']['blink_windows'][0][0] + 1]
            self.assertIsNotNone(ImageChops.difference(samples['triangle'], samples['cosine']).getbbox())

    def test_foreground_is_flattened_but_source_pattern_remains_recoverable(self):
        for public in TASKS:
            facts = self.facts(public['id'])
            if facts['family'] != 'occlusion':
                continue
            foreground = facts['properties']['foreground']
            opaque = foreground.getchannel('A').point(lambda value: 255 if value >= 250 else 0)
            with Image.open(facts['assets']['scene']) as image:
                scene = image.convert('RGB')
            for frame in authored_example_frames(public['id'], self.root / public['id']):
                changed = ImageChops.multiply(ImageChops.difference(frame, scene), opaque.convert('RGB'))
                # Fractional-alpha edge pixels can round by one level. Opaque
                # leaf interiors are exactly the provided flattened source.
                self.assertLessEqual(max(high for low, high in changed.getextrema()), 5)

    def test_unknown_identity_and_reused_inputs_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            absent = Path(root) / 'not-created'
            with self.assertRaises(ValueError):
                materialize_task('not-a-task', absent)
            self.assertFalse(absent.exists())
        first = TASKS[0]['id']
        with self.assertRaises(ValueError):
            materialize_task(first, self.root / first)
        with self.assertRaises(ValueError):
            quality_spec(TASKS[1]['id'], self.root / first)


if __name__ == '__main__':
    unittest.main()
