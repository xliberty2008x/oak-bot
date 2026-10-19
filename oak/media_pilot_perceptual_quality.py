"""Source-backed property checks for the inferred media-task pilot.

The blink oracle describes properties, not one hidden target animation. Source
landmarks/masks are regenerated only by the runner, never placed in a producer
workspace. Occlusion targets use supplied patterns and the publicly stated
motion; restoration targets are the undamaged original represented by the
recoverable donor. Visual review remains necessary for eyelid coherence, halos,
and aesthetics; a property pass cannot override a visual rejection. Every decoded
video frame is checked. Positive/negative controls exercise the actual codec and
multiple acceptable blink curves and peak times without provider/model calls.
"""

import io
from pathlib import Path
import shutil
import subprocess
import tempfile

import numpy as np
from PIL import Image, ImageFilter

from oak.media_pilot_hard_quality import _bounded_bytes, _probe_and_decode


_LIMITATION = (
    'Measures the disclosed synthetic source properties. Blink tests iris occlusion, '
    'open-state restoration, pupil position and temporal regularity, not an exact '
    'movie. Semantic source masks are runner-only. Source-preserving occlusion and '
    'registered restoration are compared within disclosed tolerances. Independent '
    'visual review must still accept coherent eyelids, edge quality and aesthetics. '
    'Lid-palette checks constrain iris crossfades but cannot distinguish a '
    'source-colored rectangular wipe from a coherent eyelid; the visual veto is mandatory.'
)


def _mask(image, size):
    if not isinstance(image, Image.Image) or image.size != tuple(size):
        raise ValueError('Runner semantic mask has invalid geometry.')
    return np.asarray(image.convert('L')) > 127


def _dilate(mask, radius):
    return np.asarray(Image.fromarray((mask * 255).astype(np.uint8)).filter(
        ImageFilter.MaxFilter(2 * radius + 1))) > 127 if radius else mask


def _erode(mask, radius):
    return np.asarray(Image.fromarray((mask * 255).astype(np.uint8)).filter(
        ImageFilter.MinFilter(2 * radius + 1))) > 127 if radius else mask


def _rgb(image, size):
    if not isinstance(image, Image.Image) or image.size != tuple(size):
        raise ValueError('Runner source image has invalid geometry.')
    return image.convert('RGB')


def _source(spec):
    with Image.open(spec['assets']['scene']) as image:
        return _rgb(image, spec['size'])


def _codec_arrays(images, size):
    """Fixed RGB -> uncompressed yuv420p -> RGB conversion, no fitted CRF."""
    width, height = size
    count = 0
    with tempfile.TemporaryDirectory(prefix='oak-property-color-') as directory:
        raw_path = Path(directory) / 'reference.rgb'
        with raw_path.open('wb') as output:
            for image in images:
                output.write(_rgb(image, size).tobytes())
                count += 1
                if count > 241:
                    raise ValueError('Reference color conversion exceeds the frame budget.')
        if not count:
            raise ValueError('No source image supplied.')
        data = subprocess.run(
            [shutil.which('ffmpeg'), '-v', 'error', '-filter_threads', '1',
             '-filter_complex_threads', '1', '-threads', '1', '-f', 'rawvideo',
             '-pixel_format', 'rgb24', '-video_size', f'{width}x{height}',
             '-i', str(raw_path), '-vf', 'format=yuv420p,format=rgb24',
             '-frames:v', str(count), '-threads', '1', '-f', 'rawvideo',
             '-pix_fmt', 'rgb24', '-'], capture_output=True, check=True, timeout=45).stdout
    if len(data) != count * width * height * 3:
        raise ValueError('Source color conversion returned an incomplete sequence.')
    return np.frombuffer(data, dtype=np.uint8).reshape(count, height, width, 3)


