"""Protocol and integrity checks; these tests make no model calls."""

import asyncio
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from oak.media_pilot_runtime import PilotRuntime, _PilotTools
from oak.quality_media import codec_environment
from oak.runtime import MODEL


PNG_BYTES = b'\x89PNG\r\n\x1a\nsynthetic image protocol fixture'
IMAGE = 'data:image/png;base64,' + base64.b64encode(PNG_BYTES).decode()


def review(verdict='pass'):
    issues = [] if verdict == 'pass' else [{'severity': 'major',
        'evidence': 'The requested title is clipped.', 'repair': 'Move the title inside the image.'}]
    return {'verdict': verdict, 'summary': 'Inspected synthetic task evidence.',
            'issues': issues, 'checks': [{'criterion': 'Requested title',
                'status': 'pass' if verdict == 'pass' else 'fail', 'evidence': 'Visible in supplied image.'}],
            'limitations': ['Only supplied visual evidence was inspected.']}


def completed(thread='producer', status='completed'):
    return {'method': 'turn/completed', 'params': {'threadId': thread,
            'turn': {'id': thread + '-turn', 'status': status}}}


class FakeClient:
    def __init__(self, factory, options):
        self.factory, self.options, self.calls = factory, options, []
        self.events = asyncio.Queue()
        self.closed = False
        self.role = None

    async def __aenter__(self):
        if self.factory.account_error:
            raise RuntimeError('private account error token=DO_NOT_RETURN')
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def request(self, method, params):
        self.calls.append((method, params))
        if method == 'config/read':
            return {'config': {'mcp_servers': self.factory.servers}}
        if method == 'thread/start':
            self.role = 'producer' if params.get('dynamicTools') else 'review'
            return {'model': MODEL, 'modelProvider': 'openai', 'thread': {'id': self.role}}
        if method == 'turn/start':
            if self.role == 'review':
                value = self.factory.verdicts.pop(0)
                self.events.put_nowait({'method': 'item/completed', 'params': {
                    'threadId': 'review', 'turnId': 'review-turn', 'item': {
                        'type': 'agentMessage', 'phase': 'final_answer', 'text': json.dumps(value)}}})
                self.events.put_nowait(completed('review'))
            elif self.factory.plan is not None:
                await self.factory.plan(self)
            return {'turn': {'id': self.role + '-turn'}}
        return {}

    async def tool(self, name, **args):
        response = await self.options['tool_handler']({'tool': name, 'arguments': args,
            'threadId': 'producer', 'turnId': 'producer-turn'})
        return json.loads(response['contentItems'][0]['text'])


class FakeFactory:
    def __init__(self, plan=None, verdicts=None):
        self.plan, self.verdicts = plan, list(verdicts or [review()])
        self.clients = []
        self.servers = {'private_server': {'enabled': True}}
        self.account_error = False

    def __call__(self, **options):
        client = FakeClient(self, options)
        self.clients.append(client)
        return client


async def inspect_fixture(tools, path):
    return {'sha256': hashlib.sha256(tools.read(tools.path(path))).hexdigest(),
            'decode_status': 'complete', 'type': 'image', 'size': 100,
            'evidence': [{'label': 'Actual bytes', 'image_url': IMAGE}]}


class PilotRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.workspace = Path(self.folder.name) / 'task'
        self.workspace.mkdir()
        self.output = self.workspace / 'result.png'
        self.output.write_bytes(PNG_BYTES)
        self.source = self.workspace / 'source.png'
        self.source.write_bytes(PNG_BYTES + b'source')
        self.task = {'id': 'synthetic', 'brief': 'Create a readable title image from source.png.',
                     'output_name': 'result.png', 'reference_paths': [str(self.source)],
                     'source_hashes': {str(self.source): hashlib.sha256(self.source.read_bytes()).hexdigest()}}
        self.inspect_patch = patch.object(_PilotTools, 'inspect', inspect_fixture)
        self.inspect_patch.start()
        self.addCleanup(self.inspect_patch.stop)

    def runtime(self, factory, timeout=2):
        return PilotRuntime(runtime_home='/explicit-authorized-runtime-home',
                            timeout=timeout, client_factory=factory)

    async def passing_plan(self, client):
        self.assertTrue((await client.tool('oak_review_artifact', path='/workspace/result.png'))['ok'])
        self.assertTrue((await client.tool('oak_send_file', path='result.png'))['queued_for_delivery'])
        client.events.put_nowait(completed())

    async def test_real_controller_prompt_and_frozen_reviewer_use_separate_scopes(self):
        factory = FakeFactory(self.passing_plan)
        result = await self.runtime(factory).run(self.task, self.workspace, 'Only a candidate procedure.')
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(result['delivered'])
        snapshot = Path(result['artifact_path'])
        self.assertEqual(snapshot.read_bytes(), self.output.read_bytes())
        self.assertFalse(snapshot.is_relative_to(self.workspace))
        self.assertEqual(result['artifact_sha256'], hashlib.sha256(snapshot.read_bytes()).hexdigest())
        self.assertEqual(len(factory.clients), 2)
        producer, reviewer = factory.clients
        self.assertTrue(producer.options['config']['features.code_mode_host'])
        self.assertFalse(producer.options['config']['features.code_mode'])
        self.assertFalse(reviewer.options['config']['features.code_mode_host'])
        for client in factory.clients:
            self.assertEqual(client.options['home'], '/explicit-authorized-runtime-home')
            self.assertEqual(client.options['sandbox'], 'read-only')
            self.assertFalse(client.options['config']['features.shell_tool'])
            self.assertFalse(client.options['config']['features.apps'])
            self.assertFalse(client.options['config']['features.image_generation'])
            self.assertTrue(client.closed)
            start = next(params for method, params in client.calls if method == 'thread/start')
            self.assertTrue(start['ephemeral'])
            self.assertFalse(start['config']['mcp_servers.private_server.enabled'])
        producer_start = next(p for m, p in producer.calls if m == 'thread/start')
        prompt = producer_start['developerInstructions']
        self.assertIn('Use oak_experience to retrieve and update relevant reusable working methods.', prompt)
        self.assertIn('For images and videos use oak_review_artifact', prompt)
        self.assertIn('Only a candidate procedure.', prompt)
        self.assertEqual(result['context_sha256'], hashlib.sha256(prompt.encode()).hexdigest())
        reviewer_start = next(p for m, p in reviewer.calls if m == 'thread/start')
        self.assertEqual(reviewer_start['dynamicTools'], [])
        reviewer_turn = next(p for m, p in reviewer.calls if m == 'turn/start')
        self.assertNotIn('candidate procedure', json.dumps(reviewer_turn))
        self.assertIn(self.task['brief'], json.dumps(reviewer_turn))
        self.assertNotIn('developerInstructions', result)

    async def test_completed_claim_without_independent_delivery_is_unverified(self):
        async def plan(client):
            denied = await client.tool('oak_send_file', path='result.png')
            self.assertFalse(denied['queued_for_delivery'])
            client.events.put_nowait(completed())
        result = await self.runtime(FakeFactory(plan)).run(self.task, self.workspace)
        self.assertEqual(result['status'], 'unverified')
        self.assertFalse(result['delivered'])
        self.assertIsNone(result['artifact_path'])

    async def test_repair_loop_reviews_new_bytes_and_does_not_reuse_old_pass(self):
        async def plan(client):
            first = await client.tool('oak_review_artifact', path='result.png')
            self.assertEqual(first['verdict'], 'revise')
            self.assertFalse((await client.tool('oak_send_file', path='result.png'))['ok'])
            self.output.write_bytes(PNG_BYTES + b'repaired')
            self.assertTrue((await client.tool('oak_review_artifact', path='result.png'))['ok'])
            self.output.write_bytes(PNG_BYTES + b'unreviewed change')
            self.assertFalse((await client.tool('oak_send_file', path='result.png'))['ok'])
            self.output.write_bytes(PNG_BYTES + b'repaired')
            self.assertTrue((await client.tool('oak_send_file', path='result.png'))['ok'])
            client.events.put_nowait(completed())
        result = await self.runtime(FakeFactory(plan, [review('revise'), review()])).run(self.task, self.workspace)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual([r['verdict'] for r in result['reviews']], ['revise', 'pass'])

    async def test_reviewer_budget_is_fixed_at_four(self):
        async def plan(client):
            for i in range(5):
                self.output.write_bytes(PNG_BYTES + bytes([i]))
                result = await client.tool('oak_review_artifact', path='result.png')
            self.assertEqual(result['error_code'], 'review_budget_exhausted')
            client.events.put_nowait(completed())
        factory = FakeFactory(plan, [review('revise')] * 4)
        result = await self.runtime(factory).run(self.task, self.workspace)
        self.assertEqual(len(result['reviews']), 4)
        self.assertEqual(len(factory.clients), 5)
        self.assertFalse(result['delivered'])

    async def test_source_mutation_invalidates_run_even_after_a_pass(self):
        async def plan(client):
            await self.passing_plan(client)
            self.source.write_bytes(b'changed source')
        result = await self.runtime(FakeFactory(plan)).run(self.task, self.workspace)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['source_immutable'])
        self.assertFalse(result['delivered'])
        self.assertIsNone(result['artifact_path'])

    async def test_unknown_dynamic_tool_and_native_tool_use_are_denied(self):
        async def unknown(client):
            result = await client.tool('oak_browser', action='open', url='https://example.test')
            self.assertEqual(result['error_code'], 'pilot_tool_not_allowed')
            client.events.put_nowait(completed())
        async def native(client):
            client.events.put_nowait({'method': 'item/started', 'params': {'threadId': 'producer',
                'turnId': 'producer-turn', 'item': {'type': 'commandExecution'}}})
        for plan in (unknown, native):
            result = await self.runtime(FakeFactory(plan)).run(self.task, self.workspace)
            self.assertEqual(result['error_code'], 'producer_tool_violation')
            self.assertFalse(result['delivered'])

    async def test_failed_turn_never_becomes_pass_after_local_delivery(self):
        async def plan(client):
            await client.tool('oak_review_artifact', path='result.png')
            await client.tool('oak_send_file', path='result.png')
            client.events.put_nowait(completed(status='failed'))
        result = await self.runtime(FakeFactory(plan)).run(self.task, self.workspace)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['delivered'])

    async def test_timeout_is_bounded_does_not_retry_and_closes_client(self):
        factory = FakeFactory()
        result = await self.runtime(factory, timeout=.02).run(self.task, self.workspace)
        self.assertEqual(result['status'], 'timeout')
        self.assertEqual(result['error_code'], 'producer_timeout')
        self.assertEqual(len(factory.clients), 1)
        self.assertTrue(factory.clients[0].closed)
        self.assertIn('turn/interrupt', [m for m, _ in factory.clients[0].calls])

    async def test_account_diagnostics_and_ambiguous_mcp_never_escape(self):
        for account_error in (True, False):
            factory = FakeFactory(self.passing_plan)
            factory.account_error = account_error
            factory.servers = {'name.with.dots': {}}
            result = await self.runtime(factory).run(self.task, self.workspace)
            self.assertEqual(result['status'], 'failed')
            self.assertNotIn('DO_NOT_RETURN', json.dumps(result))
            self.assertNotIn('thread/start', [m for c in factory.clients for m, _ in c.calls])

    async def test_every_run_starts_with_empty_conversation_memory(self):
        async def plan(client):
            found = await client.tool('oak_experience', action='search')
            self.assertEqual(found['methods'], [])
            await client.tool('oak_experience', action='save', key='local', title='Only this run',
                when_to_use='Synthetic task', procedure='Inspect local bytes.')
            client.events.put_nowait(completed())
        factory = FakeFactory(plan)
        runtime = self.runtime(factory)
        await runtime.run(self.task, self.workspace)
        await runtime.run(self.task, self.workspace)
        starts = [p for c in factory.clients for m, p in c.calls if m == 'thread/start']
        self.assertEqual(len(starts), 2)
        self.assertNotIn('Only this run', starts[1]['developerInstructions'])

    async def test_render_tool_is_the_only_route_to_python_and_paths_stay_local(self):
        def render(workspace, code, timeout):
            self.assertEqual(workspace, self.workspace)
            self.assertEqual(code, 'print("safe")')
            return {'ok': True, 'stdout': 'safe', 'stderr': ''}
        async def plan(client):
            result = await client.tool('oak_pilot_render', code='print("safe")')
            self.assertEqual(result['stdout'], 'safe')
            result = await client.tool('oak_pilot_inspect', path='/etc/passwd')
            self.assertFalse(result['ok'])
            client.events.put_nowait(completed())
        with patch.dict('sys.modules', {'oak.media_pilot_render': SimpleNamespace(render_python=render)}):
            result = await self.runtime(FakeFactory(plan)).run(self.task, self.workspace)
        self.assertEqual(result['tool_counts']['oak_pilot_render'], 1)
        self.assertEqual(len(result['render_calls']), 1)

    async def test_two_overlapping_runs_keep_memory_reviews_bytes_and_contexts_separate(self):
        other_workspace = Path(self.folder.name) / 'other-task'
        other_workspace.mkdir()
        other_source = other_workspace / 'source.png'
        other_source.write_bytes(PNG_BYTES + b'other source')
        other_output = other_workspace / 'result.png'
        other_output.write_bytes(PNG_BYTES + b'other output')
        other_task = {**self.task, 'id': 'other-synthetic',
            'brief': 'Create a second independent title image from source.png.',
            'reference_paths': [str(other_source)],
            'source_hashes': {str(other_source): hashlib.sha256(other_source.read_bytes()).hexdigest()}}
        both_producers_started = asyncio.Event()
        started = 0

        async def overlapping_plan(client):
            nonlocal started
            tools = client.options['tool_handler'].__self__
            title = tools.workspace.name
            initial = await client.tool('oak_experience', action='search')
            self.assertEqual(initial['methods'], [])
            saved = await client.tool('oak_experience', action='save', key='same local key',
                title=title, when_to_use='This synthetic task only.', procedure='Inspect its local output.')
            self.assertTrue(saved['ok'])
            started += 1
            if started == 2:
                both_producers_started.set()
            # Neither producer can finish before both have written the same
            # memory key in their independent conversation databases.
            await both_producers_started.wait()
            found = await client.tool('oak_experience', action='search')
            self.assertEqual([row['title'] for row in found['methods']], [title])
            self.assertTrue((await client.tool('oak_review_artifact', path='result.png'))['ok'])
            self.assertTrue((await client.tool('oak_send_file', path='result.png'))['ok'])
            client.events.put_nowait(completed())

        factory = FakeFactory(overlapping_plan, [review(), review()])
        runtime = self.runtime(factory, timeout=5)
        left, right = await asyncio.gather(
            runtime.run(self.task, self.workspace, 'LEFT_PROCEDURE'),
            runtime.run(other_task, other_workspace, 'RIGHT_PROCEDURE'))
        self.assertEqual(started, 2)
        for result, output in ((left, self.output), (right, other_output)):
            self.assertEqual(result['status'], 'completed')
            self.assertTrue(result['delivered'])
            self.assertTrue(result['source_immutable'])
            self.assertEqual(result['tool_calls'], 5)
            self.assertEqual(len(result['reviews']), 1)
            self.assertEqual(Path(result['artifact_path']).read_bytes(), output.read_bytes())
            self.assertEqual(result['reviews'][0]['sha256'], result['artifact_sha256'])
        self.assertNotEqual(left['context_sha256'], right['context_sha256'])
        self.assertNotEqual(left['artifact_sha256'], right['artifact_sha256'])
        self.assertNotEqual(Path(left['artifact_path']).parent, Path(right['artifact_path']).parent)
        self.assertNotEqual(left['reviews'][0]['review_id'], right['reviews'][0]['review_id'])
        producers = [client for client in factory.clients if client.role == 'producer']
        reviewers = [client for client in factory.clients if client.role == 'review']
        self.assertEqual((len(producers), len(reviewers)), (2, 2))
        self.assertIsNot(producers[0].options['tool_handler'].__self__,
                         producers[1].options['tool_handler'].__self__)
        for client in factory.clients:
            self.assertTrue(client.closed)
            self.assertEqual(client.options['home'], '/explicit-authorized-runtime-home')
        review_inputs = [json.dumps(next(params['input'] for method, params in client.calls
                                        if method == 'turn/start')) for client in reviewers]
        self.assertTrue(any(self.task['brief'] in value for value in review_inputs))
        self.assertTrue(any(other_task['brief'] in value for value in review_inputs))
        for value in review_inputs:
            self.assertNotIn('LEFT_PROCEDURE', value)
            self.assertNotIn('RIGHT_PROCEDURE', value)
            self.assertFalse(self.task['brief'] in value and other_task['brief'] in value)


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ('PIL', 'numpy'))
                     and Path('/usr/bin/ffmpeg').is_file() and Path('/usr/bin/ffprobe').is_file(),
                     'Provisioned Pillow, numpy and distro ffmpeg/ffprobe are required')
class PilotCodecFactsTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_mp4_codec_and_container_brands_are_bound_to_decoded_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            workspace, state = root / 'workspace', root / 'control'
            workspace.mkdir()
            state.mkdir()
            # Use a non-media extension: the actual container/codec must be
            # extracted from these bytes, rather than inferred from the path.
            path = workspace / 'candidate.data'
            subprocess.run(['/usr/bin/ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi',
                '-i', 'testsrc2=size=96x64:rate=6:duration=1', '-an', '-c:v', 'libx264',
                '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-f', 'mp4', str(path)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=20, env=codec_environment())
            tools = _PilotTools(SimpleNamespace(), {'brief': 'Inspect this actual video.'},
                                workspace, state, None)
            try:
                data = await tools.inspect(path)
                expected = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(data['type'], 'video')
                self.assertEqual(data['decode_status'], 'complete')
                self.assertTrue(data['evidence'])
                facts = data['codec_facts']
                self.assertEqual(data['sha256'], expected)
                self.assertEqual(facts['sha256'], expected)
                self.assertIn('mp4', facts['format_name'].split(','))
                self.assertEqual(facts['format_tags']['major_brand'], 'isom')
                self.assertIn('avc1', facts['format_tags']['compatible_brands'])
                self.assertEqual(facts['streams'], [{'codec_name': 'h264', 'codec_type': 'video'}])
                self.assertFalse(data['audio_present'])
                self.assertEqual(tools.files.handles, {})
            finally:
                tools.files.close_all()


if __name__ == '__main__':
    unittest.main()
