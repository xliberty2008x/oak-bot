"""Native creative tools: public observations and reviewed local candidates."""

import asyncio
import base64
import hashlib
import io
from pathlib import Path
import re
import tempfile
import uuid


CRITERIA = ('Verify actual eye closing/reopening and local hair movement against the original source; '
            'preserve the character, appearance, clothes and background. Reject global camera motion or a slideshow '
            'as substitutes. Check open/intermediate/closed eyelid texture, linework, shading and occlusion, '
            'hair coherence, visible defects and the loop seam. This is procedural 2D animation, not generative video.')
INTEGER = {'type': 'integer'}
POINT = {'type': 'array', 'items': INTEGER, 'minItems': 2, 'maxItems': 2}
SPECS = [
    {'type': 'function', 'name': 'oak_hf_spaces',
     'description': 'Search public Hugging Face Spaces or inspect one published Gradio Image→Video capability. '
                    'Anonymous GET-only fallback when the external Space-search connector is unavailable. '
                    'Runtime/schema are observations, never proof of free generation, remaining quota or queue wait. '
                    'No upload, generation, account login or purchase. Use action search with query, or inspect with space_id.',
     'inputSchema': {'type': 'object', 'additionalProperties': False, 'required': ['action'], 'properties': {
         'action': {'type': 'string', 'enum': ['search', 'inspect']}, 'query': {'type': 'string'},
         'space_id': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 10}}}},
    {'type': 'function', 'name': 'oak_animate_illustration',
     'description': 'Create a free local procedural 2D blink/hair animation candidate, then independently review '
                    'the actual video against its source. No automatic delivery. The native agent must inspect the '
                    'source and prepare aligned closed-eye artwork with matching texture/shading, changed only inside '
                    'the selected eye rectangles. Use existing image editing to make that artwork; never invent flat '
                    'eyelid patches or ask the child to choose technical regions. Eye bounds are exclusive right/bottom; '
                    'hair polygons must be simple, disjoint and fit the image. Supports opaque even-sized 64–1024px '
                    'illustrations only; do not promise arbitrary photo animation. Requires the execution worker and '
                    'independent quality review. Repair revise/unverified results before oak_send_file; label as procedural.',
     'inputSchema': {'type': 'object', 'additionalProperties': False,
                     'required': ['image_path', 'closed_eyes_path', 'eye_regions', 'hair_polygons'], 'properties': {
         'image_path': {'type': 'string'}, 'closed_eyes_path': {'type': 'string'},
         'eye_regions': {'type': 'array', 'minItems': 1, 'maxItems': 2,
                         'items': {'type': 'array', 'items': INTEGER, 'minItems': 4, 'maxItems': 4}},
         'hair_polygons': {'type': 'array', 'minItems': 1, 'maxItems': 4,
                           'items': {'type': 'array', 'items': POINT, 'minItems': 3, 'maxItems': 32}},
         'seconds': {'type': 'number', 'minimum': 1, 'maximum': 6},
         'fps': {'type': 'integer', 'minimum': 12, 'maximum': 30},
         'amplitude': {'type': 'number', 'minimum': 1, 'maximum': 12}}}},
]


def render(files, image_path, closed_eyes_path, image_sha256, closed_eyes_sha256,
           eye_regions, hair_polygons, seconds=3, fps=24, amplitude=3):
    """Worker-only local render from held descriptors, published by FileTransfers."""
    from PIL import Image
    from .animation import Animation
    images = []
    try:
        for path, expected in ((image_path, image_sha256), (closed_eyes_path, closed_eyes_sha256)):
            if not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected):
                raise ValueError('An immutable source digest is required.')
            data = base64.b64decode(files.read(path)['data'], validate=True)
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError('Animation source changed before rendering; inspect it again.')
            images.append(Image.open(io.BytesIO(data)))
        animation = Animation(*images, eye_regions, hair_polygons, seconds=seconds, fps=fps, amplitude=amplitude)
        with tempfile.TemporaryDirectory(prefix='oak-render-') as folder:
            output = Path(folder) / 'candidate.mp4'
            receipt = animation.encode(output)
            if output.stat().st_size > 8 * 1024 * 1024:
                raise ValueError('Local animation exceeds the 8 MiB candidate limit.')
            data = output.read_bytes()
            destination = 'artifacts/animation-' + uuid.uuid4().hex + '.mp4'
            stored = files.write(destination, base64.b64encode(data).decode('ascii'))
            return {**receipt, 'path': stored['path'], 'source_sha256': image_sha256,
                    'sha256': hashlib.sha256(data).hexdigest()}
    finally:
        for image in images:
            image.close()


async def execute(tools, chat_id, name, args):
    if not isinstance(args, dict):
        raise ValueError('Supply an object of creative tool arguments.')
    if name == 'oak_hf_spaces':
        action = args.get('action')
        allowed = {'action', 'query', 'limit'} if action == 'search' else {'action', 'space_id'}
        required = 'query' if action == 'search' else 'space_id'
        if action not in ('search', 'inspect') or required not in args or set(args) - allowed:
            raise ValueError('Search requires query; inspect requires space_id.')
        values = {key: value for key, value in args.items() if key != 'action'}
        if tools.executor:
            return await tools.executor.call('spaces.' + action, values)
        from .hf_spaces import Spaces
        return await asyncio.to_thread(getattr(Spaces(), action), **values)
    if name != 'oak_animate_illustration':
        raise ValueError('Unknown creative tool.')
    required = {'image_path', 'closed_eyes_path', 'eye_regions', 'hair_polygons'}
    if not required <= args.keys() or set(args) - required - {'seconds', 'fps', 'amplitude'}:
        raise ValueError('Supply the source, aligned closed eyes, eye rectangles and hair polygons.')
    if tools.quality is None or tools.executor is None or tools.files is None:
        raise ValueError('Local animation requires the execution worker and independent source/video review.')
    c = tools.controller
    turn = c.active.get(chat_id)
    if not turn or c.sessions.deleted(chat_id):
        raise ValueError('Local animation requires an active task.')
    values = dict(args)
    for key, sha_key in [('image_path', 'image_sha256'), ('closed_eyes_path', 'closed_eyes_sha256')]:
        path = await tools._artifact_file(values[key])
        snapshot = await tools.files.snapshot(path, limit=8 * 1024 * 1024)
        from .quality_gate import digest
        values[key] = str(path)
        values[sha_key] = await asyncio.to_thread(digest, snapshot)
    if c.active.get(chat_id) != turn or c.sessions.deleted(chat_id):
        raise ValueError('Task stopped before local animation rendering.')
    candidate = await tools.executor.call('animation.render', values)
    if c.active.get(chat_id) != turn or c.sessions.deleted(chat_id):
        raise ValueError('Task stopped during local animation rendering; nothing was delivered.')
    path = tools.file(candidate['path'])
    tools.quality.require_source(chat_id, path, values['image_path'], values['image_sha256'], CRITERIA)
    report, _ = await tools.quality.review(chat_id, path)
    return {**candidate, **report, 'path': str(path), 'method': 'procedural_2d', 'review_required': True}
