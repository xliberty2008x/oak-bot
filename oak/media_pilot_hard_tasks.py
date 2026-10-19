"""Fresh, disclosed quality challenges for the second synthetic media pilot.

Only inputs are written into producer workspaces.  The runner's reference
renderer is deliberately kept in this module, outside those workspaces.  Every
coordinate, layer, timing rule and tolerance it uses is in ``scene.json``.
"""

import hashlib
import json
import math
from pathlib import Path
import random
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFont


_SPECS = (
    ('dev-repair-owl-blink', 'dev', 'repair', 'blink', (640, 416), 'CEDAR OWL', 0),
    ('dev-create-woven-pendulum', 'dev', 'creation', 'pendulum', (672, 448), 'WOVEN LANTERN', 1),
    ('dev-create-koi-occlusion', 'dev', 'creation', 'occlusion', (704, 432), 'KOI / REED STUDY', 2),
    ('dev-repair-copper-orbit', 'dev', 'repair', 'orbit', (608, 448), 'COPPER OBSERVATORY', 3),
    ('dev-create-linen-collection', 'dev', 'creation', 'labels', (900, 660), 'LINEN FIELD NOTES', 4),
    ('dev-repair-moth-archive', 'dev', 'repair', 'archival', (860, 640), 'MOTH ARCHIVE / 07', 5),
    ('holdout-repair-fox-blink', 'holdout', 'repair', 'blink', (736, 464), 'DUNE FOX', 6),
    ('holdout-create-glass-pendulum', 'holdout', 'creation', 'pendulum', (624, 496), 'GLASS MOBILE', 7),
    ('holdout-create-beetle-occlusion', 'holdout', 'creation', 'occlusion', (752, 472), 'BEETLE / FERN STUDY', 8),
    ('holdout-repair-indigo-orbit', 'holdout', 'repair', 'orbit', (688, 512), 'INDIGO TIDE CLOCK', 9),
    ('holdout-create-seed-collection', 'holdout', 'creation', 'labels', (960, 720), 'SEED LIBRARY / 18', 10),
    ('holdout-repair-shell-archive', 'holdout', 'repair', 'archival', (920, 680), 'SHELL ARCHIVE / 23', 11),
)


def _unknown(task_id):
    raise ValueError('Unknown hard synthetic media task: ' + str(task_id))


def _spec(task_id):
    return next((row for row in _SPECS if row[0] == task_id), None) or _unknown(task_id)


def _public(spec):
    task_id, split, kind, family, size, title, variant = spec
    requests = {
        'blink': 'Repair the supplied whole-face bobbing animation. Animate two local eyelids only, using the supplied closed-eye patches. Both eyes must close and reopen twice; the textured face, pupils when open, lettering, border and background must remain fixed.',
        'pendulum': 'Create a looping articulated pendulum study from the supplied woven or glass subject. Rotate the supplied subject and its tether together around the exact marked pivot. Preserve the detailed stationary print, including its fine lettering.',
        'occlusion': 'Create a continuous passage of the supplied patterned subject behind the supplied foreground plants. Preserve its complete pattern, keep the source landscape stationary, and maintain the specified background → subject → foreground layer order at every crossing.',
        'orbit': 'Repair the supplied sparse-checkpoint orbit. Its apparent positions at a few snapshots are plausible, but it freezes between them. Make the supplied patterned marker travel continuously around the disclosed elliptical path in every frame; preserve the stationary dial and all lettering.',
        'labels': 'Create a print-ready collection card by composing the supplied textured artwork and exact fine-lettered transparent label plates. Retain all small type, accent marks, rules, registration details and paper texture. Do not retype the labels, flatten their transparent margins to white, or redraw the artwork.',
        'archival': 'Repair the supplied archival card: its central artwork is stretched, fine label strips are clipped and some paper areas were painted over. Recompose the card from the supplied original paper and layer plates at their disclosed placements; preserve every fine label and the original texture.',
    }
    video = family in {'blink', 'pendulum', 'occlusion', 'orbit'}
    brief = (requests[family] + '\nProject: ' + title + '. All assets are original synthetic illustrations. '
             'Read inputs/scene.json before rendering: it contains the full layer atlas coordinates, placements, '
             'frame timing/formulas, editable regions and acceptance tolerances. These are production specifications, '
             'not optional suggestions. Use the supplied raster layers without replacing their style. '
             'Only local Python standard libraries, Pillow and ffmpeg are needed. '
             'Coordinates use top-left origins; boxes have exclusive right/bottom edges. ')
    if video:
        brief += (f'Deliver final.mp4: silent H.264, {size[0]}×{size[1]}, exactly 3 seconds, 12 fps, '
                  '36 indexed frames (0–35), yuv420p. Evaluate the motion at n / 12 seconds; '
                  'the endpoint at 3 seconds is the next loop, not an extra frame. '
                  'Inspect motion between sampled thumbnails as well as the overall appearance. ')
    else:
        brief += f'Deliver final.png: opaque RGB PNG, exactly {size[0]}×{size[1]}. '
    if kind == 'repair':
        brief += 'inputs/broken-seed.' + ('mp4' if video else 'png') + ' is the deliberately flawed starting candidate. '
    brief += ('The visual references are inputs/scene.png, inputs/atlas.png and, for a repair, the broken seed. '
              'Additional layer files named by scene.json are ordinary supplied inputs. Do not alter input bytes.')
    return {'id': task_id, 'split': split, 'kind': kind, 'brief': brief,
            'output_name': 'final.mp4' if video else 'final.png'}