def _error(observed, expected, support, *, geometry=0, deadband=0):
    """MAE within semantic support, with only the disclosed placement allowance."""
    rows, columns = np.nonzero(support)
    if not len(rows):
        raise ValueError('A required semantic support is empty.')
    top, bottom = int(rows.min()), int(rows.max()) + 1
    left, right = int(columns.min()), int(columns.max()) + 1
    mask = support[top:bottom, left:right]
    reference = expected[top:bottom, left:right].astype(np.int16)
    height, width = observed.shape[:2]
    best = float('inf')
    for dy in range(-geometry, geometry + 1):
        for dx in range(-geometry, geometry + 1):
            if min(top + dy, left + dx) < 0 or bottom + dy > height or right + dx > width:
                continue
            sample = observed[top + dy:bottom + dy, left + dx:right + dx].astype(np.int16)
            error = float(np.maximum(0, np.abs(sample - reference)[mask] - deadband).mean())
            best = min(best, error)
    return best


def _error_check(check, name, errors, threshold, evidence):
    if not errors:
        check(name, None, 'No observable frames for this disclosed criterion.')
        return
    values = np.asarray(errors, dtype=float)
    p95, worst = float(np.percentile(values, 95)), float(values.max())
    effective = max(p95, worst / 1.5)
    check(name, effective <= threshold,
          f'{evidence} Every applicable frame checked: p95 MAE {p95:.3f}, '
          f'worst {worst:.3f}; allowed {threshold:g} and {threshold * 1.5:g}.',
          max(0., 1 - effective / (2 * threshold)))


def _settings(spec):
    acceptance = spec['contract']['acceptance']
    # Every one of these values must be in the producer's public contract.
    required = ('geometry_tolerance_px', 'channel_noise_deadband', 'motion_mae',
                'foreground_mae', 'png_mae', 'stationary_mae')
    if any(key not in acceptance for key in required):
        raise ValueError('A property tolerance was not disclosed.')
    return acceptance


