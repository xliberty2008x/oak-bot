"""Synthetic source-preserving tasks that require image perception.

Producer inputs disclose objectives and tolerances, rather than eye masks,
foreground alpha mattes, registration transforms or a prescribed blink movie.
Runner semantic masks are regenerated in memory and never saved to inputs.
Authored examples are validation controls, not the blink grader's target.
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
    ('dev-create-marten-blink', 'dev', 'creation', 'blink', (672, 456), 'MARTEN / CEDAR STUDY', 201),
    ('dev-repair-ibis-blink', 'dev', 'repair', 'blink', (720, 480), 'IBIS / REED STUDY', 202),
    ('dev-create-glasswing-passage', 'dev', 'creation', 'occlusion', (768, 448), 'GLASSWING / VINE PRINT', 203),
    ('dev-repair-paperfish-passage', 'dev', 'repair', 'occlusion', (704, 496), 'PAPERFISH / GRASS PRINT', 204),
    ('dev-create-herbarium-proof', 'dev', 'creation', 'restoration', (880, 680), 'HERBARIUM / FOLIO 41', 205),
    ('dev-repair-engraving-proof', 'dev', 'repair', 'restoration', (936, 704), 'ENGRAVING / FOLIO 52', 206),
    ('holdout-create-lynx-blink', 'holdout', 'creation', 'blink', (752, 504), 'LYNX / GRANITE STUDY', 207),
    ('holdout-repair-nightjar-blink', 'holdout', 'repair', 'blink', (688, 488), 'NIGHTJAR / DUNE STUDY', 208),
    ('holdout-create-origami-passage', 'holdout', 'creation', 'occlusion', (736, 472), 'ORIGAMI / FERN PRINT', 209),
    ('holdout-repair-copperwing-passage', 'holdout', 'repair', 'occlusion', (784, 512), 'COPPERWING / WILLOW PRINT', 210),
    ('holdout-create-almanac-proof', 'holdout', 'creation', 'restoration', (912, 728), 'ALMANAC / FOLIO 63', 211),
    ('holdout-repair-conch-proof', 'holdout', 'repair', 'restoration', (968, 744), 'CONCH / FOLIO 74', 212),
)


def _spec(task_id):
    for row in _SPECS:
        if row[0] == task_id:
            return row
    raise ValueError('Unknown perceptual task: ' + str(task_id))


def _contract(spec):
    task_id, split, kind, family, size, title, seed = spec
    video = family != 'restoration'
    windows = [[5, 14], [21, 31]] if seed % 2 else [[4, 13], [20, 30]]
    result = {
        'version': 'oak-perceptual-media-v1', 'task_id': task_id,
        'family': family, 'size': list(size), 'duration': 3 if video else 0,
        'fps': 12 if video else 0, 'frame_count': 36 if video else 1,
        'assets': {'scene': 'scene.png'},
        'acceptance': {
            'geometry_tolerance_px': 2, 'channel_noise_deadband': 2 if video else 0,
            'closure_coverage': .85, 'max_closure_step': .45, 'monotone_slack': .12,
            'open_eye_restore_fraction': .85, 'motion_mae': 10,
            'pupil_color_tolerance': 20, 'iris_change_threshold': 18,
            'lid_color_tolerance': 20,
            'pupil_preserved_fraction': .85,
            'pupil_visibility_min_fraction': .15,
            'silhouette_edge_tolerance_px': 2,
            'foreground_mae': 6, 'stationary_mae': 7 if video else 2, 'png_mae': 2,
            'pixel_error': 'Per-channel max(0, absolute error minus channel_noise_deadband), then mean. Video comparisons account for required 4:2:0 chroma sampling using a fixed raw FFmpeg RGB24 -> yuv420p -> RGB24 conversion. These properties permit different valid animation curves and masks; no hidden exact blink movie is the target.',
            'visual_review': 'An independent review must confirm coherent curved eyelids, source identity, clean silhouettes or faithful restoration as applicable. Passing numeric checks alone does not establish perceptual quality.',
        },
    }
    if family == 'blink':
        result['blink_windows'] = windows
        result['requirements'] = [
            'Infer the two eye openings from the supplied portrait; no eye boxes, masks or patches are supplied.',
            'Blink once in each inclusive indexed-frame window. Each blink must smoothly close then reopen both eyes; outside the windows the original open eyes must be restored. Frames 0 and 35 match the original open portrait.',
            'At the most closed frame in each window, at least 85% of the original iris area must be naturally occluded by eyelids. Each window must include at least two intermediate partial-closure frames.',
            'The measured closure fraction may change by at most .45 per adjacent frame; close then reopen with monotonicity slack .12. The precise velocity curve and frame of maximum closure within a window are your choices.',
            'Keep the iris and pupil in place wherever visible; reveal or occlude the original eye rather than squash or move it. Build coherent curved eyelid boundaries without detached arcs, rectangular wipes, white flashes or an instant open/closed swap.',
            'Closed eyelid fill must use the existing source-visible pale skin or adjacent fur hues. Do not simulate closure with an iris ghost, a crossfade, an arbitrary colored rectangle or a foreign eyelid color.',
            'Only the visible eye openings and a 4-pixel eyelid-edge neighborhood may change. Keep the cheeks, brows, fur, beak or nose, head, print, paper and camera stationary. Small antialiasing differences are tolerated.',
        ]
    elif family == 'occlusion':
        # Motion is intentionally disclosed: recovering the visible foreground
        # silhouette, rather than guessing an arbitrary path, is the challenge.
        result['assets']['sprite'] = 'sprite.png'
        result['motion'] = {
            'start': [40, size[1] // 2 - 56], 'end': [size[0] - 152, size[1] // 2 - 56],
            'bob_amplitude': 11 if seed % 2 else 16,
            'formula': 'For indexed frame n=0..35, u=n/35; top-left x=round(start.x+(end.x-start.x)*u), y=round(start.y+(end.y-start.y)*u+bob_amplitude*sin(2*pi*u)). Use the original unscaled 112x112 sprite with its alpha.',
        }
        result['requirements'] = [
            'Infer both foreground plant silhouettes from the flattened scene. No foreground alpha mask or unobstructed background is supplied.',
            'Move the supplied patterned subject continuously along the disclosed path, behind BOTH dark green foreground plant clusters and in front of the blue patterned mural.',
            'Preserve the entire original source sprite pattern, proportions and colors wherever it is visible; do not replace it with a plain shape or redraw its markings.',
            'Keep all opaque foreground leaf and stem interiors unchanged during crossings. Preserve the original mural, paper, type and border outside the moving subject.',
            'Allow up to 2 pixels of silhouette-edge uncertainty for antialiasing; boundaries must remain visually clean without conspicuous halos, rectangular cutouts or a moving plant layer.',
        ]
    else:
        result['assets'].update(donor='donor.png', guide='guide.png')
        result['requirements'] = [
            'Create the restored master by repairing the irregular colored stain inside the visible cyan guide boundary. The guide is a separate observation, not content for the output.',
            'Register the supplied undamaged donor photograph to the damaged print. The donor was cropped, rotated by an integer multiple of 90 degrees and translated on a larger sheet; it was never scaled, sheared, resampled or color-adjusted. Infer its registration from the shared printed landmarks.',
            'Every stained pixel has an undamaged counterpart in the donor. At least three distinctive printed landmarks shared by donor and target remain untouched by the stain.',
            'Restore the original paper texture, thin engraving lines, exact small lettering, diacritics and rules. Copy source pixels after registration; do not retype small text or invent an approximate illustration.',
            'All pixels outside the guide boundary must remain the original damaged-source pixels. Within the guide, restore faithful donor content with no seams or registration ghosting. The PNG error allowance is 2/255 mean channel error with up to 2-pixel registration tolerance.',
        ]
    visual = _visual_projection(spec)
    mechanical = {
        'delivery': {key: result[key] for key in ('size', 'duration', 'fps', 'frame_count')},
        'acceptance': result['acceptance'],
        'requirements': result['requirements'],
    }
    for name in ('blink_windows', 'motion'):
        if name in result:
            mechanical[name] = result[name]
    result['review_manifest'] = {'visual': visual, 'mechanical': mechanical}
    return result


def _visual_projection(spec):
    """Public VLM context: visible intent and identity, no numeric score recipe."""
    _, _, kind, family, _, title, _ = spec
    if family == 'blink':
        intent = ('Animate two complete, coherent local blinks in the supplied portrait. '
                  'Keep the original iris and pupil in place while curved eyelids close and reopen.')
        checks = [
            'The supplied portrait, face, pupils, fur, beak or nose, paper texture and print retain their identity.',
            'Only the eye openings and their natural eyelid edges move; the face, brows and camera remain stationary.',
            'Each blink closes then reopens smoothly. The original open eyes return between blinks and at the end.',
            'Use eyelid colors already visible in the source skin or fur. Curved lids should visibly occlude the eye; avoid ghost irises, crossfades, detached arcs, rectangular wipes, squashed pupils or a sudden state swap.',
        ]
        sources = {'scene': 'Original open portrait and source identity reference.'}
    elif family == 'occlusion':
        intent = ('Animate the original patterned subject crossing the flattened landscape behind both foreground plant clusters and in front of the mural.')
        checks = [
            'The supplied subject retains its full pattern, colors, proportions and recognizable silhouette wherever visible.',
            'Leaves and stems stay in front during crossings; the blue patterned mural stays behind the moving subject.',
            'The plant clusters, paper, lettering, border and mural remain stationary.',
            'Boundaries stay visually clean without obvious halos, rectangular cutouts or moving fragments of the landscape. The subject travels continuously rather than freezing between sampled views.',
        ]
        sources = {'scene': 'Flattened landscape containing the mural and foreground plants.',
                   'sprite': 'Original transparent patterned subject.'}
    else:
        intent = ('Produce a faithful restored print using the undamaged donor to recover the stained area marked by the separate cyan guide.')
        checks = [
            'Register donor details to the damaged original without ghosting or doubled edges.',
            'Restore the original paper texture, thin engraved lines, exact small lettering, diacritics and rules.',
            'Preserve all original print content outside the cyan guide boundary.',
            'The finished print has no stain, blur patches, invented lettering, obvious seams or cyan guide markings.',
        ]
        sources = {'scene': 'Original damaged print whose unaffected content must be preserved.',
                   'donor': 'Undamaged source content for the repair; its orientation and placement differ.',
                   'guide': 'Separate observation marking the allowed repair boundary in cyan.'}
    brief = 'Project: ' + title + '. ' + intent + ' ' + ' '.join(checks)
    return {'brief': brief, 'specification': {
        'project': title, 'family': family, 'intent': intent,
        'source_roles': sources, 'visual_checks': checks,
        'review_scope': 'Judge visible source fidelity and coherent task completion from the artifact and original references. Codec, dimensions, timing formulas and numeric tolerances are checked separately and are not visual-review criteria.',
    }}


def _public(spec):
    task_id, split, kind, family, size, title, seed = spec
    c = _contract(spec)
    brief = f'Project: {title}. Use only the supplied original synthetic artwork. '
    brief += 'Read inputs/scene.json for the complete public requirements and tolerances. '
    brief += ' '.join(c['requirements']) + ' '
    if c['duration']:
        brief += f'Deliver final.mp4: silent H.264 MP4, yuv420p, {size[0]}x{size[1]}, exactly 3 seconds, 12 fps, exactly 36 frames indexed 0 through 35. '
    else:
        brief += f'Deliver final.png: opaque RGB PNG, exactly {size[0]}x{size[1]}. '
    if kind == 'repair':
        brief += 'inputs/broken-seed.' + ('mp4' if c['duration'] else 'png') + ' is a flawed starting candidate. '
    brief += 'Local Python/Pillow/ffmpeg tools suffice. Never modify the supplied input bytes.'
    return {'id': task_id, 'split': split, 'kind': kind, 'brief': brief,
            'output_name': 'final.mp4' if c['duration'] else 'final.png'}


TASKS = tuple(_public(spec) for spec in _SPECS)


def _font(size, bold=False):
    try:
        return ImageFont.truetype('DejaVuSans' + ('-Bold' if bold else '') + '.ttf', size)
    except OSError:
        return ImageFont.load_default(size=size)


def _paper(size, seed):
    rng = random.Random('oak-perception-paper:' + str(seed))
    image = Image.new('RGB', size, '#e9dfc7')
    pixels = image.load()
    for _ in range(size[0] * size[1] // 11):
        x, y = rng.randrange(size[0]), rng.randrange(size[1])
        delta = rng.choice((-8, -4, 4, 8))
        pixels[x, y] = tuple(max(0, min(255, color + delta)) for color in (233, 223, 199))
    return image


def _print_base(spec):
    _, _, _, _, size, title, seed = spec
    image = _paper(size, seed)
    draw = ImageDraw.Draw(image)
    w, h = size
    draw.rectangle((15, 15, w - 16, h - 16), outline='#284a45', width=2)
    draw.rectangle((21, 21, w - 22, h - 22), outline='#526f5a', width=1)
    draw.text((33, 32), title, fill='#284a45', font=_font(18, True))
    draw.text((33, h - 37), f'PLANCHE {seed}  /  MÉMOIRE • MATIÈRE • ATELIER  /  2026',
              fill='#3c574d', font=_font(11))
    return image


def _portrait(spec):
    image = _print_base(spec)
    w, h = image.size
    seed = spec[-1]
    cx, cy = w // 2 + (seed % 5 - 2) * 5, h // 2 + 17
    draw = ImageDraw.Draw(image)
    face = '#b7835b' if seed % 2 else '#a89a73'
    draw.polygon([(cx - 141, cy - 92), (cx - 105, cy - 151), (cx - 35, cy - 108),
                  (cx + 44, cy - 102), (cx + 117, cy - 141), (cx + 153, cy - 82),
                  (cx + 119, cy + 112), (cx - 9, cy + 135), (cx - 128, cy + 101)],
                 fill=face, outline='#3c4a3f')
    for i in range(16):
        x = cx - 104 + i * 13
        draw.line((x, cy + 76 + i % 3 * 5, x + 5, cy + 84 + i % 3 * 5), fill='#76634b', width=2)
    draw.polygon([(cx - 15, cy + 38), (cx + 13, cy + 35), (cx + 1, cy + 63)], fill='#364842')
    eyes = []
    for i, sign in enumerate((-1, 1)):
        tile = Image.new('RGBA', (104, 80))
        td = ImageDraw.Draw(tile)
        td.ellipse((3, 3, 100, 76), fill='#d3bd91', outline='#4a5142', width=3)
        td.ellipse((10, 14, 94, 65), fill='#f2e2b8', outline='#4a5142', width=2)
        td.ellipse((37, 17, 67, 63), fill='#a17c45')
        td.ellipse((46, 21, 58, 59), fill='#243f3d')
        td.ellipse((41, 21, 46, 27), fill='#fff6d8')
        iris = Image.new('L', tile.size)
        ImageDraw.Draw(iris).ellipse((37, 17, 67, 63), fill=255)
        pupil = Image.new('L', tile.size)
        ImageDraw.Draw(pupil).ellipse((46, 21, 58, 59), fill=255)
        outer = Image.new('L', tile.size)
        ImageDraw.Draw(outer).ellipse((6, 10, 98, 69), fill=255)
        angle = (-9 if i == 0 else 7) + (seed % 3 - 1) * 3
        pos = (cx + sign * (70 + seed % 7) - 52, cy - 67 + i * (5 + seed % 4))
        tile = tile.rotate(angle, resample=Image.Resampling.BICUBIC)
        image.paste(tile, pos, tile)
        eye = {'name': 'eye-' + str(i + 1),
               'lid_colors': [[211, 189, 145], [183, 131, 91] if seed % 2 else [168, 154, 115]]}
        for key, local in (('outer', outer), ('iris', iris), ('pupil', pupil)):
            mask = Image.new('L', image.size)
            local = local.rotate(angle, resample=Image.Resampling.BICUBIC)
            mask.paste(local, pos)
            eye[key] = mask
        eyes.append(eye)
        # Authored-control geometry is private runner metadata.  Graders use
        # semantic masks/properties above and never require this exact movie.
        eye['authoring'] = {'position': pos, 'angle': angle, 'tile_size': [104, 80],
                            'aperture_box': [10, 14, 94, 65]}
    return {'scene': image}, {}, {'eyes': eyes, 'blink_windows': _contract(spec)['blink_windows']}


def _subject(seed):
    sprite = Image.new('RGBA', (112, 112))
    draw = ImageDraw.Draw(sprite)
    if seed % 2:
        draw.polygon([(56, 14), (88, 32), (101, 77), (69, 98), (27, 82), (12, 37)],
                     fill='#cb8c5d', outline='#364d48')
        for r in (13, 22, 31):
            draw.ellipse((56 - r, 56 - r, 56 + r, 56 + r), outline='#f0d8a7', width=2)
        draw.line((20, 39, 92, 74), fill='#485d50', width=3)
    else:
        draw.ellipse((12, 29, 88, 82), fill='#c79169', outline='#354c48', width=2)
        draw.polygon([(84, 56), (108, 26), (105, 87)], fill='#ddc895', outline='#354c48')
        for x in range(23, 79, 11):
            draw.arc((x - 7, 35, x + 11, 76), 270, 90, fill='#f3dfad', width=3)
        draw.ellipse((23, 43, 30, 50), fill='#25423c')
    return sprite


def _passage(spec):
    image = _print_base(spec)
    w, h = image.size
    seed = spec[-1]
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((36, 86, w - 37, h - 77), radius=15, fill='#739298', outline='#355c60', width=3)
    for y in range(103, h - 82, 17):
        draw.line((46, y, w - 47, y + 3), fill='#acbec0')
    # Native alpha comes only from downsampling a coherent leaf silhouette.
    q = 4
    foreground = Image.new('RGBA', (w * q, h * q))
    fd = ImageDraw.Draw(foreground)
    centers = [round(w * (.34 + seed % 3 * .015)), round(w * (.66 - seed % 4 * .012))]
    for index, x in enumerate(centers):
        fd.line((x * q, (h - 82) * q, (x + 17) * q, 109 * q), fill='#284e44', width=9 * q)
        for row in range(5):
            y = 120 + row * 33
            left = [(x + 11, y + 15), (x - 53 - index * 6, y - 12), (x - 29, y + 26)]
            right = [(x + 10, y + 20), (x + 59 + seed % 4 * 2, y - 13), (x + 35, y + 34)]
            fd.polygon([(round(a * q), round(b * q)) for a, b in left], fill='#355d43')
            fd.polygon([(round(a * q), round(b * q)) for a, b in right], fill='#50734d')
            fd.line(((x - 31) * q, (y + 5) * q, (x + 34) * q, (y + 13) * q), fill='#a0ad77', width=q)
    foreground = foreground.resize(image.size, Image.Resampling.LANCZOS)
    clean_background = image.copy()
    image.paste(foreground, (0, 0), foreground)
    motion = _contract(spec)['motion']
    points = []
    for n in range(36):
        u = n / 35
        points.append([round(motion['start'][0] + (motion['end'][0] - motion['start'][0]) * u),
                       round(motion['start'][1] + (motion['end'][1] - motion['start'][1]) * u
                             + motion['bob_amplitude'] * math.sin(2 * math.pi * u))])
    return ({'scene': image, 'sprite': _subject(seed)}, {'foreground': foreground.getchannel('A')},
            {'clean_background': clean_background, 'foreground': foreground, 'motion': points})


def _restoration(spec):
    clean = _print_base(spec)
    w, h = clean.size
    seed = spec[-1]
    draw = ImageDraw.Draw(clean)
    ink = '#3b5146'
    # Three distinctive registration anchors are visible in target and donor.
    anchors = [(97, 104), (w - 117, 123), (123, h - 114)]
    for i, (x, y) in enumerate(anchors):
        draw.rectangle((x - 10, y - 10, x + 10, y + 10), outline=ink, width=2)
        draw.line((x - 15, y, x + 15, y), fill=ink)
        draw.line((x, y - 15, x, y + 15), fill=ink)
        draw.text((x + 15, y + 8), ('A', 'B', 'C')[i], fill=ink, font=_font(11))
    for i in range(8):
        x = 150 + i * (w - 300) // 7
        y = 164 + (i * 17 + seed) % 53
        draw.line((x, h - 210, x + 11, y), fill='#536e52', width=3)
        for row in range(5):
            yy = y + row * 36
            draw.arc((x - 29, yy - 14, x + 13, yy + 18), 190, 350, fill='#657957', width=2)
            draw.arc((x + 9, yy - 2, x + 52, yy + 30), 190, 350, fill='#947b58', width=2)
    draw.text((141, h - 183), f'ÉCHANTILLON {seed} / SÉRIE B–{seed - 170}', fill=ink, font=_font(18, True))
    draw.text((141, h - 153), 'MÉTHODE / DRY PRESS / 32 FEUILLES / 47° N / SÉRIE 2026', fill=ink, font=_font(12))
    draw.line((141, h - 131, w - 144, h - 131), fill=ink)
    draw.text((141, h - 119), 'ORIGINAL PAPER • FINE LINES • KEEP THE DIACRITICS', fill=ink, font=_font(10))
    # Integer-only donor transform; all stained pixels lie inside this crop.
    crop_box = (70, 78, w - 77, h - 75)
    donor_crop = clean.crop(crop_box)
    donor_crop = donor_crop.transpose(Image.Transpose.ROTATE_90 if seed % 2 else Image.Transpose.ROTATE_270)
    donor = Image.new('RGB', (donor_crop.width + 87, donor_crop.height + 71), '#d5d2c4')
    donor.paste(donor_crop, (31 + seed % 11, 23 + seed % 7))
    # A narrow irregular stain crosses line art, paper and several exact glyphs.
    stain = Image.new('L', clean.size)
    sd = ImageDraw.Draw(stain)
    center_x = w // 2 + (seed % 5 - 2) * 17
    points = [(center_x - 26, 219), (center_x + 22, 228), (center_x + 14, 328),
              (center_x + 45, 387), (center_x + 27, h - 112),
              (center_x - 33, h - 121), (center_x - 18, 369), (center_x - 43, 290)]
    sd.polygon(points, fill=255)
    sd.ellipse((center_x - 51, 326, center_x + 49, 415), fill=255)
    authorization = stain.filter(ImageFilter.MaxFilter(17))
    # The guide shows the actual authorization area, rather than an undisclosed ROI.
    edge = ImageChops.subtract(authorization, authorization.filter(ImageFilter.MinFilter(5)))
    damaged = clean.copy()
    damage_color = Image.new('RGB', clean.size, '#9b537d' if seed % 2 else '#bd7653')
    damaged.paste(damage_color, (0, 0), stain)
    guide = damaged.convert('RGBA')
    guide.paste(Image.new('RGBA', guide.size, '#0babc2'), (0, 0), edge)
    guide_draw = ImageDraw.Draw(guide)
    guide_draw.text((36, 63), 'CYAN OUTLINE = ALLOWED REPAIR AREA', fill='#157b8b', font=_font(12, True))
    protected = ImageChops.invert(authorization)
    # Source-backed detail masks focus the visible fine print, including accents
    # and rules, rather than averaging their errors into the surrounding paper.
    ink_difference = ImageChops.difference(clean, Image.new('RGB', clean.size, ink))
    channels = [channel.point(lambda value: 255 if value <= 16 else 0)
                for channel in ink_difference.split()]
    ink_mask = ImageChops.darker(ImageChops.darker(channels[0], channels[1]), channels[2])
    ink_mask = ink_mask.filter(ImageFilter.MaxFilter(3))
    detail_masks = []
    for name, top, bottom in [('fine-heading', h - 185, h - 157),
                              ('fine-details', h - 155, h - 135),
                              ('fine-rule-and-caption', h - 133, h - 102)]:
        row_mask = Image.new('L', clean.size)
        ImageDraw.Draw(row_mask).rectangle((140, top, w - 140, bottom), fill=255)
        detail_masks.append({'name': name,
                             'mask': ImageChops.multiply(ImageChops.multiply(row_mask, authorization), ink_mask)})
    return ({'scene': damaged, 'donor': donor, 'guide': guide.convert('RGB')},
            {'damage': stain, 'guide': authorization, 'protected': protected},
            {'clean_original': clean, 'detail_masks': detail_masks})


def _build(spec):
    return {'blink': _portrait, 'occlusion': _passage, 'restoration': _restoration}[spec[3]](spec)


def _encode(frames, output):
    """Encode a bounded, deterministic synthetic broken input."""
    binary = shutil.which('ffmpeg')
    if not binary:
        raise RuntimeError('FFmpeg required for the synthetic broken seed.')
    first = frames[0]
    result = subprocess.run([binary, '-v', 'error', '-y', '-filter_threads', '1', '-filter_complex_threads', '1',
                             '-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size', f'{first.width}x{first.height}',
                             '-framerate', '12', '-i', 'pipe:0', '-an', '-c:v', 'libx264', '-crf', '18', '-threads', '1',
                             '-pix_fmt', 'yuv420p', '-map_metadata', '-1', '-movflags', '+faststart', str(output)],
                            input=b''.join(frame.convert('RGB').tobytes() for frame in frames),
                            capture_output=True, timeout=45)
    if result.returncode:
        raise RuntimeError('Synthetic broken-seed encoding failed.')


def materialize_task(task_id, workspace):
    spec = _spec(task_id)
    workspace = Path(workspace).resolve()
    inputs = workspace / 'inputs'
    if inputs.is_symlink() or (inputs.exists() and any(inputs.iterdir())):
        raise ValueError('Use a fresh empty input directory.')
    inputs.mkdir(parents=True, exist_ok=True)
    contract = _contract(spec)
    assets, masks, properties = _build(spec)
    # Deliberately save ONLY public observations, never semantic masks/truth.
    for name, image in assets.items():
        image.save(inputs / (name + '.png'))
    (inputs / 'scene.json').write_text(json.dumps(contract, ensure_ascii=False, indent=2) + '\n')
    if spec[2] == 'repair':
        if spec[3] == 'blink':
            frames = []
            for n in range(36):
                frame = Image.new('RGB', assets['scene'].size, '#e9dfc7')
                frame.paste(assets['scene'], (0, round(4 * math.sin(2 * math.pi * n / 12))))
                frames.append(frame)
            _encode(frames, inputs / 'broken-seed.mp4')
        elif spec[3] == 'occlusion':
            # Incorrectly covering foreground is the repair's visible defect.
            frames = []
            for point in properties['motion']:
                frame = assets['scene'].copy()
                frame.paste(assets['sprite'], tuple(point), assets['sprite'])
                frames.append(frame)
            _encode(frames, inputs / 'broken-seed.mp4')
        else:
            broken = assets['scene'].copy()
            blurred = properties['clean_original'].filter(ImageFilter.GaussianBlur(3))
            broken.paste(blurred, (0, 0), masks['guide'])
            broken.save(inputs / 'broken-seed.png')
    references = [inputs / 'scene.png']
    if spec[3] == 'occlusion':
        references.append(inputs / 'sprite.png')
    elif spec[3] == 'restoration':
        references.append(inputs / 'donor.png')
        references.append(inputs / 'guide.png')
    if spec[2] == 'repair' and spec[3] != 'restoration':
        references.append(inputs / ('broken-seed.mp4' if contract['duration'] else 'broken-seed.png'))
    public = _public(spec)
    scene_roles = {'blink': 'original open portrait', 'occlusion': 'flattened mural and foreground plants',
                   'restoration': 'original damaged print'}
    roles = {'scene.png': scene_roles[spec[3]], 'sprite.png': 'original transparent patterned subject',
             'donor.png': 'undamaged donor print with different orientation and placement',
             'guide.png': 'damaged print with cyan repair boundary; guide is not output content',
             'broken-seed.mp4': 'flawed starting animation for repair'}
    return {'task_id': task_id, **{key: public[key] for key in ('split', 'kind', 'brief', 'output_name')},
            'specification_path': str(inputs / 'scene.json'),
            'reference_paths': [str(path) for path in references],
            'reference_roles': {str(path): roles[path.name] for path in references},
            'source_hashes': {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(inputs.iterdir())}}


def quality_spec(task_id, workspace):
    """Regenerate semantic truth in memory; never forward this object to producers.

    Only public asset paths and immutable-source checks cross the producer
    boundary.  The caller keeps masks and authoring controls runner-side.
    """
    spec = _spec(task_id)
    workspace = Path(workspace).resolve()
    contract = json.loads((workspace / 'inputs' / 'scene.json').read_text())
    if contract.get('task_id') != task_id or contract != _contract(spec):
        raise ValueError('Public contract does not match the frozen task definition.')
    assets, masks, properties = _build(spec)
    return {key: contract[key] for key in ('task_id', 'family', 'size', 'duration', 'fps', 'frame_count')} | {
        'contract': contract,
        'assets': {key: str(workspace / 'inputs' / filename) for key, filename in contract['assets'].items()},
        'masks': masks, 'properties': properties,
    }


def authored_example_frames(task_id, workspace, *, curve='triangle', peak_shift=0):
    """Yield one valid source-preserving solution for control/visual QA only.

    Blink acceptance is property-based; none of these frames is its gold movie.
    Multiple curves and peak locations exercise that independence explicitly.
    No rendered frames or private geometry are written to the task workspace.
    """
    if curve not in {'triangle', 'cosine'} or peak_shift not in {-1, 0, 1}:
        raise ValueError('Unsupported authored-control curve or peak shift.')
    facts = quality_spec(task_id, workspace)
    with Image.open(facts['assets']['scene']) as source:
        scene = source.convert('RGB')
    for index in range(facts['frame_count']):
        if facts['family'] == 'restoration':
            yield facts['properties']['clean_original'].copy()
            continue
        if facts['family'] == 'occlusion':
            frame = facts['properties']['clean_background'].convert('RGBA')
            with Image.open(facts['assets']['sprite']) as source:
                sprite = source.convert('RGBA')
            frame.alpha_composite(sprite, tuple(facts['properties']['motion'][index]))
            frame.alpha_composite(facts['properties']['foreground'])
            yield frame.convert('RGB')
            continue
        closure = 0.0
        for start, end in facts['properties']['blink_windows']:
            if start <= index <= end:
                peak = (start + end) // 2 + peak_shift
                closure = ((index - start) / (peak - start) if index <= peak else
                           (end - index) / (end - peak))
                if curve == 'cosine':
                    closure = (1 - math.cos(math.pi * closure)) / 2
                break
        frame = scene.convert('RGBA')
        if closure > 0:
            for eye in facts['properties']['eyes']:
                a = eye['authoring']
                q = 4
                w, h = a['tile_size']
                left, top, right, bottom = a['aperture_box']
                original_opening = Image.new('L', (w * q, h * q))
                ImageDraw.Draw(original_opening).ellipse(
                    (left * q, top * q, right * q, bottom * q), fill=255)
                original_opening = original_opening.resize((w, h), Image.Resampling.LANCZOS)
                # Cover the original anti-aliased eye-opening outline as well
                # as the iris; retain the larger stationary cheek/eye ring.
                original_opening = original_opening.filter(ImageFilter.MaxFilter(5))
                original_opening = original_opening.rotate(a['angle'], resample=Image.Resampling.BICUBIC)
                fill_mask = Image.new('L', scene.size)
                fill_mask.paste(original_opening, tuple(a['position']))
                fill = Image.new('RGBA', scene.size, tuple(eye['lid_colors'][0]) + (255,))
                frame.paste(fill, (0, 0), fill_mask)
                local_opening = Image.new('L', (w * q, h * q))
                outline = Image.new('RGBA', (w * q, h * q))
                outline_draw = ImageDraw.Draw(outline)
                if closure >= 1:
                    line = [(12, 38), (25, 41), (40, 43), (53, 44), (66, 43), (80, 41), (92, 38)]
                    outline_draw.line([(x * q, y * q) for x, y in line], fill='#4a5142', width=2 * q, joint='curve')
                else:
                    cy = (top + bottom) / 2
                    half_height = (bottom - top) * (1 - closure) / 2
                    ellipse = [round(value * q) for value in (left, cy - half_height, right, cy + half_height)]
                    ImageDraw.Draw(local_opening).ellipse(ellipse, fill=255)
                    outline_draw.ellipse(ellipse, outline='#4a5142', width=2 * q)
                    local_opening = local_opening.resize((w, h), Image.Resampling.LANCZOS)
                    local_opening = local_opening.rotate(a['angle'], resample=Image.Resampling.BICUBIC)
                    opening_mask = Image.new('L', scene.size)
                    opening_mask.paste(local_opening, tuple(a['position']))
                    frame.paste(scene.convert('RGBA'), (0, 0), opening_mask)
                outline = outline.resize((w, h), Image.Resampling.LANCZOS)
                outline = outline.rotate(a['angle'], resample=Image.Resampling.BICUBIC)
                frame.alpha_composite(outline, tuple(a['position']))
        yield frame.convert('RGB')


def authored_example_frame(task_id, workspace, index=0, *, curve='triangle', peak_shift=0):
    """Single-frame convenience wrapper for runner tests."""
    for position, frame in enumerate(authored_example_frames(task_id, workspace, curve=curve, peak_shift=peak_shift)):
        if position == index:
            return frame
    raise ValueError('Frame index outside the task timeline.')
