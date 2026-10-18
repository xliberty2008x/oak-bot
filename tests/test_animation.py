import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw, ImageStat

from oak.animation import Animation
from oak.quality_media import codec_environment


EYES = [[76, 90, 108, 112], [137, 90, 169, 112]]
HAIR = [[[180, 49], [233, 60], [235, 181], [190, 177]]]


def illustrated_character():
    """Explicitly authored fixture, not the absent child's image."""
    source = Image.new('RGB', (256, 256), '#dbe9e8')
    draw = ImageDraw.Draw(source)
    draw.rounded_rectangle((52, 25, 226, 191), radius=55, fill='#563b32')
    draw.ellipse((57, 40, 185, 185), fill='#edba96', outline='#6c4536', width=3)
    draw.polygon([(64, 177), (173, 177), (199, 255), (37, 255)], fill='#456b9c')
    draw.line((96, 195, 149, 195), fill='#e4e2d2', width=3)
    draw.arc((105, 137, 145, 160), 0, 180, fill='#9f4e47', width=3)
    for left, top, right, bottom in EYES:
        draw.ellipse((left+3, top+3, right-4, bottom-4), fill='#fff3dd', outline='#563b32', width=2)
        draw.ellipse((left+12, top+4, left+19, bottom-3), fill='#28433e')
        draw.ellipse((left+14, top+5, left+17, top+8), fill='white')
    for x in range(187, 229, 8):
        draw.line([(x, 56), (x-3, 89), (x+3, 125), (x-1, 172)], fill='#a67750', width=3)
    closed = source.copy()
    draw = ImageDraw.Draw(closed)
    for left, top, right, bottom in EYES:
        draw.rectangle((left, top, right-1, bottom-1), fill='#edba96')
        draw.arc((left+3, top+3, right-4, bottom-6), 0, 180, fill='#563b32', width=2)
    return source, closed


