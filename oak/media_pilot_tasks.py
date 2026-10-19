"""Portable synthetic media tasks and runner-side structural checks.

No account data, model calls or committed media fixtures are used. A structural
pass never establishes good typography, fidelity, aesthetics or natural motion;
those require a separate independent review against the unchanged brief.
"""

import hashlib
import json
from pathlib import Path
import random
import shutil
import subprocess

from PIL import Image, ImageChops, ImageDraw, ImageFont


# These specifications/oracles stay outside each producer workspace. All actual
# requirements are disclosed in the brief; there are no secret layout targets.
_SPECS = (
    ('dev-create-market', 'dev', 'creation', (960, 640), 'market', '#f7f1e4', 32,
     'NIGHT MARKET', 'SAT 24 OCT / 17:00–21:00 / RIVER HALL',
     'Create an inviting event poster with one hero illustration and three clearly separated labels: FOOD, CRAFT, MUSIC.', 0, 0),
    ('dev-create-menu', 'dev', 'creation', (720, 960), 'cafe', '#fff8ed', 36,
     'SUNDAY SIPS', 'Espresso 3 / Oat latte 5 / Citrus tea 4',
     'Create a portrait cafe menu. Set the three drinks and prices in readable aligned rows, with the illustration separate from the text.', 0, 0),
    ('dev-create-kite', 'dev', 'creation', (640, 360), 'coast', '#e8f5ff', 0,
     'Coastal kite', '',
     'Animate the supplied kite gliding from left to right with a gentle rise and fall over the supplied stationary geometric landscape. Keep the complete kite visible.', 3, 12),
    ('dev-repair-workshop', 'dev', 'repair', (900, 600), 'workshop', '#f4f0e8', 30,
     'MAKER AFTERNOON', 'TOOLS PROVIDED / BEGINNERS WELCOME / 14:00',
     'Repair the supplied broken poster: restore the clipped title, remove overlapping text, and show the complete source illustration beside a clear information column.', 0, 0),
    ('dev-repair-pantry', 'dev', 'repair', (800, 800), 'pantry', '#faf2dc', 40,
     'ORCHARD BLEND', 'APPLE + PEAR / SMALL BATCH / 250 g',
     'Repair the supplied square product label: the title is clipped and the details collide with the illustration. Restore all wording, balanced spacing and an undistorted source illustration.', 0, 0),
    ('dev-repair-comet', 'dev', 'repair', (480, 480), 'night', '#17203e', 0,
     'Comet interlude', '',
     'Repair the supplied motionless interlude. Move the supplied comet diagonally across the stationary abstract landscape, keeping its whole tail visible. Produce actual moving frames, rather than a repeated still.', 3, 12),
    ('holdout-create-science', 'holdout', 'creation', (840, 1120), 'science', '#eef4fb', 42,
     'DISCOVERY DAY', 'SUN 8 NOV / 10:00–16:00 / SCIENCE HALL',
     'Create a portrait science-festival poster with the supplied illustration, a strong title and three distinct activity blocks: BUILD, EXPLORE, DISCOVER.', 0, 0),
    ('holdout-create-books', 'holdout', 'creation', (1120, 700), 'books', '#f6eddf', 35,
     'WEEKEND READS', 'FICTION 12 / NATURE 18 / DESIGN 24',
     'Create a landscape bookstore display with three readable recommendation-and-price columns and the supplied book illustration. Preserve clear hierarchy without overlapping columns.', 0, 0),
    ('holdout-create-trail', 'holdout', 'creation', (720, 406), 'forest', '#edf6e9', 0,
     'Trail marker', '',
     'Animate the supplied trail marker travelling from the lower left toward the upper right of the stationary geometric landscape. Add a restrained bob and keep the entire marker visible.', 4, 15),
    ('holdout-repair-ceramics', 'holdout', 'repair', (1200, 800), 'ceramic', '#f6eee7', 48,
     'CLAY & LIGHT', 'OPEN STUDIO / FRI 6 NOV / 18:30',
     'Repair the supplied ceramics exhibition poster: unwrap the crowded title, recover clipped date text, and place the undistorted ceramics illustration in a spacious layout.', 0, 0),
    ('holdout-repair-nursery', 'holdout', 'repair', (600, 1000), 'botanic', '#edf3e7', 30,
     'GROW SOMETHING', 'HERBS / HOUSEPLANTS / FREE CARE GUIDE',
     'Repair the supplied narrow nursery leaflet. Restore its clipped heading and overlapping plant labels, keep the supplied plant illustration undistorted and make the three detail lines distinct.', 0, 0),
    ('holdout-repair-harbor', 'holdout', 'repair', (540, 720), 'harbor', '#e8f3f5', 0,
     'Harbor postcard', '',
     'Repair the supplied motionless portrait postcard by making the supplied boat sail horizontally across the bottom band of the abstract landscape. Keep the supplied scene stationary and the complete boat visible.', 4, 15),
)


