"""Authored-only controls required before sealing the complex media pilot.

These tests are a pre-native acceptance matrix.
In particular the projective alternate-resampling/phase cases are mandatory:
PNG2 plus an integer-only alignment search is not assumed fair. Any new
projective metric or budget must be fixed from these authored-only controls and
fresh negative controls before exposing a native producer to the tasks.
"""

import importlib.util
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

if importlib.util.find_spec('numpy') is None:
    raise unittest.SkipTest('Optional complex media controls require NumPy.')

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from oak.media_pilot_complex_tasks import (
    TASKS, _homography, _warp_link, authored_example_frames, materialize_task, quality_spec,
)
from oak.media_pilot_complex_quality import evaluate_artifact


def _encode(frames, destination, crf=20):
    """Encode the actual full-resolution sequence, with bounded frame retention."""
    frames = iter(frames)
    first = next(frames).convert('RGB')
    with tempfile.TemporaryDirectory(prefix='oak-complex-test-raw-') as directory:
        source = Path(directory) / 'frames.rgb'
        count = 1
        with source.open('wb') as output:
            output.write(first.tobytes())
            for frame in frames:
                if frame.size != first.size:
                    raise ValueError('A control changed frame dimensions.')
                output.write(frame.convert('RGB').tobytes())
                count += 1
                if count > 36:
                    raise ValueError('A control exceeded its disclosed frame count.')
        if count != 36:
            raise ValueError('A control did not provide all thirty-six frames.')
        result = subprocess.run(
            [shutil.which('ffmpeg'), '-v', 'error', '-y', '-filter_threads', '1',
             '-filter_complex_threads', '1', '-threads', '1', '-f', 'rawvideo',
             '-pixel_format', 'rgb24', '-video_size', f'{first.width}x{first.height}',
             '-framerate', '12', '-i', str(source), '-an', '-c:v', 'libx264',
             '-crf', str(crf), '-threads', '1', '-pix_fmt', 'yuv420p',
             '-map_metadata', '-1', str(destination)],
            capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', 'replace'))


def _registered_donor(facts, *, resample=Image.Resampling.BICUBIC,
                      target_offsets=None, supersample=1):
    properties = facts['properties']
    target = [tuple(point) for point in properties['target_points']]
    if target_offsets is not None:
        target = [(x + dx, y + dy) for (x, y), (dx, dy) in zip(target, target_offsets)]
    target = [(x * supersample, y * supersample) for x, y in target]
    mapping = _homography(target, properties['donor_points'])
    size = tuple(value * supersample for value in facts['size'])
    with Image.open(facts['assets']['donor']) as image:
        image = image.convert('RGB').transform(size, Image.Transform.PERSPECTIVE,
                                               mapping, resample=resample,
                                               fillcolor='#e8dec8')
    if supersample != 1:
        image = image.resize(tuple(facts['size']), Image.Resampling.LANCZOS)
    return image


def _repair_only_inside_guide(facts, recovered):
    with Image.open(facts['assets']['scene']) as image:
        result = image.convert('RGB')
    result.paste(recovered.convert('RGB'), (0, 0), facts['masks']['guide'])
    return result


def _shifted(image, dx, dy):
    return image.transform(image.size, Image.Transform.AFFINE,
                           (1, 0, -dx, 0, 1, -dy),
                           resample=Image.Resampling.BICUBIC)


def _plain_original_silhouette(sprite):
    """Retain source size/alpha/base palette, remove its actual patterned marks."""
    array = np.asarray(sprite.convert('RGBA'))
    pixels = array[array[:, :, 3] == 255, :3]
    colors, counts = np.unique(pixels, axis=0, return_counts=True)
    base = tuple(int(value) for value in colors[int(np.argmax(counts))])
    flat = Image.new('RGBA', sprite.size, base + (255,))
    flat.putalpha(sprite.getchannel('A'))
    return flat


def _choreography_frames(facts, *, depth=None, freeze=None, plain=None, opaque=None):
    properties = facts['properties']
    for index in range(facts['frame_count']):
        layers = [(item['depth'], item['rgba'], (0, 0))
                  for item in properties['foregrounds']]
        for actor in properties['actors']:
            sprite = actor['rgba'].copy()
            if actor['id'] == plain:
                sprite = _plain_original_silhouette(sprite)
            if actor['id'] == opaque:
                sprite.putalpha(255)
            position = actor['positions'][0 if actor['id'] == freeze else index]
            actor_depth = depth.get(actor['id'], actor['depth']) if depth else actor['depth']
            layers.append((actor_depth, sprite, tuple(position)))
        frame = properties['background'].convert('RGBA')
        for _, layer, position in sorted(layers, key=lambda item: item[0]):
            frame.alpha_composite(layer, tuple(position))
        yield frame.convert('RGB')


def _elbow(base, endpoint, lengths, branch):
    gx, gy = base
    dx, dy = endpoint[0] - gx, endpoint[1] - gy
    distance = math.hypot(dx, dy)
    a, b = lengths
    along = (a * a - b * b + distance * distance) / (2 * distance)
    height = math.sqrt(a * a - along * along)
    return (gx + (along * dx - branch * height * dy) / distance,
            gy + (along * dy + branch * height * dx) / distance)


def _rotate_around(point, center, radians):
    x, y = point[0] - center[0], point[1] - center[1]
    c, s = math.cos(radians), math.sin(radians)
    return (center[0] + c * x - s * y, center[1] + s * x + c * y)


def _stretched_link(layer):
    """A visibly nonrigid source-part mutation; source observations stay intact."""
    changed = dict(layer)
    width, height = layer['rgba'].size
    changed['rgba'] = layer['rgba'].resize((round(width * 1.22), height), Image.Resampling.BICUBIC)
    changed['base_anchor'] = (layer['base_anchor'][0] * 1.22, layer['base_anchor'][1])
    changed['tip_anchor'] = (layer['tip_anchor'][0] * 1.22, layer['tip_anchor'][1])
    return changed


def _mechanism_frames(facts, *, branch=None, detach=False, stretch=False, jump=False):
    properties = facts['properties']
    for index, original_endpoint in enumerate(properties['endpoint_positions']):
        endpoint = original_endpoint
        if jump and index == 17:
            endpoint = (endpoint[0] + 18, endpoint[1] - 14)
        elbow = _elbow(properties['base_pivot'], endpoint, properties['lengths'],
                       properties['branch'] if branch is None else branch)
        second_base = (_rotate_around(elbow, endpoint, math.radians(8)) if detach else elbow)
        layers = list(properties['layers'])
        if stretch:
            layers[1] = _stretched_link(layers[1])
        targets = [(properties['base_pivot'], elbow), (second_base, endpoint)]
        frame = properties['background'].convert('RGBA')
        order = [1 - properties['front_link'], properties['front_link']]
        for part in order:
            base, tip = targets[part]
            frame.alpha_composite(_warp_link(layers[part], base, tip, frame.size))
        yield frame.convert('RGB')


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'requires real FFmpeg')
class ComplexQualityControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='oak-complex-quality-')
        cls.root = Path(cls.directory.name)
        cls.workspaces, cls.facts, cls.gold = {}, {}, {}
        for task in TASKS:
            task_id = task['id']
            workspace = cls.root / task_id
            materialize_task(task_id, workspace)
            facts = quality_spec(task_id, workspace)
            cls.workspaces[task_id] = workspace
            cls.facts[task_id] = facts
            output = cls.root / (task_id + ('.mp4' if facts['duration'] else '.png'))
            if facts['duration']:
                _encode(authored_example_frames(task_id, workspace), output)
            else:
                next(authored_example_frames(task_id, workspace)).save(output)
            cls.gold[task_id] = output

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def _ids(self, family):
        return [task['id'] for task in TASKS if self.facts[task['id']]['family'] == family]

    def _score(self, task_id, output):
        return evaluate_artifact(task_id, output, self.workspaces[task_id],
                                 artifact_root=self.root, task_spec=self.facts[task_id])

    def _assert_pass(self, task_id, output):
        result = self._score(task_id, output)
        self.assertEqual(result['structural_status'], 'pass', json.dumps(result, indent=2))
        self.assertEqual(result['status'], 'pass', json.dumps(result, indent=2))
        self.assertTrue(result['requires_visual_review'])
        return result

    def _assert_content_failure(self, task_id, output, criterion=None):
        result = self._score(task_id, output)
        self.assertEqual(result['structural_status'], 'pass', json.dumps(result, indent=2))
        self.assertEqual(result['status'], 'fail', json.dumps(result, indent=2))
        failures = [check['criterion'] for check in result['checks']
                    if check['status'] == 'fail' and check['criterion'].startswith('content:')]
        self.assertTrue(failures, json.dumps(result, indent=2))
        if criterion is not None:
            self.assertTrue(any(criterion in name for name in failures), json.dumps(result, indent=2))
        return result

    def _png_control(self, task_id, label, recovered):
        output = self.root / (task_id + '-' + label + '.png')
        _repair_only_inside_guide(self.facts[task_id], recovered).save(output)
        return output

    def _video_control(self, task_id, label, frames):
        output = self.root / (task_id + '-' + label + '.mp4')
        _encode(frames, output)
        return output

    def test_00_all_twelve_authored_outputs_pass(self):
        self.assertEqual(len(TASKS), 12)
        for task_id, output in self.gold.items():
            with self.subTest(task=task_id):
                self._assert_pass(task_id, output)

    def test_projective_alternate_faithful_resamplers_pass_before_native(self):
        for task_id in self._ids('projective'):
            facts = self.facts[task_id]
            variants = {
                'bilinear': _registered_donor(facts, resample=Image.Resampling.BILINEAR),
                'supersampled-bicubic': _registered_donor(facts, supersample=2),
            }
            for label, recovered in variants.items():
                with self.subTest(task=task_id, variant=label):
                    self._assert_pass(task_id, self._png_control(task_id, label, recovered))

    def test_projective_subpixel_alignment_within_public_allowance_passes(self):
        for task_id in self._ids('projective'):
            clean = self.facts[task_id]['properties']['clean_original']
            for dx, dy in ((.5, 0), (0, -.5), (.5, .5), (-.5, -.5),
                           (.37, -.21), (-1.35, .65)):
                with self.subTest(task=task_id, phase=(dx, dy)):
                    image = _shifted(clean, dx, dy)
                    output = self._png_control(task_id, f'phase-{dx}-{dy}', image)
                    self._assert_pass(task_id, output)

    def test_projective_small_landmark_variation_is_not_false_precision(self):
        # Each corresponding landmark is perturbed by <1px, below the public2px
        # allowance. The four-point mapping stays well-defined and source-backed.
        variations = (
            ((.4, .3), (-.4, .3), (-.4, -.3), (.4, -.3)),
            ((-.3, .4), (.3, -.4), (.3, .4), (-.3, -.4)),
        )
        for task_id in self._ids('projective'):
            for index, offsets in enumerate(variations):
                with self.subTest(task=task_id, variation=index):
                    recovered = _registered_donor(self.facts[task_id], target_offsets=offsets)
                    output = self._png_control(task_id, f'landmarks-{index}', recovered)
                    self._assert_pass(task_id, output)

    def test_projective_wrong_registration_and_blurred_ink_fail(self):
        for task_id in self._ids('projective'):
            clean = self.facts[task_id]['properties']['clean_original']
            for label, recovered in (
                    ('wrong-registration', _shifted(clean, 8, -6)),
                    ('blurred-fine-print-light', clean.filter(ImageFilter.GaussianBlur(1.5))),
                    ('blurred-fine-print', clean.filter(ImageFilter.GaussianBlur(3)))):
                with self.subTest(task=task_id, defect=label):
                    output = self._png_control(task_id, label, recovered)
                    self._assert_content_failure(task_id, output)

    def test_projective_original_ink_cannot_be_erased(self):
        for task_id in self._ids('projective'):
            facts = self.facts[task_id]
            masks = [detail['mask'] for detail in facts['properties']['detail_masks']]
            self.assertTrue(all(mask.getbbox() is not None for mask in masks))
            ink = Image.new('L', tuple(facts['size']))
            for mask in masks:
                ink = ImageChops.lighter(ink, mask)
            recovered = facts['properties']['clean_original'].copy()
            recovered.paste(Image.new('RGB', recovered.size, '#e8dec8'), (0, 0), ink)
            output = self._png_control(task_id, 'erased-source-ink', recovered)
            with self.subTest(task=task_id):
                self._assert_content_failure(task_id, output, 'recoverable_fine_print')

    def test_projective_unauthorized_source_changes_fail(self):
        task_id = self._ids('projective')[0]
        facts = self.facts[task_id]
        changed = facts['properties']['clean_original'].copy()
        ImageDraw.Draw(changed).rectangle((25, 25, changed.width // 4, changed.height - 25), fill='#a34f79')
        output = self.root / 'unauthorized-print-changes.png'
        changed.save(output)
        self._assert_content_failure(task_id, output, 'protected_original_unchanged')

    def test_one_wrong_depth_relation_fails(self):
        task_id = self._ids('choreography')[0]
        frames = _choreography_frames(self.facts[task_id], depth={'actor_far': 2.5})
        output = self._video_control(task_id, 'far-actor-in-front-of-veil-I', frames)
        self._assert_content_failure(task_id, output, 'actual_depth_crossing:rear_veil_I')

    def test_near_actor_must_stay_in_front_of_veil_I(self):
        task_id = self._ids('choreography')[1]
        frames = _choreography_frames(self.facts[task_id], depth={'actor_near': 1.5})
        output = self._video_control(task_id, 'near-actor-behind-veil-I', frames)
        self._assert_content_failure(task_id, output)

    def test_one_frozen_actor_fails_while_other_moves_correctly(self):
        task_id = self._ids('choreography')[0]
        output = self._video_control(task_id, 'frozen-near-actor',
                                    _choreography_frames(self.facts[task_id], freeze='actor_near'))
        self._assert_content_failure(task_id, output, 'visible_source_pattern:actor_near')

    def test_plain_replacement_cannot_keep_only_the_source_silhouette(self):
        task_id = self._ids('choreography')[0]
        for actor in ('actor_far', 'actor_near'):
            with self.subTest(actor=actor):
                output = self._video_control(task_id, 'plain-' + actor,
                    _choreography_frames(self.facts[task_id], plain=actor))
                # This control intentionally preserves the dominant source color
                # and alpha. A full-silhouette average must not dilute lost rings
                # or stripes into a false pass; source-mark support may be needed.
                self._assert_content_failure(task_id, output)

    def test_opaque_actor_padding_is_not_allowed(self):
        task_id = self._ids('choreography')[1]
        output = self._video_control(task_id, 'opaque-near-actor-padding',
            _choreography_frames(self.facts[task_id], opaque='actor_near'))
        self._assert_content_failure(task_id, output, 'transparent_actor_padding')

    def test_explicit_wrong_kinematic_branch_fails(self):
        for task_id in self._ids('mechanism'):
            facts = self.facts[task_id]
            self.assertIn('elbow_branch', facts['contract'])
            output = self._video_control(task_id, 'wrong-explicit-branch',
                _mechanism_frames(facts, branch=-facts['properties']['branch']))
            with self.subTest(task=task_id):
                self._assert_content_failure(task_id, output)

    def test_detached_hinge_fails_even_with_correct_tip(self):
        task_id = self._ids('mechanism')[0]
        output = self._video_control(task_id, 'detached-hinge-correct-tip',
                                    _mechanism_frames(self.facts[task_id], detach=True))
        self._assert_content_failure(task_id, output)

    def test_nonrigid_source_part_stretch_fails(self):
        task_id = self._ids('mechanism')[1]
        output = self._video_control(task_id, 'nonrigid-source-part-stretch',
                                    _mechanism_frames(self.facts[task_id], stretch=True))
        self._assert_content_failure(task_id, output)

    def test_one_frame_endpoint_jump_fails_full_sequence_check(self):
        task_id = self._ids('mechanism')[0]
        output = self._video_control(task_id, 'frame17-endpoint-jump',
                                    _mechanism_frames(self.facts[task_id], jump=True))
        self._assert_content_failure(task_id, output)


if __name__ == '__main__':
    unittest.main()
