"""Source-property checks for the complex media pilot.

Authored-only construction controls run before any native experiment is sealed.
It uses the existing bounded decoder and error conventions,
but never treats a property pass as an aesthetics verdict. The existing fixed
independent visual review must accept the exact delivered artifact bytes.

Targets below are consequences of the public source layers, registration
landmarks, depth order, endpoint path and fixed kinematic branch. They are not a
secret author-chosen animation. All semantic masks stay runner-side in memory.

The authored-only control suite covers all twelve tasks, faithful alternate
sampling and subpixel registration, lost source patterns, incorrect depth,
opaque padding, disconnected or stretched links, wrong branches, endpoint jumps,
misregistration and blurred fine print. Projective comparisons use three fixed
public sampling variants and coherent fractional alignment within the public
spatial allowance. Separate repair/ink budgets account for the measured sampling
floor; protected original pixels retain the stricter PNG allowance. These
budgets are fixed from authored controls before native experiment feedback.
"""

import io
import math
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image

from oak.media_pilot_hard_quality import _bounded_bytes, _probe_and_decode
from oak.media_pilot_perceptual_quality import (
    _codec_arrays, _dilate, _erode, _error, _error_check, _mask, _rgb, _source,
)


LIMITATION = (
    'Source and geometry constraints only. Checks all '
    'decoded frames, visible source patterns, recoverable registered donor '
    'content, depth crossings and rigid mechanism geometry. It does not establish '
    'natural motion, clean subpixel edges, aesthetic coherence or legibility by '
    'itself. The fixed independent visual veto remains mandatory. Projective '
    'restoration compares the supplied donor after the disclosed resampling, '
    'rather than claiming recovery of inaccessible pre-warp pixel values.'
)


def _acceptance(spec):
    acceptance = spec['contract']['acceptance']
    required = (
        'geometry_tolerance_px', 'channel_noise_deadband', 'motion_mae',
        'foreground_mae', 'stationary_mae', 'png_mae',
        'alpha_support_threshold', 'minimum_observable_support_px',
    )
    if any(name not in acceptance for name in required):
        raise ValueError('A numeric property allowance is absent from the public contract.')
    return acceptance


def _full_layer(image, position, size):
    """Preserve supplied raster size and alpha at public integer positions."""
    layer = Image.new('RGBA', tuple(size))
    if (len(position) != 2 or any(int(value) != value for value in position)):
        raise ValueError('Motion positions must be public integer pixel coordinates.')
    layer.alpha_composite(image.convert('RGBA'), tuple(int(value) for value in position))
    return layer


def _alpha(layer, threshold):
    return np.asarray(layer.getchannel('A')) > threshold


def _composite(background, layers):
    image = background.convert('RGBA')
    for layer in layers:
        image.alpha_composite(layer)
    return image.convert('RGB')


def _perspective_coefficients(output_points, input_points):
    """Unique output-to-input homography from four intact corresponding marks."""
    if len(output_points) != 4 or len(input_points) != 4:
        raise ValueError('Four corresponding fiducial centers are required.')
    rows, values = [], []
    for (x, y), (u, v) in zip(output_points, input_points):
        rows.extend(([x, y, 1, 0, 0, 0, -u * x, -u * y],
                     [0, 0, 0, x, y, 1, -v * x, -v * y]))
        values.extend((u, v))
    matrix = np.asarray(rows, dtype=float)
    if np.linalg.matrix_rank(matrix) != 8:
        raise ValueError('Fiducials do not identify a unique projective registration.')
    coefficients = np.linalg.solve(matrix, np.asarray(values, dtype=float))
    if not np.isfinite(coefficients).all():
        raise ValueError('Non-finite projective registration.')
    return tuple(float(value) for value in coefficients)