def _grade_blink(decoded, spec, check):
    acceptance = _settings(spec)
    required = ('closure_coverage', 'max_closure_step', 'monotone_slack',
                'iris_change_threshold', 'pupil_color_tolerance',
                'pupil_preserved_fraction', 'pupil_visibility_min_fraction', 'lid_color_tolerance')
    if any(key not in acceptance for key in required):
        raise ValueError('A blink property threshold was not disclosed.')
    source = _codec_arrays([_source(spec)], spec['size'])[0].astype(np.int16)
    properties = spec['properties']
    windows = [tuple(window) for window in properties['blink_windows']]
    if len(windows) != 2 or any(not 0 <= first <= last < len(decoded) for first, last in windows):
        raise ValueError('Blink windows must identify two disclosed frame intervals.')
    outer_union = np.zeros(tuple(reversed(spec['size'])), dtype=bool)
    eye_specs = []
    for eye in properties['eyes']:
        outer, iris, pupil = [_mask(eye[key], spec['size']) for key in ('outer', 'iris', 'pupil')]
        if not (iris.any() and pupil.any()) or np.any(iris & ~outer) or np.any(pupil & ~iris):
            raise ValueError('Invalid runner eye landmarks.')
        outer_union |= outer
        lid_colors = np.asarray(eye['lid_colors'], dtype=np.int16)
        if lid_colors.ndim != 2 or lid_colors.shape[1] != 3 or not len(lid_colors):
            raise ValueError('The runner must identify source-visible eyelid/fur colors.')
        eye_specs.append((eye['name'], outer, iris, pupil, lid_colors))
    deadband = acceptance['channel_noise_deadband']
    protected_errors = [_error(frame, source, ~outer_union, deadband=deadband) for frame in decoded]
    _error_check(check, 'stationary_outside_eyelids', protected_errors,
                 acceptance['stationary_mae'], 'Only the source eyelid regions may change.')
    open_frames = [index for index in range(len(decoded))
                   if not any(first <= index <= last for first, last in windows)]
    if not open_frames:
        raise ValueError('Blink task does not disclose observable open states.')
    for name, outer, iris, pupil, lid_colors in eye_specs:
        closure = []
        pupil_inside = []
        allowed_pupil = _dilate(pupil, acceptance['geometry_tolerance_px'])
        pupil_color = np.median(source[pupil], axis=0)
        pupil_minimum = max(4, int(np.count_nonzero(pupil) * acceptance['pupil_visibility_min_fraction']))
        for frame in decoded:
            observed = frame.astype(np.int16)
            changed = np.maximum(0, np.abs(observed - source) - deadband).max(axis=2)
            # A ghosted/crossfaded iris is changed, but is not a closed eyelid.
            # Require actual source-iris removal AND a source-visible lid/fur
            # palette match. Closed seams may occupy the remaining <=15% area.
            iris_pixels = observed[iris]
            palette_distance = np.min(np.max(np.abs(
                iris_pixels[:, None, :] - lid_colors[None, :, :]), axis=2), axis=1)
            lid_fill = palette_distance <= acceptance['lid_color_tolerance']
            removed = changed[iris] > acceptance['iris_change_threshold']
            closure.append(float(np.mean(removed & lid_fill)))
            # Source pupils have a visually distinct color from the lid outline.
            # We grade surviving pupil-colored pixels, allowing full occlusion.
            visible = outer & (np.max(np.abs(observed - pupil_color), axis=2)
                               <= acceptance['pupil_color_tolerance'])
            count = int(visible.sum())
            if count >= pupil_minimum:
                pupil_inside.append(float(np.count_nonzero(visible & allowed_pupil) / count))
        _error_check(check, 'open_source_restored:' + name,
                     [_error(decoded[index], source, outer, deadband=deadband) for index in open_frames],
                     acceptance['foreground_mae'], 'Outside both blink windows, restore the original open eye.')
        for number, (first, last) in enumerate(windows, 1):
            series = np.asarray(closure[first:last + 1])
            peak_index = int(np.argmax(series))
            peak = float(series[peak_index])
            check(f'iris_closed:{name}:{number}', peak >= acceptance['closure_coverage'],
                  f'Maximum source-iris occlusion in disclosed window {first}–{last}: '
                  f'{peak:.3%}; required ≥{acceptance["closure_coverage"]:.0%}.',
                  min(1., peak / acceptance['closure_coverage']))
            intermediate = int(np.count_nonzero((series > 0) & (series < acceptance['closure_coverage'])))
            check(f'intermediate_partial_states:{name}:{number}', intermediate >= 2,
                  f'{intermediate} frames show partial source-iris coverage before full closure; '
                  'the public requirement is at least two intermediate partial-closure frames.',
                  min(1., intermediate / 2))
            ascending = np.diff(series[:peak_index + 1])
            descending = np.diff(series[peak_index:])
            reversal = max(float(-ascending.min()) if len(ascending) else 0.,
                           float(descending.max()) if len(descending) else 0., 0.)
            slack = acceptance['monotone_slack']
            check(f'one_closure_cycle:{name}:{number}', reversal <= slack,
                  f'Largest reversal within close/reopen trajectory: {reversal:.3f}; allowed {slack:g}.',
                  max(0., 1 - reversal / max(2 * slack, .0001)))
        step = float(np.max(np.abs(np.diff(closure))))
        limit = acceptance['max_closure_step']
        check('bounded_closure_step:' + name, step <= limit,
              f'Maximum adjacent-frame source-iris coverage change {step:.3f}; allowed {limit:g}.',
              max(0., 1 - step / max(2 * limit, .0001)))
        minimum = min(pupil_inside) if pupil_inside else 0.
        check('visible_pupil_position:' + name, bool(pupil_inside)
              and minimum >= acceptance['pupil_preserved_fraction'],
              f'When pupil-colored pixels are visible, minimum fraction within original pupil '
              f'plus {acceptance["geometry_tolerance_px"]}px: {minimum:.3%}; '
              f'required ≥{acceptance["pupil_preserved_fraction"]:.0%}.', minimum)


