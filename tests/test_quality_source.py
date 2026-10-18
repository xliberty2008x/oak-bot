from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from oak.quality_gate import QualityGate, digest, required_source

# Reuse the existing local protocol fixture; no real runtime/provider is called.
import test_quality_gate as fixture


class RequiredSourceTests(unittest.IsolatedAsyncioTestCase):
    inspect = fixture.QualityGateTests.inspect
    verdict = staticmethod(fixture.QualityGateTests.verdict)

    def setUp(self):
        fixture.QualityGateTests.setUp(self)
        self.source = self.files.workspace / 'source.png'
        self.source.write_bytes(b'original source artwork')
        self.criteria = 'Verify a local blink and hair movement against the source; reject camera-only motion.'
        self.bind()

    def bind(self):
        self.gate.require_source(7, self.output, self.source, digest(self.source), self.criteria)

    async def test_binding_survives_new_turn_and_gate_restart(self):
        self.controller.active[7] = 'next-turn'
        self.tools.quality = self.gate = QualityGate(self.tools, {})
        self.gate.reviewer = SimpleNamespace(review=AsyncMock(return_value=self.verdict('pass')))
        report, _ = await self.gate.review(7, self.output, requirements='Only check dimensions.', reference_paths=[])
        self.assertTrue(report['ok'])
        brief, evidence, facts = self.gate.reviewer.review.await_args.args
        self.assertIn(self.criteria, brief)
        self.assertTrue(any(item['label'].startswith('REFERENCE:') for item in evidence))
        self.assertEqual(facts['references'][0]['sha256'], digest(self.source))

    async def test_changed_required_source_never_calls_reviewer_or_delivers(self):
        self.source.write_bytes(b'different artwork at same path')
        with self.assertRaisesRegex(ValueError, 'source changed'):
            await self.tools.artifact(7, self.output)
        self.gate.reviewer.review.assert_not_awaited()
        self.controller.emit.assert_not_awaited()

    async def test_disabling_quality_cannot_deliver_bound_candidate(self):
        self.tools.quality = None
        with self.assertRaisesRegex(ValueError, 'requires independent'):
            await self.tools.artifact(7, self.output)
        self.controller.emit.assert_not_awaited()

    async def test_foreign_conversation_cannot_review_or_deliver_bound_candidate(self):
        for operation in [self.gate.review, self.tools.artifact]:
            with self.assertRaisesRegex(ValueError, 'another conversation'):
                await operation(8, self.output)
        self.gate.reviewer.review.assert_not_awaited()
        self.controller.emit.assert_not_awaited()

    async def test_required_source_and_criteria_are_immutable_and_idempotent(self):
        self.bind()
        self.assertEqual(self.db.execute('SELECT count(*) FROM quality_sources').fetchone()[0], 1)
        with self.assertRaisesRegex(ValueError, 'cannot be replaced'):
            self.gate.require_source(7, self.output, self.source, digest(self.source), 'Approve everything.')
        other = self.files.workspace / 'different.png'
        other.write_bytes(b'other')
        with self.assertRaisesRegex(ValueError, 'cannot be replaced'):
            self.gate.require_source(7, self.output, other, digest(other), self.criteria)
        self.assertEqual(required_source(self.db, 7, self.output)['criteria'], self.criteria)

    async def test_explicit_extra_reference_keeps_required_original(self):
        other = self.files.workspace / 'detail.png'
        other.write_bytes(b'detail reference')
        await self.gate.review(7, self.output, reference_paths=[other, self.source])
        facts = self.gate.reviewer.review.await_args.args[2]
        self.assertEqual([row['sha256'] for row in facts['references']], [digest(self.source), digest(other)])
