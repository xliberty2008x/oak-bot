import asyncio
import base64
import json
import unittest

from oak.quality import QualityReviewer, _parse_review


PNG = 'data:image/png;base64,' + base64.b64encode(
    b'\x89PNG\r\n\x1a\nsynthetic header for protocol tests').decode()


def verdict(**changes):
    return {'verdict': 'pass', 'summary': 'The requested result is visible.',
            'issues': [], 'checks': [{'criterion': 'Requested content',
                                      'status': 'pass', 'evidence': 'Visible in evidence 1.'}],
            'limitations': ['Only supplied images were inspected.'], **changes}


class FakeClient:
    def __init__(self, events, **kwargs):
        self.options = kwargs
        self.calls = []
        self.events = asyncio.Queue()
        for event in events:
            self.events.put_nowait(event)
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def request(self, method, params):
        self.calls.append((method, params))
        if method == 'config/read':
            return {'config': {'mcp_servers': {'private_service': {'enabled': True}}}}
        if method == 'thread/start':
            return {'model': params['model'], 'modelProvider': 'openai', 'thread': {'id': 'review'}}
        if method == 'turn/start':
            return {'turn': {'id': 'review-turn'}}
        return {}


def item(value, *, thread='review', turn='review-turn', method='item/completed'):
    return {'method': method, 'params': {'threadId': thread, 'turnId': turn, 'item': value}}


def completed(status='completed'):
    return {'method': 'turn/completed', 'params': {'threadId': 'review',
            'turn': {'id': 'review-turn', 'status': status}}}


