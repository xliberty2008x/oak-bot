"""Bounded visual evidence from verified bytes, decoded inside the executor.

These measurements are evidence for a reviewer, never a quality verdict. Every
decoded video frame contributes to motion selection; only selected frames are
shown to the model. No model-supplied screenshots or codec executables are used.
"""

import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import selectors
import subprocess
import sysconfig
import tempfile
import time


MAX_BYTES = 2 * 1024 * 1024 * 1024
MAX_PIXELS = 32_000_000
MAX_FRAMES = 18000
MAX_SECONDS = 600
EVIDENCE_BYTES = 9 * 1024 * 1024  # Includes base64, below the 16 MiB RPC limit.
FFMPEG = '/usr/bin/ffmpeg'
FFPROBE = '/usr/bin/ffprobe'


def codec_environment():
    """Resolve trusted distro alternatives without mounting host /etc wholesale."""
    arch = sysconfig.get_config_var('MULTIARCH') or ''
    libraries = []
    if re.fullmatch(r'[A-Za-z0-9_-]+', arch):
        for name in ('blas', 'lapack'):
            path = Path('/usr/lib') / arch / name
            if path.is_dir():
                libraries.append(str(path))
    return {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
            'LD_LIBRARY_PATH': ':'.join(libraries)}


def _run(arguments, timeout=20):
    return subprocess.run(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, timeout=timeout, check=True,
                          env=codec_environment()).stdout


def _kind(header):
    # Inspect bytes, not filename extensions. Playlist/URL demuxers are excluded.
    if header.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff', b'BM', b'II*\x00', b'MM\x00*')):
        return 'image'
    if header.startswith((b'GIF87a', b'GIF89a')):
        return 'image'
    if header[:4] == b'RIFF' and header[8:12] == b'WEBP':
        return 'image'
    if (header[4:8] in {b'ftyp', b'moov', b'mdat', b'wide'}
            or header.startswith((b'\x1aE\xdf\xa3', b'OggS', b'FLV', b'\x00\x00\x01\xba'))
            or header[:4] == b'RIFF' and header[8:12] == b'AVI '):
        return 'video'
    return 'unsupported'


def _jpeg(image, edge=1280):
    from PIL import Image
    image = image.convert('RGB')
    image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    data = io.BytesIO()
    image.save(data, format='JPEG', quality=85)
    return 'data:image/jpeg;base64,' + base64.b64encode(data.getvalue()).decode('ascii')


def _add(evidence, image, label, edge=1280):
    url = _jpeg(image, edge)
    if sum(len(row['image_url']) for row in evidence) + len(url) <= EVIDENCE_BYTES:
        evidence.append({'image_url': url, 'label': label})