def _projective_error(observed, references, support, *, geometry, alignment_step):
    """One coherent fractional translation per source-backed regional comparison.

    Faithful sampling variants are fixed publicly, rather than fitted to an
    artifact. The search refines integer translations to the disclosed precision;
    it never chooses a different displacement independently for each pixel.
    """
    rows, columns = np.nonzero(support)
    if not len(rows) or not 0 < alignment_step <= .5:
        raise ValueError('Invalid registered source support or alignment precision.')
    top, bottom = int(rows.min()), int(rows.max()) + 1
    left, right = int(columns.min()), int(columns.max()) + 1
    selected = support[top:bottom, left:right]
    actual = observed[top:bottom, left:right].astype(np.int16)
    best = float('inf')
    for reference in references:
        cache = {}

        def error_at(dx, dy):
            key = (dx, dy)
            if key not in cache:
                sampled = reference.transform(
                    (right - left, bottom - top), Image.Transform.AFFINE,
                    (1, 0, left - dx, 0, 1, top - dy), resample=Image.Resampling.BICUBIC)
                expected = np.asarray(sampled, dtype=np.int16)
                cache[key] = float(np.abs(actual - expected)[selected].mean())
            return cache[key]

        candidates = [(float(dx), float(dy)) for dx in range(-geometry, geometry + 1)
                      for dy in range(-geometry, geometry + 1)]
        point = min(candidates, key=lambda offset: error_at(*offset))
        step = .5
        while step >= alignment_step:
            x, y = point
            nearby = [(x + dx, y + dy) for dx in (-step, 0, step)
                      for dy in (-step, 0, step)
                      if abs(x + dx) <= geometry and abs(y + dy) <= geometry]
            point = min(nearby, key=lambda offset: error_at(*offset))
            step /= 2
        best = min(best, error_at(*point))
    return best


def _grade_projective(decoded, spec, check):
    acceptance = _acceptance(spec)
    if len(decoded) != 1:
        raise ValueError('Projective print restoration requires one still image.')
    properties = spec['properties']
    if properties['resample'] != 'bicubic':
        raise ValueError('Draft only supports the explicitly disclosed bicubic reconstruction.')
    with Image.open(spec['assets']['donor']) as image:
        donor = image.convert('RGB')
    coefficients = _perspective_coefficients(
        properties['target_points'], properties['donor_points'])
    # This reference is reproducible from the supplied donor bytes and intact
    # fiducials. Never compare to a pristine pre-warp image unavailable to Oak.
    required = ('projective_alignment_step_px', 'projective_resampling_variants',
                'projective_repair_mae', 'projective_detail_mae')
    if any(key not in acceptance for key in required):
        raise ValueError('Projective alignment, sampling variants and detail budget must be public.')
    variants = acceptance['projective_resampling_variants']
    if variants != ['bicubic', 'bilinear', 'bicubic2x_lanczos']:
        raise ValueError('Unsupported undisclosed projective sampling variant.')
    registered = donor.transform(tuple(spec['size']), Image.Transform.PERSPECTIVE,
                                 coefficients, resample=Image.Resampling.BICUBIC)
    bilinear = donor.transform(tuple(spec['size']), Image.Transform.PERSPECTIVE,
                               coefficients, resample=Image.Resampling.BILINEAR)
    enlarged_points = [(2 * x, 2 * y) for x, y in properties['target_points']]
    enlarged_mapping = _perspective_coefficients(enlarged_points, properties['donor_points'])
    supersampled = donor.transform(tuple(2 * value for value in spec['size']),
                                    Image.Transform.PERSPECTIVE, enlarged_mapping,
                                    resample=Image.Resampling.BICUBIC)
    supersampled = supersampled.resize(tuple(spec['size']), Image.Resampling.LANCZOS)
    references = [registered, bilinear, supersampled]
    guide = _mask(spec['masks']['guide'], spec['size'])
    damage = _mask(spec['masks']['damage'], spec['size'])
    protected = _mask(spec['masks']['protected'], spec['size'])
    if (not guide.any() or not damage.any() or np.any(damage & ~guide)
            or not np.array_equal(protected, ~guide)):
        raise ValueError('The donor must cover every authorized repair pixel with sampling margin.')
    # Check the reconstruction's sampling neighborhood in DONOR pixels. A
    # two-output-pixel erosion is insufficient under projective magnification.
    rows, columns = np.nonzero(guide)
    x, y = columns + .5, rows + .5
    a, b, c, d, e, f, g, h = coefficients
    denominator = g * x + h * y + 1
    if np.any(denominator == 0):
        raise ValueError('The repair guide crosses the projective horizon.')
    u = (a * x + b * y + c) / denominator - .5
    v = (d * x + e * y + f) / denominator - .5
    if (not np.isfinite(u).all() or not np.isfinite(v).all()
            or np.any((u < 2) | (u >= donor.width - 2)
                      | (v < 2) | (v >= donor.height - 2))):
        raise ValueError('Every guide pixel needs a complete donor-space bicubic neighborhood.')
    observed = decoded[0]
    damaged = np.asarray(_source(spec), dtype=np.int16)
    geometry = acceptance['geometry_tolerance_px']
    threshold = acceptance['png_mae']
    _error_check(check, 'protected_original_unchanged',
                 [_error(observed, damaged, protected)], threshold,
                 'Retain all supplied original content outside the visible guide.')
    for name, support in (('complete_registered_repair', guide),
                          ('damaged_content_recovered', damage)):
        _error_check(check, name,
                     [_projective_error(observed, references, support, geometry=geometry,
                                        alignment_step=acceptance['projective_alignment_step_px'])],
                     acceptance['projective_repair_mae'],
                     'Reconstruct the supplied donor through its four intact fiducials '
                     'within the public spatial allowance and fixed faithful sampling variants.')
    details = properties['detail_masks']
    if not details:
        raise ValueError('Fine-detail supports are required for a print restoration.')
    for detail in details:
        support = _mask(detail['mask'], spec['size'])
        if not support.any() or np.any(support & ~guide):
            raise ValueError('Fine-detail support must lie in the authorized recoverable area.')
        support = _dilate(support, geometry) & guide
        _error_check(check, 'recoverable_fine_print:' + detail['name'],
                     [_projective_error(observed, references, support, geometry=geometry,
                                        alignment_step=acceptance['projective_alignment_step_px'])],
                     acceptance['projective_detail_mae'],
                     'Compare the actual recoverable donor glyphs, rules and diacritics; '
                     'use one coherent fractional alignment per region and include its spatial allowance.')


