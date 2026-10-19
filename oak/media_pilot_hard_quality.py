"""Independent content checks for the disclosed, synthetic harder media tasks.

The producer sees the source layers, placement/timing contract and tolerances.
Only this runner sees the reference compositor.  We compare decoded pixels in
separately named critical regions, with a small disclosed placement allowance;
we do not compare PNG hashes or require bit-identical H.264 encoding.  Every
video frame is decoded, including frames between the supplied checkpoints.

``workspace`` supplies the original read-only inputs. ``path`` may be a separate
runner-owned immutable snapshot: the caller must freeze it and verify its hash
before/after scoring. This function also copies bounded regular-file bytes to
private scratch space before passing them to an external decoder.
"""

from fractions import Fraction
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

import numpy as np
from PIL import Image

from .media_pilot_hard_tasks import quality_spec, reference_frame


_MAX_BYTES = 32 * 1024 * 1024
_MAX_PIXELS = 2_000_000
_MAX_FRAMES = 240
_LIMITATION = (
    'Measures the disclosed synthetic source-layer, text-layer, spatial and temporal '
    'requirements in every decoded frame. It does not establish naturalistic motion, '
    'general aesthetics, OCR accuracy for arbitrary fonts, or quality on external media.'
)


def _bounded_bytes(path):
    path = Path(path).absolute()
    # Do not follow either the submitted file or a parent substituted by a producer.
    if path.resolve(strict=True) != path:
        raise ValueError('Artifact path contains a symbolic link.')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                         | getattr(os, 'O_NONBLOCK', 0))
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= _MAX_BYTES:
            raise ValueError('Artifact must be an ordinary nonempty file of at most 32 MiB.')
        with os.fdopen(descriptor, 'rb', closefd=False) as source:
            data = source.read(_MAX_BYTES + 1)
        if not data or len(data) > _MAX_BYTES:
            raise ValueError('Artifact exceeds its decoding budget.')
        return data
    finally:
        os.close(descriptor)