def _public(spec):
    task_id, split, kind, size, theme, background, margin, title, details, request, duration, fps = spec
    brief = (f'{request}\nProject: {title}. The supplied artwork is deliberately stylized; '
             'preserve its abstract style rather than replacing it with photorealistic imagery. ')
    if duration:
        brief += (f'Deliver a silent H.264 MP4, {size[0]}×{size[1]}, {duration} seconds, '
                  f'{fps} fps. Use inputs/scene.png as the background and inputs/accent.png as the moving subject. '
                  'Do not substitute a slideshow or animate the entire background.')
    else:
        brief += (f'Deliver an opaque PNG, {size[0]}×{size[1]}, with solid {background} background '
                  f'and a clear {margin}-pixel gutter on every edge. Exact title: {title}. '
                  f'Exact supporting text: {details}. Use inputs/scene.png as the main artwork; do not stretch it. '
                  'inputs/accent.png is an optional decorative emblem. '
                  'Decorative content and all text must stay inside the gutter.')
    if kind == 'repair':
        brief += ' inputs/broken-seed.' + ('mp4' if duration else 'png') + ' is the deliberately flawed starting candidate.'
    return {'id': task_id, 'split': split, 'kind': kind, 'brief': brief,
            'output_name': 'final.mp4' if duration else 'final.png'}


TASKS = tuple(_public(spec) for spec in _SPECS)


def _spec(task_id):
    return next((spec for spec in _SPECS if spec[0] == task_id), None) or _unknown(task_id)


def _unknown(task_id):
    raise ValueError('Unknown synthetic media task: ' + str(task_id))


def _font(size):
    # DejaVu ships on the supported VM; Pillow's default remains a portable fallback.
    try:
        return ImageFont.truetype('DejaVuSans.ttf', size)
    except OSError:
        return ImageFont.load_default(size=size)