def _grade_choreography(decoded, spec, check):
    acceptance = _acceptance(spec)
    properties = spec['properties']
    size = spec['size']
    actors = properties['actors']
    foregrounds = properties['foregrounds']
    if len(actors) != 2 or len(foregrounds) != 2:
        raise ValueError('Two supplied actors and two foreground objects are required.')
    if any(len(actor['positions']) != len(decoded) for actor in actors):
        raise ValueError('Every actor needs a complete publicly determined trajectory.')
    objects = [dict(item, moving=True) for item in actors]
    objects += [dict(item, moving=False) for item in foregrounds]
    if len({item['id'] for item in objects}) != len(objects):
        raise ValueError('Layer identifiers must be unique.')
    depths = [item['depth'] for item in objects]
    if len(set(depths)) != len(depths):
        raise ValueError('A unique public depth order is required at every crossing.')
    objects.sort(key=lambda item: item['depth'])
    background = _rgb(properties['background'], size)
    geometry = acceptance['geometry_tolerance_px']
    alpha_threshold = acceptance['alpha_support_threshold']
    minimum = acceptance['minimum_observable_support_px']
    frame_layers, masks, nominal = [], [], []
    for index in range(len(decoded)):
        layers = []
        for item in objects:
            image = item['rgba'].convert('RGBA')
            if item['moving']:
                image = _full_layer(image, item['positions'][index], size)
            elif image.size != tuple(size):
                raise ValueError('Runner foreground must use full-canvas source coordinates.')
            layers.append(image)
        frame_layers.append(layers)
        masks.append([_alpha(layer, alpha_threshold) for layer in layers])
        nominal.append(_composite(background, layers))
    reference = _codec_arrays(nominal, size)
    base = _codec_arrays([_source(spec)], size)[0].astype(np.int16)
    deadband = acceptance['channel_noise_deadband']
    errors = {item['id']: [] for item in objects}
    crossing_errors = {item['id']: [] for item in objects}
    stationary_errors, padding_errors = [], []
    for index, actual in enumerate(decoded):
        expected = reference[index].astype(np.int16)
        source_masks = masks[index]
        all_moving = np.logical_or.reduce([
            source_masks[j] for j, item in enumerate(objects) if item['moving']])
        boundary_band = np.zeros(tuple(reversed(size)), dtype=bool)
        for j, item in enumerate(objects):
            if not item['moving']:
                boundary_band |= (_dilate(source_masks[j], geometry)
                                  & ~_erode(source_masks[j], geometry))
        for j, item in enumerate(objects):
            support = source_masks[j]
            higher = np.zeros_like(support)
            for upper in source_masks[j + 1:]:
                higher |= _dilate(upper, geometry)
            visible = _erode(support, geometry) & ~higher
            if np.count_nonzero(visible) >= minimum:
                errors[item['id']].append(_error(actual, expected, visible,
                    geometry=geometry if item['moving'] else 0, deadband=deadband))
            # Grade the exact overlap where this object must cover a lower
            # moving actor. This avoids diluting a wrong ordering in broad ROIs.
            lower_moving = np.zeros_like(support)
            for lower_index, lower in enumerate(objects[:j]):
                if lower['moving']:
                    lower_moving |= source_masks[lower_index]
            crossing = visible & lower_moving
            if np.count_nonzero(crossing) >= minimum:
                crossing_errors[item['id']].append(
                    _error(actual, expected, crossing, deadband=deadband))
            if item['moving']:
                extent = _full_layer(Image.new('RGBA', item['rgba'].size, 'white'),
                                     item['positions'][index], size)
                padding = _alpha(extent, 0) & ~_dilate(all_moving, geometry) & ~boundary_band
                if np.count_nonzero(padding) >= minimum:
                    padding_errors.append(_error(actual, base, padding, deadband=deadband))
        stationary = ~_dilate(all_moving, geometry) & ~boundary_band
        if np.count_nonzero(stationary) >= minimum:
            stationary_errors.append(_error(actual, base, stationary, deadband=deadband))
    for item in objects:
        _error_check(check, 'visible_source_pattern:' + item['id'], errors[item['id']],
                     acceptance['motion_mae'] if item['moving'] else acceptance['foreground_mae'],
                     'Retain this supplied layer at its public trajectory and depth.')
        # The task constructor must declare exactly which overlaps are required;
        # an object with no crossings is not silently awarded a crossing pass.
        if item['id'] in properties['required_crossing_objects']:
            _error_check(check, 'actual_depth_crossing:' + item['id'],
                         crossing_errors[item['id']],
                         acceptance['motion_mae'] if item['moving'] else acceptance['foreground_mae'],
                         'The upper source layer must cover the lower moving actor on actual overlap pixels.')
    _error_check(check, 'stationary_source_scene', stationary_errors,
                 acceptance['stationary_mae'], 'Original artwork outside both moving actors stays fixed.')
    _error_check(check, 'transparent_actor_padding', padding_errors,
                 acceptance['stationary_mae'], 'Original artwork remains visible through transparent actor padding.')