TASKS = tuple(_public(spec) for spec in _SPECS)


def _font(size, bold=False):
    try:
        return ImageFont.truetype('DejaVuSans' + ('-Bold' if bold else '') + '.ttf', size)
    except OSError:
        return ImageFont.load_default(size=size)


def _paper(size, seed, dark=False):
    rng = random.Random('oak-hard-paper:' + str(seed))
    base = (29, 49, 57) if dark else (234, 224, 201)
    image = Image.new('RGB', size, base)
    pixels = image.load()
    # Restrained paper grain is retained under ordinary H.264 encoding.
    for _ in range(size[0] * size[1] // 9):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        delta = rng.choice((-8, -5, 5, 8))
        pixels[x, y] = tuple(max(0, min(255, c + delta)) for c in base)
    draw = ImageDraw.Draw(image)
    for i in range(20):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        draw.line((x, y, min(size[0] - 1, x + rng.randrange(6, 36)), y),
                  fill=(54, 75, 78) if dark else (210, 198, 176), width=1)
    return image


def _scene(size, title, variant):
    image = _paper(size, variant, dark=variant % 4 == 3)
    draw = ImageDraw.Draw(image)
    w, h = size
    ink = '#eddfb8' if variant % 4 == 3 else '#253f42'
    draw.rectangle((14, 14, w - 15, h - 15), outline=ink, width=2)
    draw.rectangle((19, 19, w - 20, h - 20), outline=ink, width=1)
    draw.text((30, 27), title, fill=ink, font=_font(17, True))
    draw.text((30, h - 36), f'PLATE {variant + 31:02d}  /  TEXTURE • MOTION • STUDY  /  2026',
              fill=ink, font=_font(10))
    for x in range(32, w - 30, 19):
        draw.line((x, h - 54, x, h - 50 - (3 if x % 3 else 0)), fill=ink, width=1)
    return image


def _region(name, box, role, max_mae=6.0):
    return {'name': name, 'box': list(map(int, box)), 'role': role, 'max_mae': max_mae}


def _sprite(variant, family):
    image = Image.new('RGBA', (112, 120))
    draw = ImageDraw.Draw(image)
    ink = '#25494a'
    if family == 'pendulum':
        draw.line((56, 0, 56, 65), fill='#dfba77', width=3)
        if variant < 6:
            draw.polygon([(56, 56), (85, 74), (77, 108), (36, 108), (26, 74)], fill='#ca7050', outline=ink)
            for y in range(69, 108, 5):
                draw.line((32, y, 80, y), fill='#f2d18b', width=2)
            for x in range(39, 80, 8):
                draw.line((x, 65, x - 7, 108), fill=ink, width=1)
        else:
            draw.ellipse((30, 59, 82, 111), fill='#6caaa7', outline=ink, width=2)
            draw.arc((35, 64, 76, 106), 100, 280, fill='#e7e1ba', width=3)
            draw.line((56, 59, 56, 111), fill='#e7e1ba', width=2)
    elif family == 'occlusion':
        if variant < 6:
            draw.ellipse((12, 35, 92, 85), fill='#d7774c', outline=ink, width=2)
            draw.polygon([(90, 60), (110, 31), (108, 88)], fill='#edd59f', outline=ink)
            for x in range(23, 85, 12):
                draw.arc((x - 8, 41, x + 8, 80), 270, 90, fill='#f8e5b6', width=3)
            draw.ellipse((22, 49, 28, 55), fill=ink)
        else:
            draw.ellipse((24, 21, 88, 100), fill='#467976', outline=ink, width=3)
            draw.line((56, 24, 56, 99), fill='#ebbf64', width=3)
            for y in range(35, 94, 13):
                draw.arc((30, y - 10, 53, y + 7), 10, 170, fill='#d7cda0', width=2)
                draw.arc((59, y - 10, 82, y + 7), 10, 170, fill='#d7cda0', width=2)
            for y in (39, 61, 83):
                draw.line((26, y, 10, y - 9), fill=ink, width=3)
                draw.line((86, y, 103, y - 9), fill=ink, width=3)
    else:
        fill = '#ddac69' if variant < 6 else '#728eaa'
        pattern = ink if variant < 6 else '#e5d9b8'
        draw.polygon([(56, 21), (88, 59), (56, 98), (24, 59)], fill=fill, outline=ink)
        for inset in (8, 16, 24):
            draw.polygon([(56, 21 + inset), (88 - inset, 59), (56, 98 - inset), (24 + inset, 59)], outline=pattern)
        draw.ellipse((50, 53, 62, 65), fill='#f6edcd')
    return image


def _base_contract(spec):
    task_id, _, _, family, size, title, variant = spec
    video = family in {'blink', 'pendulum', 'occlusion', 'orbit'}
    return {
        'version': 'oak-hard-media-v2', 'task_id': task_id, 'family': family,
        'size': list(size), 'duration': 3 if video else 0, 'fps': 12 if video else 0,
        'frame_count': 36 if video else 1,
        'coordinate_convention': 'integer top-left; box right/bottom exclusive; source-over RGBA alpha composition',
        'assets': {'scene': 'scene.png', 'atlas': 'atlas.png'}, 'regions': [],
        'acceptance': {
            'geometry_tolerance_px': 2,
            'mean_absolute_channel_error': 6 if video else 2,
            'region_tolerance_note': 'The default region tolerance is 6 for video or 2 for PNG. Each region.max_mae is authoritative: moving pendulum, subject-passage and orbit sweep regions allow 10; video eye, foreground and fine-text regions retain 6. PNG regions retain 2.',
            'channel_noise_deadband': 2 if video else 0,
            'note': 'Source-preserving quality tolerances allow codec noise, subpixel interpolation and up to 2px geometry rounding. Do not substitute artwork, retype supplied raster text, shift protected regions, or freeze continuous motion. Regional checks include small eyes and fine text, not only a full-frame average.',
            'reference_transform': ('For video comparison only, convert the specified RGB composition and base scene through FFmpeg raw RGB24 with filters format=yuv420p,format=rgb24 and standard swscale defaults, without lossy reference encoding or a CRF setting. This fixed chroma-subsampling reference accounts for the required yuv420p delivery format. PNG comparisons use the native RGB composition and scene.'),
            'regional_metric': 'Compare every indexed frame to the source-over composition defined here. For support_mode=full use the complete named region. Otherwise use pixels where any RGB channel differs from the base scene by more than 12 for the specified composition or more than 16 for the submitted frame; use the complete region if the specified composition has no pixels differing from the base by more than 12. Before averaging, each absolute channel error becomes max(0, abs(submitted-reference)-channel_noise_deadband); the fixed deadband is 2/255 for video and 0 for PNG. Mean channel error on this support allows the best translation of at most 2px. The 95th percentile across frames must be at most region.max_mae and the worst frame at most 1.5 times region.max_mae. The stationary complement uses the same channel deadband, allows no translation and has worst-frame MAE at most 7 for video or 2 for PNG.',
        },
    }


def _make_assets(spec, inputs):
    task_id, _, _, family, size, title, variant = spec
    w, h = size
    contract = _base_contract(spec)
    scene = _scene(size, title, variant)
    draw = ImageDraw.Draw(scene)
    atlas = Image.new('RGBA', (384, 256))
    if family == 'blink':
        cx, cy = w // 2, h // 2 + 6
        fur = '#bb835b' if variant < 6 else '#b58b69'
        if variant < 6:
            draw.polygon([(cx - 138, cy - 87), (cx - 108, cy - 139), (cx - 38, cy - 107),
                          (cx + 38, cy - 107), (cx + 108, cy - 139), (cx + 138, cy - 87),
                          (cx + 118, cy + 112), (cx, cy + 135), (cx - 118, cy + 112)], fill=fur, outline='#3b4540')
        else:
            draw.polygon([(cx - 145, cy - 100), (cx - 120, cy - 153), (cx - 49, cy - 85),
                          (cx + 49, cy - 85), (cx + 120, cy - 153), (cx + 145, cy - 100),
                          (cx + 104, cy + 78), (cx, cy + 132), (cx - 104, cy + 78)], fill=fur, outline='#3b4540')
        eye_skin = '#ddc59b' if variant < 6 else '#d5bf9d'
        for sign in (-1, 1):
            draw.ellipse((cx + sign * 75 - 61, cy - 70, cx + sign * 75 + 61, cy + 49), fill=eye_skin, outline='#514e3f', width=3)
        boxes = [(cx - 115, cy - 40, cx - 31, cy + 16), (cx + 31, cy - 40, cx + 115, cy + 16)]
        for index, box in enumerate(boxes):
            x0, y0, x1, y1 = box
            draw.ellipse((x0 + 2, y0 + 2, x1 - 3, y1 - 3), fill='#f1e0b8', outline='#514e3f', width=2)
            draw.ellipse((x0 + 25, y0 + 6, x0 + 57, y1 - 6), fill='#ad7838')
            draw.ellipse((x0 + 35, y0 + 10, x0 + 46, y1 - 10), fill='#223b3b')
            draw.ellipse((x0 + 32, y0 + 11, x0 + 36, y0 + 15), fill='#fff8db')
            closed = Image.new('RGBA', (84, 56), eye_skin)
            cd = ImageDraw.Draw(closed)
            cd.arc((6, 5, 78, 47), 15, 165, fill='#514e3f', width=3)
            for tx in range(8, 78, 11):
                cd.line((tx, 8, tx + 4, 11), fill='#c7ad83', width=1)
            atlas.alpha_composite(closed, (index * 100, 0))
            contract['regions'].append(_region('eye-' + str(index + 1), box, 'editable') | {'support_mode': 'full'})
        draw.polygon([(cx - 13, cy + 31), (cx + 13, cy + 31), (cx, cy + 61)], fill='#425052')
        for dx in range(-84, 85, 21):
            draw.arc((cx + dx - 8, cy + 69, cx + dx + 8, cy + 89), 0, 170, fill='#8d674e', width=2)
        contract['animation'] = {
            'type': 'blink-local-bands', 'eyes': [{'box': list(box), 'closed_atlas_box': [i * 100, 0, i * 100 + 84, 56]} for i, box in enumerate(boxes)],
            'centers': [10, 27] if variant < 6 else [8, 25], 'half_span': 4 if variant < 6 else 5,
            'formula': 'closure(n)=max(0, max(1-abs(n-center)/half_span for center in centers)); k=round(eye_height*closure/2). Copy the first k and last k rows of each supplied closed patch over its source eye box. All other pixels remain the original scene.',
        }
    elif family == 'pendulum':
        pivot = [w // 2 + (22 if variant < 6 else -19), 104 if variant < 6 else 116]
        draw.rounded_rectangle((90, 71, w - 91, h - 76), radius=18, outline='#55706a', width=3)
        for y in range(84, h - 90, 13):
            draw.line((103, y, w - 104, y), fill='#b9b394', width=1)
        draw.ellipse((pivot[0] - 5, pivot[1] - 5, pivot[0] + 5, pivot[1] + 5), fill='#304d49')
        subject = _sprite(variant, family)
        atlas.alpha_composite(subject, (0, 0))
        # Long tether is supplied in its original proportion; no geometric redrawing needed.
        contract['animation'] = {'type': 'pendulum', 'atlas_box': [0, 0, 112, 120],
                                 'pivot': pivot, 'sprite_pivot': [56, 0], 'scale': 1.75 if variant < 6 else 1.9,
                                 'amplitude_degrees': 29 if variant < 6 else 24, 'phase_degrees': 0 if variant < 6 else 90,
                                 'formula': 'angle(n)=amplitude_degrees*sin(2*pi*n/36 + radians(phase_degrees)). Rotate a transparent full-canvas subject layer counterclockwise around pivot, using bicubic interpolation. The scene and printed pivot remain stationary.'}
        contract['regions'].append(_region('pendulum-sweep', [pivot[0] - 132, pivot[1] - 8, pivot[0] + 132, h - 69], 'editable', 10))
    elif family == 'occlusion':
        water = '#718e86' if variant < 6 else '#a6af86'
        draw.rounded_rectangle((38, 83, w - 39, h - 77), radius=17, fill=water, outline='#355957', width=3)
        for y in range(103, h - 85, 18):
            draw.line((48, y, w - 49, y + (variant % 3 - 1) * 3), fill='#bdc6a5', width=1)
        subject = _sprite(variant, family)
        atlas.alpha_composite(subject, (0, 0))
        foreground = Image.new('RGBA', size)
        fd = ImageDraw.Draw(foreground)
        centers = [round(w * .39), round(w * .68)] if variant < 6 else [round(w * .32), round(w * .61)]
        regions = []
        for index, x in enumerate(centers):
            bottom = h - 82
            fd.line((x - 9, bottom, x + 13, 111), fill='#254e44', width=11)
            for row in range(5):
                y = 129 + row * 34
                fd.polygon([(x + 10, y + 14), (x - 49 - index * 5, y - 13), (x - 31, y + 22)], fill='#355d46')
                fd.polygon([(x + 9, y + 19), (x + 61, y - 12), (x + 38, y + 31)], fill='#52734c')
                fd.line((x - 33, y + 1, x + 39, y + 11), fill='#9aab70', width=1)
            regions.append(_region('foreground-' + str(index + 1), [x - 57, 108, x + 65, bottom + 7], 'occlusion'))
        foreground.save(inputs / 'foreground.png')
        contract['assets']['foreground'] = 'foreground.png'
        contract['animation'] = {'type': 'passage', 'atlas_box': [0, 0, 112, 120],
                                 'start': [49, h // 2 - 60], 'end': [w - 161, h // 2 - 60],
                                 'bob_amplitude': 13 if variant < 6 else 19, 'bob_cycles': 1 if variant < 6 else 2,
                                 'formula': 'u=n/35; x=round(start.x+(end.x-start.x)*u); y=round(start.y+(end.y-start.y)*u + bob_amplitude*sin(2*pi*bob_cycles*u)). Paste the complete unscaled subject, then the full-canvas foreground with its alpha. Layer order is scene, subject, foreground.'}
        contract['regions'].append(_region('subject-sweep', [44, h // 2 - 91, w - 44, h // 2 + 87], 'editable', 10))
        contract['regions'].extend(regions)
    elif family == 'orbit':
        center = [w // 2, h // 2 + 9]
        radii = [round(w * .29), round(h * .23)] if variant < 6 else [round(w * .30), round(h * .25)]
        draw.ellipse((center[0] - radii[0], center[1] - radii[1], center[0] + radii[0], center[1] + radii[1]), outline='#ae9e78', width=2)
        for i in range(24):
            a = 2 * math.pi * i / 24
            x, y = center[0] + (radii[0] + 10) * math.cos(a), center[1] + (radii[1] + 10) * math.sin(a)
            draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill='#dec58c')
        draw.text((center[0] - 47, center[1] - 10), 'MÉRIDIEN', fill='#e9d5a5', font=_font(15, True))
        subject = _sprite(variant, family).resize((56, 60), Image.Resampling.LANCZOS)
        atlas.alpha_composite(subject, (0, 0))
        contract['animation'] = {'type': 'orbit', 'atlas_box': [0, 0, 56, 60], 'center': center,
                                 'radii': radii, 'phase_degrees': 180 if variant < 6 else 270,
                                 'direction': 1 if variant < 6 else -1,
                                 'formula': 'theta=radians(phase_degrees)+direction*2*pi*n/36; x=round(center.x+radii.x*cos(theta)-sprite_width/2); y=round(center.y+radii.y*sin(theta)-sprite_height/2). Paste the unchanged sprite at (x,y) on every frame. The source dial stays fixed. Never quantize n into checkpoints.'}
        contract['regions'].append(_region('orbit-sweep', [center[0] - radii[0] - 30, center[1] - radii[1] - 32, center[0] + radii[0] + 30, center[1] + radii[1] + 32], 'editable', 10))
    else:
        _make_print_layers(spec, scene, inputs, contract)
        atlas = Image.open(inputs / 'atlas.png').convert('RGBA')
    # Persistent print strips are deliberately fine and excluded from any motion mask.
    contract['regions'].extend([_region('title-print', [28, 25, w - 28, 49], 'text', 6 if contract['duration'] else 2),
                                _region('footer-print', [28, h - 39, w - 28, h - 22], 'text', 6 if contract['duration'] else 2)])
    scene.save(inputs / 'scene.png')
    atlas.save(inputs / 'atlas.png')
    return contract


def _make_print_layers(spec, scene, inputs, contract):
    _, _, _, family, size, title, variant = spec
    w, h = size
    # Atlas rows contain the actual production raster labels, including alpha.
    atlas = Image.new('RGBA', (720, 420))
    labels = ([('ÉTUDE No. 04', 'FLAX / HANDWOVEN / 32 THREADS'),
               ('LOT B–17 • NATURAL', 'DRY SHADE  /  KEEP FLAT  /  240 g'),
               ('SOURCE & PROCESS', 'FIELD 08  /  51° N  /  SERIES 2026')]
              if variant < 6 else
              [('RÉSERVE No. 18', 'SEEDS / DRY STORE / 48 SAMPLES'),
               ('LOT C–23 • COASTAL', 'LOW LIGHT  /  KEEP DRY  /  180 g'),
               ('ORIGIN & RECORD', 'PLOT 19  /  47° N  /  SERIES 2026')])
    if family == 'archival':
        labels[0] = ('LÉPIDOPTÈRE / 07', 'WING VENATION  /  SCALE 2:1') if variant < 6 else ('COQUILLE / 23', 'SPIRAL RIDGES  /  SCALE 3:2')
    layers = []
    art_w, art_h = (w - 126, 285 if family == 'labels' else 268)
    art = _paper((art_w, art_h), 'art:' + str(variant), dark=False).convert('RGBA')
    ad = ImageDraw.Draw(art)
    rng = random.Random('oak-hard-print:' + str(variant))
    for i in range(9):
        x, y = 28 + i * (art_w - 56) / 9, 36 + rng.randrange(75)
        if family == 'archival':
            ad.ellipse((x - 15, y, x + 52, art_h - 24), fill=('#bb8c68' if variant < 6 else '#b6a380'), outline='#485f54', width=2)
            for line in range(7):
                ad.arc((x - 11 + line * 2, y + line * 8, x + 47 - line * 2, art_h - 30), 230, 110, fill='#e3c8a1', width=2)
        else:
            ad.line((x + 12, art_h - 21, x + 17, y), fill='#4e6a51', width=3)
            for row in range(4):
                yy = y + row * 28
                ad.ellipse((x - 8, yy, x + 17, yy + 17), fill='#91a075', outline='#4e6a51')
                ad.ellipse((x + 13, yy + 9, x + 39, yy + 27), fill='#bcaa74', outline='#4e6a51')
    ad.rectangle((0, 0, art_w - 1, art_h - 1), outline='#4e6a51', width=2)
    for x in range(8, art_w - 8, 13):
        ad.line((x, art_h - 12, x, art_h - 7), fill='#4e6a51')
    art.save(inputs / 'artwork.png')
    contract['assets']['artwork'] = 'artwork.png'
    art_pos = [63, 80]
    layers.append({'name': 'artwork', 'asset': 'artwork', 'position': art_pos, 'size': [art_w, art_h], 'operation': 'paste unscaled source-over'})
    contract['regions'].append(_region('original-artwork', [63, 80, 63 + art_w, 80 + art_h], 'art', 2))
    top = art_pos[1] + art_h + 24
    row_h = 68
    for index, (headline, detail) in enumerate(labels):
        box = [0, index * 120, 720, index * 120 + 100]
        ld = ImageDraw.Draw(atlas)
        yy = index * 120
        ld.text((3, yy + 4), headline, fill='#2e4b45', font=_font(19, True))
        ld.text((3, yy + 34), detail, fill='#3a534b', font=_font(12))
        ld.line((3, yy + 57, 680 - 15 * index, yy + 57), fill='#5f7762', width=1)
        for tick in range(9):
            ld.line((540 + tick * 13, yy + 13, 540 + tick * 13, yy + 23 + tick % 3 * 3), fill='#3a534b')
        pos = [63, top + index * row_h]
        # Atlases deliberately contain transparent padding: retain the paper below.
        layers.append({'name': 'label-' + str(index + 1), 'asset': 'atlas', 'crop': box, 'position': pos, 'operation': 'paste unscaled source-over; preserve alpha'})
        contract['regions'].append(_region('fine-label-' + str(index + 1), [pos[0], pos[1], pos[0] + 690, pos[1] + 60], 'text', 2))
    contract['layers'] = layers
    contract['composition'] = 'Start with scene.png at native size; apply layers in listed order. Crop atlas with exclusive box bounds, use its alpha, and paste at the specified integer position. Do not rescale or retype.'
    atlas.save(inputs / 'atlas.png')


def _load_contract(workspace):
    return json.loads((Path(workspace) / 'inputs' / 'scene.json').read_text())


def quality_spec(task_id, workspace):
    """Runner-only metadata; all requirements also appear in scene.json.

    The runner must not inject this object or this module into a model prompt.
    Asset paths are workspace inputs; no oracle outputs are written there.
    """
    _spec(task_id)
    workspace = Path(workspace).resolve()
    contract = _load_contract(workspace)
    if contract.get('task_id') != task_id:
        raise ValueError('Task workspace identity does not match.')
    return {key: contract[key] for key in ('task_id', 'family', 'size', 'duration', 'fps', 'frame_count', 'regions')} | {
        'contract': contract, 'assets': {key: str(workspace / 'inputs' / value) for key, value in contract['assets'].items()}}


def reference_frame(task_id, workspace, index=0):
    """Render the disclosed layer contract; never use this as a producer tool."""
    spec = quality_spec(task_id, workspace)
    contract = spec['contract']
    if not isinstance(index, int) or not 0 <= index < spec['frame_count']:
        raise ValueError('Frame index is outside the disclosed timeline.')
    with Image.open(spec['assets']['scene']) as source:
        frame = source.convert('RGBA')
    with Image.open(spec['assets']['atlas']) as source:
        atlas = source.convert('RGBA')
    if not spec['duration']:
        for layer in contract['layers']:
            if layer['asset'] == 'atlas':
                image = atlas.crop(layer['crop'])
            else:
                with Image.open(spec['assets'][layer['asset']]) as source:
                    image = source.convert('RGBA')
            frame.alpha_composite(image, tuple(layer['position']))
        return frame.convert('RGB')
    animation = contract['animation']
    family = spec['family']
    if family == 'blink':
        closure = max(0, max(1 - abs(index - center) / animation['half_span'] for center in animation['centers']))
        for eye in animation['eyes']:
            box = eye['box']
            patch = atlas.crop(eye['closed_atlas_box'])
            k = round(patch.height * closure / 2)
            if k:
                frame.alpha_composite(patch.crop((0, 0, patch.width, k)), (box[0], box[1]))
                frame.alpha_composite(patch.crop((0, patch.height - k, patch.width, patch.height)), (box[0], box[3] - k))
    elif family == 'pendulum':
        sprite = atlas.crop(animation['atlas_box'])
        scale = animation['scale']
        sprite = sprite.resize((round(sprite.width * scale), round(sprite.height * scale)), Image.Resampling.LANCZOS)
        pivot = animation['pivot']
        layer = Image.new('RGBA', frame.size)
        layer.alpha_composite(sprite, (pivot[0] - round(animation['sprite_pivot'][0] * scale), pivot[1]))
        angle = animation['amplitude_degrees'] * math.sin(2 * math.pi * index / spec['frame_count'] + math.radians(animation['phase_degrees']))
        layer = layer.rotate(angle, resample=Image.Resampling.BICUBIC, center=tuple(pivot))
        frame.alpha_composite(layer)
    else:
        sprite = atlas.crop(animation['atlas_box'])
        if family == 'occlusion':
            u = index / (spec['frame_count'] - 1)
            x = round(animation['start'][0] + (animation['end'][0] - animation['start'][0]) * u)
            y = round(animation['start'][1] + (animation['end'][1] - animation['start'][1]) * u + animation['bob_amplitude'] * math.sin(2 * math.pi * animation['bob_cycles'] * u))
        else:
            angle = math.radians(animation['phase_degrees']) + animation['direction'] * 2 * math.pi * index / spec['frame_count']
            x = round(animation['center'][0] + animation['radii'][0] * math.cos(angle) - sprite.width / 2)
            y = round(animation['center'][1] + animation['radii'][1] * math.sin(angle) - sprite.height / 2)
        frame.alpha_composite(sprite, (x, y))
        if family == 'occlusion':
            with Image.open(spec['assets']['foreground']) as source:
                frame.alpha_composite(source.convert('RGBA'))
    return frame.convert('RGB')


def _encode(frames, destination, fps):
    binary = shutil.which('ffmpeg')
    if not binary:
        raise RuntimeError('ffmpeg is required to generate synthetic video inputs.')
    process = subprocess.Popen([binary, '-v', 'error', '-y', '-filter_threads', '1', '-filter_complex_threads', '1',
                                '-f', 'rawvideo', '-pixel_format', 'rgb24',
                                '-video_size', f'{frames[0].width}x{frames[0].height}', '-framerate', str(fps),
                                '-i', 'pipe:0', '-an', '-c:v', 'libx264', '-crf', '18', '-threads', '1',
                                '-pix_fmt', 'yuv420p', '-map_metadata', '-1', '-movflags', '+faststart', str(destination)],
                               stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        process.communicate(b''.join(frame.convert('RGB').tobytes() for frame in frames), timeout=40)
        if process.returncode:
            raise RuntimeError('Synthetic video encoding failed.')
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def _broken_seed(task_id, workspace, contract):
    inputs = workspace / 'inputs'
    family = contract['family']
    if family == 'blink':
        with Image.open(inputs / 'scene.png') as source:
            source = source.convert('RGB')
        # Deliberately wrong locality: the full face/print bobs instead of blinking.
        frames = []
        for n in range(contract['frame_count']):
            offset = round(4 * math.sin(2 * math.pi * n / 12))
            frame = Image.new('RGB', source.size, '#eae0c9')
            frame.paste(source, (0, offset))
            frames.append(frame)
        _encode(frames, inputs / 'broken-seed.mp4', contract['fps'])
    elif family == 'orbit':
        frames = [reference_frame(task_id, workspace, n // 9 * 9) for n in range(contract['frame_count'])]
        _encode(frames, inputs / 'broken-seed.mp4', contract['fps'])
    else:
        with Image.open(inputs / 'scene.png') as source:
            broken = source.convert('RGB')
        with Image.open(inputs / 'artwork.png') as source:
            distorted = source.convert('RGBA').resize((contract['size'][0] - 90, 210), Image.Resampling.BILINEAR)
        broken.paste(distorted.convert('RGB'), (45, 80))
        with Image.open(inputs / 'atlas.png') as source:
            atlas = source.convert('RGBA')
        draw = ImageDraw.Draw(broken)
        draw.rectangle((49, 301, contract['size'][0] - 51, contract['size'][1] - 65), fill='#f3e9d2')
        for index, layer in enumerate(contract['layers'][1:]):
            crop = atlas.crop(layer['crop'])
            # Clip alternate label ends and crowd the original placements.
            crop = crop.crop((18 if index % 2 else 0, 0, 475, 41))
            broken.paste(crop, (48, 311 + index * 39), crop)
        broken.save(inputs / 'broken-seed.png')


def materialize_task(task_id, workspace):
    """Write deterministic synthetic sources and a complete public contract."""
    spec = _spec(task_id)
    workspace = Path(workspace).resolve()
    inputs = workspace / 'inputs'
    if inputs.is_symlink() or (inputs.exists() and any(inputs.iterdir())):
        raise ValueError('Materialize each task in a fresh workspace with empty inputs.')
    inputs.mkdir(parents=True, exist_ok=True)
    contract = _make_assets(spec, inputs)
    (inputs / 'scene.json').write_text(json.dumps(contract, ensure_ascii=False, indent=2) + '\n')
    if spec[2] == 'repair':
        _broken_seed(task_id, workspace, contract)
    references = [inputs / 'scene.png', inputs / 'atlas.png']
    if spec[2] == 'repair':
        references.append(inputs / ('broken-seed.mp4' if contract['duration'] else 'broken-seed.png'))
    public = _public(spec)
    files = sorted(inputs.iterdir())
    return {'task_id': task_id, **{key: public[key] for key in ('split', 'kind', 'brief', 'output_name')},
            'reference_paths': [str(path) for path in references],
            'source_hashes': {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}}
