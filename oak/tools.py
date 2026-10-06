"""Oak's native tool catalog and per-conversation dispatch."""

import asyncio
import json
from pathlib import Path


def _spec(name, description, properties, required=()):
    return {'type': 'function', 'name': name, 'description': description,
            'inputSchema': {'type': 'object', 'properties': properties,
                            'required': list(required), 'additionalProperties': False}}


STRING = {'type': 'string'}
NUMBER = {'type': 'number'}
SPECS = [
    _spec('oak_memory_read', 'Read or search long-term memory for this private conversation.', {'query': STRING}),
    _spec('oak_memory_remember', 'Save a durable fact or preference the owner wants remembered.', {'text': STRING}, ['text']),
    _spec('oak_memory_forget', 'Forget one note by ID at the owner request.', {'id': STRING}, ['id']),
    _spec('oak_schedule', 'Schedule a reminder or agent task, one time or recurring. Dates use the deployment timezone.',
          {'prompt': STRING, 'delay_seconds': NUMBER, 'at': STRING, 'interval_seconds': NUMBER,
           'mode': {'type': 'string', 'enum': ['remind', 'run']}}, ['prompt']),
    _spec('oak_tasks', 'List scheduled tasks, or cancel the specified task.', {'cancel_id': STRING}),
    _spec('oak_create_image', 'Create a local thumbnail/poster with text; this is layout rendering, not generative artwork.',
          {'title': STRING, 'subtitle': STRING}, ['title']),
    _spec('oak_create_video', 'Create an MP4 preview from a title or a workspace image.',
          {'title': STRING, 'image_path': STRING, 'duration': NUMBER}),
    _spec('oak_montage', 'Join workspace images into a short MP4 slideshow.',
          {'image_paths': {'type': 'array', 'items': STRING}, 'seconds_per_image': NUMBER}, ['image_paths']),
    _spec('oak_speak', 'Generate local Ukrainian narration and return the audio file.', {'text': STRING}, ['text']),
    _spec('oak_transcribe', 'Transcribe a Ukrainian audio file from the workspace locally.', {'path': STRING}, ['path']),
    _spec('oak_research', 'Search the public web, read a web page, or obtain public YouTube metadata.',
          {'action': {'type': 'string', 'enum': ['search', 'fetch', 'youtube']}, 'query': STRING}, ['action', 'query']),
    _spec('oak_browser', 'Use the isolated browser: open/read/click/type/screenshot. Confirm publishing or purchases with the owner.',
          {'action': {'type': 'string', 'enum': ['open', 'read', 'click', 'type', 'screenshot']},
           'url': STRING, 'selector': STRING, 'text': STRING}, ['action']),
    _spec('oak_send_file', 'Deliver a generated file from the workspace to the owner chat.',
          {'path': STRING, 'caption': STRING}, ['path']),
]


class Tools:
    specs = SPECS

    def __init__(self, controller, config):
        from .media import MediaTools
        from .research import ResearchTools
        from .browser import BrowserTools
        self.controller = controller
        self.workspace = Path(controller.cwd).resolve()
        self.media = MediaTools(self.workspace, piper_model=config.get('piper_model'),
                               vosk_model=config.get('vosk_model'), whisper_model=config.get('whisper_model'))
        self.research = ResearchTools(search_url=config.get('search_url'))
        self.browser = BrowserTools(self.workspace)

    def file(self, value):
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.workspace / path
        resolved = path.resolve()
        if not resolved.is_relative_to(self.workspace) or not resolved.is_file():
            raise ValueError('Файл має бути у робочій папці Oak.')
        if any(p.startswith('.') or p.lower() in {'secrets', 'credentials', 'browser-profile'} for p in resolved.relative_to(self.workspace).parts):
            raise ValueError('Приватні службові файли не надсилаються.')
        return resolved

    async def artifact(self, chat_id, path, caption=''):
        path = self.file(path)
        await self.controller.emit(chat_id, {'type': 'CUSTOM', 'name': 'artifact',
                                           'value': {'path': str(path), 'caption': caption}})
        return {'path': str(path), 'queued_for_delivery': True}

    async def handle(self, metadata):
        chat_id = self.controller.chat_for_thread(metadata.get('threadId'))
        if chat_id is None:
            return {'success': False, 'contentItems': [{'type': 'inputText', 'text': 'Unknown conversation.'}]}
        try:
            args = metadata.get('arguments') or {}
            if isinstance(args, str):
                args = json.loads(args)
            value = await self.execute(chat_id, metadata['tool'], args, metadata)
            return {'success': True, 'contentItems': [{'type': 'inputText', 'text': json.dumps(value, ensure_ascii=False)}]}
        except Exception as exc:
            # Tool wrappers redact credential-bearing transport errors.
            return {'success': False, 'contentItems': [{'type': 'inputText', 'text': str(exc)[:1200]}]}

    async def execute(self, chat_id, name, args, metadata=None):
        c = self.controller
        if name == 'oak_memory_read':
            return c.memory.search(chat_id, args.get('query', ''))
        if name == 'oak_memory_remember':
            return {'id': c.memory.remember(chat_id, args['text'])}
        if name == 'oak_memory_forget':
            return {'forgotten': c.memory.forget(chat_id, args['id'])}
        if name == 'oak_schedule':
            return {'id': c.scheduler.add(chat_id, **args)}
        if name == 'oak_tasks':
            return {'cancelled': c.scheduler.cancel(chat_id, args['cancel_id'])} if args.get('cancel_id') else c.scheduler.list(chat_id)
        if name == 'oak_create_image':
            path = await asyncio.to_thread(self.media.generate_image, **args)
            return await self.artifact(chat_id, path, args['title'])
        if name == 'oak_create_video':
            if args.get('image_path'):
                args['image_path'] = str(self.file(args['image_path']))
            path = await asyncio.to_thread(self.media.generate_video, **args)
            return await self.artifact(chat_id, path, args.get('title', 'Відео'))
        if name == 'oak_speak':
            path = await asyncio.to_thread(self.media.speak, args['text'], language='uk')
            return await self.artifact(chat_id, path, 'Озвучення')
        if name == 'oak_montage':
            paths = [str(self.file(path)) for path in args['image_paths']]
            path = await asyncio.to_thread(self.media.montage, paths, args.get('seconds_per_image', 3))
            return await self.artifact(chat_id, path, 'Монтаж')
        if name == 'oak_transcribe':
            text = await asyncio.to_thread(self.media.transcribe, self.file(args['path']))
            return {'text': text}
        if name == 'oak_research':
            fn = getattr(self.research, args['action'], None)
            if args['action'] not in ('search', 'fetch', 'youtube') or fn is None:
                raise ValueError('Unknown research action.')
            return await asyncio.to_thread(fn, args['query'])
        if name == 'oak_browser':
            if args.get('action') in ('click', 'type'):
                accepted = await c.interactions.ask(metadata or {'threadId': c.threads.get(chat_id)},
                                                     'approval', 'Дія в браузері потребує підтвердження:\n' + json.dumps(args, ensure_ascii=False))
                if not accepted:
                    raise ValueError('Дію не підтверджено.')
            result = await self.browser.run(**args)
            if result.get('path'):
                await self.artifact(chat_id, result['path'], 'Знімок браузера')
            return result
        if name == 'oak_send_file':
            return await self.artifact(chat_id, args['path'], args.get('caption', ''))
        raise ValueError('Unknown Oak tool.')