def _ik(base, endpoint, lengths, branch):
    """The public fixed branch makes each reachable two-link pose unique."""
    base = np.asarray(base, dtype=float)
    endpoint = np.asarray(endpoint, dtype=float)
    lengths = np.asarray(lengths, dtype=float)
    delta = endpoint - base
    distance = float(np.linalg.norm(delta))
    if (branch not in (-1, 1) or not np.all(lengths > 0) or distance <= 0
            or not abs(lengths[0] - lengths[1]) < distance < sum(lengths)):
        raise ValueError('Every public endpoint must be strictly reachable on one fixed branch.')
    along = float((lengths[0] ** 2 - lengths[1] ** 2 + distance ** 2) / (2 * distance))
    height = math.sqrt(max(0., float(lengths[0] ** 2 - along ** 2)))
    unit = delta / distance
    elbow = base + along * unit + branch * height * np.array([-unit[1], unit[0]])
    return tuple(elbow)


def _rigid_layer(layer, old_pivot, old_tip, new_pivot, new_tip, size):
    old_angle = math.atan2(old_tip[1] - old_pivot[1], old_tip[0] - old_pivot[0])
    new_angle = math.atan2(new_tip[1] - new_pivot[1], new_tip[0] - new_pivot[0])
    angle = new_angle - old_angle
    c, s = math.cos(angle), math.sin(angle)
    ox, oy = old_pivot
    nx, ny = new_pivot
    inverse = (c, s, ox - c * nx - s * ny,
               -s, c, oy + s * nx - c * ny)
    # Each supplied part has its own source tile; anchors are local to that
    # tile, while new pivots are in output-board coordinates.
    return layer.convert('RGBA').transform(tuple(size), Image.Transform.AFFINE,
                                           inverse, resample=Image.Resampling.BICUBIC)


