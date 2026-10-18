"""Bounded, local 2D animation from explicitly selected artwork and regions.

This is procedural compositing, not generative image-to-video. The native agent
must select suitable geometry and provide matching closed-eye artwork. A rendered
file is a candidate for independent source/video review, never a quality verdict.
"""

import math
import os
from pathlib import Path
import subprocess
import tempfile

from .quality_media import codec_environment


def _integer(value):
    return type(value) is int


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _intersects(a, b, c, d):
    def on(a, b, c):
        return _cross(a, b, c) == 0 and min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])
    return (on(a, b, c) or on(a, b, d) or on(c, d, a) or on(c, d, b)
            or (_cross(a, b, c) > 0) != (_cross(a, b, d) > 0)
            and (_cross(c, d, a) > 0) != (_cross(c, d, b) > 0))


class Animation:
    """Immutable source pixels plus one blink and a periodic local hair warp."""

    def __init__(self, source, closed_eyes, eye_regions, hair_polygons, *, seconds=3, fps=24, amplitude=3):
        from PIL import Image, ImageChops, ImageDraw, ImageFilter
        width, height = source.size
        if (not 64 <= width <= 1024 or not 64 <= height <= 1024 or width % 2 or height % 2
                or closed_eyes.size != source.size):
            raise ValueError('Use aligned, even-sized source and closed-eye images, 64–1024 pixels per side.')
        for image in (source, closed_eyes):
            if getattr(image, 'n_frames', 1) != 1 or image.convert('RGBA').getchannel('A').getextrema() != (255, 255):
                raise ValueError('Use single-frame opaque source artwork.')
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 1 <= seconds <= 6:
            raise ValueError('Animation duration must be 1–6 seconds.')
        if not _integer(fps) or not 12 <= fps <= 30:
            raise ValueError('Frame rate must be 12–30 fps.')
        if type(amplitude) not in (int, float) or not math.isfinite(amplitude) or not 1 <= amplitude <= 12:
            raise ValueError('Hair displacement must be 1–12 pixels.')
        if not isinstance(eye_regions, (list, tuple)) or not 1 <= len(eye_regions) <= 2:
            raise ValueError('Select one or two eye rectangles.')
        if not isinstance(hair_polygons, (list, tuple)) or not 1 <= len(hair_polygons) <= 4:
            raise ValueError('Select one to four hair polygons.')
        self.source, self.closed = source.convert('RGB').copy(), closed_eyes.convert('RGB').copy()
        self.fps, self.count, self.amplitude = fps, round(seconds * fps), amplitude
        self.mask = Image.new('L', source.size)
        eyes_mask = Image.new('L', source.size)
        self.eyes, self.hair = [], []

        def add(mask):
            if ImageChops.multiply(self.mask, mask).getbbox():
                raise ValueError('Eye and hair regions must not overlap.')
            self.mask = ImageChops.lighter(self.mask, mask)

        for bounds in eye_regions:
            if not isinstance(bounds, (list, tuple)) or len(bounds) != 4 or not all(_integer(v) for v in bounds):
                raise ValueError('Eye bounds must be integer [left, top, right, bottom].')
            left, top, right, bottom = bounds
            if not (0 <= left < right <= width and 0 <= top < bottom <= height and right-left >= 4 and bottom-top >= 4):
                raise ValueError('Eye rectangles must fit inside the image and span at least four pixels.')
            mask = Image.new('L', source.size)
            mask.paste(255, tuple(bounds))
            add(mask)
            eyes_mask = ImageChops.lighter(eyes_mask, mask)
            if not ImageChops.difference(self.source.crop(bounds), self.closed.crop(bounds)).getbbox():
                raise ValueError('Each eye needs distinct, aligned closed-eye artwork.')
            self.eyes.append(tuple(bounds))
        changed = ImageChops.difference(self.source, self.closed)
        outside_eyes = ImageChops.invert(eyes_mask)
        if ImageChops.multiply(changed, outside_eyes.convert('RGB')).getbbox():
            raise ValueError('Closed-eye artwork must preserve all source pixels outside the selected eyes.')
        for polygon in hair_polygons:
            if not isinstance(polygon, (list, tuple)) or not 3 <= len(polygon) <= 32:
                raise ValueError('Hair polygons need 3–32 vertices.')
            points = []
            for point in polygon:
                if (not isinstance(point, (list, tuple)) or len(point) != 2 or not all(_integer(v) for v in point)
                        or not 0 <= point[0] < width or not 0 <= point[1] < height):
                    raise ValueError('Hair vertices must be integer pixels inside the source image.')
                points.append(tuple(point))
            if len(set(points)) != len(points):
                raise ValueError('Hair polygons must have distinct vertices.')
            edges = list(zip(points, points[1:] + points[:1]))
            if sum(a[0]*b[1]-b[0]*a[1] for a, b in edges) == 0:
                raise ValueError('Hair polygons must enclose an area.')
            if any(_intersects(*edges[i], *edges[j]) for i in range(len(edges)) for j in range(i + 1, len(edges))
                   if j != i + 1 and (i, j) != (0, len(edges) - 1)):
                raise ValueError('Hair polygons must not intersect themselves.')
            mask = Image.new('L', source.size)
            ImageDraw.Draw(mask).polygon(points, fill=255)
            bounds = mask.getbbox()
            if not bounds or min(bounds[2]-bounds[0], bounds[3]-bounds[1]) < max(12, amplitude * 10):
                raise ValueError('Hair region is too narrow for the requested displacement.')
            add(mask)
            # Feather inward only: every pixel outside the selected polygon stays exact.
            feather = ImageChops.multiply(mask, mask.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.GaussianBlur(1.5)))
            self.hair.append((bounds, feather.crop(bounds)))

    def frame(self, index):
        from PIL import Image
        if not _integer(index) or not 0 <= index < self.count:
            raise ValueError('Frame index is outside this animation.')
        frame = self.source.copy()
        wave = math.sin(2 * math.pi * index / (self.count - 1))
        if abs(wave) > 1e-9:
            for bounds, mask in self.hair:
                patch = self.source.crop(bounds)
                width, height = patch.size
                def point(x, y):
                    shift = self.amplitude * wave * math.sin(math.pi*x/width)**2 * math.sin(math.pi*y/height)**2
                    return x - shift, y
                mesh = []
                for row in range(8):
                    for column in range(8):
                        left, right = column*width//8, (column+1)*width//8
                        top, bottom = row*height//8, (row+1)*height//8
                        quad = (*point(left, top), *point(left, bottom), *point(right, bottom), *point(right, top))
                        mesh.append(((left, top, right, bottom), quad))
                moving = patch.transform(patch.size, Image.Transform.MESH, mesh, Image.Resampling.BICUBIC)
                frame.paste(Image.composite(moving, patch, mask), bounds)
        center, half_blink = (self.count-1)//2, max(2, round(self.fps * .16))
        distance = abs(index-center)
        amount = .5 + .5*math.cos(math.pi*distance/half_blink) if distance < half_blink else 0
        if amount:
            for left, top, right, bottom in self.eyes:
                frame.paste(self.closed.crop((left, top, right, bottom)), (left, top))
                opening = round((bottom-top) * (1-amount))
                if opening:
                    y = top + (bottom-top-opening)//2
                    frame.paste(self.source.crop((left, y, right, y+opening)), (left, y))
        return frame

    def encode(self, output):
        """Encode a review candidate; no network, delivery or quality claim."""
        output = Path(output)
        if output.suffix.lower() != '.mp4' or output.exists():
            raise ValueError('Use a new MP4 output path.')
        try:
            with tempfile.TemporaryDirectory(prefix='oak-animation-', dir=output.parent) as folder:
                for index in range(self.count):
                    self.frame(index).save(Path(folder) / f'frame-{index:03d}.png')
                subprocess.run(['/usr/bin/ffmpeg', '-nostdin', '-v', 'error', '-n', '-framerate', str(self.fps),
                                '-i', str(Path(folder) / 'frame-%03d.png'), '-frames:v', str(self.count),
                                '-an', '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast', '-crf', '18',
                                '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(Path(folder) / 'encoded.mp4')],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               env=codec_environment(), timeout=90, check=True)
                # Publish once without overwriting a concurrently created file.
                os.link(Path(folder) / 'encoded.mp4', output)
        except (OSError, subprocess.SubprocessError):
            raise ValueError('Local animation encoding failed; no verified video is available.') from None
        return {'path': str(output), 'method': 'procedural_2d', 'frames': self.count, 'fps': self.fps,
                'duration': self.count/self.fps, 'review_required': True,
                'limitations': ['Geometry and closed-eye artwork were supplied by the native agent.',
                                'Pixels outside masks are unchanged before lossy H.264 encoding.',
                                'Independent source/video review is required; motion metrics do not prove quality.']}