def _sheet(frames, labels, *, cell_width=360, columns=3):
    from PIL import Image, ImageDraw
    if not frames:
        raise ValueError('No decoded frames')
    ratio = min(1.5, max(0.5, frames[0].height / frames[0].width))
    cell_height = int(cell_width * ratio)
    columns = min(columns, len(frames))
    result = Image.new('RGB', (cell_width * columns, (cell_height + 24) * math.ceil(len(frames) / columns)), '#202020')
    draw = ImageDraw.Draw(result)
    for index, (frame, label) in enumerate(zip(frames, labels)):
        frame = frame.copy().convert('RGB')
        factor = min(cell_width / frame.width, cell_height / frame.height)
        frame = frame.resize((max(1, round(frame.width * factor)), max(1, round(frame.height * factor))),
                             Image.Resampling.LANCZOS)
        x, y = (index % columns) * cell_width, (index // columns) * (cell_height + 24)
        result.paste(frame, (x + (cell_width - frame.width) // 2, y))
        draw.text((x + 5, y + cell_height + 4), label, fill='white')
    return result


def _image(path, result):
    from PIL import Image, ImageOps
    with Image.open(path) as source:
        if source.width * source.height > MAX_PIXELS:
            raise ValueError('Image dimensions exceed review limit')
        count = getattr(source, 'n_frames', 1)
        if count > 1:
            return _video(path, result)
        source.load()
        image = ImageOps.exif_transpose(source).convert('RGB')
    result.update(type='image', dimensions={'width': image.width, 'height': image.height},
                  frame_count=1, decoded_frames=1, decode_status='complete',
                  coverage='Full image plus overlapping detail crops; visual judgment remains fallible.')
    _add(result['evidence'], image, 'Complete image')
    if max(image.size) > 900:
        for x, y in ((0, 0), (0.45, 0), (0, 0.45), (0.45, 0.45)):
            box = (int(x * image.width), int(y * image.height),
                   min(image.width, int((x + .55) * image.width)),
                   min(image.height, int((y + .55) * image.height)))
            _add(result['evidence'], image.crop(box), 'Image detail, pixel bounds ' + str(box), 900)
    return result


def _number(value):
    try:
        if isinstance(value, str) and '/' in value:
            a, b = value.split('/', 1)
            number = float(a) / float(b)
        else:
            number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def _local_motion_regions(frames, peaks, maximum=3):
    """Find small changing details from transitions, with no subject assumptions.

    A union of all movement may include an entire character or scene. Small
    spatial windows isolate details that otherwise disappear in that context.
    Consecutive frames matter: comparing only to frame zero makes accumulated
    broad movement dominate brief local transitions.
    """
    import numpy as np
    first = next(iter(frames.values()))
    width, height = first.size
    window = max(2, round(min(width, height) * .018))
    crop_width, crop_height = max(12, round(width * .16)), max(12, round(height * .16))
    candidates = []
    for peak in peaks:
        previous = peak - 1
        if previous not in frames or peak not in frames:
            continue
        delta = np.abs(np.asarray(frames[peak], dtype=np.float32)
                       - np.asarray(frames[previous], dtype=np.float32)).mean(axis=2)
        sums = np.pad(delta, ((1, 0), (1, 0))).cumsum(axis=0).cumsum(axis=1)
        scores = (sums[window:, window:] - sums[:-window, window:]
                  - sums[window:, :-window] + sums[:-window, :-window]) / (window * window)
        for _ in range(maximum):
            y, x = np.unravel_index(scores.argmax(), scores.shape)
            score = float(scores[y, x])
            if score < 1:
                break
            center_x, center_y = int(x) + window // 2, int(y) + window // 2
            left = max(0, min(width - crop_width, center_x - crop_width // 2))
            top = max(0, min(height - crop_height, center_y - crop_height // 2))
            candidates.append({'score': score, 'peak_frame': peak,
                               'bounds': (left, top, left + crop_width, top + crop_height)})
            scores[max(0, y - crop_height // 2):y + crop_height // 2 + 1,
                   max(0, x - crop_width // 2):x + crop_width // 2 + 1] = 0
    regions = []
    for candidate in sorted(candidates, key=lambda item: item['score'], reverse=True):
        box = candidate['bounds']
        # Keep distinct spatial details, rather than several views of the same
        # high-contrast edge in successive movement peaks.
        overlaps = False
        for existing in regions:
            other = existing['bounds']
            area = max(0, min(box[2], other[2]) - max(box[0], other[0])) * max(0, min(box[3], other[3]) - max(box[1], other[1]))
            if area > crop_width * crop_height * .3:
                overlaps = True
                break
        if not overlaps:
            regions.append(candidate)
        if len(regions) >= maximum:
            break
    return regions


def _stream_frames(path, width, height, deadline):
    """Decode every frame at bounded resolution without accumulating raw video."""
    command = [FFMPEG, '-nostdin', '-v', 'error', '-xerror', '-threads', '1',
               '-protocol_whitelist', 'file,pipe', '-i', str(path), '-map', '0:v:0',
               '-an', '-sn', '-dn', '-vf', f'scale={width}:{height}', '-vsync', '0',
               '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-threads', '1', 'pipe:1']
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, env=codec_environment())
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    pending = bytearray()
    frame_bytes = width * height * 3
    count = 0
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(min(remaining, 1)):
                if remaining <= 0:
                    raise TimeoutError('Video decode deadline reached')
                continue
            block = os.read(process.stdout.fileno(), 65536)
            if not block:
                break
            pending.extend(block)
            while len(pending) >= frame_bytes:
                count += 1
                if count > MAX_FRAMES:
                    raise ValueError('Video frame count exceeds review limit')
                yield bytes(pending[:frame_bytes])
                del pending[:frame_bytes]
        if pending or process.wait(timeout=max(.1, deadline - time.monotonic())) != 0:
            raise ValueError('Video did not decode completely')
    finally:
        selector.close()
        process.stdout.close()
        if process.poll() is None:
            process.kill()
        process.wait()


def _video(path, result):
    import numpy as np
    from PIL import Image
    probe = json.loads(_run([FFPROBE, '-v', 'error', '-protocol_whitelist', 'file,pipe',
                            '-show_entries', 'stream=codec_type,width,height,avg_frame_rate,r_frame_rate,duration:format=duration',
                            '-of', 'json', str(path)]))
    stream = next((s for s in probe.get('streams', []) if s.get('codec_type') == 'video'), None)
    if not stream:
        raise ValueError('No video stream')
    width, height = stream.get('width'), stream.get('height')
    if (type(width) is not int or type(height) is not int or min(width, height) < 1
            or width * height > MAX_PIXELS):
        raise ValueError('Video dimensions exceed review limit')
    fps = _number(stream.get('avg_frame_rate')) or _number(stream.get('r_frame_rate'))
    duration = _number(stream.get('duration')) or _number(probe.get('format', {}).get('duration'))
    if duration and duration > MAX_SECONDS:
        raise ValueError('Video duration exceeds review limit')
    if (fps and duration and fps * duration > MAX_FRAMES * 1.1):
        raise ValueError('Video frame count exceeds review limit')
    scale = min(1, 256 / max(width, height))
    small_width, small_height = max(1, round(width * scale)), max(1, round(height * scale))
    previous = mean = variation = None
    scores, global_motion = [], []
    deadline = time.monotonic() + 95
    for index, raw in enumerate(_stream_frames(path, small_width, small_height, deadline)):
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(small_height, small_width, 3).astype(np.float32)
        gray = frame.mean(axis=2)
        if previous is None:
            mean, variation = gray.copy(), np.zeros_like(gray)
            scores.append(0.)
            global_motion.append(0.)
        else:
            delta = np.abs(frame - previous).mean(axis=2)
            # The strongest 0.1% of pixels exposes brief local movement even
            # when a large background remains static (or other areas drift).
            flat = delta.ravel()
            top = max(1, len(flat) // 1000)
            scores.append(float(np.partition(flat, -top)[-top:].mean()))
            global_motion.append(float(delta.mean()))
            change = gray - mean
            mean += change / (index + 1)
            variation += change * (gray - mean)
        previous = frame
    count = len(scores)
    if not count:
        raise ValueError('No decoded frames')
    uniform = sorted(set(round(i * (count - 1) / 11) for i in range(12)))
    peaks = []
    separation = max(2, round((fps or 25) * .15))
    for index in sorted(range(1, count), key=lambda i: scores[i], reverse=True):
        if scores[index] <= .1:
            break
        if all(abs(index - chosen) >= separation for chosen in peaks):
            peaks.append(index)
        if len(peaks) == 6:
            break
    selected = sorted(set(uniform + [max(0, min(count - 1, i + d)) for i in peaks for d in (-2, -1, 0, 1, 2)]))
    expression = '+'.join(f'eq(n\\,{index})' for index in selected)
    with tempfile.TemporaryDirectory(prefix='oak-quality-frames-') as folder:
        _run([FFMPEG, '-nostdin', '-v', 'error', '-xerror', '-threads', '1',
              '-protocol_whitelist', 'file,pipe', '-i', str(path), '-map', '0:v:0',
              '-an', '-sn', '-dn', '-vf', f'select={expression},scale=\'min(1280,iw)\':\'min(1280,ih)\':force_original_aspect_ratio=decrease',
              '-vsync', '0', '-frames:v', str(len(selected)), '-q:v', '2', '-threads', '1',
              str(Path(folder) / '%04d.jpg')], timeout=45)
        paths = sorted(Path(folder).glob('*.jpg'))
        if len(paths) != len(selected):
            raise ValueError('Selected frame extraction was incomplete')
        frames = {}
        for index, filename in zip(selected, paths):
            with Image.open(filename) as image:
                frames[index] = image.copy()
    def label(index):
        return f'frame {index}' + (f', approx {index / fps:.3f}s' if fps else '')
    evidence = result['evidence']
    _add(evidence, _sheet([frames[i] for i in uniform], [label(i) for i in uniform]),
         'Uniform sequence across the complete video', 1800)
    moving = sorted(set(max(0, min(count - 1, peak + delta)) for peak in peaks for delta in (-2, -1, 0, 1, 2)))
    for start in range(0, len(moving), 9):
        indices = moving[start:start + 9]
        _add(evidence, _sheet([frames[i] for i in indices], [label(i) for i in indices]),
             'Brief movement transitions, ordered by frame index', 1800)
    local_regions = _local_motion_regions(frames, peaks)
    region_metadata = []
    for region in local_regions:
        peak, box = region['peak_frame'], region['bounds']
        indices = [0, *[i for i in range(max(1, peak - 2), peak + 3) if i in frames]]
        normalized = [round(value / (frames[0].width if i % 2 == 0 else frames[0].height), 5)
                      for i, value in enumerate(box)]
        _add(evidence, _sheet([frames[i].crop(box) for i in indices],
                             [('Baseline ' if i == 0 else '') + label(i) for i in indices],
                             cell_width=500, columns=2),
             'Magnified localized transition, normalized bounds ' + str(normalized)
             + '; compare texture, edges and shading with baseline and surrounding transition', 2000)
        region_metadata.append({'normalized_bounds': normalized, 'peak_frame': peak,
                                'transition_score': round(region['score'], 5), 'frame_indices': indices})
    # One generic spatial detail region derived from temporal variation. It
    # covers changing pixels rather than assuming a face or particular subject.
    deviation = np.sqrt(variation / max(count - 1, 1))
    changed = deviation > max(1.5, float(deviation.max()) * .08)
    detail_box = None
    if changed.any():
        ys, xs = np.where(changed)
        pad = max(3, round(min(small_width, small_height) * .05))
        detail_box = (max(0, int(xs.min()) - pad) / small_width,
                      max(0, int(ys.min()) - pad) / small_height,
                      min(small_width, int(xs.max()) + pad + 1) / small_width,
                      min(small_height, int(ys.max()) + pad + 1) / small_height)
        detail_indices = sorted(set([0, *moving]))
        crops = []
        for index in detail_indices:
            frame = frames[index]
            box = tuple(round(value * (frame.width if i % 2 == 0 else frame.height))
                        for i, value in enumerate(detail_box))
            crops.append(frame.crop(box))
        for start in range(0, len(crops), 6):
            _add(evidence, _sheet(crops[start:start + 6], [label(i) for i in detail_indices[start:start + 6]],
                                 cell_width=500, columns=2),
                 'Detail of the automatically detected changing region, normalized bounds ' + str(detail_box), 2000)
    _add(evidence, frames[uniform[0]], 'First frame, complete composition', 1280)
    result.update(type='video', dimensions={'width': width, 'height': height}, duration=duration,
                  fps=fps, frame_count=count, decoded_frames=count, decode_status='complete',
                  sampled_frame_indices=selected, motion_peak_indices=peaks,
                  localized_regions=region_metadata,
                  mean_frame_change=round(sum(global_motion) / count, 5),
                  max_local_frame_change=round(max(scores), 5),
                  coverage=('Every frame decoded at reduced resolution for movement selection; visual evidence '
                            'contains uniform frames and local movement peaks with neighbors. Audio is not evaluated; '
                            'sampled visual review cannot establish quality of every frame.'),
                  audio_present=any(s.get('codec_type') == 'audio' for s in probe.get('streams', [])))
    return result


def inspect_media(files, path, sha256):
    """Copy through held descriptors and compare against gateway snapshot hash."""
    if not isinstance(sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', sha256):
        raise ValueError('Expected SHA256 is required')
    opened = files.open_read(path)
    handle = opened['handle']
    try:
        if not 0 <= opened['size'] <= MAX_BYTES:
            raise ValueError('File exceeds review limit')
        with tempfile.TemporaryDirectory(prefix='oak-quality-input-') as folder:
            source = Path(folder) / 'input'
            digest, size = hashlib.sha256(), 0
            with source.open('xb') as output:
                while True:
                    chunk = files.read_chunk(handle)
                    data = base64.b64decode(chunk['data'], validate=True)
                    size += len(data)
                    if size > opened['size'] or size > MAX_BYTES:
                        raise ValueError('File changed during inspection')
                    output.write(data)
                    digest.update(data)
                    if chunk['eof']:
                        break
            if size != opened['size'] or digest.hexdigest() != sha256:
                raise ValueError('File does not match reviewed snapshot')
            with source.open('rb') as input_file:
                kind = _kind(input_file.read(32))
            result = {'sha256': sha256, 'size': size, 'type': kind, 'decode_status': 'unsupported', 'evidence': []}
            try:
                if kind == 'image':
                    result = _image(source, result)
                elif kind == 'video':
                    result = _video(source, result)
                else:
                    result['coverage'] = 'Unsupported visual format; no visual quality claim can be made.'
            except (OSError, ValueError, TimeoutError, subprocess.SubprocessError):
                result.update(decode_status='failed', evidence=[], coverage='Media could not be completely decoded within review limits.')
            after = hashlib.sha256()
            with source.open('rb') as input_file:
                while block := input_file.read(65536):
                    after.update(block)
            if after.hexdigest() != sha256:
                raise ValueError('Inspected bytes changed during decoding')
            return result
    finally:
        if handle in files.handles:
            files.close(handle)