def _probe_and_decode(data, spec, check):
    ffprobe, ffmpeg = shutil.which('ffprobe'), shutil.which('ffmpeg')
    if not ffprobe or not ffmpeg:
        check('video_decoding', None, 'FFmpeg and ffprobe are required to verify video content.')
        return None
    width, height = spec['size']
    count = spec['frame_count']
    if width * height > _MAX_PIXELS or not 1 <= count <= _MAX_FRAMES:
        raise ValueError('Task exceeds the full-frame decoding budget.')
    with tempfile.TemporaryDirectory(prefix='oak-hard-quality-') as directory:
        snapshot = Path(directory) / 'artifact.mp4'
        snapshot.write_bytes(data)
        probe = subprocess.run(
            [ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe', '-f', 'mov',
             '-show_streams', '-show_format', '-of', 'json', str(snapshot)],
            capture_output=True, check=True, timeout=20)
        metadata = json.loads(probe.stdout)
        streams = metadata['streams']
        videos = [item for item in streams if item.get('codec_type') == 'video']
        stream = videos[0] if videos else {}
        brand = metadata['format'].get('tags', {}).get('major_brand', '').strip()
        mp4_brand = brand.startswith(('iso', 'mp4')) or brand in {'avc1', 'M4V', 'MSNV'}
        check('silent_h264_mp4', len(videos) == 1 and len(streams) == 1
              and stream.get('codec_name') == 'h264'
              and 'mp4' in metadata['format'].get('format_name', '') and mp4_brand,
              'Required: one H.264 video stream in MP4 and no audio or auxiliary streams.')
        check('yuv420p', stream.get('pix_fmt') == 'yuv420p',
              f"Observed {stream.get('pix_fmt')}; required yuv420p.")
        same_size = (stream.get('width'), stream.get('height')) == (width, height)
        check('dimensions', same_size,
              f"Observed {stream.get('width')}×{stream.get('height')}; required {width}×{height}.")
        if not same_size:
            return None
        fps = float(Fraction(stream.get('avg_frame_rate', '0/1')))
        duration = float(metadata['format'].get('duration', 0))
        check('frame_rate', abs(fps - spec['fps']) < .01,
              f"Observed {fps:.4f} fps; required {spec['fps']} fps.")
        duration_tolerance = 1 / spec['fps'] + .005
        check('duration', abs(duration - spec['duration']) <= duration_tolerance,
              f"Observed {duration:.4f}s; required {spec['duration']}s ±one frame.")
        timestamps = subprocess.run(
            [ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe', '-f', 'mov',
             '-read_intervals', f'%+#{count + 16}', '-select_streams', 'v:0',
             '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', str(snapshot)],
            capture_output=True, check=True, timeout=25)
        times = [float(item['best_effort_timestamp_time'])
                 for item in json.loads(timestamps.stdout).get('frames', [])
                 if 'best_effort_timestamp_time' in item]
        cadence = bool(times) and len(times) == count and all(
            abs(moment - index / spec['fps']) <= .1 / spec['fps']
            for index, moment in enumerate(times))
        check('complete_regular_timestamps', cadence,
              f'{len(times)} timestamped frames; all must occur at their declared frame times '
              'within one tenth of a frame.')
        raw = subprocess.run(
            [ffmpeg, '-v', 'error', '-filter_threads', '1', '-filter_complex_threads', '1',
             '-protocol_whitelist', 'file,pipe', '-f', 'mov', '-threads', '1',
             '-i', str(snapshot), '-map', '0:v:0', '-vsync', '0', '-frames:v', str(count + 1),
             '-threads', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
            capture_output=True, check=True, timeout=45).stdout
        frame_bytes = width * height * 3
        observed = len(raw) // frame_bytes
        check('complete_decoded_sequence', len(raw) % frame_bytes == 0 and observed == count,
              f'{observed} full-resolution frames decoded; required {count}.')
        if observed != count or len(raw) % frame_bytes:
            return None
        return np.frombuffer(raw, dtype=np.uint8).reshape(count, height, width, 3)


def _baseline(spec, workspace):
    """Obtain the disclosed stationary source, never producer-created output."""
    assets = spec.get('assets', {})
    path = assets.get('background') or assets.get('scene')
    if path:
        path = Path(path)
        path.resolve(strict=True).relative_to((Path(workspace) / 'inputs').resolve(strict=True))
        with Image.open(path) as image:
            if image.size == tuple(spec['size']):
                return np.asarray(image.convert('RGB'), dtype=np.int16)
    # A full composition can start from a disclosed solid canvas. This baseline
    # only selects salient pixels; expected pixels still come from all layers.
    color = spec.get('contract', {}).get('background_color', '#ffffff')
    return np.asarray(Image.new('RGB', tuple(spec['size']), color), dtype=np.int16)


def _codec_references(task_id, workspace, spec, baseline):
    """Use the delivery codec's actual uncompressed 4:2:0 color conversion.

    No lossy encoding, bitrate or CRF is fitted to a submission. The mandatory
    chroma sampling is fixed before scoring, using FFmpeg RGB→yuv420p→RGB. The
    source baseline undergoes the same transformation. Writing one frame at a
    time avoids retaining the original PIL sequence or another full RGB join.
    """
    width, height = spec['size']
    count = spec['frame_count'] + 1
    with tempfile.TemporaryDirectory(prefix='oak-hard-color-') as directory:
        raw_path = Path(directory) / 'reference.rgb'
        with raw_path.open('wb') as output:
            output.write(baseline.astype(np.uint8).tobytes())
            for index in range(spec['frame_count']):
                frame = reference_frame(task_id, workspace, index)
                if frame.size != (width, height):
                    raise ValueError('Reference renderer returned invalid geometry.')
                output.write(frame.convert('RGB').tobytes())
        raw = subprocess.run(
            [shutil.which('ffmpeg'), '-v', 'error', '-filter_threads', '1',
             '-filter_complex_threads', '1', '-threads', '1', '-f', 'rawvideo',
             '-pixel_format', 'rgb24', '-video_size', f'{width}x{height}',
             '-framerate', str(spec['fps']), '-i', str(raw_path),
             '-vf', 'format=yuv420p,format=rgb24', '-frames:v', str(count),
             '-threads', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
            capture_output=True, check=True, timeout=45).stdout
    if len(raw) != count * width * height * 3:
        raise ValueError('Color conversion did not return the complete reference.')
    return np.frombuffer(raw, dtype=np.uint8).reshape(count, height, width, 3)


def _region_error(observed, expected, baseline, box, tolerance, support_mode='adaptive', deadband=0):
    """Compression-aware regional MAE with a bounded local placement allowance.

    The support includes discrepancies in *both* the expected and submitted
    composition against the source. Thus a misplaced/duplicated small subject
    cannot disappear into a large swept bounding box's stationary background.
    A local translation allowance does not permit any rotation, scale or color
    changes to the supplied art/text layer.
    """
    left, top, right, bottom = box
    target = expected[top:bottom, left:right]
    base = baseline[top:bottom, left:right]
    expected_support = np.max(np.abs(target - base), axis=2) > 12
    height, width = expected.shape[:2]
    candidates = [(0, 0)] + [(x, y) for y in range(-tolerance, tolerance + 1)
                            for x in range(-tolerance, tolerance + 1) if x or y]
    best = float('inf')
    for x, y in candidates:
        if left + x < 0 or top + y < 0 or right + x > width or bottom + y > height:
            continue
        submitted = observed[top + y:bottom + y, left + x:right + x]
        support = expected_support | (np.max(np.abs(submitted - base), axis=2) > 16)
        if support_mode == 'full' or not expected_support.any():
            support = np.ones(target.shape[:2], dtype=bool)
        error = float(np.maximum(0, np.abs(submitted - target)[support] - deadband).mean())
        best = min(best, error)
        # Exact/near-exact matches need no search. We still score the actual error
        # instead of treating all accepted frames as indistinguishable.
        if best < .25:
            break
    return best


def _content_checks(task_id, decoded, spec, workspace, check):
    baseline = _baseline(spec, workspace)
    references = None
    if spec['duration']:
        references = _codec_references(task_id, workspace, spec, baseline)
        baseline = references[0].astype(np.int16)
    width, height = spec['size']
    static = np.ones((height, width), dtype=bool)
    regions = spec['regions']
    if not regions:
        check('disclosed_content_regions', None, 'No source-backed critical regions were supplied.')
        return
    for region in regions:
        left, top, right, bottom = region['box']
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            raise ValueError('Invalid task region.')
        static[top:bottom, left:right] = False
    region_errors = {region['name']: [] for region in regions}
    stationary_errors = []
    default_tolerance = int(spec.get('contract', {}).get('acceptance', {}).get('geometry_tolerance_px', 2))
    deadband = int(spec.get('contract', {}).get('acceptance', {}).get(
        'channel_noise_deadband', 2 if spec['duration'] else 0))
    if not 0 <= deadband <= 2:
        raise ValueError('Invalid task codec noise allowance.')
    for index, frame in enumerate(decoded):
        if references is not None:
            expected = references[index + 1].astype(np.int16)
        else:
            expected_image = reference_frame(task_id, workspace, index)
            if expected_image.size != (width, height):
                raise ValueError('Reference renderer returned invalid geometry.')
            expected = np.asarray(expected_image.convert('RGB'), dtype=np.int16)
        observed = np.asarray(frame, dtype=np.int16)
        if static.any():
            stationary_errors.append(float(np.maximum(0, np.abs(observed - expected)[static] - deadband).mean()))
        for region in regions:
            tolerance = min(2, max(0, int(region.get('geometry_tolerance_px', default_tolerance))))
            region_errors[region['name']].append(_region_error(
                observed, expected, baseline, region['box'], tolerance,
                region.get('support_mode', 'adaptive'), deadband))
    if stationary_errors:
        threshold = 7.0 if spec['duration'] else 2.0
        worst = max(stationary_errors)
        check('stationary_source_preservation', worst <= threshold,
              f'All {len(decoded)} frames: worst stationary-region MAE {worst:.3f}/255; '
              f'allowed {threshold:g}, per-channel noise allowance {deadband}. '
              'Outside disclosed editable/layer regions.',
              max(0., 1 - worst / (2 * threshold)))
    for region in regions:
        errors = np.asarray(region_errors[region['name']])
        threshold = float(region['max_mae'])
        if not 0 < threshold <= 20:
            raise ValueError('Invalid task tolerance.')
        p95, worst = float(np.percentile(errors, 95)), float(errors.max())
        # The tail bound tolerates isolated codec edge errors, but cannot hide a
        # missing blink, wrong checkpoint, or a one-frame large composition jump.
        effective = max(p95, worst / 1.5)
        failed_frames = int(np.sum(errors > threshold))
        check('content:' + region['name'], effective <= threshold,
              f"{region.get('role', 'art')} region {region['box']}: every frame checked; "
              f'p95 MAE {p95:.3f}, worst {worst:.3f}/255; allowed p95 ≤{threshold:g} '
              f'and worst ≤{1.5 * threshold:g}; {failed_frames}/{len(errors)} frames '
              f'exceed the nominal tolerance; per-channel noise allowance {deadband}. '
              'Uses source/text layers, alpha and disclosed timing.',
              max(0., 1 - effective / (2 * threshold)))


def evaluate_artifact(task_id, path, workspace, *, artifact_root=None):
    """Evaluate an immutable candidate against original, runner-owned task inputs.

    A pass concerns these deliberately constrained source-layer tasks. Model
    reviews remain separate secondary evidence and cannot override a failed
    disclosed requirement. Decode/tool failures are never semantic passes.
    """
    spec = quality_spec(task_id, workspace)
    checks = []

    def check(criterion, passed, evidence, score=None):
        status = 'unverified' if passed is None else 'pass' if passed else 'fail'
        item = {'criterion': criterion, 'status': status, 'evidence': evidence}
        if score is not None:
            item['score'] = round(max(0., min(1., float(score))), 6)
        checks.append(item)

    def result():
        statuses = {item['status'] for item in checks}
        status = 'fail' if 'fail' in statuses else 'unverified' if 'unverified' in statuses else 'pass'
        content_scores = [item['score'] for item in checks if 'score' in item]
        quality = sum(content_scores) / len(content_scores) if content_scores else 0.
        structural = {item['status'] for item in checks
                      if not item['criterion'].startswith('content:')
                      and item['criterion'] != 'stationary_source_preservation'}
        structural_status = ('fail' if 'fail' in structural else 'unverified'
                             if 'unverified' in structural else 'pass')
        return {'task_id': task_id, 'status': status, 'checks': checks,
                'structural_only': False, 'quality_score': round(quality, 6),
                'structural_status': structural_status,
                'limitation': _LIMITATION}

    try:
        path = Path(path)
        if not path.is_absolute():
            path = Path(workspace) / path
        if artifact_root is not None:
            path.resolve(strict=True).relative_to(Path(artifact_root).resolve(strict=True))
        data = _bounded_bytes(path)
        check('bounded_regular_artifact', True, 'Frozen nonempty regular-file bytes, at most 32 MiB.')
    except (OSError, ValueError):
        check('bounded_regular_artifact', False, 'Artifact is missing, symbolic, not regular, empty or over 32 MiB.')
        return result()
    try:
        if spec['duration']:
            decoded = _probe_and_decode(data, spec, check)
        else:
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > _MAX_PIXELS:
                    raise ValueError('Image exceeds decoding budget.')
                image.load()
                check('opaque_static_png', image.format == 'PNG' and not getattr(image, 'is_animated', False)
                      and image.mode == 'RGB',
                      'Required: a static RGB PNG with fully opaque output pixels.')
                correct_size = image.size == tuple(spec['size'])
                check('dimensions', correct_size, f"Observed {image.size}; required {tuple(spec['size'])}.")
                decoded = [np.asarray(image.convert('RGB'))] if correct_size else None
        if decoded is not None:
            _content_checks(task_id, decoded, spec, workspace, check)
    except (OSError, ValueError, KeyError, IndexError, ZeroDivisionError,
            subprocess.SubprocessError, Image.DecompressionBombError):
        check('content_decoding', False, 'Artifact or source-backed content could not be decoded within the limits.')
    return result()
