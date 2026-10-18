from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from oak.animation_tools import CRITERIA
from oak.executor_worker import Worker
from oak.quality_gate import digest
from oak.tools import SPECS
import test_quality_gate as gate_fixture
from test_animation import illustrated_character, EYES, HAIR


class CreativeDispatchTests(unittest.IsolatedAsyncioTestCase):
    inspect = gate_fixture.QualityGateTests.inspect
    verdict = staticmethod(gate_fixture.QualityGateTests.verdict)

    def setUp(self):
        gate_fixture.QualityGateTests.setUp(self)
        self.source = self.files.workspace / 'source.png'
        self.closed = self.files.workspace / 'closed.png'
        self.source.write_bytes(b'source')
        self.closed.write_bytes(b'closed')
        self.args = {'image_path': str(self.source), 'closed_eyes_path': str(self.closed),
                     'eye_regions': EYES, 'hair_polygons': HAIR}
        original = self.tools.executor.call.side_effect
        async def dispatch(method, values):
            if method == 'animation.render':
                self.assertEqual(values['image_sha256'], digest(self.source))
                self.assertEqual(values['closed_eyes_sha256'], digest(self.closed))
                return {'path': str(self.output), 'method': 'procedural_2d', 'sha256': digest(self.output)}
            return await original(method, values)
        self.tools.executor.call.side_effect = dispatch

    async def test_render_immediately_reviews_source_and_candidate_without_delivery(self):
        result = await self.tools.execute(7, 'oak_animate_illustration', self.args)
        self.assertTrue(result['ok'])
        self.assertFalse(result['queued_for_delivery'])
        self.assertTrue(result['review_required'])
        brief, evidence, _ = self.gate.reviewer.review.await_args.args
        self.assertIn(CRITERIA, brief)
        self.assertTrue(any(item['label'].startswith('REFERENCE:') for item in evidence))
        self.controller.emit.assert_not_awaited()
        self.assertEqual(self.db.execute('SELECT source_sha256 FROM quality_sources').fetchone()[0], digest(self.source))

    async def test_unverified_candidate_stays_unsent_and_quality_disabled_never_renders(self):
        self.gate.reviewer.review.return_value = self.verdict('unverified')
        result = await self.tools.execute(7, 'oak_animate_illustration', self.args)
        self.assertFalse(result['ok'])
        self.controller.emit.assert_not_awaited()
        self.tools.executor.call.reset_mock()
        self.tools.quality = None
        with self.assertRaisesRegex(ValueError, 'independent source/video review'):
            await self.tools.execute(7, 'oak_animate_illustration', self.args)
        self.tools.executor.call.assert_not_awaited()

    async def test_cancelled_task_during_render_does_not_review_or_deliver(self):
        async def cancel(*_):
            self.controller.active.pop(7)
            return {'path': str(self.output)}
        self.tools.executor.call.side_effect = cancel
        with self.assertRaisesRegex(ValueError, 'Task stopped'):
            await self.tools.execute(7, 'oak_animate_illustration', self.args)
        self.gate.reviewer.review.assert_not_awaited()
        self.controller.emit.assert_not_awaited()

    async def test_candidate_change_before_gateway_snapshot_prevents_binding_and_review(self):
        self.tools.executor.call = AsyncMock(return_value={'path': str(self.output), 'sha256': 'a' * 64})
        with self.assertRaisesRegex(ValueError, 'candidate changed'):
            await self.tools.execute(7, 'oak_animate_illustration', self.args)
        self.gate.reviewer.review.assert_not_awaited()
        self.assertEqual(self.db.execute('SELECT count(*) FROM quality_sources').fetchone()[0], 0)
        self.controller.emit.assert_not_awaited()

    async def test_public_discovery_tool_uses_fixed_worker_operation(self):
        self.tools.executor.call = AsyncMock(return_value={'status': 'observed', 'generation_available': 'unknown'})
        result = await self.tools.execute(7, 'oak_hf_spaces', {'action': 'search', 'query': 'image to video', 'limit': 2})
        self.assertEqual(result['generation_available'], 'unknown')
        self.tools.executor.call.assert_awaited_once_with('spaces.search', {'query': 'image to video', 'limit': 2})
        with self.assertRaises(ValueError):
            await self.tools.execute(7, 'oak_hf_spaces', {'action': 'inspect', 'space_id': 'owner/space', 'query': 'extra'})

    async def test_public_discovery_without_worker_still_uses_owned_readonly_module(self):
        self.tools.executor = None
        with patch('oak.hf_spaces.Spaces.inspect', return_value={'status': 'unavailable'}) as inspect:
            result = await self.tools.execute(7, 'oak_hf_spaces', {'action': 'inspect', 'space_id': 'owner/space'})
        self.assertEqual(result['status'], 'unavailable')
        inspect.assert_called_once_with(space_id='owner/space')

    async def test_catalog_exposes_bounded_geometry_without_provider_generation_claims(self):
        specs = {spec['name']: spec for spec in SPECS}
        self.assertIn('oak_hf_spaces', specs)
        spec = specs['oak_animate_illustration']
        self.assertEqual(spec['inputSchema']['properties']['eye_regions']['maxItems'], 2)
        self.assertIn('No automatic delivery', spec['description'])


class CreativeWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.worker = Worker({'workspace': str(self.root), 'runtime_home': str(self.root / 'runtime')})
        self.addCleanup(self.worker.files.close_all)
        source, closed = illustrated_character()
        source.save(self.root / 'source.png')
        closed.save(self.root / 'closed.png')
        self.args = {'image_path': 'source.png', 'closed_eyes_path': 'closed.png',
                     'image_sha256': digest(self.root / 'source.png'), 'closed_eyes_sha256': digest(self.root / 'closed.png'),
                     'eye_regions': EYES, 'hair_polygons': HAIR, 'seconds': 1, 'fps': 12}

    async def test_source_changed_or_symlink_cannot_reach_encoder(self):
        with patch('oak.animation.Animation.encode') as encode:
            with self.assertRaisesRegex(ValueError, 'source changed'):
                await self.worker.dispatch('animation.render', {**self.args, 'image_sha256': 'a' * 64})
            (self.root / 'alias.png').symlink_to(self.root / 'source.png')
            with self.assertRaises((ValueError, OSError)):
                await self.worker.dispatch('animation.render', {**self.args, 'image_path': 'alias.png'})
            encode.assert_not_called()
        self.assertEqual(self.worker.files.handles, {})

    @unittest.skipUnless(Path('/usr/bin/ffmpeg').is_file(), 'actual local FFmpeg required')
    async def test_worker_renders_actual_mp4_and_publishes_exact_digest_through_file_boundary(self):
        result = await self.worker.dispatch('animation.render', self.args)
        self.assertEqual(result['sha256'], digest(result['path']))
        self.assertEqual(result['source_sha256'], self.args['image_sha256'])
        self.assertEqual(result['frames'], 12)
        self.assertTrue(Path(result['path']).is_relative_to(self.root / 'artifacts'))
        self.assertEqual(Path(result['path']).stat().st_mode & 0o777, 0o600)
        self.assertTrue(result['review_required'])
        self.assertEqual(self.worker.files.handles, {})

    async def test_spaces_worker_cannot_accept_tokens_or_generation_arguments(self):
        with patch('oak.hf_spaces.Spaces.search', return_value={'status': 'observed'}) as search:
            result = await self.worker.dispatch('spaces.search', {'query': 'image to video'})
            self.assertEqual(result['status'], 'observed')
            with self.assertRaises(ValueError):
                await self.worker.dispatch('spaces.search', {'query': 'video', 'token': 'CANARY'})
            search.assert_called_once_with(query='image to video')
        with self.assertRaises(ValueError):
            await self.worker.dispatch('spaces.generate', {})
