from pathlib import Path
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
        self.gate.require_source(7, self.output, self.source, digest(self.source), self.criteria,
                                 artifact_sha256=digest(self.output))

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
            self.gate.require_source(7, self.output, self.source, digest(self.source), 'Approve everything.',
                                     artifact_sha256=digest(self.output))
        other = self.files.workspace / 'different.png'
        other.write_bytes(b'other')
        with self.assertRaisesRegex(ValueError, 'cannot be replaced'):
            self.gate.require_source(7, self.output, other, digest(other), self.criteria,
                                     artifact_sha256=digest(self.output))
        self.assertEqual(required_source(self.db, 7, self.output)['criteria'], self.criteria)

    async def test_explicit_extra_reference_keeps_required_original(self):
        other = self.files.workspace / 'detail.png'
        other.write_bytes(b'detail reference')
        await self.gate.review(7, self.output, reference_paths=[other, self.source])
        facts = self.gate.reviewer.review.await_args.args[2]
        self.assertEqual([row['sha256'] for row in facts['references']], [digest(self.source), digest(other)])

    async def test_copied_then_renamed_bytes_keep_source_after_restart(self):
        copied = self.files.workspace / 'copied.mp4'
        copied.write_bytes(self.output.read_bytes())
        renamed = self.files.workspace / 'renamed.mp4'
        copied.rename(renamed)
        self.output.unlink()
        self.controller.active[7] = 'next-turn'
        self.tools.quality = self.gate = QualityGate(self.tools, {})
        self.gate.reviewer = SimpleNamespace(review=AsyncMock(return_value=self.verdict('pass')))
        await self.gate.review(7, renamed, reference_paths=[])
        brief, _, facts = self.gate.reviewer.review.await_args.args
        self.assertIn(self.criteria, brief)
        self.assertEqual(facts['references'][0]['sha256'], digest(self.source))

    async def test_quality_disabled_rejects_exact_copy_even_with_different_extension(self):
        copied = self.files.workspace / 'copied.data'
        copied.write_bytes(self.output.read_bytes())
        self.tools.quality = None
        with self.assertRaisesRegex(ValueError, 'requires independent'):
            await self.tools.artifact(7, copied)
        self.controller.emit.assert_not_awaited()

    async def test_foreign_scope_cannot_deliver_or_review_exact_copy(self):
        copied = self.files.workspace / 'copied.mp4'
        copied.write_bytes(self.output.read_bytes())
        for operation in [self.gate.review, self.tools.artifact]:
            with self.assertRaisesRegex(ValueError, 'another conversation'):
                await operation(8, copied)
        self.controller.emit.assert_not_awaited()
        self.gate.reviewer.review.assert_not_awaited()

    async def test_unrelated_bytes_retain_existing_delivery_behavior(self):
        other = self.files.workspace / 'note.txt'
        other.write_bytes(b'an unrelated plain text artifact')
        self.tools.quality = None
        result = await self.tools.artifact(7, other)
        self.assertTrue(result['queued_for_delivery'])
        delivered = self.controller.emit.await_args.args[1]['value']['path']
        self.assertEqual(Path(delivered).read_bytes(), other.read_bytes())

    async def test_copied_unknown_suffix_still_requires_review(self):
        copied = self.files.workspace / 'copied.data'
        copied.write_bytes(self.output.read_bytes())
        self.gate.reviewer.review.return_value = self.verdict('revise')
        result = await self.tools.artifact(7, copied)
        self.assertFalse(result['queued_for_delivery'])
        self.controller.emit.assert_not_awaited()

    async def test_turn_replaced_during_identity_snapshot_cannot_deliver_in_new_task(self):
        copied = self.files.workspace / 'copied.mp4'
        copied.write_bytes(self.output.read_bytes())
        snapshot = self.files.snapshot
        async def replace_turn(*args, **kwargs):
            result = await snapshot(*args, **kwargs)
            self.controller.active[7] = 'new-task'
            return result
        self.files.snapshot = replace_turn
        with self.assertRaisesRegex(ValueError, 'Task stopped'):
            await self.tools.artifact(7, copied)
        self.gate.reviewer.review.assert_not_awaited()
        self.controller.emit.assert_not_awaited()

    async def test_trusted_generation_of_identical_bytes_can_register_second_conversation(self):
        other_output = self.files.workspace / 'independently-rendered.mp4'
        other_output.write_bytes(self.output.read_bytes())
        other_source = self.files.workspace / 'second-source.png'
        other_source.write_bytes(self.source.read_bytes())
        self.gate.require_source(8, other_output, other_source, digest(other_source), self.criteria,
                                 artifact_sha256=digest(other_output))
        self.assertEqual(self.db.execute('SELECT count(*) FROM quality_source_hashes').fetchone()[0], 2)
        for scope, output in [(7, self.output), (8, other_output)]:
            report, _ = await self.gate.review(scope, output)
            self.assertTrue(report['ok'])
            self.assertEqual(required_source(self.db, scope, output, digest(output))['sha256'], digest(self.source))
        with self.assertRaisesRegex(ValueError, 'another conversation'):
            self.gate.require_source(8, self.output, other_source, digest(other_source), self.criteria,
                                     artifact_sha256=digest(self.output))

    async def test_single_owner_hash_schema_migrates_without_losing_binding(self):
        row = self.db.execute('SELECT * FROM quality_source_hashes').fetchone()
        self.db.execute('DROP TABLE quality_source_hashes')
        self.db.execute('''CREATE TABLE quality_source_hashes (sha256 TEXT PRIMARY KEY, chat_id INTEGER NOT NULL,
            source TEXT NOT NULL, source_sha256 TEXT NOT NULL, criteria TEXT NOT NULL)''')
        self.db.execute('INSERT INTO quality_source_hashes VALUES (?,?,?,?,?)', tuple(row))
        self.db.commit()
        self.tools.quality = self.gate = QualityGate(self.tools, {})
        primary = [r[1] for r in self.db.execute('PRAGMA table_info(quality_source_hashes)') if r[5]]
        self.assertEqual(primary, ['sha256', 'chat_id'])
        self.assertEqual(tuple(self.db.execute('SELECT * FROM quality_source_hashes').fetchone()), tuple(row))
        with self.assertRaisesRegex(ValueError, 'another conversation'):
            required_source(self.db, 8, '/copy.mp4', digest(self.output))

    async def test_fresh_trusted_same_scope_source_copy_keeps_immutable_byte_criteria(self):
        other_output = self.files.workspace / 'same-scope-new-render.mp4'
        other_output.write_bytes(self.output.read_bytes())
        other_source = self.files.workspace / 'same-source-copy.png'
        other_source.write_bytes(self.source.read_bytes())
        self.gate.require_source(7, other_output, other_source, digest(other_source), self.criteria,
                                 artifact_sha256=digest(other_output))
        binding = required_source(self.db, 7, other_output, digest(other_output))
        self.assertEqual(binding['path'], str(other_source))
        self.assertEqual(required_source(self.db, 7, self.output, digest(self.output))['path'], str(self.source))