def _artwork(theme, size):
    """Deterministic original illustrations, with independently varied geometry."""
    rng = random.Random('oak-media-pilot:' + theme)
    palette = [tuple(rng.randint(35, 210) for _ in range(3)) for _ in range(5)]
    width, height = size
    image = Image.new('RGB', size, palette[0])
    draw = ImageDraw.Draw(image)
    if theme in {'coast', 'night', 'forest', 'harbor'}:
        draw.rectangle((0, height * .58, width, height), fill=palette[1])
        draw.ellipse((width * .7, height * .08, width * .88, height * .26), fill=palette[3])
        for i in range(6):
            x = width * (i / 5)
            draw.polygon([(x - width * .2, height * .65), (x, height * rng.uniform(.2, .48)),
                          (x + width * .2, height * .65)], fill=palette[2 + i % 2])
    elif theme in {'botanic', 'science', 'market'}:
        for i in range(9):
            x, y = rng.uniform(.15, .85) * width, rng.uniform(.15, .85) * height
            radius = min(size) * rng.uniform(.05, .15)
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=palette[1 + i % 4])
            draw.line((width / 2, height * .9, x, y), fill=palette[2], width=max(2, width // 100))
    else:
        for i in range(4):
            x = width * (.12 + .2 * i)
            y = height * rng.uniform(.24, .4)
            box = (x, y, x + width * .15, height * .82)
            draw.rounded_rectangle(box, radius=min(size) // (8 if theme in {'cafe', 'ceramic'} else 32),
                                   fill=palette[i + 1], outline=palette[0], width=3)
            draw.line((x + width * .03, y + height * .1, x + width * .12, y + height * .1),
                      fill=palette[0], width=max(3, width // 90))
    return image


def _accent(theme):
    image = Image.new('RGBA', (140, 120))
    draw = ImageDraw.Draw(image)
    if theme == 'harbor':
        draw.polygon([(20, 78), (125, 78), (100, 105), (38, 105)], fill='#dd713e')
        draw.polygon([(70, 15), (70, 73), (118, 73)], fill='#fff9da')
        draw.line((68, 12, 68, 80), fill='#263d51', width=5)
    elif theme == 'forest':
        draw.ellipse((35, 10, 105, 80), fill='#e7a826', outline='#294b32', width=4)
        draw.polygon([(42, 63), (98, 63), (70, 108)], fill='#e7a826')
        draw.ellipse((55, 30, 85, 60), fill='#fff9df')
    elif theme == 'night':
        draw.polygon([(15, 100), (75, 15), (118, 42)], fill='#efb55b')
        draw.ellipse((68, 10, 125, 66), fill='#fff1c2')
    elif theme == 'coast':
        draw.polygon([(70, 10), (115, 48), (70, 92), (25, 48)], fill='#ef7754', outline='#733c45')
        draw.line((70, 10, 70, 106), fill='#fff3d8', width=3)
        draw.line((25, 48, 115, 48), fill='#fff3d8', width=3)
    else:
        rng = random.Random('oak-media-emblem:' + theme)
        color = tuple(rng.randint(30, 190) for _ in range(3))
        inset = 12 + len(theme)
        draw.rounded_rectangle((inset, inset, 140 - inset, 120 - inset), radius=12 + len(theme), fill=color)
        for index in range(3):
            y = 35 + index * 17
            draw.line((35, y, 75 + rng.randrange(30), y), fill='#fff8e8', width=5)
    return image


def _encode_still(source, destination, duration, fps):
    binary = shutil.which('ffmpeg')
    if not binary:
        raise RuntimeError('ffmpeg is required to generate synthetic video inputs.')
    subprocess.run([binary, '-v', 'error', '-y', '-loop', '1', '-i', str(source),
                    '-t', str(duration), '-r', str(fps), '-an', '-c:v', 'libx264',
                    '-threads', '1', '-pix_fmt', 'yuv420p', '-map_metadata', '-1',
                    '-movflags', '+faststart', str(destination)], check=True, capture_output=True, timeout=40)


def materialize_task(task_id, workspace: Path):
    """Write only synthetic inputs; return the task brief and input byte identities."""
    spec = _spec(task_id)
    _, _, kind, size, theme, background, _, title, details, _, duration, fps = spec
    workspace = Path(workspace).resolve()
    inputs = workspace / 'inputs'
    inputs.mkdir(parents=True, exist_ok=True)
    if inputs.is_symlink() or any(inputs.iterdir()):
        raise ValueError('Materialize each task in a fresh workspace with empty inputs.')
    source_size = size if duration else (520 + len(theme) * 14, 380 + len(theme) * 8)
    scene = _artwork(theme, source_size)
    scene.save(inputs / 'scene.png')
    _accent(theme).save(inputs / 'accent.png')
    if kind == 'repair':
        if duration:
            _encode_still(inputs / 'scene.png', inputs / 'broken-seed.mp4', duration, fps)
        else:
            # Wrong aspect ratio, clipped heading and collisions are intentional.
            broken = Image.new('RGB', (size[0], max(180, size[1] // 2)), background)
            broken.paste(scene.resize((size[0], broken.height)), (0, 0))
            draw = ImageDraw.Draw(broken)
            draw.text((-25, -15), title, fill='white', font=_font(max(52, size[0] // 9)))
            draw.text((10, 35), details, fill='white', font=_font(32))
            broken.save(inputs / 'broken-seed.png')
    references = [str(path) for path in sorted(inputs.iterdir())]
    public = _public(spec)
    return {'task_id': task_id, **{key: public[key] for key in ('split', 'kind', 'brief', 'output_name')},
            'reference_paths': references,
            'source_hashes': {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in references}}


def evaluate_artifact(task_id, path, workspace):
    """Decode bounded local bytes and check disclosed technical requirements only."""
    spec = _spec(task_id)
    _, _, _, size, _, background, margin, _, _, _, duration, fps = spec
    workspace = Path(workspace).resolve()
    path = Path(path)
    path = path if path.is_absolute() else workspace / path
    checks = []

    def check(criterion, passed, evidence):
        checks.append({'criterion': criterion, 'status': 'pass' if passed else 'fail', 'evidence': evidence})

    def result():
        statuses = {item['status'] for item in checks}
        status = 'fail' if 'fail' in statuses else 'unverified' if 'unverified' in statuses else 'pass'
        return {'task_id': task_id, 'status': status, 'checks': checks, 'structural_only': True,
                'limitation': 'Structural checks do not establish visual quality, text accuracy, source fidelity or natural motion.'}

    try:
        path.resolve().relative_to(workspace)
        valid = path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 32 * 1024 * 1024
    except (OSError, ValueError):
        valid = False
    check('bounded_local_artifact', valid, 'Required: ordinary workspace file, nonempty, at most 32 MiB.')
    if not valid:
        return result()
    if not duration:
        try:
            with Image.open(path) as image:
                if image.width * image.height > 16_000_000:
                    raise ValueError('Image exceeds decoding budget.')
                image.load()
                check('png_encoding', image.format == 'PNG' and not getattr(image, 'is_animated', False), str(image.format))
                check('dimensions', image.size == size, f'Observed {image.size}; required {size}.')
                check('opaque', image.convert('RGBA').getchannel('A').getextrema() == (255, 255), 'Alpha must be fully opaque.')
                rgb = image.convert('RGB')
                diff = ImageChops.difference(rgb, Image.new('RGB', rgb.size, background))
                bands = [(0, 0, rgb.width, margin), (0, rgb.height - margin, rgb.width, rgb.height),
                         (0, margin, margin, rgb.height - margin), (rgb.width - margin, margin, rgb.width, rgb.height - margin)]
                pixels = [pixel for box in bands if box[2] > box[0] and box[3] > box[1]
                          for pixel in diff.crop(box).get_flattened_data()]
                marked = sum(max(pixel) > 12 for pixel in pixels)
                check('clear_gutter', bool(pixels) and marked / len(pixels) <= .005,
                      f'{marked}/{len(pixels)} sampled gutter pixels differ from required {background}.')
                thumbnail = diff.resize((64, 64))
                occupied = sum(max(pixel) > 20 for pixel in thumbnail.get_flattened_data())
                check('nonblank_composition', occupied >= 160, f'{occupied}/4096 thumbnail pixels contain foreground.')
        except (OSError, ValueError, Image.DecompressionBombError):
            check('image_decoding', False, 'Image could not be decoded within the size budget.')
        return result()
    if not shutil.which('ffprobe') or not shutil.which('ffmpeg'):
        checks.append({'criterion': 'video_decoding', 'status': 'unverified', 'evidence': 'ffmpeg/ffprobe unavailable.'})
        return result()
    try:
        probe = subprocess.run([shutil.which('ffprobe'), '-v', 'error', '-protocol_whitelist', 'file,pipe',
                                '-f', 'mov', '-show_streams', '-show_format',
                                '-of', 'json', str(path)], check=True, capture_output=True, timeout=15)
        metadata = json.loads(probe.stdout)
        videos = [stream for stream in metadata['streams'] if stream.get('codec_type') == 'video']
        stream = videos[0]
        observed_duration = float(metadata['format']['duration'])
        numerator, denominator = map(float, stream['avg_frame_rate'].split('/'))
        observed_fps = numerator / denominator
        check('mp4_h264', 'mp4' in metadata['format']['format_name'] and stream['codec_name'] == 'h264',
              f"Container {metadata['format']['format_name']}; codec {stream['codec_name']}.")
        check('dimensions', (stream['width'], stream['height']) == size, f"Observed {stream['width']}×{stream['height']}; required {size}.")
        check('duration', abs(observed_duration - duration) <= .2, f'Observed {observed_duration:.3f}s; required {duration}s ±0.2s.')
        check('frame_rate', abs(observed_fps - fps) <= .1, f'Observed {observed_fps:.3f} fps; required {fps}.')
        check('silent_video', not any(item.get('codec_type') == 'audio' for item in metadata['streams']), 'No audio stream requested.')
        decoded = subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-protocol_whitelist', 'file,pipe',
                                  '-f', 'mov', '-i', str(path), '-t', str(duration + .25),
                                  '-vf', 'fps=4,scale=128:96', '-frames:v', '20', '-threads', '1',
                                  '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                                 check=True, capture_output=True, timeout=30).stdout
        frame_bytes = 128 * 96 * 3
        frames = [decoded[index:index + frame_bytes] for index in range(0, len(decoded), frame_bytes)
                  if len(decoded[index:index + frame_bytes]) == frame_bytes]
        check('decoded_frames', len(frames) >= duration * 4 - 1, f'{len(frames)} frames decoded at 4 fps.')
        distinct = len({hashlib.sha256(frame).digest() for frame in frames})
        changes = [sum(abs(a - b) for a, b in zip(first, second)) / frame_bytes for first, second in zip(frames, frames[1:])]
        check('temporal_change', distinct >= 3 and sum(change > .08 for change in changes) >= 2,
              f'{distinct} distinct sampled frames; {sum(change > .08 for change in changes)} material transitions. This does not prove natural or correct motion.')
    except (OSError, ValueError, KeyError, IndexError, ZeroDivisionError, subprocess.SubprocessError):
        check('video_decoding', False, 'Video metadata or bounded frame decoding failed.')
    return result()