def _grade_mechanism(decoded, spec, check):
    acceptance = _acceptance(spec)
    properties = spec['properties']
    links = properties['layers']
    if len(links) != 2 or len(properties['endpoint_positions']) != len(decoded):
        raise ValueError('Two source links and every public endpoint are required.')
    if len(properties['joint_support_radii']) != 3:
        raise ValueError('The source ground, elbow and endpoint markers all need visible supports.')
    lengths = [math.dist(link['base_anchor'], link['tip_anchor']) for link in links]
    if not np.allclose(lengths, properties['lengths'], rtol=0, atol=1e-6):
        raise ValueError('Link lengths must follow the visible source marker anchors.')
    base = properties['base_pivot']
    for link in links:
        for x, y in (link['base_anchor'], link['tip_anchor']):
            if not (0 <= x < link['rgba'].width and 0 <= y < link['rgba'].height):
                raise ValueError('Source pivots must be visible within their supplied part tiles.')
    size = spec['size']
    geometry = acceptance['geometry_tolerance_px']
    if any(key not in acceptance for key in ('joint_tolerance_px', 'endpoint_tolerance_px')):
        raise ValueError('Physical hinge and endpoint allowances must be public.')
    alpha_threshold = acceptance['alpha_support_threshold']
    minimum = acceptance['minimum_observable_support_px']
    background = _rgb(properties['background'], size)
    frames, masks, poses = [], [], []
    for endpoint in properties['endpoint_positions']:
        elbow = _ik(base, endpoint, lengths, properties['branch'])
        joints = [(base, elbow), (elbow, endpoint)]
        layers = [_rigid_layer(link['rgba'], link['base_anchor'], link['tip_anchor'], new_base, new_tip, size)
                  for link, (new_base, new_tip) in zip(links, joints)]
        if properties['front_link'] not in (0, 1):
            raise ValueError('The source-paper link order must be publicly specified.')
        order = [1 - properties['front_link'], properties['front_link']]
        frames.append(_composite(background, [layers[j] for j in order]))
        masks.append([_alpha(layer, alpha_threshold) for layer in layers])
        poses.append((base, elbow, endpoint))
    reference = _codec_arrays(frames, size)
    base_frame = _codec_arrays([background], size)[0].astype(np.int16)
    deadband = acceptance['channel_noise_deadband']
    link_errors = [[], []]
    joint_errors, stationary_errors = [], []
    for index, actual in enumerate(decoded):
        expected = reference[index].astype(np.int16)
        union = masks[index][0] | masks[index][1]
        for j in range(2):
            visible = _erode(masks[index][j], geometry)
            if j != properties['front_link']:
                visible &= ~_dilate(masks[index][properties['front_link']], geometry)
            if np.count_nonzero(visible) >= minimum:
                link_errors[j].append(_error(actual, expected, visible,
                                            geometry=geometry, deadband=deadband))
        # Runner source marker centers/radii identify real visible artwork. No
        # arbitrary new color classifier or hidden minimum marker size is used.
        for joint_index, (joint, radius) in enumerate(zip(
                poses[index], properties['joint_support_radii'])):
            yy, xx = np.ogrid[:size[1], :size[0]]
            support = (xx - joint[0]) ** 2 + (yy - joint[1]) ** 2 <= radius ** 2
            if np.count_nonzero(support) < minimum:
                raise ValueError('A disclosed visible joint support is too small to verify.')
            placement = acceptance['endpoint_tolerance_px' if joint_index == 2 else 'joint_tolerance_px']
            joint_errors.append(_error(actual, expected, support,
                                       geometry=placement, deadband=deadband))
        stationary = ~_dilate(union, geometry)
        if np.count_nonzero(stationary) >= minimum:
            stationary_errors.append(_error(actual, base_frame, stationary, deadband=deadband))
    for j, link in enumerate(links):
        _error_check(check, 'rigid_source_link:' + link['id'], link_errors[j],
                     acceptance['motion_mae'],
                     'Preserve the supplied rigid link around its connected source pivots; '
                     'the public endpoint path and branch determine each pose.')
    _error_check(check, 'connected_hinges_and_endpoint_path', joint_errors,
                 acceptance['motion_mae'],
                 'The stationary base, shared elbow and moving tip must remain connected '
                 'at the unique public-path positions on every frame.')
    _error_check(check, 'stationary_source_board', stationary_errors,
                 acceptance['stationary_mae'], 'Only the two paper links may move; the board stays fixed.')


