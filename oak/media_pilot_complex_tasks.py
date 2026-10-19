"""Synthetic projective restoration, depth composition and articulated motion.

Producer observations disclose physical/visual goals, reference identities and
tolerances. Semantic masks, measured source pivots and registration truth remain
runner-only and are never written to inputs. Authored outputs are calibration
controls; source fidelity and physical constraints define the scored objectives.
"""

import hashlib
import json
import math
from pathlib import Path
import random
import shutil
import subprocess

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont


_SPECS = (
    ('dev-create-folio301-perspective', 'dev', 'creation', 'projective', (912, 704), 'BOTANICAL FOLIO 301', 301),
    ('dev-repair-folio302-perspective', 'dev', 'repair', 'projective', (960, 736), 'TEXTILE FOLIO 302', 302),
    ('dev-create-vines303-depth', 'dev', 'creation', 'choreography', (800, 512), 'COPPER & GLASS / 303', 303),
    ('dev-repair-vines304-depth', 'dev', 'repair', 'choreography', (832, 528), 'PAPER & LINEN / 304', 304),
    ('dev-create-linkage305-motion', 'dev', 'creation', 'mechanism', (800, 544), 'PAPER ARM / 305', 305),
    ('dev-repair-linkage306-motion', 'dev', 'repair', 'mechanism', (848, 560), 'WOVEN ARM / 306', 306),
    ('holdout-create-folio307-perspective', 'holdout', 'creation', 'projective', (984, 760), 'HERBARIUM FOLIO 307', 307),
    ('holdout-repair-folio308-perspective', 'holdout', 'repair', 'projective', (928, 728), 'SHELL FOLIO 308', 308),
    ('holdout-create-reeds309-depth', 'holdout', 'creation', 'choreography', (864, 544), 'INDIGO & AMBER / 309', 309),
    ('holdout-repair-reeds310-depth', 'holdout', 'repair', 'choreography', (816, 552), 'LEAF & GLASS / 310', 310),
    ('holdout-create-linkage311-motion', 'holdout', 'creation', 'mechanism', (864, 576), 'LATTICE ARM / 311', 311),
    ('holdout-repair-linkage312-motion', 'holdout', 'repair', 'mechanism', (832, 568), 'BRASS ARM / 312', 312),
)


def _spec(task_id):
    for spec in _SPECS:
        if spec[0] == task_id:
            return spec
    raise ValueError('Unknown complex media task: ' + str(task_id))


