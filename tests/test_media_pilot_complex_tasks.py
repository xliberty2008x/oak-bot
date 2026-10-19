"""Construction and recoverability checks for the complex media task set."""

import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile
import unittest

from PIL import Image, ImageChops

from oak.media_pilot_complex_tasks import (
    TASKS, _homography, _ik, _warp_link, authored_example_frames,
    materialize_task, quality_spec,
)


def _binary(image):
    return image.convert('L').point(lambda value: 255 if value > 16 else 0)


def _count(mask):
    return sum(mask.histogram()[1:])


def _placed_alpha(image, point, size):
    canvas = Image.new('L', tuple(size))
    canvas.paste(_binary(image.getchannel('A')), tuple(point))
    return canvas


def _mapped(matrix, point):
    x, y = point
    a, b, c, d, e, f, g, h = matrix
    denominator = g * x + h * y + 1
    return ((a * x + b * y + c) / denominator,
            (d * x + e * y + f) / denominator)


@unittest.skipUnless(shutil.which('ffmpeg'), 'local synthetic repair inputs require FFmpeg')
class ComplexTaskContracts(unittest.TestCase):
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

    def test_twelve_fresh_balanced_cases(self):
        from oak.media_pilot_tasks import TASKS as FIRST
        from oak.media_pilot_hard_tasks import TASKS as SECOND
        from oak.media_pilot_perceptual_tasks import TASKS as THIRD
        identities = {task['id'] for task in TASKS}
        self.assertEqual(len(TASKS), 12)
        self.assertEqual(len(identities), 12)
        self.assertFalse(identities & {task['id'] for task in (*FIRST, *SECOND, *THIRD)})
        for split in ('dev', 'holdout'):
            tasks = [task for task in TASKS if task['split'] == split]
            self.assertEqual(len(tasks), 6)
            self.assertEqual(sum(task['kind'] == 'creation' for task in tasks), 3)
            self.assertEqual(sum(task['output_name'] == 'final.mp4' for task in tasks), 4)
            self.assertEqual(sorted(self.facts(task['id'])['family'] for task in tasks),
                             ['choreography', 'choreography', 'mechanism', 'mechanism', 'projective', 'projective'])

    def test_complete_hashed_refs_roles_and_no_private_input_files(self):
        forbidden_keys = {'masks', 'properties', 'donor_points', 'target_points', 'clean_original',
                          'base_pivot', 'base_anchor', 'tip_anchor', 'lengths', 'rgba'}
        for task_id, task in self.tasks.items():
            with self.subTest(task=task_id):
                workspace = self.root / task_id
                files = {str(path) for path in (workspace / 'inputs').iterdir()}
                self.assertEqual(set(task['source_hashes']), files)
                self.assertEqual(len(task['reference_paths']), 3)
                self.assertEqual(set(task['reference_roles']), set(task['reference_paths']))
                self.assertTrue(set(task['reference_paths']) <= files)
                self.assertIn(task['specification_path'], files)
                for path, identity in task['source_hashes'].items():
                    self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), identity)
                for purpose in task['reference_roles'].values():
                    self.assertTrue(0 < len(purpose) <= 80)
                self.assertFalse((workspace / task['output_name']).exists())
                self.assertEqual({path.name for path in workspace.iterdir()}, {'inputs'})
                contract = json.loads(Path(task['specification_path']).read_text())
                self.assertLess(Path(task['specification_path']).stat().st_size, 65536)
                expected = set(contract['assets'].values()) | {'scene.json'}
                if task['kind'] == 'repair':
                    expected.add('broken-seed.mp4' if contract['duration'] else 'broken-seed.png')
                self.assertEqual({Path(path).name for path in files}, expected)
                def inspect_keys(value):
                    if isinstance(value, dict):
                        self.assertFalse(set(value) & forbidden_keys)
                        for child in value.values():
                            inspect_keys(child)
                    elif isinstance(value, list):
                        for child in value:
                            inspect_keys(child)
                inspect_keys(contract)
                self.assertEqual(set(contract['review_manifest']), {'visual', 'mechanical'})
                self.assertEqual(set(contract['review_manifest']['visual']), {'brief', 'specification'})
                visual = json.dumps(contract['review_manifest']['visual'])
                for mechanical in ('channel_noise_deadband', 'geometry_tolerance_px', 'sin(',
                                   'cos(', 'alpha_support_threshold', 'png_mae', 'motion_mae'):
                    self.assertNotIn(mechanical, visual)

    def test_projective_single_sample_recoverability_and_intact_unique_landmarks(self):
        for public in TASKS:
            facts = self.facts(public['id'])
            if facts['family'] != 'projective':
                continue
            p, masks = facts['properties'], facts['masks']
            matrix = _homography(p['target_points'], p['donor_points'])
            for target, donor_point in zip(p['target_points'], p['donor_points']):
                mapped = _mapped(matrix, target)
                self.assertAlmostEqual(mapped[0], donor_point[0], places=6)
                self.assertAlmostEqual(mapped[1], donor_point[1], places=6)
            self.assertEqual(len(set(map(tuple, p['target_points']))), 4)
            for points in (p['target_points'], p['donor_points']):
                for a in range(4):
                    triangle = [points[index] for index in range(4) if index != a]
                    (ax, ay), (bx, by), (cx, cy) = triangle
                    self.assertGreater(abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax)), 1000)
            with Image.open(facts['assets']['donor']) as source:
                donor = source.convert('RGB')
            reconstructed = donor.transform(tuple(facts['size']), Image.Transform.PERSPECTIVE,
                                             matrix, resample=Image.Resampling.BICUBIC,
                                             fillcolor='#e8dec8')
            self.assertIsNone(ImageChops.difference(reconstructed, p['clean_original']).getbbox())
            guide = _binary(masks['guide'])
            damage = _binary(masks['damage'])
            self.assertIsNone(ImageChops.multiply(damage, ImageChops.invert(guide)).getbbox())
            self.assertIsNone(ImageChops.difference(masks['protected'], ImageChops.invert(masks['guide'])).getbbox())
            # Check every authorized pixel against the actual donor domain,
            # including a two-donor-pixel bicubic sampling neighborhood.
            left, top, right, bottom = guide.getbbox()
            for y in range(top, bottom):
                for x in range(left, right):
                    if guide.getpixel((x, y)):
                        u, v = _mapped(matrix, (x, y))
                        self.assertTrue(2 <= u < donor.width - 2 and 2 <= v < donor.height - 2)
            with Image.open(facts['assets']['scene']) as image:
                damaged = image.convert('RGB')
            for x, y in p['target_points']:
                box = (round(x) - 16, round(y) - 16, round(x) + 33, round(y) + 33)
                self.assertIsNone(masks['guide'].crop(box).getbbox())
                self.assertIsNone(ImageChops.difference(damaged.crop(box), reconstructed.crop(box)).getbbox())
            for detail in p['detail_masks']:
                self.assertGreater(_count(_binary(detail['mask'])), 8)
                damaged_detail = ImageChops.multiply(_binary(detail['mask']), damage)
                self.assertGreater(_count(damaged_detail), 4)

    def test_all_frame_depth_crossings_are_observable_and_actors_remain_visible(self):
        for public in TASKS:
            facts = self.facts(public['id'])
            if facts['family'] != 'choreography':
                continue
            p = facts['properties']
            crossing_frames = {name: [] for name in p['required_crossing_objects']}
            for n in range(36):
                entries = [(item['id'], item['depth'], _binary(item['rgba'].getchannel('A')), False)
                           for item in p['foregrounds']]
                for actor in p['actors']:
                    position = actor['positions'][n]
                    self.assertTrue(0 <= position[0] <= facts['size'][0] - actor['rgba'].width)
                    self.assertTrue(0 <= position[1] <= facts['size'][1] - actor['rgba'].height)
                    entries.append((actor['id'], actor['depth'],
                                    _placed_alpha(actor['rgba'], position, facts['size']), True))
                for name, depth, mask, moving in entries:
                    above = Image.new('L', tuple(facts['size']))
                    behind_actors = Image.new('L', tuple(facts['size']))
                    for other, other_depth, other_mask, other_moving in entries:
                        if other_depth > depth:
                            above = ImageChops.lighter(above, other_mask)
                        if other_moving and other_depth < depth:
                            behind_actors = ImageChops.lighter(behind_actors, other_mask)
                    visible = ImageChops.multiply(mask, ImageChops.invert(above))
                    if moving:
                        self.assertGreaterEqual(_count(visible), 16, (public['id'], name, n))
                    if name in crossing_frames:
                        crossing = ImageChops.multiply(visible, behind_actors)
                        if _count(crossing) >= 16:
                            crossing_frames[name].append(n)
            for name, moments in crossing_frames.items():
                self.assertGreaterEqual(len(moments), 2, (public['id'], name))
                self.assertTrue(any(b == a + 1 for a, b in zip(moments, moments[1:])), (public['id'], name))

    def test_every_ik_pose_is_reachable_on_the_public_branch_and_unclipped(self):
        for public in TASKS:
            facts = self.facts(public['id'])
            if facts['family'] != 'mechanism':
                continue
            p = facts['properties']
            self.assertEqual(len(p['endpoint_positions']), 36)
            self.assertEqual(p['front_link'], 1)
            base = p['base_pivot']
            path = facts['contract']['endpoint_path']
            a, b = p['lengths']
            pad = 300
            w, h = facts['size']
            for n, endpoint in enumerate(p['endpoint_positions']):
                angle = 2 * math.pi * n / 36 + math.radians(path['phase_degrees'])
                expected = (path['center'][0] + path['radii'][0] * math.cos(angle),
                            path['center'][1] + path['radii'][1] * math.sin(angle))
                self.assertAlmostEqual(endpoint[0], expected[0], places=6)
                self.assertAlmostEqual(endpoint[1], expected[1], places=6)
                distance = math.dist(base, endpoint)
                self.assertGreater(distance - abs(a - b), 12)
                self.assertGreater(a + b - distance, 12)
                elbow = _ik(p, endpoint)
                self.assertAlmostEqual(math.dist(base, elbow), a, places=6)
                self.assertAlmostEqual(math.dist(elbow, endpoint), b, places=6)
                signed = ((endpoint[0] - base[0]) * (elbow[1] - base[1])
                          - (endpoint[1] - base[1]) * (elbow[0] - base[0]))
                self.assertEqual(1 if signed > 0 else -1, p['branch'])
                for layer, start, end in ((p['layers'][0], base, elbow), (p['layers'][1], elbow, endpoint)):
                    source_length = math.dist(layer['base_anchor'], layer['tip_anchor'])
                    self.assertAlmostEqual(source_length, math.dist(start, end), places=6)
                    expanded = _warp_link(layer, (start[0] + pad, start[1] + pad),
                                          (end[0] + pad, end[1] + pad), (w + 2 * pad, h + 2 * pad))
                    left, top, right, bottom = expanded.getchannel('A').getbbox()
                    self.assertTrue(pad <= left < right <= pad + w)
                    self.assertTrue(pad <= top < bottom <= pad + h)

    def test_authored_examples_do_not_write_oracle_files_into_producer_inputs(self):
        for public in TASKS:
            workspace = self.root / public['id']
            facts = self.facts(public['id'])
            before = {path.name for path in (workspace / 'inputs').iterdir()}
            frames = authored_example_frames(public['id'], workspace)
            for frame in frames:
                self.assertEqual(frame.size, tuple(facts['size']))
                self.assertEqual(frame.mode, 'RGB')
            self.assertEqual({path.name for path in (workspace / 'inputs').iterdir()}, before)
            self.assertFalse((workspace / public['output_name']).exists())


if __name__ == '__main__':
    unittest.main()