def evaluate_artifact(task_id, path, workspace, *, artifact_root=None, task_spec=None):
    """Score immutable candidate bytes, with mandatory separate visual review.

    The caller must freeze/hash candidate bytes and original input identities.
    ``task_spec`` must be runner-regenerated data, never a producer-supplied map.
    """
    if task_spec is None:
        from oak.media_pilot_complex_tasks import quality_spec
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
        return {
            'task_id': task_id,
            'status': 'fail' if 'fail' in statuses else 'unverified' if 'unverified' in statuses else 'pass',
            'structural_status': ('fail' if 'fail' in structural else 'unverified'
                                  if 'unverified' in structural else 'pass'),
            'structural_only': False, 'requires_visual_review': True,
            'quality_score': round(sum(scores) / len(scores), 6) if scores else 0.,
            'checks': checks, 'limitation': LIMITATION,
        }

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
                    raise ValueError('Still image exceeds the fixed decoding budget.')
                image.load()
                check('opaque_static_rgb_png', image.format == 'PNG' and image.mode == 'RGB'
                      and not getattr(image, 'is_animated', False), 'Require an opaque, static RGB PNG.')
                same_size = image.size == tuple(spec['size'])
                check('dimensions', same_size, f"Observed {image.size}; required {spec['size']}.")
                decoded = [np.asarray(image.convert('RGB'))] if same_size else None
        for row in checks:
            row['structural'] = True
        if decoded is None:
            return result()
    except (OSError, ValueError, KeyError, IndexError, ZeroDivisionError,
            subprocess.SubprocessError, Image.DecompressionBombError):
        check('artifact_decoding', False, 'Candidate could not be decoded within fixed limits.')
        checks[-1]['structural'] = True
        return result()

    def content_check(criterion, passed, evidence, score=None):
        check('content:' + criterion, passed, evidence, score)

    try:
        graders = {'projective': _grade_projective, 'choreography': _grade_choreography,
                   'mechanism': _grade_mechanism}
        graders[spec['family']](decoded, spec, content_check)
    except (OSError, ValueError, KeyError, IndexError, ZeroDivisionError,
            np.linalg.LinAlgError, subprocess.SubprocessError):
        content_check('property_verification', None,
                      'Runner properties or references could not be verified; no semantic pass.')
    return result()