def _grade_occlusion(decoded, spec, check):
    acceptance = _settings(spec)
    properties = spec['properties']
    foreground = properties['foreground'].convert('RGBA')
    background = _rgb(properties['clean_background'], spec['size'])
    with Image.open(spec['assets']['sprite']) as image:
        sprite = image.convert('RGBA')
    motion = properties['motion']
    if len(motion) != len(decoded):
        raise ValueError('Disclosed public motion has the wrong frame count.')
    fg = _mask(spec['masks']['foreground'], spec['size'])
    allowance = acceptance['geometry_tolerance_px']
    # Flat silhouettes do not uniquely identify their original antialias alpha.
    # A disclosed two-pixel boundary band is left to independent visual review.
    fg_core = _erode(fg, allowance)
    fg_band = _dilate(fg, allowance) & ~fg_core
    if not fg_core.any():
        raise ValueError('Foreground has no testable opaque interior.')

    def expected_frames():
        yield _source(spec)
        for position in motion:
            frame = background.convert('RGBA')
            frame.alpha_composite(sprite, tuple(position))
            frame.alpha_composite(foreground)
            yield frame.convert('RGB')

    references = _codec_arrays(expected_frames(), spec['size'])
    base = references[0].astype(np.int16)
    foreground_errors, visible_errors, stationary_errors, padding_errors = [], [], [], []
    deadband = acceptance['channel_noise_deadband']
    for index, (frame, position) in enumerate(zip(decoded, motion)):
        alpha = Image.new('L', tuple(spec['size']))
        alpha.paste(sprite.getchannel('A'), tuple(position))
        subject = np.asarray(alpha) > 16
        visible = subject & ~_dilate(fg, allowance)
        hidden = subject & fg_core
        expected = references[index + 1].astype(np.int16)
        if visible.any():
            visible_errors.append(_error(frame, expected, visible,
                                         geometry=allowance, deadband=deadband))
        # Focus crossings on the actual opaque overlap, not the complete plant
        # rectangle: otherwise a small wrongly foregrounded subject is diluted.
        support = hidden if np.count_nonzero(hidden) >= 16 else fg_core
        foreground_errors.append(_error(frame, base, support, deadband=deadband))
        stationary = ~_dilate(subject, allowance) & ~fg_band
        if stationary.any():
            stationary_errors.append(_error(frame, base, stationary, deadband=deadband))
        # Transparent padding is a small but important stationary region. A
        # white rectangular cutout can be diluted in a full-canvas average.
        extent = Image.new('L', tuple(spec['size']))
        extent.paste(Image.new('L', sprite.size, 255), tuple(position))
        padding = (np.asarray(extent) > 127) & stationary
        if padding.any():
            padding_errors.append(_error(frame, base, padding, deadband=deadband))
    _error_check(check, 'visible_subject_source_pattern', visible_errors,
                 acceptance['motion_mae'], 'Use the supplied subject at the public trajectory positions.')
    _error_check(check, 'actual_opaque_foreground_occlusion', foreground_errors,
                 acceptance['foreground_mae'], 'Opaque foreground interiors must remain visible at crossings.')
    _error_check(check, 'stationary_flattened_scene', stationary_errors,
                 acceptance['stationary_mae'], 'Outside the moving subject and allowed silhouette edge band.')
    _error_check(check, 'unchanged_transparent_sprite_padding', padding_errors,
                 acceptance['stationary_mae'], 'Original scene must remain visible through the supplied sprite padding.')


def _grade_restoration(decoded, spec, check):
    acceptance = _settings(spec)
    if len(decoded) != 1:
        raise ValueError('A print restoration requires one still image.')
    observed = decoded[0]
    clean = np.asarray(_rgb(spec['properties']['clean_original'], spec['size']), dtype=np.int16)
    damaged = np.asarray(_source(spec), dtype=np.int16)
    guide = _mask(spec['masks']['guide'], spec['size'])
    damage = _mask(spec['masks']['damage'], spec['size'])
    protected = _mask(spec['masks']['protected'], spec['size'])
    if (not guide.any() or not damage.any() or np.any(damage & ~guide)
            or not np.array_equal(protected, ~guide)):
        raise ValueError('The disclosed authorization guide and runner damage masks disagree.')
    threshold = acceptance['png_mae']
    geometry = acceptance['geometry_tolerance_px']
    _error_check(check, 'protected_original_unchanged',
                 [_error(observed, damaged, protected)], threshold,
                 'Outside the visible repair authorization guide, retain the supplied original bytes visually.')
    _error_check(check, 'complete_registered_repair',
                 [_error(observed, clean, guide, geometry=geometry)], threshold,
                 'The complete authorized area, including undamaged margins, must match the recoverable donor.')
    _error_check(check, 'damaged_content_recovered',
                 [_error(observed, clean, damage, geometry=geometry)], threshold,
                 'Recover all stained source content using the donor registration.')
    for detail in spec['properties'].get('detail_masks', []):
        support = _mask(detail['mask'], spec['size'])
        _error_check(check, 'fine_print_preserved:' + detail['name'],
                     [_error(observed, clean, support, geometry=geometry)], threshold,
                     'Preserve the original visible raster lettering, rules and registration marks.')


