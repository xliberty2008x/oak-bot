"""Actual decoded media, positive controls and purposeful semantic mutations."""

import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops

from oak.media_pilot_hard_tasks import (
    TASKS, materialize_task, quality_spec, reference_frame,
)
from oak.media_pilot_hard_quality import evaluate_artifact


def _encode(frames, destination, fps=12, crf=20):
    # Deliberately independent of the fixture author's encoder. These are real
    # MP4 bytes at a conventional quality setting, not mocked decoder responses.
    frames = list(frames)
    process = subprocess.run(
        [shutil.which('ffmpeg'), '-v', 'error', '-y', '-filter_threads', '1',
         '-filter_complex_threads', '1', '-f', 'rawvideo', '-pixel_format', 'rgb24',
         '-video_size', f'{frames[0].width}x{frames[0].height}', '-framerate', str(fps),
         '-i', 'pipe:0', '-an', '-c:v', 'libx264', '-crf', str(crf), '-threads', '1',
         '-pix_fmt', 'yuv420p', str(destination)],
        input=b''.join(frame.convert('RGB').tobytes() for frame in frames),
        capture_output=True, timeout=45)
    if process.returncode:
        raise RuntimeError(process.stderr.decode('utf-8', 'replace'))


def _passage(workspace, index, *, subject_in_front=False, opaque_subject=False):
    """Build public-path frames in a separate test implementation."""
    contract = json.loads((workspace / 'inputs' / 'scene.json').read_text())
    animation = contract['animation']
    with Image.open(workspace / 'inputs' / 'scene.png') as image:
        frame = image.convert('RGBA')
    with Image.open(workspace / 'inputs' / 'foreground.png') as image:
        foreground = image.convert('RGBA')
    with Image.open(workspace / 'inputs' / 'atlas.png') as image:
        subject = image.convert('RGBA').crop(animation['atlas_box'])
    progress = index / (contract['frame_count'] - 1)
    position = (
        round(animation['start'][0] * (1 - progress) + animation['end'][0] * progress),
        round(animation['start'][1] * (1 - progress) + animation['end'][1] * progress
              + animation['bob_amplitude'] * math.sin(2 * math.pi * animation['bob_cycles'] * progress)),
    )
    if opaque_subject:
        paper = Image.new('RGBA', subject.size, 'white')
        paper.alpha_composite(subject)
        subject = paper
    if subject_in_front:
        frame.alpha_composite(foreground)
    frame.alpha_composite(subject, position)
    if not subject_in_front:
        frame.alpha_composite(foreground)
    return frame.convert('RGB')