class AnimationTests(unittest.TestCase):
    def setUp(self):
        self.source, self.closed = illustrated_character()
        self.animation = Animation(self.source, self.closed, EYES, HAIR)

    def test_blink_hair_motion_and_loop_keep_every_outside_pixel_exact(self):
        outside = ImageChops.invert(self.animation.mask).convert('RGB')
        baseline = self.source.tobytes()
        self.assertEqual(self.animation.frame(0).tobytes(), baseline)
        self.assertEqual(self.animation.frame(self.animation.count-1).tobytes(), baseline)
        center = (self.animation.count-1)//2
        for bounds in EYES:
            self.assertEqual(self.animation.frame(center).crop(bounds).tobytes(), self.closed.crop(bounds).tobytes())
        hair_bounds = self.animation.hair[0][0]
        self.assertNotEqual(self.animation.frame(18).crop(hair_bounds).tobytes(), self.source.crop(hair_bounds).tobytes())
        self.assertNotEqual(self.animation.frame(center-2).crop(EYES[0]).tobytes(), self.closed.crop(EYES[0]).tobytes())
        for index in range(self.animation.count):
            difference = ImageChops.difference(self.animation.frame(index), self.source)
            self.assertIsNone(ImageChops.multiply(difference, outside).getbbox())
        self.assertEqual(self.source.tobytes(), baseline)

    def test_source_pixels_and_parameter_regions_are_snapshotted(self):
        self.source.paste('red', (0, 0, 256, 256))
        self.closed.paste('blue', (0, 0, 256, 256))
        expected, _ = illustrated_character()
        self.assertEqual(self.animation.frame(0).tobytes(), expected.tobytes())

    def test_every_blink_pose_keeps_complete_authored_contours_at_all_supported_rates(self):
        for fps in range(12, 31):
            with self.subTest(fps=fps):
                animation = Animation(self.source, self.closed, EYES, HAIR, seconds=1, fps=fps)
                closed_indices = []
                for index in range(animation.count):
                    frame = animation.frame(index)
                    states = []
                    for bounds in EYES:
                        eye = frame.crop(bounds).tobytes()
                        opened, closed = self.source.crop(bounds).tobytes(), self.closed.crop(bounds).tobytes()
                        # A clipped iris or missing lower outline matches neither authored pose.
                        self.assertIn(eye, (opened, closed))
                        states.append(eye == closed)
                    self.assertEqual(len(set(states)), 1)
                    if states[0]:
                        closed_indices.append(index)
                self.assertTrue(closed_indices)
                self.assertGreater(closed_indices[0], 0)
                self.assertLess(closed_indices[-1], animation.count-1)
                self.assertEqual(closed_indices, list(range(closed_indices[0], closed_indices[-1]+1)))
                # 100 ms has to be quantized to whole frames at the selected rate.
                self.assertLessEqual(abs(len(closed_indices)/fps - .1), .5/fps + 1e-10)

    def test_misaligned_or_unedited_closed_eyes_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'aligned'):
            Animation(self.source, self.closed.resize((128, 128)), EYES, HAIR)
        with self.assertRaisesRegex(ValueError, 'distinct'):
            Animation(self.source, self.source, EYES, HAIR)
        self.closed.putpixel((1, 1), (0, 0, 0))
        with self.assertRaisesRegex(ValueError, 'outside'):
            Animation(self.source, self.closed, EYES, HAIR)

    def test_unsupported_or_overlapping_geometry_is_rejected(self):
        for polygons in [[[[76, 90], [150, 90], [150, 150], [76, 150]]],
                         [[[180, 49], [235, 181], [233, 60], [190, 177]]],
                         [[[180, 49], [200, 49], [220, 49]]],
                         [[[180, 49], [200, 65], [180, 49]]],
                         [[[180, 49], [256, 60], [190, 177]]],
                         [[[180, 49], [185, 60], [180, 65]]]]:
            with self.subTest(polygons=polygons), self.assertRaises(ValueError):
                Animation(self.source, self.closed, EYES, polygons)
        for eyes in [[], EYES+EYES, [[True, 90, 108, 112]], [[-1, 90, 108, 112]], [EYES[0], EYES[0]]]:
            with self.subTest(eyes=eyes), self.assertRaises(ValueError):
                Animation(self.source, self.closed, eyes, HAIR)

    def test_resources_and_alpha_are_bounded(self):
        for kwargs in [{'seconds': 0}, {'seconds': 7}, {'seconds': float('nan')},
                       {'fps': True}, {'fps': 60}, {'amplitude': float('inf')}, {'amplitude': 15}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Animation(self.source, self.closed, EYES, HAIR, **kwargs)
        transparent = self.source.convert('RGBA')
        transparent.putpixel((0, 0), (0, 0, 0, 0))
        with self.assertRaisesRegex(ValueError, 'opaque'):
            Animation(transparent, self.closed, EYES, HAIR)
        with self.assertRaisesRegex(ValueError, '64–1024'):
            Animation(self.source.resize((257, 256)), self.closed.resize((257, 256)), EYES, HAIR)

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'existing.mp4'
            output.write_bytes(b'original')
            with self.assertRaises(ValueError):
                self.animation.encode(output)
            self.assertEqual(output.read_bytes(), b'original')

    def test_encoder_failure_cleans_staging_without_claiming_candidate(self):
        with tempfile.TemporaryDirectory() as folder, patch('oak.animation.subprocess.run', side_effect=subprocess.TimeoutExpired('ffmpeg', 90)):
            with self.assertRaisesRegex(ValueError, 'failed'):
                self.animation.encode(Path(folder) / 'candidate.mp4')
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_publication_race_does_not_remove_an_existing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'candidate.mp4'
            def concurrent_file(*_):
                output.write_bytes(b'concurrent original')
                raise FileExistsError()
            with patch('oak.animation.subprocess.run'), patch('oak.animation.os.link', side_effect=concurrent_file):
                with self.assertRaisesRegex(ValueError, 'failed'):
                    self.animation.encode(output)
            self.assertEqual(output.read_bytes(), b'concurrent original')
            self.assertEqual(list(Path(folder).iterdir()), [output])

    @unittest.skipUnless(Path('/usr/bin/ffmpeg').is_file() and Path('/usr/bin/ffprobe').is_file(), 'actual local FFmpeg required')
    def test_actual_mp4_decodes_local_eye_and_hair_changes_with_stable_background(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'candidate.mp4'
            receipt = self.animation.encode(output)
            self.assertEqual(receipt['method'], 'procedural_2d')
            self.assertTrue(receipt['review_required'])
            self.assertNotIn('queued_for_delivery', receipt)
            metadata = json.loads(subprocess.check_output(['/usr/bin/ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(output)], env=codec_environment()))
            self.assertEqual(len(metadata['streams']), 1)
            self.assertEqual(metadata['streams'][0]['codec_name'], 'h264')
            self.assertEqual(metadata['streams'][0]['nb_frames'], str(self.animation.count))
            data = subprocess.check_output(['/usr/bin/ffmpeg', '-v', 'error', '-i', str(output), '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], env=codec_environment())
            size = 256*256*3
            frames = [Image.frombytes('RGB', (256, 256), data[i:i+size]) for i in range(0, len(data), size)]
            self.assertEqual(len(frames), self.animation.count)
            center = (self.animation.count-1)//2
            closed_indices = []
            for index, frame in enumerate(frames):
                states = []
                for bounds in EYES:
                    errors = [max(ImageStat.Stat(ImageChops.difference(frame.crop(bounds), pose.crop(bounds))).mean)
                              for pose in (frames[0], frames[center])]
                    # Fixture codec error stays below 6; old clipped transitions exceed 10.
                    self.assertLess(min(errors), 6, (index, errors))
                    states.append(errors[1] < errors[0])
                self.assertEqual(len(set(states)), 1)
                if states[0]:
                    closed_indices.append(index)
            self.assertEqual(len(closed_indices), 2)
            self.assertEqual(closed_indices[1], closed_indices[0]+1)
            self.assertGreater(closed_indices[0], 0)
            self.assertLess(closed_indices[-1], len(frames)-1)
            self.assertGreater(sum(ImageStat.Stat(ImageChops.difference(frames[0].crop(EYES[0]), frames[center].crop(EYES[0]))).mean), 20)
            bounds = self.animation.hair[0][0]
            self.assertGreater(sum(ImageStat.Stat(ImageChops.difference(frames[0].crop(bounds), frames[18].crop(bounds))).mean), 5)
            outside = ImageChops.invert(self.animation.mask)
            for index in (18, center, len(frames)-1):
                # Fixture-specific codec error budget, not a semantic quality threshold.
                difference = ImageChops.difference(frames[0], frames[index])
                self.assertLess(max(ImageStat.Stat(difference, outside).mean), 2)
            self.assertLess(max(ImageStat.Stat(ImageChops.difference(frames[0], frames[-1])).mean), 2)


if __name__ == '__main__':
    unittest.main()
