import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from oak.bus import EventBus
from oak.controller import Controller
from oak.memory import MemoryStore
from oak.quality_gate import QualityGate, digest
from oak.tools import Tools


class SnapshotFiles:
    def __init__(self, root):
        self.workspace = root / 'workspace'
        self.directory = root / 'snapshots'
        self.workspace.mkdir()
        self.directory.mkdir()
        self.count = 0

    def path(self, path):
        path = Path(path)
        path = path if path.is_absolute() else self.workspace / path
        if not path.resolve().is_relative_to(self.workspace):
            raise ValueError('Outside workspace')
        return path

    async def snapshot(self, path, **_):
        path = Path(path)
        if path.parent == self.directory:
            return path
        self.count += 1
        snapshot = self.directory / (str(self.count) + path.suffix)
        snapshot.write_bytes(self.path(path).read_bytes())
        return snapshot

    async def chunks(self, path, **_):
        yield self.path(path).read_bytes()

    def stored(self, path):
        return Path(path).parent == self.directory and Path(path).is_file()


class QualityGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.files = SnapshotFiles(Path(self.folder.name))
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.controller = SimpleNamespace(
            cwd=str(self.files.workspace), db=self.db, active={7: 'turn-1', 8: 'other-turn'},
            client=SimpleNamespace(home=Path(self.folder.name) / 'runtime'),
            task_briefs={7: 'Animate the character blinking while keeping the drawing intact.'},
            threads={7: 'thread-1'}, sessions=SimpleNamespace(deleted=lambda _: False),
            model_for=lambda _: 'gpt-6.1-sol', effort_for=lambda _: 'high', emit=AsyncMock(),
            memory=MemoryStore(self.db))
        self.tools = Tools(self.controller, {})
        self.tools.files = self.files
        self.tools.executor = SimpleNamespace(call=AsyncMock(side_effect=self.inspect))
        self.tools.quality = self.gate = QualityGate(self.tools, {})
        self.gate.reviewer = SimpleNamespace(review=AsyncMock(return_value=self.verdict('pass')))
        self.controller.tools = self.tools
        self.output = self.files.workspace / 'animation.mp4'
        self.output.write_bytes(b'original reviewed video bytes')

    @staticmethod
    def verdict(value):
        return {'verdict': value, 'summary': 'Independent inspection result.',
                'checks': [], 'issues': [], 'limitations': []}

    async def inspect(self, method, arguments):
        self.assertEqual(method, 'quality.inspect')
        path = Path(arguments['path'])
        return {'type': 'video', 'sha256': digest(path), 'frame_count': 180, 'decode_status': 'complete',
                'evidence': [{'label': 'decoded frame at 1.4 seconds',
                              'image_url': 'data:image/png;base64,ZmFrZQ=='}]}

    async def test_delivery_uses_reviewed_snapshot_even_if_producer_changes_source(self):
        original = self.output.read_bytes()

        async def review(*_, **__):
            self.output.write_bytes(b'changed after evidence extraction')
            return self.verdict('pass')

        self.gate.reviewer.review.side_effect = review
        bus = EventBus(self.controller)
        self.controller.emit = bus.emit
        sink = AsyncMock()
        bus.add_sink(sink)
        result = await self.tools.artifact(7, self.output, 'Finished animation')
        self.assertTrue(result['queued_for_delivery'])
        record = self.db.execute('SELECT path,name,mime FROM artifacts WHERE chat_id=7').fetchone()
        self.assertEqual(Path(record[0]).read_bytes(), original)
        self.assertNotEqual(Path(record[0]), self.output)
        self.assertEqual(record[1:], ('animation.mp4', 'video/mp4'))
        self.assertEqual(digest(record[0]), result['sha256'])
        sink.assert_awaited_once()

    async def test_changed_bytes_between_snapshot_and_inspection_are_rejected(self):
        original_snapshot = self.files.snapshot

        async def snapshot(path, **kwargs):
            result = await original_snapshot(path, **kwargs)
            self.output.write_bytes(b'modified before inspector opens source')
            return result

        self.files.snapshot = snapshot
        with self.assertRaisesRegex(ValueError, 'changed'):
            await self.tools.artifact(7, self.output)
        self.gate.reviewer.review.assert_not_awaited()
        self.controller.emit.assert_not_awaited()
        self.assertEqual(self.db.execute('SELECT count(*) FROM quality_reviews').fetchone()[0], 0)

    async def test_cached_review_requires_same_bytes_brief_references_and_task(self):
        first, _ = await self.gate.review(7, self.output, requirements='Two blinks.')
        again, _ = await self.gate.review(7, self.output)
        self.assertEqual(again['review_id'], first['review_id'])
        self.assertEqual(self.gate.reviewer.review.await_count, 1)
        self.output.write_bytes(b'new render at the same path')
        changed, _ = await self.gate.review(7, self.output)
        self.assertNotEqual(changed['review_id'], first['review_id'])
        self.assertNotEqual(changed['sha256'], first['sha256'])
        self.controller.task_briefs[7] = 'Now produce three blinks.'
        rebrief, _ = await self.gate.review(7, self.output)
        self.assertNotEqual(rebrief['review_id'], changed['review_id'])
        self.controller.active[7] = 'turn-2'
        new_task, _ = await self.gate.review(7, self.output)
        self.assertNotEqual(new_task['review_id'], rebrief['review_id'])
        self.assertEqual(self.gate.reviewer.review.await_count, 4)

    async def test_reference_mutation_invalidates_cached_pass(self):
        reference = self.files.workspace / 'source.png'
        reference.write_bytes(b'original source')
        first, _ = await self.gate.review(7, self.output, reference_paths=[str(reference)])
        reference.write_bytes(b'changed reference')
        changed, _ = await self.gate.review(7, self.output)
        self.assertNotEqual(first['review_id'], changed['review_id'])
        args = self.gate.reviewer.review.await_args.args
        self.assertTrue(any(item['label'].startswith('OUTPUT:') for item in args[1]))
        self.assertTrue(any(item['label'].startswith('REFERENCE:') for item in args[1]))

    async def test_failed_or_unverified_reviews_do_not_emit_artifacts(self):
        for verdict in ('revise', 'unverified'):
            with self.subTest(verdict=verdict):
                self.controller.active[7] = 'task-' + verdict
                self.gate.reviewer.review.return_value = self.verdict(verdict)
                result = await self.tools.artifact(7, self.output)
                self.assertFalse(result['queued_for_delivery'])
                self.assertFalse(result['ok'])
                self.assertEqual(result['verdict'], verdict)
        self.controller.emit.assert_not_awaited()

    async def test_reference_images_cannot_substitute_for_failed_or_missing_output_evidence(self):
        reference = self.files.workspace / 'reference.png'
        reference.write_bytes(b'valid reference evidence')
        inspector = self.tools.executor.call.side_effect
        for status, keep_evidence in [('failed', True), ('complete', False)]:
            with self.subTest(decode_status=status):
                self.controller.active[7] = 'decode-' + status

                async def inspect(method, arguments):
                    result = await inspector(method, arguments)
                    if Path(arguments['path']) == self.output:
                        result['decode_status'] = status
                        if not keep_evidence:
                            result['evidence'] = []
                    return result

                self.tools.executor.call.side_effect = inspect
                result, _ = await self.gate.review(7, self.output, reference_paths=[str(reference)])
                self.assertEqual(result['verdict'], 'unverified')
                self.assertFalse(result['ok'])
        self.gate.reviewer.review.assert_not_awaited()
        self.controller.emit.assert_not_awaited()

    async def test_owner_changes_requirements_during_same_turn_review_prevents_delivery(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def review(*_, **__):
            entered.set()
            await release.wait()
            return self.verdict('pass')

        self.gate.reviewer.review.side_effect = review
        pending = asyncio.create_task(self.tools.artifact(7, self.output))
        await asyncio.wait_for(entered.wait(), 2)
        self.controller.task_briefs[7] = 'Change the requested result to three blinks and still hair.'
        release.set()
        with self.assertRaisesRegex(ValueError, 'changed|requirements'):
            await pending
        self.controller.emit.assert_not_awaited()
        self.assertEqual(self.db.execute('SELECT count(*) FROM quality_reviews').fetchone()[0], 0)

    async def test_classification_and_delivery_share_snapshot_when_nonvisual_source_changes_to_image(self):
        self.output = self.files.workspace / 'candidate.dat'
        original = b'plain text document'
        replacement = b'\x89PNG\r\n\x1a\nnew visual content'
        self.output.write_bytes(original)
        snapshot = self.files.snapshot

        async def copy_then_change(path, **kwargs):
            result = await snapshot(path, **kwargs)
            if Path(path) == self.output:
                self.output.write_bytes(replacement)
            return result

        async def header_then_change(path, **_):
            try:
                yield self.files.path(path).read_bytes()
            finally:
                self.output.write_bytes(replacement)

        self.files.snapshot = copy_then_change
        self.files.chunks = header_then_change
        bus = EventBus(self.controller)
        self.controller.emit = bus.emit
        result = await self.tools.artifact(7, self.output)
        self.assertTrue(result['queued_for_delivery'])
        artifact = self.db.execute('SELECT path FROM artifacts WHERE chat_id=7').fetchone()
        self.assertEqual(Path(artifact[0]).read_bytes(), original)
        self.assertEqual(self.output.read_bytes(), replacement)
        self.gate.reviewer.review.assert_not_awaited()

    async def test_stopping_task_during_review_prevents_recording_pass_or_delivery(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def review(*_, **__):
            entered.set()
            await release.wait()
            return self.verdict('pass')

        self.gate.reviewer.review.side_effect = review
        pending = asyncio.create_task(self.tools.artifact(7, self.output))
        await asyncio.wait_for(entered.wait(), 2)
        self.controller.active.pop(7)
        release.set()
        with self.assertRaisesRegex(ValueError, 'stopped'):
            await pending
        self.controller.emit.assert_not_awaited()
        self.assertEqual(self.db.execute('SELECT count(*) FROM quality_reviews').fetchone()[0], 0)

    async def test_stopping_task_while_cached_review_is_being_inspected_prevents_delivery(self):
        await self.gate.review(7, self.output)
        entered, release = asyncio.Event(), asyncio.Event()
        inspector = self.tools.executor.call.side_effect

        async def inspect(method, args):
            entered.set()
            await release.wait()
            return await inspector(method, args)

        self.tools.executor.call.side_effect = inspect
        pending = asyncio.create_task(self.tools.artifact(7, self.output))
        await asyncio.wait_for(entered.wait(), 2)
        self.controller.active.pop(7)
        release.set()
        with self.assertRaisesRegex(ValueError, 'stopped'):
            await pending
        self.controller.emit.assert_not_awaited()

    async def test_review_budget_does_not_deliver_or_call_model_after_four_attempts(self):
        self.gate.reviewer.review.return_value = self.verdict('revise')
        for version in range(4):
            self.output.write_bytes(('render ' + str(version)).encode())
            await self.gate.review(7, self.output)
        self.output.write_bytes(b'fifth distinct render')
        result = await self.tools.artifact(7, self.output)
        self.assertEqual(result['error_code'], 'review_budget_exhausted')
        self.assertFalse(result['queued_for_delivery'])
        self.assertEqual(self.gate.reviewer.review.await_count, 4)
        self.controller.emit.assert_not_awaited()

    async def test_only_same_conversation_pass_receipt_backs_experience(self):
        passed, _ = await self.gate.review(7, self.output)
        args = {'action': 'save', 'key': 'character-animation', 'title': 'Character animation',
                'when_to_use': 'Blinking animation', 'procedure': 'Inspect decoded eye transitions.',
                'review_id': passed['review_id']}
        with self.assertRaisesRegex(ValueError, 'conversation'):
            await self.tools.execute(8, 'oak_experience', args)
        saved = await self.tools.execute(7, 'oak_experience', args)
        self.assertEqual(saved['status'], 'proven')
        lesson = self.controller.memory.experience_search(7)[0]
        self.assertEqual(lesson['evidence'][0]['artifact_sha256'], passed['sha256'])
        self.assertEqual(self.controller.memory.experience_search(8), [])
        self.output.write_bytes(b'failed alternative')
        self.gate.reviewer.review.return_value = self.verdict('revise')
        failed, _ = await self.gate.review(7, self.output)
        saved = await self.tools.execute(7, 'oak_experience', {**args, 'review_id': failed['review_id']})
        self.assertEqual(saved['status'], 'unverified')

    async def test_native_generated_image_is_staged_for_explicit_review_and_returns_workspace_path(self):
        native_root = self.controller.client.home / 'generated_images'
        native_root.mkdir(parents=True)
        source = native_root / 'native.png'
        source.write_bytes(b'native generated image')
        original_snapshot = self.files.snapshot

        async def snapshot(path, **kwargs):
            if kwargs.get('namespace') == 'generated_images':
                self.assertEqual(Path(path).parent, native_root)
                destination = self.files.directory / 'native.png'
                destination.write_bytes(Path(path).read_bytes())
                return destination
            return await original_snapshot(path, **kwargs)

        async def write(source, destination):
            destination.parent.mkdir(exist_ok=True)
            destination.write_bytes(source.read_bytes())

        self.files.snapshot = AsyncMock(side_effect=snapshot)
        self.files.write_trusted = AsyncMock(side_effect=write)
        result = await self.tools.execute(7, 'oak_review_artifact', {'path': str(source)})
        staged = Path(result['path'])
        self.assertTrue(staged.is_relative_to(self.files.workspace / 'artifacts'))
        self.assertEqual(staged.read_bytes(), source.read_bytes())
        self.files.write_trusted.assert_awaited_once()
        self.controller.emit.assert_not_awaited()
        delivered = await self.tools.artifact(7, staged)
        self.assertTrue(delivered['queued_for_delivery'])
        self.assertEqual(self.gate.reviewer.review.await_count, 1)
        outside = self.controller.client.home / 'other-private-file.png'
        outside.write_bytes(b'outside generated image namespace')
        with self.assertRaises(ValueError):
            await self.tools.execute(7, 'oak_review_artifact', {'path': str(outside)})
        self.files.write_trusted.assert_awaited_once()

    async def test_producer_cannot_supply_internal_snapshot_parameter(self):
        trusted = await self.files.snapshot(self.output)
        with self.assertRaises(ValueError):
            await self.tools.execute(7, 'oak_review_artifact',
                                     {'path': str(self.output), 'snapshot': str(trusted)})
        self.tools.executor.call.assert_not_awaited()
        self.gate.reviewer.review.assert_not_awaited()


class ExperienceDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_image_event_is_not_auto_delivered_or_reviewed_behind_producers_back(self):
        with tempfile.TemporaryDirectory() as directory:
            client = SimpleNamespace(events=asyncio.Queue())
            controller = Controller(client, ':memory:', directory, AsyncMock())
            self.addCleanup(controller.close)
            controller.threads[7] = 'thread-7'
            controller.tools = SimpleNamespace(quality=object(), artifact=AsyncMock(),
                                               files=SimpleNamespace(snapshot=AsyncMock()))
            await client.events.put({'method': 'item/completed', 'params': {
                'threadId': 'thread-7', 'turnId': 'turn-1', 'item': {
                    'id': 'image-1', 'type': 'imageGeneration', 'savedPath': '/native/generated_images/output.png'}}})
            await client.events.put({'method': 'client/disconnected'})
            with self.assertRaisesRegex(RuntimeError, 'disconnected'):
                await asyncio.wait_for(controller.run_events(), 2)
            controller.tools.artifact.assert_not_awaited()
            controller.tools.files.snapshot.assert_not_awaited()

    async def test_new_thread_discovers_bounded_scoped_method_index_without_full_private_procedures(self):
        with tempfile.TemporaryDirectory() as directory:
            client = AsyncMock()

            async def request(method, params):
                self.assertEqual(method, 'thread/start')
                return {'model': params['model'], 'modelProvider': 'openai', 'thread': {'id': 'new-thread'}}

            client.request.side_effect = request
            controller = Controller(client, ':memory:', directory, AsyncMock())
            self.addCleanup(controller.close)
            for index in range(20):
                controller.memory.save_experience(7, 'method-' + str(index), 'Method ' + str(index),
                    'Relevant when ' + 'conditions ' * 150, 'Full private procedure must be fetched separately.')
            controller.memory.save_experience(8, 'other-conversation', 'Secret other topic',
                                              'Other topic only', 'Unrelated private procedure.')
            await controller._thread(7)
            instructions = client.request.await_args.args[1]['developerInstructions']
            index = json.loads(instructions.split('retrieve relevant procedures):\n', 1)[1])
            self.assertLessEqual(len(json.dumps(index, ensure_ascii=False)), 10000)
            self.assertTrue(index)
            self.assertNotIn('Secret other topic', instructions)
            self.assertNotIn('Full private procedure', instructions)
            self.assertEqual(index[0]['key'], 'method-19')


if __name__ == '__main__':
    unittest.main()