class HardImageQuality(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.workspace = self.root / 'sources'
        self.task_id = 'dev-create-linen-collection'
        materialize_task(self.task_id, self.workspace)
        self.output = self.root / 'immutable-snapshot.png'

    def score(self, **options):
        return evaluate_artifact(self.task_id, self.output, self.workspace, **options)

    def test_source_layers_and_text_are_content_not_just_geometry(self):
        reference_frame(self.task_id, self.workspace).save(self.output)
        good = self.score(artifact_root=self.root)
        self.assertEqual(good['status'], 'pass', good)
        self.assertEqual(good['quality_score'], 1)
        self.assertFalse(good['structural_only'])
        with Image.open(self.workspace / 'inputs' / 'scene.png') as image:
            image.save(self.output)
        missing_content = self.score()
        self.assertEqual(missing_content['structural_status'], 'pass')
        self.assertEqual(missing_content['status'], 'fail')
        self.assertTrue(any(item['criterion'].startswith('content:fine-label')
                            and item['status'] == 'fail' for item in missing_content['checks']))
        self.assertLess(missing_content['quality_score'], good['quality_score'])

    def test_disclosed_two_pixel_placement_rounding_is_accepted(self):
        spec = quality_spec(self.task_id, self.workspace)
        with Image.open(spec['assets']['scene']) as source:
            composed = source.convert('RGBA')
        for layer in spec['contract']['layers']:
            with Image.open(spec['assets'][layer['asset']]) as source:
                plate = source.convert('RGBA')
            if 'crop' in layer:
                plate = plate.crop(layer['crop'])
            # A permitted label-placement rounding difference, without any glyph,
            # opacity or artwork changes. This must not require an exact golden.
            x, y = layer['position']
            composed.alpha_composite(plate, (x + (2 if layer['asset'] == 'atlas' else 0), y))
        composed.convert('RGB').save(self.output)
        result = self.score()
        self.assertEqual(result['status'], 'pass', result)

    def test_single_label_damage_and_white_transparent_margins_are_rejected(self):
        spec = quality_spec(self.task_id, self.workspace)
        good = reference_frame(self.task_id, self.workspace)
        first_label = spec['contract']['layers'][1]
        x, y = first_label['position']
        with Image.open(spec['assets']['scene']) as source:
            paper = source.convert('RGB')
        damaged = good.copy()
        damaged.paste(paper.crop((x, y, x + 160, y + 29)), (x, y))
        damaged.save(self.output)
        result = self.score()
        self.assertEqual(result['structural_status'], 'pass')
        self.assertEqual(result['status'], 'fail')
        label_result = next(item for item in result['checks']
                            if item['criterion'] == 'content:fine-label-1')
        self.assertEqual(label_result['status'], 'fail')
        with Image.open(spec['assets']['scene']) as source:
            flattened = source.convert('RGBA')
        for layer in spec['contract']['layers']:
            with Image.open(spec['assets'][layer['asset']]) as source:
                plate = source.convert('RGBA')
            if 'crop' in layer:
                plate = plate.crop(layer['crop'])
                white = Image.new('RGBA', plate.size, 'white')
                white.alpha_composite(plate)
                plate = white
            flattened.alpha_composite(plate, tuple(layer['position']))
        flattened.convert('RGB').save(self.output)
        self.assertEqual(self.score()['status'], 'fail')

    def test_repair_seed_retains_correct_geometry_but_fails_quality(self):
        workspace = self.root / 'archival'
        task_id = 'dev-repair-moth-archive'
        materialize_task(task_id, workspace)
        seed = workspace / 'inputs' / 'broken-seed.png'
        result = evaluate_artifact(task_id, seed, workspace)
        self.assertEqual(result['structural_status'], 'pass')
        self.assertEqual(result['status'], 'fail')
        self.assertTrue(any(item['criterion'] == 'content:original-artwork'
                            and item['status'] == 'fail' for item in result['checks']))
        reference_frame(task_id, workspace).save(self.output)
        self.assertEqual(evaluate_artifact(task_id, self.output, workspace)['status'], 'pass')

    def test_authored_png_compositions_are_valid_for_both_splits(self):
        # Construction QA only: no model-produced held-out artifact is inspected
        # and no reference composition enters a producer workspace or prompt.
        for task in TASKS:
            if task['output_name'] != 'final.png':
                continue
            with self.subTest(task=task['id']):
                workspace = self.root / task['id']
                materialize_task(task['id'], workspace)
                reference_frame(task['id'], workspace).save(self.output)
                self.assertEqual(evaluate_artifact(task['id'], self.output, workspace)['status'], 'pass')

    def test_decoding_and_snapshot_bounds_fail_closed(self):
        self.output.write_bytes(b'not an image')
        self.assertEqual(self.score()['structural_status'], 'fail')
        self.output.unlink()
        self.output.symlink_to(self.workspace / 'inputs' / 'scene.png')
        self.assertEqual(self.score()['status'], 'fail')
        self.output.unlink()
        reference_frame(self.task_id, self.workspace).save(self.output)
        self.assertEqual(self.score(artifact_root=self.workspace)['status'], 'fail')
        Image.new('RGB', (8, 8)).save(self.output)
        self.assertEqual(self.score()['structural_status'], 'fail')
        reference_frame(self.task_id, self.workspace).convert('RGBA').save(self.output)
        self.assertEqual(self.score()['structural_status'], 'fail')
        self.output.unlink()
        os.mkfifo(self.output)
        self.assertEqual(self.score()['status'], 'fail')


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'requires real FFmpeg')
class HardVideoQuality(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        cls.workspaces = {}
        cls.good = {}
        for task in TASKS:
            if task['output_name'] != 'final.mp4':
                continue
            workspace = cls.root / task['id']
            materialize_task(task['id'], workspace)
            cls.workspaces[task['id']] = workspace
            output = cls.root / (task['id'] + '.mp4')
            # Passage's positive control is independently composited from the
            # public asset/path specification, rather than the scorer renderer.
            frames = [(_passage(workspace, n) if quality_spec(task['id'], workspace)['family'] == 'occlusion'
                       else reference_frame(task['id'], workspace, n)) for n in range(36)]
            _encode(frames, output)
            cls.good[task['id']] = output

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def score(self, task_id, output):
        return evaluate_artifact(task_id, output, self.workspaces[task_id], artifact_root=self.root)

    def test_all_video_families_pass_with_real_lossy_h264(self):
        for task_id, output in self.good.items():
            with self.subTest(task=task_id):
                result = self.score(task_id, output)
                self.assertEqual(result['status'], 'pass', result)
                self.assertEqual(result['structural_status'], 'pass')
                self.assertGreater(result['quality_score'], .65)
                self.assertTrue(any(item['criterion'] == 'complete_decoded_sequence'
                                    and '36' in item['evidence'] for item in result['checks']))

    def test_open_eyes_forever_and_whole_face_bob_are_quality_failures(self):
        task_id = 'dev-repair-owl-blink'
        workspace = self.workspaces[task_id]
        with Image.open(workspace / 'inputs' / 'scene.png') as image:
            unchanged = image.convert('RGB')
        frozen = self.root / 'frozen-eyes.mp4'
        _encode([unchanged] * 36, frozen)
        for output in (frozen, workspace / 'inputs' / 'broken-seed.mp4'):
            with self.subTest(output=output.name):
                result = self.score(task_id, output)
                self.assertEqual(result['structural_status'], 'pass')
                self.assertEqual(result['status'], 'fail')
                self.assertTrue(any(item['criterion'].startswith('content:eye-')
                                    and item['status'] == 'fail' for item in result['checks']))

    def test_moving_pendulum_in_wrong_phase_is_rejected(self):
        task_id = 'dev-create-woven-pendulum'
        workspace = self.workspaces[task_id]
        output = self.root / 'wrong-phase.mp4'
        _encode([reference_frame(task_id, workspace, (n + 9) % 36) for n in range(36)], output)
        result = self.score(task_id, output)
        self.assertEqual(result['structural_status'], 'pass')
        self.assertEqual(result['status'], 'fail')
        self.assertTrue(any(item['criterion'] == 'content:pendulum-sweep'
                            and item['status'] == 'fail' for item in result['checks']))

    def test_correct_motion_with_replaced_source_pattern_is_rejected(self):
        task_id = 'dev-create-woven-pendulum'
        workspace = self.workspaces[task_id]
        with Image.open(workspace / 'inputs' / 'scene.png') as image:
            scene = image.convert('RGB')
        frames = []
        for index in range(36):
            correct = reference_frame(task_id, workspace, index)
            subject_mask = ImageChops.difference(correct, scene).convert('L').point(
                lambda value: 255 if value > 12 else 0)
            flattened_pattern = scene.copy()
            flattened_pattern.paste('#ca7050', mask=subject_mask)
            frames.append(flattened_pattern)
        output = self.root / 'replaced-source-pattern.mp4'
        _encode(frames, output)
        result = self.score(task_id, output)
        self.assertEqual(result['structural_status'], 'pass')
        self.assertEqual(result['status'], 'fail')
        self.assertEqual(next(item for item in result['checks']
                              if item['criterion'] == 'content:pendulum-sweep')['status'], 'fail')

    def test_authored_fine_pattern_survives_normal_encoder_quality(self):
        # CRF 24 covers ordinary H.264 delivery, including the default CRF 23.
        # A content check must not mistake unavoidable chroma/quantization loss
        # for a wrong trajectory or a missing source pattern.
        for task_id in ('dev-create-woven-pendulum', 'dev-repair-copper-orbit'):
            with self.subTest(task=task_id):
                workspace = self.workspaces[task_id]
                output = self.root / ('crf24-' + task_id + '.mp4')
                _encode([reference_frame(task_id, workspace, n) for n in range(36)], output, crf=24)
                result = self.score(task_id, output)
                self.assertEqual(result['status'], 'pass', result)

    def test_wrong_foreground_order_and_opaque_subject_cutout_are_rejected(self):
        task_id = 'dev-create-koi-occlusion'
        workspace = self.workspaces[task_id]
        for name, options in [('wrong-order', {'subject_in_front': True}),
                              ('white-cutout', {'opaque_subject': True})]:
            with self.subTest(mutation=name):
                output = self.root / (name + '.mp4')
                _encode([_passage(workspace, n, **options) for n in range(36)], output)
                result = self.score(task_id, output)
                self.assertEqual(result['structural_status'], 'pass')
                self.assertEqual(result['status'], 'fail')
                self.assertTrue(any(item['criterion'].startswith('content:')
                                    and item['status'] == 'fail' for item in result['checks']))

    def test_checkpoint_correct_but_intermediate_frozen_orbit_is_rejected(self):
        task_id = 'dev-repair-copper-orbit'
        workspace = self.workspaces[task_id]
        # This negative control matches the public trajectory exactly at frames
        # 0,9,18,27. Sparse thumbnail checks would overlook the held positions.
        result = self.score(task_id, workspace / 'inputs' / 'broken-seed.mp4')
        self.assertEqual(result['structural_status'], 'pass')
        self.assertEqual(result['status'], 'fail')
        orbit = next(item for item in result['checks'] if item['criterion'] == 'content:orbit-sweep')
        self.assertEqual(orbit['status'], 'fail')
        self.assertIn('/36 frames', orbit['evidence'])

    def test_missing_decoder_never_becomes_a_quality_pass(self):
        task_id = 'dev-repair-copper-orbit'
        with patch('oak.media_pilot_hard_quality.shutil.which', return_value=None):
            result = self.score(task_id, self.good[task_id])
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(result['structural_status'], 'unverified')
        self.assertEqual(result['quality_score'], 0)

    def test_h264_wrong_pixel_format_or_quicktime_container_is_rejected(self):
        task_id = 'dev-repair-copper-orbit'
        for name, options, criterion in (
            ('wrong-pixels', ['-pix_fmt', 'yuv444p'], 'yuv420p'),
            ('quicktime', ['-pix_fmt', 'yuv420p', '-f', 'mov'], 'silent_h264_mp4'),
        ):
            with self.subTest(mutation=name):
                output = self.root / (name + '.mp4')
                subprocess.run(
                    [shutil.which('ffmpeg'), '-v', 'error', '-y', '-filter_threads', '1',
                     '-threads', '1', '-i', str(self.good[task_id]), '-an', '-c:v', 'libx264',
                     '-crf', '18', '-threads', '1', *options, str(output)],
                    check=True, capture_output=True, timeout=30)
                result = self.score(task_id, output)
                self.assertEqual(result['structural_status'], 'fail')
                self.assertEqual(next(item for item in result['checks']
                                      if item['criterion'] == criterion)['status'], 'fail')


if __name__ == '__main__':
    unittest.main()
