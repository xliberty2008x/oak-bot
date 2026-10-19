"""Protocol and integrity checks; these tests make no model calls."""

import asyncio
import base64
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from oak.media_pilot_runtime import (
    PilotRuntime, _PilotTools, _canonical_sha, _image_codec_facts, _video_codec_facts)
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
        self.assertEqual(result['candidate_path'], result['artifact_path'])
        self.assertEqual(result['candidate_sha256'], result['artifact_sha256'])
        self.assertTrue(result['candidate_complete'])
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
        candidate = Path(result['candidate_path'])
        self.assertEqual(candidate.read_bytes(), self.output.read_bytes())
        self.assertEqual(result['candidate_sha256'], hashlib.sha256(candidate.read_bytes()).hexdigest())
        self.assertFalse(candidate.is_relative_to(self.workspace))
        self.assertEqual(candidate.stat().st_mode & 0o777, 0o400)
        self.assertTrue(result['candidate_complete'])
        self.output.write_bytes(b'changed after run')
        self.assertEqual(candidate.read_bytes(), PNG_BYTES)

    def specification_task(self, raw):
        path = self.workspace / 'scene.json'
        path.write_bytes(raw)
        return {**self.task, 'specification_path': str(path),
                'source_hashes': {**self.task['source_hashes'],
                                  str(path): hashlib.sha256(raw).hexdigest()}}

    async def test_original_hashed_specification_reaches_both_review_arms_without_producer_overrides(self):
        specification = {'layers': [{'asset': 'original.png', 'position': [12, 18]}],
                         'timing': {'frame_count': 36, 'fps': 12},
                         'requirement': 'SUPPLIED_ORIGINAL_SPECIFICATION'}
        raw = json.dumps(specification, ensure_ascii=False, indent=2).encode()
        task = self.specification_task(raw)

        async def plan(client):
            report = await client.tool('oak_review_artifact', path='result.png',
                requirements='PRODUCER_OVERRIDE_CANARY', reference_paths=['forged-reference.png'])
            self.assertTrue(report['ok'])
            await client.tool('oak_send_file', path='result.png')
            client.events.put_nowait(completed())

        review_inputs = []
        for procedure in ('', 'Inspect local edits before final review.'):
            factory = FakeFactory(plan)
            result = await self.runtime(factory).run(task, self.workspace, procedure)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['adapter'], 'oak-controller-with-offline-bwrap-media-tools-v3')
            reviewer = next(client for client in factory.clients if client.role == 'review')
            inputs = next(params['input'] for method, params in reviewer.calls if method == 'turn/start')
            metadata = json.loads(inputs[0]['text'])
            self.assertEqual(metadata['owner_brief'], task['brief'])
            self.assertEqual(metadata['extraction_facts']['supplied_requirements_specification'], {
                'path': '/workspace/scene.json', 'sha256': hashlib.sha256(raw).hexdigest(),
                'content': specification})
            self.assertNotIn('PRODUCER_OVERRIDE_CANARY', json.dumps(inputs))
            self.assertNotIn('forged-reference.png', json.dumps(inputs))
            review_inputs.append(inputs)
        self.assertEqual(review_inputs[0], review_inputs[1])

    def manifest_specification(self):
        return {'unscoped_private_to_subreview': 'FULL_SPEC_ONLY_CANARY',
                'review_manifest': {
                    'visual': {'brief': 'Assess visible source identity, coherence and layer order.',
                               'specification': {'source_identity': 'ÉTUDE original',
                                                 'visible_checks': ['No cutout seams.', 'Preserve labels.']}},
                    'mechanical': {'error_tolerance': 6, 'marker': 'MECHANICAL_ONLY_CANARY'}}}

    def manifest_task(self, content=None):
        content = self.manifest_specification() if content is None else content
        task = self.specification_task(json.dumps(content, ensure_ascii=False).encode())
        task.update(brief='Create the requested result. Read scene.json. FULL_TASK_ONLY_CANARY',
                    reference_roles={str(self.source): 'original scene'})
        return task

    async def test_public_manifest_scopes_only_original_visual_projection_and_hashes_each_section(self):
        specification = self.manifest_specification()
        task = self.manifest_task(specification)
        originals = []
        for name, role in (('scene.png', 'damaged print'), ('donor.png', 'original source artwork'),
                           ('guide.png', 'placement guide')):
            path = self.workspace / name
            path.write_bytes(PNG_BYTES + name.encode())
            originals.append((path, role))
        task.update(reference_paths=[str(path) for path, _ in originals],
                    reference_roles={str(path): role for path, role in originals})
        task['source_hashes'].update({str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                      for path, _ in originals})

        async def plan(client):
            tools = client.options['tool_handler'].__self__
            # The complete original public task/specification stays available
            # in the producer's own task filesystem, including mechanical rules.
            self.assertEqual(json.loads(tools.read(task['specification_path'])), specification)
            result = await client.tool('oak_review_artifact', path='result.png',
                requirements='PRODUCER_SCOPE_OVERRIDE', reference_paths=['forged-reference.png'])
            self.assertTrue(result['ok'])
            await client.tool('oak_send_file', path='result.png')
            client.events.put_nowait(completed())

        reviewer_inputs = []
        for procedure in ('', 'Check visible source fidelity.'):
            factory = FakeFactory(plan)
            result = await self.runtime(factory).run(task, self.workspace, procedure)
            self.assertEqual(result['status'], 'completed')
            original_sha = hashlib.sha256(Path(task['specification_path']).read_bytes()).hexdigest()
            visual = specification['review_manifest']['visual']
            mechanical = specification['review_manifest']['mechanical']
            self.assertEqual(result['specification_sha256'], original_sha)
            self.assertEqual(result['visual_requirements_sha256'], _canonical_sha(visual))
            self.assertEqual(result['mechanical_requirements_sha256'], _canonical_sha(mechanical))
            producer = next(client for client in factory.clients if client.role == 'producer')
            producer_turn = next(params for method, params in producer.calls if method == 'turn/start')
            self.assertIn('FULL_TASK_ONLY_CANARY', json.dumps(producer_turn))
            reviewer = next(client for client in factory.clients if client.role == 'review')
            inputs = next(params['input'] for method, params in reviewer.calls if method == 'turn/start')
            request = json.loads(inputs[0]['text'])
            self.assertEqual(request['owner_brief'], visual['brief'])
            metadata = request['extraction_facts']
            self.assertEqual(metadata['supplied_requirements_specification'], {
                'path': '/workspace/scene.json', 'source_sha256': original_sha,
                'projection': 'review_manifest.visual.specification',
                'visual_requirements_sha256': _canonical_sha(visual), 'content': visual['specification']})
            for item, (path, role) in zip(metadata['references'], originals, strict=True):
                digest = task['source_hashes'][str(path)]
                self.assertEqual(item['source_path'], '/workspace/' + path.name)
                self.assertEqual(item['source_role'], role)
                self.assertEqual(item['sha256'], digest)
                self.assertTrue(any(role in row.get('text', '') and path.name in row.get('text', '')
                                    and digest in row.get('text', '') for row in inputs))
            serialized = json.dumps(inputs)
            for canary in ('FULL_TASK_ONLY_CANARY', 'FULL_SPEC_ONLY_CANARY',
                           'MECHANICAL_ONLY_CANARY', 'PRODUCER_SCOPE_OVERRIDE', 'forged-reference.png'):
                self.assertNotIn(canary, serialized)
            reviewer_inputs.append(inputs)
        self.assertEqual(reviewer_inputs[0], reviewer_inputs[1])

    async def test_manifest_hashes_are_canonical_and_include_visual_brief(self):
        content = self.manifest_specification()
        specification_changed = copy.deepcopy(content)
        specification_changed['review_manifest']['visual']['specification']['source_identity'] = 'Changed original'
        outcomes = []
        for changed, kwargs in ((content, {}), (content, {'sort_keys': True, 'indent': 2}),
                ({**content, 'review_manifest': {**content['review_manifest'],
                    'visual': {**content['review_manifest']['visual'], 'brief': 'A changed visual brief.'}}}, {}),
                ({**content, 'review_manifest': {**content['review_manifest'],
                    'mechanical': {'error_tolerance': 4}}}, {})):
            task = self.manifest_task(changed)
            raw = json.dumps(changed, ensure_ascii=False, **kwargs).encode()
            Path(task['specification_path']).write_bytes(raw)
            task['source_hashes'][task['specification_path']] = hashlib.sha256(raw).hexdigest()
            factory = FakeFactory(self.passing_plan)
            result = await self.runtime(factory).run(task, self.workspace)
            self.assertEqual(result['status'], 'completed')
            outcomes.append(result)
        first, reformatted, visual_changed, mechanical_changed = outcomes
        self.assertNotEqual(first['specification_sha256'], reformatted['specification_sha256'])
        for key in ('visual_requirements_sha256', 'mechanical_requirements_sha256'):
            self.assertEqual(first[key], reformatted[key])
        self.assertNotEqual(first['visual_requirements_sha256'], visual_changed['visual_requirements_sha256'])
        self.assertEqual(first['mechanical_requirements_sha256'], visual_changed['mechanical_requirements_sha256'])
        self.assertEqual(first['visual_requirements_sha256'], mechanical_changed['visual_requirements_sha256'])
        self.assertNotEqual(first['mechanical_requirements_sha256'], mechanical_changed['mechanical_requirements_sha256'])
        task = self.manifest_task(specification_changed)
        changed = await self.runtime(FakeFactory(self.passing_plan)).run(task, self.workspace)
        self.assertNotEqual(first['visual_requirements_sha256'], changed['visual_requirements_sha256'])
        self.assertEqual(first['mechanical_requirements_sha256'], changed['mechanical_requirements_sha256'])
        expected = hashlib.sha256(json.dumps(content['review_manifest']['visual'],
            sort_keys=True, ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()
        self.assertEqual(first['visual_requirements_sha256'], expected)

    async def test_invalid_public_review_manifests_are_denied_before_model_scope(self):
        visual = self.manifest_specification()['review_manifest']['visual']
        manifests = (None, {}, {'visual': visual}, {'visual': visual, 'mechanical': [],},
            {'visual': visual, 'mechanical': {}, 'extra': {}},
            *({'visual': {**visual, **change}, 'mechanical': {}} for change in (
                {'brief': None}, {'brief': ''}, {'brief': ' '}, {'brief': 'x' * 16001},
                {'brief': ' ' * 16000 + 'x'}, {'specification': []}, {'extra': 'not permitted'})))
        for manifest in manifests:
            with self.subTest(manifest=str(manifest)[:100]):
                content = {'review_manifest': manifest}
                task = self.manifest_task(content)
                factory = FakeFactory(self.passing_plan)
                result = await self.runtime(factory).run(task, self.workspace)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(factory.clients, [])

    async def test_reference_roles_require_complete_original_hash_bound_paths(self):
        for roles, remove_hash in (({}, False), ([], False),
                ({'/workspace/unknown.png': 'unknown original'}, False),
                ({str(self.source): ''}, False), ({str(self.source): 'x' * 81}, False),
                ({str(self.source): 'injected\nrole'}, False), ({str(self.source): 3}, False),
                ({str(self.source): 'original scene'}, True),
                ({str(self.source): 'original scene', '/workspace/source.png': 'alias'}, False)):
            with self.subTest(roles=roles, missing_hash=remove_hash):
                task = self.manifest_task()
                task['reference_roles'] = roles
                if remove_hash:
                    task['source_hashes'].pop(str(self.source))
                factory = FakeFactory(self.passing_plan)
                result = await self.runtime(factory).run(task, self.workspace)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(factory.clients, [])

    async def test_specification_must_be_explicitly_allowlisted_and_match_source_hash(self):
        task = self.specification_task(b'{"production":true}')
        for modified in (
                {**task, 'source_hashes': self.task['source_hashes']},
                {**task, 'source_hashes': {**task['source_hashes'], task['specification_path']: '0' * 64}},
                {**task, 'specification_path': '/outside/scene.json'}):
            factory = FakeFactory(self.passing_plan)
            result = await self.runtime(factory).run(modified, self.workspace)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(factory.clients, [])

    async def test_invalid_or_oversized_specification_fails_before_any_model_scope(self):
        for raw in (b'\xff', b'{', b'[]', b'{"x":NaN}', b'{"x":Infinity}',
                    b'{"x":"' + b'x' * (64 * 1024) + b'"}'):
            with self.subTest(size=len(raw), prefix=raw[:12]):
                task = self.specification_task(raw)
                factory = FakeFactory(self.passing_plan)
                result = await self.runtime(factory).run(task, self.workspace)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(factory.clients, [])

    async def test_bounded_specification_accepts_exact_64kib_file_without_relaxing_review_limits(self):
        raw = b'{}' + b' ' * (64 * 1024 - 2)
        task = self.specification_task(raw)
        factory = FakeFactory(self.passing_plan)
        result = await self.runtime(factory).run(task, self.workspace)
        self.assertEqual(result['status'], 'completed')
        reviewer = next(client for client in factory.clients if client.role == 'review')
        inputs = next(params['input'] for method, params in reviewer.calls if method == 'turn/start')
        forwarded = json.loads(inputs[0]['text'])['extraction_facts']['supplied_requirements_specification']
        self.assertEqual(forwarded['content'], {})
        self.assertEqual(forwarded['sha256'], hashlib.sha256(raw).hexdigest())

    async def test_specification_mutation_prevents_review_and_candidate_credit(self):
        task = self.specification_task(b'{"production":"original"}')

        async def plan(client):
            Path(task['specification_path']).write_bytes(b'{"production":"changed"}')
            result = await client.tool('oak_review_artifact', path='result.png')
            self.assertFalse(result['ok'])
            client.events.put_nowait(completed())

        factory = FakeFactory(plan)
        result = await self.runtime(factory).run(task, self.workspace)
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['source_immutable'])
        self.assertFalse(result['delivered'])
        self.assertIsNone(result['candidate_path'])
        self.assertEqual(len(factory.clients), 1)

    async def test_rejected_output_remains_available_without_independent_pass_or_delivery(self):
        async def plan(client):
            rejected = await client.tool('oak_review_artifact', path='result.png')
            self.assertEqual(rejected['verdict'], 'revise')
            client.events.put_nowait(completed())
        result = await self.runtime(FakeFactory(plan, [review('revise')])).run(self.task, self.workspace)
        self.assertEqual(result['status'], 'unverified')
        self.assertFalse(result['delivered'])
        self.assertIsNone(result['artifact_path'])
        self.assertTrue(result['candidate_complete'])
        self.assertEqual(Path(result['candidate_path']).read_bytes(), PNG_BYTES)

    async def test_final_candidate_does_not_reuse_previously_delivered_bytes(self):
        async def plan(client):
            await self.passing_plan(client)
            self.output.write_bytes(PNG_BYTES + b'unreviewed final candidate')
        result = await self.runtime(FakeFactory(plan)).run(self.task, self.workspace)
        self.assertTrue(result['delivered'])
        self.assertEqual(Path(result['artifact_path']).read_bytes(), PNG_BYTES)
        self.assertEqual(Path(result['candidate_path']).read_bytes(), self.output.read_bytes())
        self.assertNotEqual(result['candidate_sha256'], result['artifact_sha256'])

    async def test_missing_unsafe_or_oversized_requested_output_never_supplies_candidate(self):
        async def plan(client):
            client.events.put_nowait(completed())
        for name in ('missing.png', 'linked.png', '../outside.png', 'source.png'):
            with self.subTest(name=name):
                if name == 'linked.png':
                    (self.workspace / name).symlink_to(self.output)
                task = {**self.task, 'output_name': name}
                result = await self.runtime(FakeFactory(plan)).run(task, self.workspace)
                self.assertIsNone(result['candidate_path'])
                self.assertIsNone(result['candidate_sha256'])
                self.assertFalse(result['candidate_complete'])
                self.assertFalse(result['delivered'])
        with patch('oak.media_pilot_runtime._MAX_ARTIFACT', len(PNG_BYTES) - 1):
            # Avoid oversized source rejection so the output snapshot itself
            # supplies the failure under test.
            task = {**self.task, 'reference_paths': [], 'source_hashes': {}}
            result = await self.runtime(FakeFactory(plan)).run(task, self.workspace)
        self.assertIsNone(result['candidate_path'])
        self.assertFalse(result['delivered'])

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
        self.assertIsNone(result['candidate_path'])
        self.assertFalse(result['candidate_complete'])

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
        self.assertEqual(Path(result['candidate_path']).read_bytes(), PNG_BYTES)
        self.assertFalse(result['candidate_complete'])

    async def test_timeout_is_bounded_does_not_retry_and_closes_client(self):
        factory = FakeFactory()
        result = await self.runtime(factory, timeout=.02).run(self.task, self.workspace)
        self.assertEqual(result['status'], 'timeout')
        self.assertEqual(result['error_code'], 'producer_timeout')
        self.assertEqual(len(factory.clients), 1)
        self.assertTrue(factory.clients[0].closed)
        self.assertIn('turn/interrupt', [m for m, _ in factory.clients[0].calls])
        self.assertEqual(Path(result['candidate_path']).read_bytes(), PNG_BYTES)
        self.assertFalse(result['candidate_complete'])
        self.assertFalse(result['delivered'])

    async def test_timeout_waits_for_render_worker_after_client_closes_before_snapshot(self):
        closed = threading.Event()
        finished = threading.Event()
        original_exit = FakeClient.__aexit__

        async def exit_client(client, *args):
            await original_exit(client, *args)
            closed.set()

        def render(workspace, code, timeout):
            self.output.write_bytes(PNG_BYTES + b'partial')
            if not closed.wait(2):
                raise AssertionError('Render cleanup started before native client shutdown.')
            self.output.write_bytes(PNG_BYTES + b'finished after client closed')
            finished.set()
            return {'ok': True, 'stdout': '', 'stderr': ''}

        async def plan(client):
            await client.tool('oak_pilot_render', code='render synthetic output')
            client.events.put_nowait(completed())

        factory = FakeFactory(plan)
        with patch.object(FakeClient, '__aexit__', exit_client), patch.dict(
                'sys.modules', {'oak.media_pilot_render': SimpleNamespace(render_python=render)}):
            result = await self.runtime(factory, timeout=.02).run(self.task, self.workspace)
        self.assertTrue(finished.is_set())
        self.assertEqual(result['status'], 'timeout')
        self.assertFalse(result['candidate_complete'])
        self.assertFalse(result['delivered'])
        self.assertEqual(Path(result['candidate_path']).read_bytes(), PNG_BYTES + b'finished after client closed')
        self.assertEqual(result['candidate_sha256'], hashlib.sha256(self.output.read_bytes()).hexdigest())

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


@unittest.skipUnless(importlib.util.find_spec('PIL'), 'Pillow is required for actual image facts')
class PilotImageFactsTests(unittest.IsolatedAsyncioTestCase):
    def image_bytes(self, mode='RGB', *, transparent=False, palette=False, unused_transparency=False):
        from PIL import Image
        image = Image.new('P' if palette else mode, (8, 6), 1 if palette else 'white')
        options = {}
        if palette:
            image.putpalette([255, 0, 0, 25, 70, 100] + [0] * (256 * 3 - 6))
            options['transparency'] = 0  # Index zero must not disappear in a truthiness check.
            if not unused_transparency:
                image.putpixel((0, 0), 0)
        elif transparent:
            image.putpixel((0, 0), (25, 70, 100, 0))
        output = io.BytesIO()
        image.save(output, format='PNG', **options)
        return output.getvalue()

    def test_png_original_mode_opacity_and_palette_transparency_come_from_encoded_bytes(self):
        cases = [({}, 'RGB', False, [255, 255], True),
                 ({'mode': 'RGBA'}, 'RGBA', True, [255, 255], True),
                 ({'mode': 'RGBA', 'transparent': True}, 'RGBA', True, [0, 255], False),
                 ({'palette': True}, 'P', True, [0, 255], False),
                 ({'palette': True, 'unused_transparency': True}, 'P', True, [255, 255], True)]
        for options, mode, alpha_present, alpha_extrema, opaque in cases:
            with self.subTest(options=options):
                raw = self.image_bytes(**options)
                digest = hashlib.sha256(raw).hexdigest()
                facts = _image_codec_facts(raw, digest)
                self.assertEqual(facts, {'sha256': digest, 'format': 'PNG', 'mode': mode,
                    'dimensions': {'width': 8, 'height': 6}, 'frame_count': 1,
                    'alpha_present': alpha_present, 'alpha_extrema': alpha_extrema,
                    'fully_opaque': opaque, 'decode_status': 'complete'})

    def test_image_facts_reject_hash_mismatch_invalid_bytes_and_decoding_limits(self):
        from PIL import Image
        raw = self.image_bytes()
        with self.assertRaises(ValueError):
            _image_codec_facts(raw + b'changed bytes', hashlib.sha256(raw).hexdigest())
        with self.assertRaises(OSError):
            _image_codec_facts(b'invalid image', hashlib.sha256(b'invalid image').hexdigest())
        with patch('oak.media_pilot_runtime.MAX_PIXELS', 1), self.assertRaises(ValueError):
            _image_codec_facts(raw, hashlib.sha256(raw).hexdigest())
        with patch('oak.media_pilot_runtime._MAX_ARTIFACT', len(raw) - 1), self.assertRaises(ValueError):
            _image_codec_facts(raw, hashlib.sha256(raw).hexdigest())
        animated = io.BytesIO()
        Image.new('RGB', (3, 3), 'white').save(animated, format='PNG', save_all=True,
            append_images=[Image.new('RGB', (3, 3), 'black')], duration=100, loop=0)
        data = animated.getvalue()
        with self.assertRaises(ValueError):
            _image_codec_facts(data, hashlib.sha256(data).hexdigest())

    async def test_real_image_inspection_preserves_encoded_facts_and_sha_despite_filename(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            workspace, state = root / 'workspace', root / 'control'
            workspace.mkdir()
            state.mkdir()
            raw = self.image_bytes(mode='RGBA', transparent=True)
            path = workspace / 'candidate.data'
            path.write_bytes(raw)
            tools = _PilotTools(SimpleNamespace(), {'brief': 'Inspect actual source image.'},
                                workspace, state, None)
            try:
                data = await tools.inspect(path)
                self.assertEqual(data['type'], 'image')
                self.assertEqual(data['decode_status'], 'complete')
                self.assertTrue(data['evidence'])
                self.assertEqual(data['image_facts'], _image_codec_facts(raw, hashlib.sha256(raw).hexdigest()))
                self.assertEqual(data['image_facts']['mode'], 'RGBA')
                self.assertEqual(tools.files.handles, {})
            finally:
                tools.files.close_all()

    async def test_image_mutation_between_visual_and_encoded_facts_invalidates_observation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            workspace, state = root / 'workspace', root / 'control'
            workspace.mkdir()
            state.mkdir()
            original = self.image_bytes()
            modified = self.image_bytes(mode='RGBA', transparent=True)
            tools = _PilotTools(SimpleNamespace(), {'brief': 'Inspect output and original source.'},
                                workspace, state, None)
            try:
                for name in ('result.png', 'source.png'):
                    with self.subTest(name=name):
                        path = workspace / name
                        path.write_bytes(original)

                        def changed(data, digest):
                            path.write_bytes(modified)
                            return _image_codec_facts(data, digest)

                        with patch('oak.media_pilot_runtime._image_codec_facts', side_effect=changed):
                            with self.assertRaises(ValueError):
                                await tools.inspect(path)
                        self.assertEqual(tools.files.handles, {})
            finally:
                tools.files.close_all()


class PilotCodecProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_pixel_format_is_a_validated_hash_bound_probe_fact(self):
        digest = hashlib.sha256(PNG_BYTES).hexdigest()
        for pixels in ('yuv420p', 'yuv444p', None, 'yuv420p; untrusted claim'):
            with self.subTest(pixels=pixels):
                video = {'codec_name': 'h264', 'codec_type': 'video'}
                if pixels is not None:
                    video['pix_fmt'] = pixels
                payload = {'format': {'format_name': 'mov,mp4', 'tags': {'major_brand': 'isom'}},
                           'streams': [video, {'codec_name': 'aac', 'codec_type': 'audio'}]}
                process = SimpleNamespace(returncode=0, wait=AsyncMock(return_value=0),
                    stdin=SimpleNamespace(write=Mock(), drain=AsyncMock(), close=Mock(),
                                          wait_closed=AsyncMock()),
                    stdout=SimpleNamespace(read=AsyncMock(side_effect=[json.dumps(payload).encode(), b''])))
                with patch('oak.media_pilot_runtime.asyncio.create_subprocess_exec',
                           new_callable=AsyncMock, return_value=process) as probe:
                    if pixels in {'yuv420p', 'yuv444p'}:
                        facts = await _video_codec_facts(PNG_BYTES, digest)
                        self.assertEqual(facts['sha256'], digest)
                        self.assertEqual(facts['streams'][0]['pix_fmt'], pixels)
                        self.assertNotIn('pix_fmt', facts['streams'][1])
                    else:
                        with self.assertRaises(ValueError):
                            await _video_codec_facts(PNG_BYTES, digest)
                self.assertTrue(any('stream=codec_name,codec_type,pix_fmt' in value
                                    for value in probe.await_args.args if isinstance(value, str)))


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
                '-threads', '1', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-f', 'mp4', str(path)],
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
                self.assertEqual(facts['streams'], [{'codec_name': 'h264', 'codec_type': 'video',
                                                    'pix_fmt': 'yuv420p'}])
                self.assertFalse(data['audio_present'])
                self.assertEqual(tools.files.handles, {})
            finally:
                tools.files.close_all()


if __name__ == '__main__':
    unittest.main()