class ReviewContracts(unittest.IsolatedAsyncioTestCase):
    def reviewer(self, events, *, timeout=1):
        self.clients = []

        def factory(**kwargs):
            client = FakeClient(events, **kwargs)
            self.clients.append(client)
            return client

        return QualityReviewer({'workspace': '/workspace', 'runtime_home': '/runtime',
                                'execution': {'profile': '/trusted/profile.json'}},
                               client_factory=factory, timeout=timeout)

    async def run_review(self, reviewer, **kwargs):
        return await reviewer.review('Make a readable title card.',
                                     [{'image_url': PNG, 'label': 'Actual candidate'}],
                                     {'sha256': 'a' * 64}, **kwargs)

    async def test_independent_images_selected_model_and_tools_disabled(self):
        reviewer = self.reviewer([
            item({'type': 'agentMessage', 'text': json.dumps(verdict(summary='Wrong thread'))}, thread='producer'),
            item({'type': 'agentMessage', 'text': json.dumps(verdict()), 'phase': 'final_answer'}),
            completed(),
        ])
        result = await self.run_review(reviewer, model='owner-selected-model', effort='ultra')
        self.assertEqual(result['verdict'], 'pass')
        self.assertEqual(result['model'], 'owner-selected-model')
        client = self.clients[0]
        self.assertEqual(client.options['sandbox'], 'read-only')
        self.assertFalse(client.options['config']['features.shell_tool'])
        self.assertFalse(client.options['config']['features.apps'])
        self.assertFalse(client.options['config']['features.plugins'])
        start = next(params for method, params in client.calls if method == 'thread/start')
        self.assertTrue(start['ephemeral'])
        self.assertEqual(start['dynamicTools'], [])
        self.assertEqual(start['config']['mcp_servers.private_service.enabled'], False)
        turn = next(params for method, params in client.calls if method == 'turn/start')
        self.assertEqual(turn['effort'], 'ultra')
        self.assertEqual(turn['model'], 'owner-selected-model')
        self.assertEqual(turn['input'][2]['url'], PNG)
        self.assertNotIn('path', turn['input'][2])
        self.assertIn('outputSchema', turn)
        self.assertTrue(client.closed)

    async def test_tool_attempt_invalidates_review_and_interrupts(self):
        reviewer = self.reviewer([
            item({'type': 'commandExecution', 'command': 'untrusted'}, method='item/started'),
            item({'type': 'agentMessage', 'text': json.dumps(verdict())}), completed(),
        ])
        result = await self.run_review(reviewer)
        self.assertEqual(result['verdict'], 'unverified')
        self.assertEqual(result['error_code'], 'review_attempted_tool_use')
        self.assertIn('turn/interrupt', [method for method, _ in self.clients[0].calls])

    async def test_completed_turn_without_valid_findings_cannot_pass(self):
        for response in ['done', '{}', json.dumps({'verdict': 'pass'})]:
            reviewer = self.reviewer([
                item({'type': 'agentMessage', 'text': response}), completed()])
            self.assertEqual((await self.run_review(reviewer))['verdict'], 'unverified')

    async def test_failed_turn_cannot_pass_even_after_positive_message(self):
        reviewer = self.reviewer([
            item({'type': 'agentMessage', 'text': json.dumps(verdict())}), completed('failed')])
        self.assertEqual((await self.run_review(reviewer))['verdict'], 'unverified')

    async def test_timeout_does_not_become_pass_or_retry(self):
        reviewer = self.reviewer([])
        result = await self.run_review(reviewer)
        self.assertEqual(result['error_code'], 'review_timeout')
        self.assertEqual(len(self.clients), 1)
        self.assertTrue(self.clients[0].closed)

    async def test_cancellation_remains_cancellation_and_closes_client(self):
        reviewer = self.reviewer([])
        task = asyncio.create_task(self.run_review(reviewer))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(self.clients[0].closed)

    async def test_external_image_urls_never_leave_for_review(self):
        reviewer = self.reviewer([])
        result = await reviewer.review('Review the result.', [
            {'image_url': 'https://private.example/token', 'label': 'Candidate'}], {})
        self.assertEqual(result['verdict'], 'unverified')
        self.assertEqual(self.clients, [])

    async def test_runtime_diagnostics_are_not_returned(self):
        def factory(**kwargs):
            raise RuntimeError('secret native account data')

        reviewer = QualityReviewer({}, client_factory=factory)
        result = await self.run_review(reviewer)
        self.assertEqual(result['error_code'], 'review_runtime_unavailable')
        self.assertNotIn('secret', json.dumps(result))

    async def test_ambiguous_mcp_name_never_starts_a_review_thread(self):
        class AmbiguousServerClient(FakeClient):
            async def request(self, method, params):
                if method == 'config/read':
                    return {'config': {'mcp_servers': {'name.with.dots': {'enabled': True}}}}
                return await super().request(method, params)

        client = AmbiguousServerClient([])
        reviewer = QualityReviewer({}, client_factory=lambda **kwargs: client)
        result = await self.run_review(reviewer)
        self.assertEqual(result['error_code'], 'review_tool_configuration_invalid')
        self.assertNotIn('thread/start', [method for method, _ in client.calls])


class VerdictContracts(unittest.TestCase):
    def test_claimed_pass_with_material_defect_requires_revision(self):
        result = _parse_review(json.dumps(verdict(issues=[{
            'severity': 'major', 'evidence': 'Title is clipped at the right edge.',
            'repair': 'Reflow the title inside the frame.',
        }])))
        self.assertEqual(result['verdict'], 'revise')

    def test_unknown_required_criterion_cannot_pass(self):
        result = _parse_review(json.dumps(verdict(checks=[{
            'criterion': 'Requested animation', 'status': 'unknown',
            'evidence': 'Only a single still was supplied.',
        }])))
        self.assertEqual(result['verdict'], 'unverified')

    def test_empty_or_unsupported_verdict_cannot_pass(self):
        self.assertEqual(_parse_review(json.dumps(verdict(checks=[])))['verdict'], 'unverified')
        self.assertEqual(_parse_review(json.dumps(verdict(verdict='revise', checks=[])))['verdict'], 'unverified')


if __name__ == '__main__':
    unittest.main()