def evaluate_artifact(task_id, path, workspace, *, artifact_root=None, task_spec=None):
    """Score frozen candidate bytes against the original immutable task inputs.

    The caller freezes the candidate, validates input hashes and verifies byte
    identities before/after scoring. ``task_spec`` permits runner-side caching of
    regenerated source properties; it must never originate from a producer.
    """
    if task_spec is None:
        from oak.media_pilot_perceptual_tasks import quality_spec
        spec = quality_spec(task_id, workspace)
    else:
        spec = task_spec
    checks = []

    def check(criterion, passed, evidence, score=None):
        row = {'criterion': criterion,
               'status': 'unverified' if passed is None else 'pass' if passed else 'fail',
               'evidence': evidence}
        if score is not None:
            row['score'] = round(max(0., min(1., float(score))), 6)
        checks.append(row)

    def result():
        statuses = {row['status'] for row in checks}
        structural = {row['status'] for row in checks if row.get('structural')}
        scores = [row['score'] for row in checks if 'score' in row]
        return {'task_id': task_id,
                'status': 'fail' if 'fail' in statuses else 'unverified' if 'unverified' in statuses else 'pass',
                'structural_status': ('fail' if 'fail' in structural else 'unverified'
                                      if 'unverified' in structural else 'pass'),
                'structural_only': False,
                'quality_score': round(sum(scores) / len(scores), 6) if scores else 0.,
                'checks': checks, 'limitation': _LIMITATION}

    try:
        path = Path(path)
        if not path.is_absolute():
            path = Path(workspace) / path
        if artifact_root is not None:
            path.resolve(strict=True).relative_to(Path(artifact_root).resolve(strict=True))
        data = _bounded_bytes(path)
        check('bounded_regular_artifact', True, 'Bounded frozen regular-file candidate.')
        if spec['duration']:
            decoded = _probe_and_decode(data, spec, check)
        else:
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 2_000_000:
                    raise ValueError('Still image exceeds the decoding budget.')
                image.load()
                same_size = image.size == tuple(spec['size'])
                check('opaque_static_rgb_png', image.format == 'PNG'
                      and image.mode == 'RGB' and not getattr(image, 'is_animated', False),
                      'Require an opaque, static RGB PNG.')
                check('dimensions', same_size, f"Observed {image.size}; required {spec['size']}.")
                decoded = [np.asarray(image.convert('RGB'))] if same_size else None
        for row in checks:
            row['structural'] = True
        if decoded is None:
            return result()
    except (OSError, ValueError, KeyError, IndexError, ZeroDivisionError,
            subprocess.SubprocessError, Image.DecompressionBombError):
        check('artifact_decoding', False, 'Candidate could not be decoded within the fixed limits.')
        checks[-1]['structural'] = True
        return result()

    def content_check(criterion, passed, evidence, score=None):
        # The pilot's development gate recognizes a content: failure alongside
        # a structural pass. Keep this namespace for every semantic property,
        # while the decoder's original structural criterion names stay intact.
        check('content:' + criterion, passed, evidence, score)

    try:
        graders = {'blink': _grade_blink, 'occlusion': _grade_occlusion,
                   'restoration': _grade_restoration}
        graders[spec['family']](decoded, spec, content_check)
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        content_check('property_verification', None,
                      'Runner properties or source references could not be verified; no semantic pass.')
    return result()
