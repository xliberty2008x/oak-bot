"""Decoded authored controls for inference tasks; no model/provider calls."""

import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

if importlib.util.find_spec('numpy') is None:
    raise unittest.SkipTest('Optional media pilot checks require NumPy.')

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from oak.media_pilot_perceptual_tasks import (
    TASKS, authored_example_frames, materialize_task, quality_spec,
)
from oak.media_pilot_perceptual_quality import evaluate_artifact


def _encode(frames, destination, crf=20):
    frames = list(frames)
    process = subprocess.run(
        [shutil.which('ffmpeg'), '-v', 'error', '-y', '-filter_threads', '1',
         '-filter_complex_threads', '1', '-f', 'rawvideo', '-pixel_format', 'rgb24',
         '-video_size', f'{frames[0].width}x{frames[0].height}', '-framerate', '12',
         '-i', 'pipe:0', '-an', '-c:v', 'libx264', '-crf', str(crf), '-threads', '1',
         '-pix_fmt', 'yuv420p', str(destination)],
        input=b''.join(frame.convert('RGB').tobytes() for frame in frames),
        capture_output=True, timeout=45)
    if process.returncode:
        raise RuntimeError(process.stderr.decode('utf-8', 'replace'))


def _pupil_colored_closed_seams(task_id, workspace, curve):
    """A valid alternative source-colored seam, unrelated to pupil identity."""
    facts = quality_spec(task_id, workspace)
    frames = list(authored_example_frames(task_id, workspace, curve=curve))
    for index in {(first + last) // 2 for first, last in facts['properties']['blink_windows']}:
        frame = frames[index].convert('RGBA')
        for eye in facts['properties']['eyes']:
            author = eye['authoring']
            width, height = author['tile_size']
            scale = 4
            points = [(12, 38), (25, 41), (40, 43), (53, 44), (66, 43), (80, 41), (92, 38)]
            points = [(x * scale, y * scale) for x, y in points]
            erase = Image.new('L', (width * scale, height * scale))
            ImageDraw.Draw(erase).line(points, fill=255, width=5 * scale, joint='curve')
            erase = erase.resize((width, height), Image.Resampling.LANCZOS).rotate(
                author['angle'], resample=Image.Resampling.BICUBIC)
            fill = Image.new('RGBA', (width, height), tuple(eye['lid_colors'][0]) + (255,))
            frame.paste(fill, tuple(author['position']), erase)
            line = Image.new('RGBA', (width * scale, height * scale))
            ImageDraw.Draw(line).line(points, fill='#243f3d', width=3 * scale, joint='curve')
            line = line.resize((width, height), Image.Resampling.LANCZOS).rotate(
                author['angle'], resample=Image.Resampling.BICUBIC)
            frame.alpha_composite(line, tuple(author['position']))
        frames[index] = frame.convert('RGB')
    return frames


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'requires real FFmpeg')
class PerceptualQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        cls.workspaces = {}
        cls.outputs = {}
        for task in TASKS:
            workspace = cls.root / task['id']
            materialize_task(task['id'], workspace)
            cls.workspaces[task['id']] = workspace
            output = cls.root / (task['id'] + Path(task['output_name']).suffix)
            frames = authored_example_frames(task['id'], workspace)
            if task['output_name'].endswith('.mp4'):
                _encode(frames, output)
            else:
                next(frames).convert('RGB').save(output)
            cls.outputs[task['id']] = output

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def score(self, task_id, output):
        return evaluate_artifact(task_id, output, self.workspaces[task_id], artifact_root=self.root)

    def spec(self, task_id):
        return quality_spec(task_id, self.workspaces[task_id])

    def assert_content_failure(self, result, prefix=None):
        self.assertEqual(result['structural_status'], 'pass', result)
        self.assertEqual(result['status'], 'fail', result)
        self.assertTrue(any(row['status'] == 'fail'
                            and row['criterion'].startswith(prefix or 'content:')
                            for row in result['checks']), result)

    def test_all_twelve_authored_compositions_pass_both_splits(self):
        # These are valid examples, not a hidden blink movie used by the scorer.
        for task in TASKS:
            with self.subTest(task=task['id']):
                result = self.score(task['id'], self.outputs[task['id']])
                self.assertEqual(result['status'], 'pass', result)
                self.assertEqual(result['structural_status'], 'pass')
                self.assertFalse(result['structural_only'])

    def test_different_valid_blink_curves_and_peak_times_are_accepted(self):
        for task_id in self.workspaces:
            if self.spec(task_id)['family'] != 'blink':
                continue
            for curve, shift in [('cosine', 0), ('triangle', 1)]:
                with self.subTest(task=task_id, curve=curve, shift=shift):
                    output = self.root / f'valid-{task_id}-{curve}-{shift}.mp4'
                    _encode(authored_example_frames(task_id, self.workspaces[task_id],
                                                    curve=curve, peak_shift=shift), output)
                    result = self.score(task_id, output)
                    self.assertEqual(result['status'], 'pass', result)

    def test_source_pupil_colored_closed_lid_seam_is_not_a_moved_pupil(self):
        for task_id in self.workspaces:
            if self.spec(task_id)['family'] != 'blink':
                continue
            for curve in ('triangle', 'cosine'):
                with self.subTest(task=task_id, curve=curve):
                    output = self.root / f'dark-seam-{task_id}-{curve}.mp4'
                    _encode(_pupil_colored_closed_seams(task_id, self.workspaces[task_id], curve), output)
                    self.assertEqual(self.score(task_id, output)['status'], 'pass')

    def test_frozen_eyes_are_a_real_content_failure(self):
        task_id = 'dev-create-marten-blink'
        with Image.open(self.spec(task_id)['assets']['scene']) as image:
            original = image.convert('RGB')
        output = self.root / 'frozen-eyes.mp4'
        _encode([original] * 36, output)
        self.assert_content_failure(self.score(task_id, output), 'content:iris_closed:')

    def test_instant_closed_frame_fails_smoothness(self):
        task_id = 'dev-create-marten-blink'
        facts = self.spec(task_id)
        with Image.open(facts['assets']['scene']) as image:
            original = image.convert('RGB')
        closed = original.copy()
        for eye in facts['properties']['eyes']:
            closed.paste(tuple(eye['lid_colors'][0]), mask=eye['outer'])
        frames = [original] * 36
        for first, last in facts['properties']['blink_windows']:
            frames[(first + last) // 2] = closed
        output = self.root / 'jump-closure.mp4'
        _encode(frames, output)
        self.assert_content_failure(self.score(task_id, output), 'content:bounded_closure_step:')

    def test_ghost_crossfade_does_not_count_as_closed_iris(self):
        task_id = 'dev-create-marten-blink'
        facts = self.spec(task_id)
        with Image.open(facts['assets']['scene']) as image:
            original = image.convert('RGB')
        frames = []
        for frame in authored_example_frames(task_id, self.workspaces[task_id]):
            frames.append(Image.blend(original, frame, .5))
        output = self.root / 'ghost-iris.mp4'
        _encode(frames, output)
        self.assert_content_failure(self.score(task_id, output), 'content:iris_closed:')

    def test_visible_pupils_cannot_move_under_unchanged_face(self):
        task_id = 'dev-create-marten-blink'
        facts = self.spec(task_id)
        frames = list(authored_example_frames(task_id, self.workspaces[task_id]))
        windows = facts['properties']['blink_windows']
        for index, frame in enumerate(frames):
            if any(first <= index <= last for first, last in windows):
                continue
            for eye in facts['properties']['eyes']:
                frame.paste('#a17c45', mask=eye['pupil'])
                frame.paste('#243f3d', mask=ImageChops.offset(eye['pupil'], 8, 0))
        output = self.root / 'moved-pupils.mp4'
        _encode(frames, output)
        self.assert_content_failure(self.score(task_id, output), 'content:visible_pupil_position:')

    def test_pupil_position_is_still_checked_during_partial_closure(self):
        task_id = 'dev-create-marten-blink'
        facts = self.spec(task_id)
        frames = list(authored_example_frames(task_id, self.workspaces[task_id]))
        partial = {first + offset for first, _ in facts['properties']['blink_windows']
                   for offset in (1, 2)}
        for index in partial:
            frame = frames[index]
            difference = ImageChops.difference(frame, Image.new('RGB', frame.size, '#243f3d'))
            channels = [channel.point(lambda value: 255 if value <= 20 else 0)
                        for channel in difference.split()]
            dark = ImageChops.darker(ImageChops.darker(channels[0], channels[1]), channels[2])
            for eye in facts['properties']['eyes']:
                visible_pupil = ImageChops.multiply(dark, eye['pupil'])
                frame.paste('#a17c45', mask=visible_pupil)
                shifted = ImageChops.multiply(ImageChops.offset(visible_pupil, 8, 0), eye['iris'])
                frame.paste('#243f3d', mask=shifted)
        output = self.root / 'pupil-moves-only-during-partial-closure.mp4'
        _encode(frames, output)
        self.assert_content_failure(self.score(task_id, output), 'content:visible_pupil_position:')

    def test_whole_face_motion_is_not_local_eyelid_animation(self):
        task_id = 'dev-create-marten-blink'
        frames = []
        for index, original in enumerate(authored_example_frames(task_id, self.workspaces[task_id])):
            moved = Image.new('RGB', original.size, '#e9dfc7')
            moved.paste(original, (0, 10 if index % 2 else -10))
            frames.append(moved)
        output = self.root / 'moving-face.mp4'
        _encode(frames, output)
        self.assert_content_failure(self.score(task_id, output), 'content:stationary_outside_eyelids')

    def test_flat_foreground_must_occlude_the_actual_crossing_pixels(self):
        task_id = 'dev-create-glasswing-passage'
        facts = self.spec(task_id)
        with Image.open(facts['assets']['scene']) as image:
            scene = image.convert('RGBA')
        with Image.open(facts['assets']['sprite']) as image:
            sprite = image.convert('RGBA')
        frames = []
        for position in facts['properties']['motion']:
            frame = scene.copy()
            frame.alpha_composite(sprite, tuple(position))
            frames.append(frame.convert('RGB'))
        output = self.root / 'wrong-occlusion.mp4'
        _encode(frames, output)
        self.assert_content_failure(self.score(task_id, output), 'content:actual_opaque_foreground_occlusion')

    def test_opaque_padding_cannot_hide_in_full_frame_background_average(self):
        task_id = 'dev-create-glasswing-passage'
        facts = self.spec(task_id)
        with Image.open(facts['assets']['sprite']) as image:
            sprite = image.convert('RGBA')
        opaque = Image.new('RGBA', sprite.size, 'white')
        opaque.alpha_composite(sprite)
        frames = []
        for position in facts['properties']['motion']:
            frame = facts['properties']['clean_background'].convert('RGBA')
            frame.alpha_composite(opaque, tuple(position))
            frame.alpha_composite(facts['properties']['foreground'])
            frames.append(frame.convert('RGB'))
        output = self.root / 'opaque-padding.mp4'
        _encode(frames, output)
        self.assert_content_failure(self.score(task_id, output), 'content:unchanged_transparent_sprite_padding')

    def test_restoration_rejects_wrong_registration_and_blurred_fine_type(self):
        task_id = 'dev-create-herbarium-proof'
        facts = self.spec(task_id)
        clean = facts['properties']['clean_original']
        with Image.open(facts['assets']['scene']) as image:
            damaged = image.convert('RGB')
        for label, replacement in (
            ('wrong-registration', ImageChops.offset(clean, 9, -7)),
            ('blurred-type', clean.filter(ImageFilter.GaussianBlur(3))),
        ):
            with self.subTest(mutation=label):
                result_image = damaged.copy()
                result_image.paste(replacement, (0, 0), facts['masks']['guide'])
                output = self.root / (label + '.png')
                result_image.save(output)
                result = self.score(task_id, output)
                self.assert_content_failure(result)
                self.assertTrue(any(row['criterion'].startswith('content:fine_print_preserved:')
                                    and row['status'] == 'fail' for row in result['checks']), result)

    def test_restoration_preserves_pixels_outside_visible_guide(self):
        task_id = 'dev-create-herbarium-proof'
        image = self.spec(task_id)['properties']['clean_original'].copy()
        ImageDraw.Draw(image).rectangle((20, 20, 180, 180), fill='black')
        output = self.root / 'outside-guide.png'
        image.save(output)
        self.assert_content_failure(self.score(task_id, output), 'content:protected_original_unchanged')

    def test_missing_decoder_and_non_rgb_png_never_receive_a_semantic_pass(self):
        task_id = 'dev-create-marten-blink'
        with patch('oak.media_pilot_hard_quality.shutil.which', return_value=None):
            result = self.score(task_id, self.outputs[task_id])
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(result['quality_score'], 0)
        task_id = 'dev-create-herbarium-proof'
        output = self.root / 'rgba-output.png'
        self.spec(task_id)['properties']['clean_original'].convert('RGBA').save(output)
        self.assertEqual(self.score(task_id, output)['structural_status'], 'fail')


if __name__ == '__main__':
    unittest.main()