def _contract(spec):
    task_id, split, kind, family, size, title, seed = spec
    video = family != 'projective'
    acceptance = {
        'geometry_tolerance_px': 2, 'channel_noise_deadband': 2 if video else 0,
        'stationary_mae': 7 if video else 2, 'motion_mae': 10,
        'foreground_mae': 6, 'png_mae': 2,
        'alpha_support_threshold': 16, 'minimum_observable_support_px': 16,
        'silhouette_edge_tolerance_px': 2, 'joint_tolerance_px': 3,
        'endpoint_tolerance_px': 3,
        'kinematic_tolerance_note': 'The 3px joint and endpoint allowances apply to physical pivot coincidence and endpoint tracking. Source-pattern registration retains the separate 2px geometry allowance; the joint allowance does not permit translating or distorting an entire rigid part by 3px.',
        'error_aggregation': 'For every applicable indexed frame, measure the disclosed mean absolute channel error after the fixed channel deadband. Across frames require the 95th percentile at most the named budget and the worst frame at most 1.5 times that budget. At least 16 supported pixels are required for an observable crossing; source alpha above 16 identifies visible support.',
        'reference_resampling': 'Projective targets are sampled directly once from the provided higher-resolution donor with bicubic interpolation. Alternative faithful interpolation has the disclosed spatial and pixel allowances. Video reference colors use fixed raw RGB24 -> yuv420p -> RGB24 conversion, without lossy reference encoding.',
        'visual_veto': 'Independent visual review must accept source identity, sharp lettering, clean layer boundaries, coherent occlusion and connected joints. Numeric properties alone do not establish visual quality.',
    }
    result = {'version': 'oak-complex-media-v1', 'task_id': task_id,
              'family': family, 'size': list(size), 'duration': 3 if video else 0,
              'fps': 12 if video else 0, 'frame_count': 36 if video else 1,
              'assets': {'scene': 'scene.png'}, 'acceptance': acceptance}
    if family == 'projective':
        result['assets'].update(donor='donor.png', guide='guide.png')
        acceptance.update({
            'projective_alignment_step_px': .125,
            'projective_resampling_variants': ['bicubic', 'bilinear', 'bicubic2x_lanczos'],
            'projective_repair_mae': 6,
            'projective_detail_mae': 20,
            'projective_comparison': 'Registered donor content is compared against the fixed bicubic, bilinear, and bicubic-at-double-resolution-then-Lanczos-downsample reconstructions. Each complete repair/detail region permits one common translation within the 2px geometry allowance, searched in 0.125px steps. Fine-ink support includes its stated 2px spatial neighborhood. The complete repair/stain supports allow MAE 6; source-ink detail supports allow MAE 20 to accommodate the disclosed projective landmark and sampling uncertainty. Outside the repair guide still uses the unchanged original target with PNG MAE 2. The same p95/worst-frame aggregation applies. These budgets were calibrated with authored source-preserving registration variants before model execution.',
        })
        visual = [
            'Restore the stained print using the complete, sharper donor. Match the four distinct intact landmarks labeled A, B, C and D before copying donor detail into the marked damage.',
            'Preserve the target print perspective, paper texture, fine engraved lines, exact small lettering, diacritics and rules. Do not flatten the whole target into the donor view.',
            'Repair only inside the separate cyan authorization guide. All unaffected content outside it stays fixed, and the final image contains no guide marks, stain, blur, seams or doubled edges.',
        ]
        requirements = visual + [
            'The donor has higher resolution and contains all target content. The target was produced directly by one projective sample from that supplied donor; no original detail absent from the donor is required.',
            'The four labeled landmarks are uniquely matched, non-collinear and outside the damaged area in both observations. Infer the projective registration; numeric landmark locations and the transform are not supplied.',
            'Use a source-preserving reconstruction. Bicubic sampling defines the canonical comparison, with the public spatial and pixel tolerances for faithful alternate interpolation.',
        ]
    elif family == 'choreography':
        result['assets'].update(atlas='actors.png', depth_guide='depth-guide.png',
                                actor_far='actor-far.png', actor_near='actor-near.png')
        result['actor_atlas_crops'] = {'actor_far': [8, 8, 120, 120], 'actor_near': [136, 8, 248, 120]}
        result['depth_order'] = ['background', 'actor_far', 'rear_veil_I', 'actor_near', 'front_veil_II']
        w, h = size
        result['motion'] = {
            'actor_far': {'start': [40, h // 2 - 58], 'end': [w - 152, h // 2 - 58], 'bob': 13},
            'actor_near': {'start': [w - 152, h // 2 - 42], 'end': [40, h // 2 - 42], 'bob': -11},
            'formula': 'For n=0..35 use u=n/35; x=round(start.x+(end.x-start.x)*u); y=round(start.y+(end.y-start.y)*u+bob*sin(2*pi*u)). Paste each original unscaled 112x112 actor with its original alpha.',
        }
        visual = [
            'Animate both supplied patterned actors passing one another through the two foreground veils. Preserve both original patterns, colors, proportions and recognizable silhouettes.',
            'The far actor passes behind veil I and behind the near actor. The near actor passes in front of veil I. Veil II remains in front of both actors throughout. Use the depth guide to identify each veil.',
            'Maintain continuous movement and correct partial visibility at actor/actor and actor/plant crossings. Keep all plants, mural, lettering, border, paper and camera stationary, with clean edges and transparent sprite padding.',
        ]
        requirements = visual + [
            'Use the public actor trajectories and depth order. Infer the visible veil silhouettes from the flattened scene and guide; no hidden alpha recovery is needed because the authored veils have opaque stencil interiors and transparent exteriors.',
            'The background mural intentionally reuses the same green and vein pigments as the veils. Pigment alone does not establish depth: identify the continuous outlined foreground stems and attached leaf contours shown by the guide. Their contrasting boundaries remain visible even where pigments coincide.',
            'The actor atlas is an original reference containing both complete actors. Separate identical actor PNGs are also supplied as ordinary inputs. Allow the disclosed silhouette-edge neighborhood for raster antialiasing.',
        ]
    else:
        result['assets'].update(link_1='link-1.png', link_2='link-2.png')
        branch = 1 if seed % 2 else -1
        w, h = size
        result['endpoint_path'] = {
            'center': [round(w * .53), round(h * (.62 if branch == 1 else .40))],
            'radii': [31 + seed % 3 * 2, 23 + seed % 4], 'phase_degrees': seed % 4 * 90,
            'formula': 'For n=0..35, theta=2*pi*n/36+radians(phase_degrees). The magenta tip center is (center.x+radii.x*cos(theta), center.y+radii.y*sin(theta)). The light dotted loop on the base depicts the same public path.',
        }
        result['elbow_branch'] = branch
        visual = [
            'Assemble and animate the two complete patterned paper links on the supplied stationary base. Match the cyan ground pivot to the cyan first-link pivot, join the two orange hinge markers, and make the magenta tip follow the visible endpoint loop.',
            'Infer physical pivot centers and link lengths from the source parts. Rotate rigid parts without stretching, repainting, changing their pattern or losing any portion of either link.',
            ('Keep the ground pivot fixed, the shared hinge connected and both source links recognizable throughout. '
             'The elbow stays ' + ('BELOW' if branch == 1 else 'ABOVE') +
             ' the line from the cyan ground pivot to the magenta tip. Maintain that side throughout; do not flip branches or snap through a different pose.'),
        ]
        requirements = visual + [
            'The endpoint positions are public and reachable with a margin throughout the loop. Numeric source pivot coordinates and link lengths are not supplied.',
            'The signed elbow branch is +1 or -1 as given by elbow_branch: for ground g, elbow e and endpoint t, sign((t.x-g.x)*(e.y-g.y)-(t.y-g.y)*(e.x-g.x)) must equal elbow_branch in image coordinates (positive y down).',
            'Preserve the source link patterns and pivot markers. Link 2 is visually in front of link 1 at their shared hinge. Minor resampling and the stated pivot/joint tolerances are allowed.',
            'Canonical source-layer reconstruction uses an inverse affine rigid-rotation mapping with bicubic resampling and source-over alpha composition. Other visually faithful sampling is permitted within the declared pixel and joint allowances.',
        ]
    result['requirements'] = requirements
    result['review_manifest'] = {
        'visual': {'brief': 'Project: ' + title + '. ' + ' '.join(visual),
                   'specification': {'project': title, 'family': family, 'visual_requirements': visual}},
        'mechanical': {'delivery': {key: result[key] for key in ('size', 'duration', 'fps', 'frame_count')},
                       'acceptance': acceptance, 'requirements': requirements,
                       **{key: result[key] for key in ('motion', 'depth_order', 'actor_atlas_crops',
                                                     'endpoint_path', 'elbow_branch') if key in result}},
    }
    return result


def _public(spec):
    c = _contract(spec)
    brief = f'Project: {spec[5]}. Read inputs/scene.json and use the original synthetic references. '
    brief += ' '.join(c['requirements']) + ' '
    brief += (f'Deliver final.mp4: silent H.264, yuv420p, {spec[4][0]}x{spec[4][1]}, 3 seconds, 12 fps, 36 indexed frames. '
              if c['duration'] else f'Deliver final.png: opaque RGB PNG, {spec[4][0]}x{spec[4][1]}. ')
    if spec[2] == 'repair':
        brief += 'The deliberately flawed starting candidate is inputs/broken-seed.' + ('mp4. ' if c['duration'] else 'png. ')
    brief += 'Local Python standard libraries, Pillow and ffmpeg suffice. Preserve input bytes.'
    return {'id': spec[0], 'split': spec[1], 'kind': spec[2], 'brief': brief,
            'output_name': 'final.mp4' if c['duration'] else 'final.png'}


TASKS = tuple(_public(spec) for spec in _SPECS)


def _font(size, bold=False):
    try:
        return ImageFont.truetype('DejaVuSans' + ('-Bold' if bold else '') + '.ttf', size)
    except OSError:
        return ImageFont.load_default(size=size)


def _paper(size, seed):
    image = Image.new('RGB', size, '#e8dec8')
    rng = random.Random('oak-complex-paper:' + str(seed))
    pixels = image.load()
    for _ in range(size[0] * size[1] // 13):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        delta = rng.choice((-7, -4, 4, 7))
        pixels[x, y] = tuple(color + delta for color in (232, 222, 200))
    return image


def _base(spec):
    image = _paper(spec[4], spec[-1])
    draw = ImageDraw.Draw(image)
    w, h = image.size
    draw.rectangle((16, 16, w - 17, h - 17), outline='#34554b', width=2)
    draw.text((31, 30), spec[5], fill='#34554b', font=_font(18, True))
    draw.text((31, h - 35), f'ATELIER / PLANCHE {spec[-1]} / TEXTURE • MÉMOIRE • MOTION',
              fill='#34554b', font=_font(11))
    return image


def _homography(points_from, points_to):
    """Eight-parameter projective fit using only standard-library elimination."""
    rows = []
    for (x, y), (u, v) in zip(points_from, points_to):
        rows.append([x, y, 1., 0., 0., 0., -u * x, -u * y, u])
        rows.append([0., 0., 0., x, y, 1., -v * x, -v * y, v])
    for col in range(8):
        pivot = max(range(col, 8), key=lambda row: abs(rows[row][col]))
        if abs(rows[pivot][col]) < 1e-10:
            raise ValueError('Landmark configuration is degenerate.')
        rows[col], rows[pivot] = rows[pivot], rows[col]
        scale = rows[col][col]
        rows[col] = [value / scale for value in rows[col]]
        for row in range(8):
            if row != col:
                factor = rows[row][col]
                rows[row] = [a - factor * b for a, b in zip(rows[row], rows[col])]
    return tuple(rows[row][-1] for row in range(8))


def _projective(spec):
    w, h = spec[4]
    seed = spec[-1]
    donor = _paper((w * 2, h * 2), seed)
    dw, dh = donor.size
    draw = ImageDraw.Draw(donor)
    ink = '#365348'
    draw.rectangle((36, 36, dw - 37, dh - 37), outline=ink, width=4)
    draw.text((185, 85), spec[5], fill=ink, font=_font(40, True))
    donor_points = [(110, 130), (dw - 130, 145), (dw - 150, dh - 125), (125, dh - 145)]
    for label, (x, y) in zip('ABCD', donor_points):
        draw.ellipse((x - 12, y - 12, x + 12, y + 12), outline=ink, width=3)
        draw.line((x - 23, y, x + 23, y), fill=ink, width=2)
        draw.line((x, y - 23, x, y + 23), fill=ink, width=2)
        draw.text((x + 24, y - 11), label, fill=ink, font=_font(22, True))
    for i in range(10):
        x = 220 + i * (dw - 440) // 9
        top = 290 + (i * 43 + seed) % 110
        draw.line((x, dh - 410, x + 19, top), fill='#527052', width=5)
        for j in range(6):
            yy = top + j * 66
            draw.arc((x - 52, yy - 25, x + 24, yy + 29), 190, 350, fill='#73815a', width=4)
            draw.arc((x + 16, yy - 10, x + 89, yy + 45), 190, 350, fill='#a2875e', width=4)
    draw.text((220, dh - 350), f'ÉCHANTILLON {seed} / SÉRIE B–{seed - 250} / FOLIO', fill=ink, font=_font(35, True))
    draw.text((220, dh - 289), 'MÉTHODE / DRY PRESS / 48 FEUILLES / SÉRIE 2026 / ATELIER', fill=ink, font=_font(25))
    draw.line((220, dh - 246, dw - 225, dh - 246), fill=ink, width=2)
    draw.text((220, dh - 214), 'ORIGINAL PAPER • FINE LINES • KEEP THE DIACRITICS / ARCHIVE 2026', fill=ink, font=_font(22))
    target_points = [(74 + seed % 7, 83), (w - 91, 105 + seed % 9),
                     (w - 121 - seed % 5, h - 96), (103, h - 76 - seed % 11)]
    mapping = _homography(target_points, donor_points)
    # Critical fairness property: the clean target is sampled ONCE DIRECTLY
    # from the supplied high-resolution donor, never from a missing original.
    clean = donor.transform((w, h), Image.Transform.PERSPECTIVE, mapping,
                            resample=Image.Resampling.BICUBIC, fillcolor='#e8dec8')
    damage = Image.new('L', (w, h))
    dd = ImageDraw.Draw(damage)
    cx = w // 2 + (seed % 3 - 1) * 13
    dd.polygon([(cx - 35, h * .32), (cx + 23, h * .35), (cx + 17, h * .55),
                (cx + 48, h * .64), (cx + 29, h * .85), (cx - 31, h * .84),
                (cx - 22, h * .61), (cx - 46, h * .48)], fill=255)
    guide_mask = damage.filter(ImageFilter.MaxFilter(17))
    protected = ImageChops.invert(guide_mask)
    damaged = clean.copy()
    damaged.paste(Image.new('RGB', (w, h), '#a4617c'), (0, 0), damage)
    guide = damaged.copy()
    edge = ImageChops.subtract(guide_mask, guide_mask.filter(ImageFilter.MinFilter(5)))
    guide.paste(Image.new('RGB', (w, h), '#00a9c0'), (0, 0), edge)
    details = []
    # Focus on the actual visible target ink, not broad rectangles of paper.
    # Warp donor semantic row neighborhoods with the same directly sampled
    # camera geometry; no hidden font reconstruction is expected of producers.
    ink_difference = ImageChops.difference(clean, Image.new('RGB', (w, h), ink))
    channels = [channel.point(lambda value: 255 if value <= 24 else 0)
                for channel in ink_difference.split()]
    target_ink = ImageChops.darker(ImageChops.darker(channels[0], channels[1]), channels[2])
    target_ink = target_ink.filter(ImageFilter.MaxFilter(3))
    for name, y0, y1 in [('heading', dh - 354, dh - 307),
                         ('details', dh - 293, dh - 254),
                         ('rule-caption', dh - 250, dh - 181)]:
        donor_row = Image.new('L', donor.size)
        ImageDraw.Draw(donor_row).rectangle((200, y0, dw - 200, y1), fill=255)
        row = donor_row.transform((w, h), Image.Transform.PERSPECTIVE, mapping,
                                   resample=Image.Resampling.BICUBIC)
        details.append({'name': name, 'mask': ImageChops.multiply(
            ImageChops.multiply(row, guide_mask), target_ink)})
    return ({'scene': damaged, 'donor': donor, 'guide': guide},
            {'damage': damage, 'guide': guide_mask, 'protected': protected},
            {'clean_original': clean, 'donor_points': donor_points, 'target_points': target_points,
             'resample': 'bicubic', 'detail_masks': details})


def _actor(seed, near=False):
    sprite = Image.new('RGBA', (112, 112))
    draw = ImageDraw.Draw(sprite)
    if near:
        fill = (165 + seed % 13 * 4, 103 + seed % 17 * 3, 68 + seed % 11 * 4)
        draw.ellipse((11, 29, 88, 83), fill=fill, outline='#34564b', width=2)
        draw.polygon([(83, 55), (108, 25), (106, 86)], fill='#dfc994', outline='#34564b')
        for x in range(24, 81, 9 + seed % 4):
            draw.arc((x - 8, 35, x + 10, 77), 270, 90, fill='#f5dfad', width=3)
        draw.ellipse((22, 45, 29, 52), fill='#34564b')
    else:
        fill = (80 + seed % 17 * 3, 130 + seed % 13 * 3, 150 + seed % 11 * 4)
        draw.polygon([(56, 14), (94, 34), (99, 79), (63, 99), (20, 78), (13, 36)],
                     fill=fill, outline='#34564b')
        for radius in (12 + seed % 3, 21 + seed % 5, 33):
            draw.ellipse((56 - radius, 56 - radius, 56 + radius, 56 + radius), outline='#f0d8a6', width=2)
        draw.line((20, 35, 92, 79), fill='#34564b', width=3)
    return sprite


def _choreography(spec):
    background = _base(spec)
    w, h = background.size
    draw = ImageDraw.Draw(background)
    draw.rounded_rectangle((35, 80, w - 36, h - 66), radius=13, fill='#789499', outline='#355750', width=3)
    for y in range(98, h - 70, 18):
        draw.line((45, y, w - 46, y + 2), fill='#b2c1bc')
    # Deliberately share pigments with the foreground.  A global color mask
    # would incorrectly move/occlude these unmistakably printed mural motifs.
    for j in range(4):
        for i in range(7):
            x, y = 69 + i * (w - 138) // 6, 124 + j * 69
            color = '#47645b' if (i + j) % 2 else '#355b43'
            draw.polygon([(x, y - 14), (x + 18, y), (x, y + 14), (x - 18, y)],
                         fill=color, outline='#a3ad78')
            draw.line((x - 10, y, x + 10, y), fill='#a3ad78')
    foregrounds = []
    for i, x in enumerate((round(w * .36), round(w * .65))):
        fg = Image.new('RGBA', background.size)
        fd = ImageDraw.Draw(fg)
        color = '#47645b' if i == 0 else '#355b43'
        fd.line((x - 9, h - 78, x + 11, 106), fill='#dbc79b', width=13)
        fd.line((x - 9, h - 78, x + 11, 106), fill=color, width=9)
        for j in range(6):
            y = 118 + j * 39
            fd.polygon([(x + 10, y + 16), (x - 56, y - 12), (x - 31, y + 31)],
                       fill=color, outline='#dbc79b', width=2)
            fd.polygon([(x + 10, y + 17), (x + 63, y - 15), (x + 39, y + 35)],
                       fill=color, outline='#dbc79b', width=2)
            fd.line((x - 33, y + 5, x + 35, y + 12), fill='#a3ad78')
        # Opaque native stencils: original matting is observable, not guessed.
        foregrounds.append({'id': 'rear_veil_I' if i == 0 else 'front_veil_II',
                            'rgba': fg, 'depth': 2 if i == 0 else 4})
    scene = background.convert('RGBA')
    for fg in foregrounds:
        scene.alpha_composite(fg['rgba'])
    scene = scene.convert('RGB')
    guide = scene.copy()
    gd = ImageDraw.Draw(guide)
    for i, x in enumerate((round(w * .36), round(w * .65))):
        gd.text((x - 46, 62), 'VEIL I' if i == 0 else 'VEIL II', fill='#154d52', font=_font(15, True))
        gd.rectangle((x - 61, 99, x + 67, h - 75), outline='#00a9c0' if i == 0 else '#b7629c', width=2)
    gd.text((44, h - 60), 'FAR ACTOR < VEIL I < NEAR ACTOR < VEIL II', fill='#355750', font=_font(13, True))
    actors = []
    atlas = Image.new('RGBA', (256, 128))
    c = _contract(spec)
    for i, name in enumerate(('actor_far', 'actor_near')):
        sprite = _actor(spec[-1], near=bool(i))
        atlas.alpha_composite(sprite, (8 + i * 128, 8))
        m = c['motion'][name]
        positions = []
        for n in range(36):
            u = n / 35
            positions.append([round(m['start'][0] + (m['end'][0] - m['start'][0]) * u),
                              round(m['start'][1] + (m['end'][1] - m['start'][1]) * u + m['bob'] * math.sin(2 * math.pi * u))])
        actors.append({'id': name, 'rgba': sprite, 'depth': 1 if i == 0 else 3, 'positions': positions})
    return ({'scene': scene, 'atlas': atlas, 'depth_guide': guide,
             'actor_far': actors[0]['rgba'], 'actor_near': actors[1]['rgba']},
            {}, {'background': background, 'foregrounds': foregrounds, 'actors': actors,
                 'required_crossing_objects': ['rear_veil_I', 'actor_near', 'front_veil_II']})


def _link(seed, second=False):
    image = Image.new('RGBA', (268, 124))
    draw = ImageDraw.Draw(image)
    base = (37 if second else 34, 39 if second else 43)
    delta = (160 + seed % 5 * 4, 14 + seed % 3 * 3) if second else (180 + seed % 4 * 6, 23 - seed % 3 * 4)
    tip = (base[0] + delta[0], base[1] + delta[1])
    length = math.hypot(*delta)
    nx, ny = -delta[1] / length * 11, delta[0] / length * 11
    polygon = [(base[0] + nx, base[1] + ny), (tip[0] + nx, tip[1] + ny),
               (tip[0] - nx, tip[1] - ny), (base[0] - nx, base[1] - ny)]
    draw.polygon(polygon, fill='#c9b487' if second else '#d9c69a', outline='#415b4a')
    hatch_count = 11 + seed % 4
    for k in range(1, hatch_count + 1):
        u = k / (hatch_count + 2)
        x, y = base[0] + u * delta[0], base[1] + u * delta[1]
        draw.line((x + nx * .7, y + ny * .7, x - nx * .7, y - ny * .7), fill='#7c8b62', width=2)
    for point, color in ((base, '#e39a43' if second else '#00a4b5'),
                         (tip, '#c54888' if second else '#e39a43')):
        x, y = point
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=color, outline='#fff1cf', width=2)
        draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill='#304d43')
    return {'id': 'link_2' if second else 'link_1', 'rgba': image,
            'base_anchor': base, 'tip_anchor': tip}, length


def _mechanism(spec):
    background = _base(spec)
    w, h = background.size
    c = _contract(spec)
    branch = c['elbow_branch']
    base_pivot = (round(w * .30), round(h * (.30 if branch == 1 else .50)))
    draw = ImageDraw.Draw(background)
    x, y = base_pivot
    draw.ellipse((x - 10, y - 10, x + 10, y + 10), fill='#00a4b5', outline='#fff1cf', width=2)
    draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill='#304d43')
    path = c['endpoint_path']
    endpoints = []
    for n in range(36):
        theta = 2 * math.pi * n / 36 + math.radians(path['phase_degrees'])
        point = (path['center'][0] + path['radii'][0] * math.cos(theta),
                 path['center'][1] + path['radii'][1] * math.sin(theta))
        endpoints.append(point)
        draw.ellipse((point[0] - 1, point[1] - 1, point[0] + 1, point[1] + 1), fill='#b68c9c')
    layers_and_lengths = [_link(spec[-1], second=False), _link(spec[-1], second=True)]
    layers = [item[0] for item in layers_and_lengths]
    lengths = [item[1] for item in layers_and_lengths]
    return ({'scene': background, 'link_1': layers[0]['rgba'], 'link_2': layers[1]['rgba']}, {},
            {'background': background, 'layers': layers, 'base_pivot': base_pivot,
             'lengths': lengths, 'endpoint_positions': endpoints, 'branch': branch,
             'front_link': 1, 'joint_support_radii': [7, 7, 7]})


def _build(spec):
    return {'projective': _projective, 'choreography': _choreography, 'mechanism': _mechanism}[spec[3]](spec)


def _ik(properties, endpoint):
    gx, gy = properties['base_pivot']
    dx, dy = endpoint[0] - gx, endpoint[1] - gy
    distance = math.hypot(dx, dy)
    a, b = properties['lengths']
    if not abs(a - b) < distance < a + b:
        raise ValueError('Endpoint is outside the reachable non-singular annulus.')
    along = (a * a - b * b + distance * distance) / (2 * distance)
    height = math.sqrt(max(0., a * a - along * along))
    sign = properties['branch']
    return (gx + along * dx / distance - sign * height * dy / distance,
            gy + along * dy / distance + sign * height * dx / distance)


def _warp_link(layer, base, tip, output_size):
    sx, sy = layer['base_anchor']
    tx, ty = layer['tip_anchor']
    source_angle = math.atan2(ty - sy, tx - sx)
    angle = math.atan2(tip[1] - base[1], tip[0] - base[0]) - source_angle
    cosine, sine = math.cos(angle), math.sin(angle)
    # Pillow coefficients map destination to source; rotation preserves length.
    inverse = (cosine, sine, sx - cosine * base[0] - sine * base[1],
               -sine, cosine, sy + sine * base[0] - cosine * base[1])
    return layer['rgba'].transform(output_size, Image.Transform.AFFINE, inverse,
                                   resample=Image.Resampling.BICUBIC)


def _authored_frame(facts, index):
    p = facts['properties']
    if facts['family'] == 'projective':
        return p['clean_original'].copy()
    frame = p['background'].convert('RGBA')
    if facts['family'] == 'choreography':
        layers = [(item['depth'], item['rgba'], (0, 0)) for item in p['foregrounds']]
        layers += [(item['depth'], item['rgba'], item['positions'][index]) for item in p['actors']]
        for depth, image, point in sorted(layers, key=lambda item: item[0]):
            frame.alpha_composite(image, tuple(point))
    else:
        endpoint = p['endpoint_positions'][index]
        elbow = _ik(p, endpoint)
        for layer, base, tip in ((p['layers'][0], p['base_pivot'], elbow),
                                 (p['layers'][1], elbow, endpoint)):
            frame.alpha_composite(_warp_link(layer, base, tip, frame.size))
    return frame.convert('RGB')


def _encode(frames, destination):
    binary = shutil.which('ffmpeg')
    if not binary:
        raise RuntimeError('FFmpeg is required for synthetic repair inputs.')
    result = subprocess.run([binary, '-v', 'error', '-y', '-filter_threads', '1', '-filter_complex_threads', '1',
                             '-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size', f'{frames[0].width}x{frames[0].height}',
                             '-framerate', '12', '-i', 'pipe:0', '-an', '-c:v', 'libx264', '-crf', '18', '-threads', '1',
                             '-pix_fmt', 'yuv420p', '-map_metadata', '-1', str(destination)],
                            input=b''.join(frame.tobytes() for frame in frames), capture_output=True, timeout=45)
    if result.returncode:
        raise RuntimeError('Synthetic repair input encoding failed.')


def materialize_task(task_id, workspace):
    spec = _spec(task_id)
    workspace = Path(workspace).resolve()
    inputs = workspace / 'inputs'
    if inputs.is_symlink() or (inputs.exists() and any(inputs.iterdir())):
        raise ValueError('Use a fresh empty input directory.')
    inputs.mkdir(parents=True, exist_ok=True)
    c = _contract(spec)
    assets, masks, properties = _build(spec)
    for name, image in assets.items():
        image.save(inputs / c['assets'][name])
    (inputs / 'scene.json').write_text(json.dumps(c, ensure_ascii=False, indent=2) + '\n')
    facts = {**{key: c[key] for key in ('task_id', 'family', 'size', 'duration', 'fps', 'frame_count')},
             'contract': c, 'properties': properties, 'masks': masks}
    if spec[2] == 'repair':
        if not c['duration']:
            broken = assets['scene'].copy()
            broken.paste(properties['clean_original'].filter(ImageFilter.GaussianBlur(3)), (0, 0), masks['guide'])
            broken.save(inputs / 'broken-seed.png')
        elif spec[3] == 'mechanism':
            _encode([_authored_frame(facts, 0)] * 36, inputs / 'broken-seed.mp4')
        else:
            frames = []
            for n in range(36):
                frame = assets['scene'].convert('RGBA')
                # Flawed depth: both actors incorrectly cover both veils.
                for actor in properties['actors']:
                    frame.alpha_composite(actor['rgba'], tuple(actor['positions'][n]))
                frames.append(frame.convert('RGB'))
            _encode(frames, inputs / 'broken-seed.mp4')
    reference_names = {'projective': ['scene', 'donor', 'guide'],
                       'choreography': ['scene', 'atlas', 'depth_guide'],
                       'mechanism': ['scene', 'link_1', 'link_2']}[spec[3]]
    purposes = {'scene': 'original damaged print' if spec[3] == 'projective' else 'original stationary scene or mechanism base',
                'donor': 'complete higher-resolution original donor with four labeled landmarks',
                'guide': 'original damaged print with cyan repair authorization boundary',
                'atlas': 'original atlas containing both complete patterned actors',
                'depth_guide': 'public guide identifying both veils and required depth order',
                'link_1': 'complete first rigid link with cyan and orange pivot markers',
                'link_2': 'complete second rigid link with orange and magenta pivot markers'}
    references = [inputs / c['assets'][name] for name in reference_names]
    public = _public(spec)
    return {'task_id': task_id, **{key: public[key] for key in ('split', 'kind', 'brief', 'output_name')},
            'specification_path': str(inputs / 'scene.json'),
            'reference_paths': [str(path) for path in references],
            'reference_roles': {str(path): purposes[name] for path, name in zip(references, reference_names)},
            'source_hashes': {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(inputs.iterdir())}}


def quality_spec(task_id, workspace):
    spec = _spec(task_id)
    workspace = Path(workspace).resolve()
    c = json.loads((workspace / 'inputs' / 'scene.json').read_text())
    if c != _contract(spec):
        raise ValueError('Public task contract does not match the definition.')
    assets, masks, properties = _build(spec)
    return {**{key: c[key] for key in ('task_id', 'family', 'size', 'duration', 'fps', 'frame_count')},
            'contract': c, 'assets': {name: str(workspace / 'inputs' / filename) for name, filename in c['assets'].items()},
            'masks': masks, 'properties': properties}


def authored_example_frames(task_id, workspace, **_unused):
    facts = quality_spec(task_id, workspace)
    for index in range(facts['frame_count']):
        yield _authored_frame(facts, index)


def authored_example_frame(task_id, workspace, index=0, **kwargs):
    for n, frame in enumerate(authored_example_frames(task_id, workspace, **kwargs)):
        if n == index:
            return frame
    raise ValueError('Frame index outside the public timeline.')
