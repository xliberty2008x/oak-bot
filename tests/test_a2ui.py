import asyncio
import base64
import copy
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlencode, urlsplit

from aiohttp.test_utils import TestClient, TestServer

from oak.a2ui import IMAGE_BYTES, StaleSurface, VERSION, input_id, validate_messages
from oak.a2ui_demo import card_messages, form_messages, message, result_messages
from oak.a2ui_telegram import card_blocks
from oak.bus import EventBus
from oak.controller import Controller
from oak.events import validate_event
from oak.transfer import ExecutorFiles
from oak.tools import Tools
from oak.web import WebGateway


class A2UITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.runtime = AsyncMock()
        self.runtime.request.return_value = {'turn': {'id': 'action-turn'}}
        self.c = Controller(self.runtime, self.root / 'state.sqlite', self.root, AsyncMock(),
                            config={'state_dir': str(self.root)})
        self.addCleanup(self.c.db.close)
        self.c.tools = Tools(self.c, {})
        self.bus = EventBus(self.c)
        self.c.emit = self.bus.emit
        self.c.threads[11] = 'thread-one'
        self.c.loaded.add('thread-one')
        with self.c.db:
            self.c.db.execute('INSERT INTO chats(chat_id,thread_id,tools_version) VALUES (?,?,?)',
                              (11, 'thread-one', self.c.tools.version))

    async def publish(self, messages=None, metadata=None):
        return await self.c.tools.execute(11, 'oak_a2ui', {
            'action': 'publish', 'messages_json': json.dumps(messages or form_messages()),
            'fallback': 'Обери тему й темп у панелі Oak або напиши їх тут у Telegram.'},
            metadata or {'threadId': 'thread-one', 'turnId': 'form-turn'})

    def action(self, name='submit', request='request-one', revision=None):
        if revision is None:
            revision = self.c.a2ui.row(11, 'oak-plan')['revision']
        return {'requestId': request, 'revision': revision, 'inputs': {'/form/topic': 'Подорож', '/form/pace': ['focused']},
                'message': {'version': VERSION, 'action': {'name': name, 'surfaceId': 'oak-plan',
                    'sourceComponentId': name, 'timestamp': '2026-10-08T05:00:00Z',
                    'context': {'topic': 'Подорож', 'pace': ['focused']} if name == 'submit' else {}}}}

    async def test_native_tool_form_action_incremental_result_and_text_fallback(self):
        catalogue = await self.c.tools.execute(11, 'oak_a2ui', {'action': 'catalog'})
        self.assertEqual(catalogue['catalog']['catalogId'], 'urn:oak:a2ui:canonical:v1')
        await self.publish()
        initial = self.c.a2ui.snapshot(11)['surfaces'][0]
        self.assertEqual(initial['dataModel']['form']['pace'], ['gentle'])
        receipt = await self.c.a2ui.act(11, self.action())
        self.assertEqual(receipt, {'status': 'accepted'})
        method, payload = self.runtime.request.await_args.args
        self.assertEqual(method, 'turn/start')
        self.assertEqual(payload['threadId'], 'thread-one')
        self.assertEqual(payload['model'], 'gpt-6.1-sol')
        self.assertIn('Подорож', payload['input'][0]['text'])
        await self.publish(result_messages({'topic': 'Подорож', 'pace': ['focused']}),
                           {'threadId': 'thread-one', 'turnId': 'action-turn'})
        final = self.c.a2ui.snapshot(11)['surfaces'][0]
        self.assertGreater(final['revision'], initial['revision'])
        self.assertEqual(final['components']['root'], initial['components']['root'])
        self.assertIn('Подорож', final['dataModel']['result'])
        self.assertFalse(final['consumed'])
        events = [x['event'] for x in self.bus.replay(11)]
        self.assertEqual(sum(e.get('name') == 'a2ui' for e in events), 2)
        cards = [e['value'] for e in events if e.get('name') == 'a2ui_card']
        self.assertEqual([card['revision'] for card in cards], [initial['revision'], final['revision']])
        self.assertIn('Подорож', cards[-1]['state']['dataModel']['result'])
        self.assertEqual(sum(e['type'] == 'TEXT_MESSAGE_END' for e in events), 0)

    async def test_publish_links_visible_surface_in_general_and_topic(self):
        self.c.web = SimpleNamespace(public_url='https://oak.example.test/')
        topic = self.c.sessions.resolve(11, 42)
        self.c.threads[topic] = 'thread-topic'
        fallback = 'Обери тему або напиши її тут. ' * 30
        for chat, conversation in [(11, 'telegram'), (topic, 'topic:42')]:
            result = await self.c.a2ui.publish(chat, form_messages(), fallback)
            events = [x['event'] for x in self.bus.replay(chat)]
            self.assertEqual([e.get('name') for e in events], ['a2ui', 'a2ui_card'])
            card = events[-1]['value']
            self.assertEqual(card['label'], fallback)
            self.assertEqual(card['button_label'], 'Відкрити форму')
            self.assertEqual(card['state']['components']['root']['component'], 'Card')
            self.assertEqual(parse_qs(urlsplit(card['url']).query),
                             {'conversation': [conversation], 'surface': ['oak-plan']})
            self.assertTrue(result['ui_available'])
            self.assertEqual(result['web_app_url'], card['url'])
        result = await self.c.a2ui.publish(11, card_messages(), 'Денна ціль готова.')
        card = [x['event'] for x in self.bus.replay(11)][-1]['value']
        self.assertEqual((card['surfaceId'], card['button_label']), ('oak-day', 'Відкрити в Oak'))
        self.assertEqual(result['telegram_cards'], 1)

    async def test_publish_keeps_text_for_no_https_delete_and_rootless_surface(self):
        self.c.web = SimpleNamespace(public_url='http://oak.example.test/')
        result = await self.publish()
        self.assertFalse(result['ui_available'])
        self.assertEqual(result['telegram_cards'], 1)
        self.c.web.public_url = 'https://oak.example.test/'
        for messages in [[message('deleteSurface', {})],
                         [message('createSurface', {'catalogId': 'urn:oak:a2ui:canonical:v1'})]]:
            result = await self.publish(messages)
            self.assertTrue(result['published'])
            self.assertFalse(result['ui_available'])
            self.assertNotIn('web_app_url', result)
        events = [x['event'] for x in self.bus.replay(11)]
        self.assertFalse(any(e.get('name') == 'web_app_link' for e in events))
        cards = [e['value'] for e in events if e.get('name') == 'a2ui_card']
        self.assertNotIn('url', cards[0])
        self.assertEqual(cards[1], {'surfaceId': 'oak-plan', 'label': cards[0]['label'], 'deleted': True, 'revision': cards[0]['revision'] + 1})
        self.assertEqual(len(cards), 2)
        self.assertEqual(sum(e['type'] == 'TEXT_MESSAGE_END' for e in events), 2)

    async def test_display_card_components_bindings_and_limits(self):
        await self.publish(card_messages())
        surface = self.c.a2ui.snapshot(11)['surfaces'][0]
        self.assertEqual(surface['components']['targets']['children'], ['kcal', 'protein', 'fat', 'carbs'])
        self.assertEqual(surface['dataModel']['note'][:8], 'Сума БЖВ')
        def card(*components, data=None):
            return [message('createSurface', {'catalogId': 'urn:oak:a2ui:canonical:v1'}),
                    message('updateComponents', {'components': [
                        {'id': 'root', 'component': 'Column', 'children': [c['id'] for c in components]}, *components]}),
                    *([message('updateDataModel', {'value': data})] if data is not None else [])]
        await self.publish([{'version': VERSION, 'deleteSurface': {'surfaceId': 'oak-day'}}])
        candidates = [
            {'id': 'x', 'component': 'Chart', 'kind': 'bar', 'labels': ['a', 'b'], 'series': [{'label': 's', 'values': [1]}]},
            {'id': 'x', 'component': 'Chart', 'kind': 'pie', 'labels': ['a'], 'series': [{'label': 's', 'values': [1]}]},
            {'id': 'x', 'component': 'Chart', 'kind': 'line', 'labels': ['a'], 'series': [{'label': 's', 'values': [True]}]},
            {'id': 'x', 'component': 'Table', 'columns': ['a', 'b'], 'rows': [['only one']]},
            {'id': 'x', 'component': 'Table', 'columns': ['a'], 'rows': [[{'path': '/x'}]]},
            {'id': 'x', 'component': 'Progress', 'segments': [{'label': 'a', 'value': -1}]},
            {'id': 'x', 'component': 'Progress', 'segments': [{'label': 'a', 'value': 0}]},
            {'id': 'x', 'component': 'Badge', 'text': 'ok', 'tone': 'neon'},
            {'id': 'x', 'component': 'Metric', 'label': 'a', 'value': '1', 'style': 'color:red'},
            {'id': 'x', 'component': 'Image', 'alt': 'a'},
            {'id': 'x', 'component': 'Image', 'alt': 'a', 'artifact': 'f' * 32},
            {'id': 'x', 'component': 'Image', 'alt': 'a', 'path': 'https://example.test/a.png'},
            {'id': 'x', 'component': 'Divider', 'text': 'a'},
        ]
        for item in candidates:
            with self.subTest(item=item), self.assertRaises(ValueError):
                await self.publish(card(item))
        with self.assertRaises(ValueError):
            await self.publish(card({'id': 'x', 'component': 'Metric', 'label': 'a', 'value': {'path': '/v'}}, data={'v': 3}))
        self.assertEqual(self.c.a2ui.snapshot(11)['surfaces'], [])

    async def test_images_become_conversation_artifacts(self):
        (self.root / 'artifacts').mkdir(exist_ok=True)
        (self.root / 'artifacts' / 'meal.png').write_bytes(b'\x89PNG\r\n\x1a\nsynthetic')
        (self.root / 'notes.txt').write_text('not an image')
        image = {'id': 'photo', 'component': 'Image', 'alt': 'Вівсянка', 'path': 'artifacts/meal.png'}
        messages = [message('createSurface', {'catalogId': 'urn:oak:a2ui:canonical:v1'}),
                    message('updateComponents', {'components': [
                        {'id': 'root', 'component': 'Column', 'children': ['photo', 'again']}, image,
                        {**image, 'id': 'again'}]})]
        await self.publish(messages)
        stored = self.c.a2ui.snapshot(11)['surfaces'][0]['components']
        self.assertNotIn('path', stored['photo'])
        self.assertEqual(stored['photo']['artifact'], stored['again']['artifact'])
        self.assertEqual(tuple(self.c.db.execute('SELECT chat_id,mime FROM artifacts WHERE id=?', (stored['photo']['artifact'],)).fetchone()),
                         (11, 'image/png'))
        events = json.dumps([x['event'] for x in self.bus.replay(11)])
        self.assertNotIn('meal.png', events)
        reuse = [message('updateComponents', {'components': [{'id': 'again', 'component': 'Image', 'alt': 'Знову',
                                                              'artifact': stored['photo']['artifact']}]})]
        await self.publish(reuse)
        self.c.threads[22] = 'thread-two'
        with self.assertRaises(ValueError):
            await self.c.a2ui.publish(22, [message('createSurface', {'catalogId': 'urn:oak:a2ui:canonical:v1'}), *reuse], 'Інша сесія.')
        for path in ['notes.txt', '../outside.png', 'missing.png']:
            bad = copy.deepcopy(messages)
            bad[1]['updateComponents']['components'][1]['path'] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                await self.publish([message('deleteSurface', {}), *bad])

    def image_messages(self, path='artifacts/remote.png'):
        return [message('createSurface', {'catalogId': 'urn:oak:a2ui:canonical:v1'}),
                message('updateComponents', {'components': [
                    {'id': 'root', 'component': 'Column', 'children': ['photo']},
                    {'id': 'photo', 'component': 'Image', 'alt': 'Synthetic image', 'path': path}]})]

    def worker_images(self, data=b'\x89PNG\r\n\x1a\nsynthetic', size=None):
        client = SimpleNamespace(call=AsyncMock())
        async def rpc(method, args):
            if method == 'file.open_read':
                return {'handle': 'fixture-handle', 'size': len(data) if size is None else size}
            if method == 'file.read_chunk':
                return {'data': base64.b64encode(data).decode(), 'eof': True}
            if method == 'file.close':
                return {}
            raise AssertionError(method)
        client.call.side_effect = rpc
        # The gateway has no direct file at this executor-only workspace path.
        self.c.tools.files = ExecutorFiles(client, self.root / 'isolated-worker', self.root / 'gateway')
        return client

    async def test_worker_images_stream_to_private_snapshot_and_reuse_without_rpc(self):
        client = self.worker_images()
        await self.publish(self.image_messages())
        surface = self.c.a2ui.snapshot(11)['surfaces'][0]
        artifact = surface['components']['photo']['artifact']
        row = self.c.db.execute('SELECT path,name,mime FROM artifacts WHERE id=?', (artifact,)).fetchone()
        self.assertTrue(self.c.tools.files.stored(row['path']))
        self.assertEqual(Path(row['path']).read_bytes(), b'\x89PNG\r\n\x1a\nsynthetic')
        self.assertEqual(tuple(row)[1:], ('remote.png', 'image/png'))
        self.assertEqual([call.args[0] for call in client.call.await_args_list],
                         ['file.open_read', 'file.read_chunk', 'file.close'])
        self.assertNotIn('remote.png', json.dumps(self.bus.replay(11)))
        self.assertNotIn('isolated-worker', json.dumps(self.bus.replay(11)))
        client.call.reset_mock()
        await self.publish([message('updateComponents', {'components': [
            {'id': 'photo', 'component': 'Image', 'alt': 'Again', 'artifact': artifact}]})])
        client.call.assert_not_awaited()

    async def test_oversized_worker_image_is_rejected_before_any_read(self):
        client = self.worker_images(size=IMAGE_BYTES + 1)
        with self.assertRaises(ValueError):
            await self.publish(self.image_messages())
        self.assertEqual([call.args[0] for call in client.call.await_args_list], ['file.open_read', 'file.close'])
        self.assertEqual(list(self.c.tools.files.directory.iterdir()), [])
        self.assertEqual(self.c.db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0], 0)
        self.assertEqual(self.bus.replay(11), [])

    async def test_rejected_image_batch_registers_nothing_and_stale_scope_does_not_read(self):
        client = self.worker_images()
        invalid = self.image_messages()
        invalid[1]['updateComponents']['components'][0]['children'].append('root')
        with self.assertRaises(ValueError):
            await self.publish(invalid)
        self.assertEqual(self.c.db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0], 0)
        self.assertEqual(self.c.a2ui.snapshot(11)['surfaces'], [])
        client.call.reset_mock()
        with self.assertRaises(StaleSurface):
            await self.publish(self.image_messages(), {'threadId': 'old-thread', 'turnId': 'old-run'})
        client.call.assert_not_awaited()

    async def test_session_change_during_snapshot_does_not_register_or_publish(self):
        client = self.worker_images()
        original = client.call.side_effect
        async def rpc(method, args):
            response = await original(method, args)
            if method == 'file.read_chunk':
                self.c.threads[11] = 'replacement-thread'
            return response
        client.call.side_effect = rpc
        with self.assertRaises(StaleSurface):
            await self.publish(self.image_messages())
        self.assertEqual(self.c.db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0], 0)
        self.assertEqual(self.bus.replay(11), [])

    async def test_concurrent_publish_cards_keep_committed_state_and_thread(self):
        blocked, release = asyncio.Event(), asyncio.Event()
        async def emit(chat, event):
            if event.get('name') == 'a2ui' and event['value']['revisions'].get('oak-plan') == 1:
                blocked.set()
                await release.wait()
            await self.bus.emit(chat, event)
        self.c.emit = emit
        first = asyncio.create_task(self.publish())
        try:
            await asyncio.wait_for(blocked.wait(), 2)
            await self.publish([message('updateDataModel', {'path': '/form/topic', 'value': 'New revision'})],
                               {'threadId': 'thread-one', 'turnId': 'second-turn'})
            self.c.threads[11] = 'replacement-thread'
            self.c.active[11] = 'replacement-turn'
        finally:
            release.set()
            await first
        cards = [x['event'] for x in self.bus.replay(11) if x['event'].get('name') == 'a2ui_card']
        self.assertEqual([event['value']['revision'] for event in cards], [2, 1])
        self.assertEqual(cards[0]['value']['state']['dataModel']['form']['topic'], 'New revision')
        self.assertNotEqual(cards[1]['value']['state']['dataModel']['form']['topic'], 'New revision')
        self.assertEqual([event['threadId'] for event in cards], ['thread-one', 'thread-one'])
        self.assertEqual([event['runId'] for event in cards], ['second-turn', 'form-turn'])

    async def test_late_delete_has_tombstone_revision_older_than_recreated_card(self):
        await self.publish()
        blocked, release = asyncio.Event(), asyncio.Event()
        async def emit(chat, event):
            if event.get('name') == 'a2ui' and any('deleteSurface' in m for m in event['value']['messages']):
                blocked.set()
                await release.wait()
            await self.bus.emit(chat, event)
        self.c.emit = emit
        deletion = asyncio.create_task(self.publish([message('deleteSurface', {})]))
        try:
            await asyncio.wait_for(blocked.wait(), 2)
            recreated = await self.publish()
        finally:
            release.set()
            removed = await deletion
        cards = [x['event']['value'] for x in self.bus.replay(11) if x['event'].get('name') == 'a2ui_card']
        self.assertEqual([card['revision'] for card in cards], [1, 3, 2])
        self.assertTrue(cards[-1]['deleted'])
        self.assertEqual(recreated['revisions'], {'oak-plan': 3})
        self.assertEqual(removed['revisions'], {})
        self.assertEqual(self.c.a2ui.row(11, 'oak-plan')['revision'], 3)
        for revision in (None, 0, True):
            event = {'type': 'CUSTOM', 'threadId': 'thread-one', 'runId': 'form-turn',
                     'name': 'a2ui_card', 'value': {**cards[-1], 'revision': revision}}
            with self.assertRaises(ValueError):
                validate_event(event)

    async def test_rootless_fallback_does_not_leak_into_replacement_thread(self):
        blocked, release = asyncio.Event(), asyncio.Event()
        async def emit(chat, event):
            if event.get('name') == 'a2ui':
                blocked.set()
                await release.wait()
            await self.bus.emit(chat, event)
        self.c.emit = emit
        publication = asyncio.create_task(self.publish([
            message('createSurface', {'catalogId': 'urn:oak:a2ui:canonical:v1'})]))
        try:
            await asyncio.wait_for(blocked.wait(), 2)
            self.c.threads[11] = 'replacement-thread'
        finally:
            release.set()
            result = await publication
        self.assertEqual(result['telegram_cards'], 0)
        events = [x['event'] for x in self.bus.replay(11)]
        self.assertEqual([event.get('name') for event in events], ['a2ui'])
        self.assertEqual(events[0]['threadId'], 'thread-one')

    async def test_card_event_rejects_private_paths_and_unvalidated_component_state(self):
        await self.publish()
        event = next(x['event'] for x in self.bus.replay(11) if x['event'].get('name') == 'a2ui_card')
        validate_event(event)
        variants = []
        forged = copy.deepcopy(event)
        forged['value']['state']['components']['photo'] = {
            'id': 'photo', 'component': 'Image', 'alt': 'Unsafe', 'path': 'private.png'}
        variants.append(forged)
        forged = copy.deepcopy(event)
        forged['value']['state']['private'] = 'not a catalogue field'
        variants.append(forged)
        forged = copy.deepcopy(event)
        forged['value']['state']['components']['root']['id'] = 'mismatched'
        variants.append(forged)
        forged = copy.deepcopy(event)
        forged['value']['state']['components']['unknown'] = {'id': 'unknown', 'component': 'HTML', 'text': '<script>'}
        variants.append(forged)
        for forged in variants:
            with self.assertRaises(ValueError):
                validate_event(forged)

    def test_telegram_blocks_for_display_card(self):
        state = {'components': {}, 'dataModel': {}}
        for item in card_messages():
            body = item.get('updateComponents') or {}
            state['components'].update({x['id']: x for x in body.get('components', [])})
            if 'updateDataModel' in item:
                state['dataModel'] = item['updateDataModel']['value']
        state['components']['photo'] = {'id': 'photo', 'component': 'Image', 'alt': 'Сніданок', 'artifact': 'a' * 32}
        state['components']['body']['children'].append('photo')
        blocks = card_blocks(state, lambda artifact: 'card0')
        self.assertEqual(blocks[0], {'type': 'heading', 'text': 'Твоя денна ціль', 'size': 3})
        self.assertEqual([c['text'] for c in blocks[1]['cells'][0]], ['Калорії', 'Білки', 'Жири', 'Вуглеводи'])
        self.assertEqual(blocks[1]['cells'][1][0]['text'], {'type': 'bold', 'text': '1600'})
        self.assertEqual(blocks[2]['text'][1], 'Білки 20% · Жири 34% · Вуглеводи 46%')
        self.assertEqual((blocks[3]['type'], blocks[3]['caption'], len(blocks[3]['cells'])), ('table', 'Меню на один день', 5))
        self.assertEqual(blocks[4]['cells'][1][1]['text'], '400 ккал')
        self.assertEqual(blocks[5]['text']['type'], 'italic')
        self.assertEqual(blocks[6], {'type': 'photo', 'photo': {'type': 'photo', 'media': 'attach://card0'}})
        self.assertEqual(len(card_blocks(state, lambda artifact: None)), 6)

    async def test_repeated_requests_single_use_and_fingerprint(self):
        await self.publish()
        data = self.action()
        self.assertEqual(await self.c.a2ui.act(11, data), {'status': 'accepted'})
        self.assertEqual(await self.c.a2ui.act(11, data), {'status': 'accepted'})
        self.runtime.request.assert_awaited_once()
        forged = copy.deepcopy(data)
        forged['message']['action']['context']['topic'] = 'Different'
        with self.assertRaises(ValueError):
            await self.c.a2ui.act(11, forged)
        with self.assertRaises(StaleSurface):
            await self.c.a2ui.act(11, self.action(request='second-click'))

    async def test_stale_reset_delete_recreate_and_expiration(self):
        await self.publish()
        old = self.action()
        await self.publish([message('deleteSurface', {})])
        await self.publish()
        with self.assertRaises(StaleSurface):
            await self.c.a2ui.act(11, old)
        with self.c.db:
            self.c.db.execute('UPDATE a2ui_surfaces SET expires=0')
        self.assertEqual(self.c.a2ui.snapshot(11)['surfaces'], [])
        with self.assertRaises(StaleSurface):
            await self.c.a2ui.act(11, old)
        await self.c.new(11)
        self.assertEqual(self.c.a2ui.snapshot(11)['surfaces'], [])
        self.runtime.request.assert_not_awaited()

    async def test_wrong_action_context_path_choice_and_unreachable_button(self):
        await self.publish()
        candidates = []
        for key, value in [('name', 'shell'), ('sourceComponentId', 'unoffered'), ('context', {'topic': 'forged'})]:
            data = self.action(); data['message']['action'][key] = value; candidates.append(data)
        data = self.action(); data['inputs']['/private'] = 'data'; candidates.append(data)
        data = self.action(); data['inputs']['/form/pace'] = ['unoffered']; candidates.append(data)
        data = self.action(); data['inputs']['/form/pace'] = [{}]; candidates.append(data)
        for data in candidates:
            with self.subTest(data=data), self.assertRaises(ValueError):
                await self.c.a2ui.act(11, data)
        await self.publish([message('updateComponents', {'components': [
            {'id': 'hidden', 'component': 'Button', 'label': 'Hidden', 'action': {'event': {'name': 'hidden'}}}]})])
        data = self.action('hidden')
        with self.assertRaises(ValueError):
            await self.c.a2ui.act(11, data)
        self.runtime.request.assert_not_awaited()

    async def test_cancel_allows_empty_required_inputs_and_agent_deletes_surface(self):
        await self.publish()
        data = self.action('cancel')
        data['inputs'] = {'/form/topic': '', '/form/pace': []}
        self.assertEqual(await self.c.a2ui.act(11, data), {'status': 'accepted'})
        await self.publish([message('deleteSurface', {})], {'threadId': 'thread-one', 'turnId': 'action-turn'})
        self.assertEqual(self.c.a2ui.snapshot(11)['surfaces'], [])

    async def test_optional_field_without_initial_data_resolves_empty_default(self):
        messages = [message('createSurface', {'catalogId': 'urn:oak:a2ui:canonical:v1'}),
                    message('updateComponents', {'components': [
                        {'id': 'root', 'component': 'Column', 'children': ['note', 'submit']},
                        {'id': 'note', 'component': 'TextField', 'label': 'Примітка', 'value': {'path': '/note'}},
                        {'id': 'submit', 'component': 'Button', 'label': 'Далі',
                         'action': {'event': {'name': 'submit', 'context': {'note': {'path': '/note'}}}}}]})]
        await self.publish(messages)
        data = self.action()
        data['inputs'] = {}
        data['message']['action']['context'] = {'note': ''}
        self.assertEqual(await self.c.a2ui.act(11, data), {'status': 'accepted'})

    async def test_editable_parent_conflict_rejected_before_initial_data(self):
        messages = [message('createSurface', {'catalogId': 'urn:oak:a2ui:canonical:v1'}),
                    message('updateComponents', {'components': [
                        {'id': 'root', 'component': 'Column', 'children': ['parent', 'child']},
                        {'id': 'parent', 'component': 'TextField', 'label': 'Батьківське поле', 'value': {'path': '/form'}},
                        {'id': 'child', 'component': 'TextField', 'label': 'Дочірнє поле', 'value': {'path': '/form/topic'}}]})]
        with self.assertRaises(ValueError):
            await self.publish(messages)
        self.assertEqual(self.c.a2ui.snapshot(11)['surfaces'], [])

    async def test_malformed_atomic_updates_cycles_dag_bindings_and_limits(self):
        await self.publish()
        initial = self.c.a2ui.snapshot(11)
        candidates = [
            [message('updateComponents', {'components': [{'id': 'x', 'component': 'Script', 'code': 'alert(1)'}]})],
            [message('updateComponents', {'components': [{'id': 'root', 'component': 'Column', 'children': ['root']}]})],
            [message('updateComponents', {'components': [{'id': 'x', 'component': 'Column', 'children': ['topic']}]})],
            [message('updateComponents', {'components': [{'id': 'x', 'component': 'Column', 'children': [{}]}]})],
            [message('updateDataModel', {'path': '/form/pace', 'value': ['wrong']})],
            [message('updateDataModel', {'path': '/form', 'value': 'not-an-object'})],
            [message('updateDataModel', {'value': {'__proto__': {}}})],
            [message('updateDataModel', {'path': '/form/~2', 'value': 'bad'})],
            [message('updateDataModel', {'path': '/form/topic', 'value': {'code': 'bad'}})],
            [message('updateDataModel', {'path': '/result', 'value': float('nan')})],
            [message('updateDataModel', {'path': '/result', 'value': 'x' * 65536})],
        ]
        for messages in candidates:
            with self.subTest(messages=str(messages)[:160]), self.assertRaises(ValueError):
                await self.publish(messages)
            self.assertEqual(self.c.a2ui.snapshot(11), initial)
        text = [message('updateComponents', {'components': [{'id': 'title', 'component': 'Text', 'text': {'path': '/result'}}]}),
                message('updateDataModel', {'path': '/result', 'value': {}})]
        with self.assertRaises(ValueError):
            await self.publish(text)

    async def test_late_cancelled_run_does_not_overwrite_current_state(self):
        await self.publish()
        initial = self.c.a2ui.snapshot(11)
        with self.c.db:
            self.c.db.execute('INSERT INTO turns VALUES (?,?,?,?)', ('old-turn', 'thread-one', 11, 'interrupted'))
        with self.assertRaises(StaleSurface):
            await self.publish(result_messages({'topic': 'bad', 'pace': ['gentle']}), {'threadId': 'thread-one', 'turnId': 'old-turn'})
        self.assertEqual(self.c.a2ui.snapshot(11), initial)

    async def test_uncertain_action_and_crash_pending_input_never_replay(self):
        await self.publish()
        data = self.action()
        self.runtime.request.side_effect = ConnectionError('synthetic disconnect')
        with self.assertRaises(ConnectionError):
            await self.c.a2ui.act(11, data)
        self.assertEqual(await self.c.a2ui.act(11, data), {'status': 'uncertain'})
        self.runtime.request.assert_awaited_once()
        self.c.a2ui.invalidate(11)
        with self.c.db:
            self.c.db.execute("INSERT INTO a2ui_actions VALUES (?,?,?,?)", (11, 'crash', 'synthetic', 'dispatching'))
            self.c.db.execute('INSERT INTO inputs(update_id,chat_id,text,status) VALUES (?,?,?,?)', (input_id(11, 'crash'), 11, 'stale UI action', 'pending'))
        self.runtime.request.reset_mock()
        await self.c.recover()
        self.runtime.request.assert_not_awaited()
        row = self.c.db.execute('SELECT status FROM inputs WHERE update_id=?', (input_id(11, 'crash'),)).fetchone()
        self.assertEqual(row[0], 'uncertain')

    async def test_durable_receipt_after_restart(self):
        await self.publish()
        data = self.action()
        await self.c.a2ui.act(11, data)
        other = Controller(self.runtime, self.root / 'state.sqlite', self.root, AsyncMock(), config={'state_dir': str(self.root)})
        try:
            self.assertEqual(await other.a2ui.act(11, data), {'status': 'accepted'})
            self.runtime.request.assert_awaited_once()
        finally:
            other.close()

    async def test_signed_telegram_api_owner_origin_and_session_scope(self):
        await self.publish()
        gateway = WebGateway(self.c, self.bus, '123:synthetic-token', [11, 22], {})
        client = TestClient(TestServer(gateway.app))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        fields = {'auth_date': str(int(time.time())), 'user': json.dumps({'id': 11})}
        secret = hmac.new(b'WebAppData', gateway.token.encode(), hashlib.sha256).digest()
        fields['hash'] = hmac.new(secret, '\n'.join(k + '=' + v for k, v in sorted(fields.items())).encode(), hashlib.sha256).hexdigest()
        response = await client.get('/api/a2ui')
        self.assertEqual(response.status, 401)
        response = await client.post('/api/session', json={'initData': urlencode(fields)})
        self.assertEqual(response.status, 200)
        first = {'Authorization': 'Bearer ' + (await response.json())['access_token']}
        response = await client.get('/api/a2ui', headers=first)
        self.assertEqual((await response.json())['surfaces'][0]['id'], 'oak-plan')
        for invalid in [None, [], 'not-an-action', 7]:
            response = await client.post('/api/a2ui/action', headers={**first, 'Content-Type': 'application/json'},
                                         data=json.dumps(invalid))
            self.assertEqual(response.status, 400)
        self.assertFalse(self.c.a2ui.snapshot(11)['surfaces'][0]['consumed'])
        response = await client.post('/api/session', json={'key': gateway.keys['22']})
        second = {'Authorization': 'Bearer ' + (await response.json())['access_token']}
        response = await client.get('/api/a2ui', headers=second)
        self.assertEqual((await response.json())['surfaces'], [])
        response = await client.post('/api/a2ui/action', headers=second, json=self.action())
        self.assertEqual(response.status, 409)
        response = await client.post('/api/a2ui/action', headers={**first, 'Origin': 'https://untrusted.example'}, json=self.action())
        self.assertEqual(response.status, 403)
        response = await client.post('/api/a2ui/action?conversation=topic:999', headers=first, json=self.action())
        self.assertEqual(response.status, 404)
        response = await client.post('/api/a2ui/action', headers=first, json=self.action())
        self.assertEqual((await response.json())['status'], 'accepted')
        client.session.cookie_jar.clear()
        with self.c.db:
            self.c.db.execute('UPDATE web_sessions SET expires=0')
        response = await client.get('/api/a2ui', headers=first)
        self.assertEqual(response.status, 401)

    def test_version_and_envelope_rejected(self):
        for messages in [[{'version': 'v1.0', 'deleteSurface': {'surfaceId': 'x'}}],
                         [{'version': VERSION, 'deleteSurface': {'surfaceId': 'x'}, 'code': 'alert(1)'}],
                         [{'version': VERSION, 'createSurface': {'surfaceId': 'x', 'catalogId': 'unapproved'}}]]:
            with self.assertRaises(ValueError):
                validate_messages(messages)

    def test_pinned_wire_fixtures(self):
        fixtures = json.loads((Path(__file__).parent / 'fixtures' / 'a2ui' / 'profile.json').read_text())
        validate_messages(fixtures['server'])
        for message in fixtures['invalid']:
            with self.assertRaises(ValueError):
                validate_messages([message])
