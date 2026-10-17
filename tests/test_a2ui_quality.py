"""A2UI image cards cannot publish around the artifact quality gate."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from oak.a2ui import StaleSurface
from oak.a2ui_demo import message
from tests import test_a2ui as fixtures


class A2UIQualityTests(unittest.IsolatedAsyncioTestCase):
    publish = fixtures.A2UITests.publish
    image_messages = fixtures.A2UITests.image_messages
    worker_images = fixtures.A2UITests.worker_images

    def setUp(self):
        fixtures.A2UITests.setUp(self)
        self.worker_images()
        self.c.tools.workspace = self.c.tools.files.workspace
        self.c.active[11] = 'form-turn'
        self.c.task_briefs[11] = 'Create an image card with the requested drawing.'
        self.quality = SimpleNamespace(review=AsyncMock(side_effect=self.review))
        self.c.tools.quality = self.quality

        async def write(source, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
        self.c.tools.files.write_trusted = AsyncMock(side_effect=write)

    async def review(self, chat, source, *, snapshot):
        self.assertEqual(chat, 11)
        self.assertTrue(self.c.tools.files.stored(snapshot))
        self.assertFalse(self.c._lock(chat).locked())
        return {'ok': True, 'verdict': 'pass', 'review_id': 'reviewed-image'}, snapshot

    async def test_rejected_image_returns_findings_without_card_or_artifact(self):
        async def review(chat, source, *, snapshot):
            return {'verdict': 'revise', 'summary': 'Required text is clipped.',
                    'issues': [{'severity': 'major', 'evidence': 'Right edge cuts the title.',
                                'repair': 'Move the title inside the frame.'}]}, snapshot
        self.quality.review.side_effect = review
        result = await self.publish(self.image_messages())
        self.assertFalse(result['published'])
        self.assertFalse(result['queued_for_delivery'])
        self.assertEqual(result['issues'][0]['repair'], 'Move the title inside the frame.')
        self.assertEqual(self.c.db.execute('SELECT count(*) FROM artifacts').fetchone()[0], 0)
        self.assertEqual(self.bus.replay(11), [])

    async def test_reviewed_snapshot_is_registered_without_reopening_changed_source(self):
        expected = b'\x89PNG\r\n\x1a\nsynthetic'
        files = self.c.tools.files
        original_snapshot = files.snapshot
        seen = []
        async def snapshot(path, **kwargs):
            seen.append(path)
            return await original_snapshot(path, **kwargs)
        files.snapshot = snapshot
        async def review(chat, source, *, snapshot):
            Path(source).parent.mkdir(parents=True, exist_ok=True)
            Path(source).write_bytes(b'replaced after extraction')
            return {'verdict': 'pass'}, snapshot
        self.quality.review.side_effect = review
        result = await self.publish(self.image_messages())
        self.assertTrue(result['published'])
        row = self.c.db.execute('SELECT path FROM artifacts').fetchone()
        self.assertEqual(Path(row[0]).read_bytes(), expected)
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.quality.review.await_count, 1)

    async def test_old_artifact_id_is_staged_and_reviewed_in_current_task(self):
        self.c.tools.quality = None
        await self.publish(self.image_messages())
        artifact = self.c.a2ui.snapshot(11)['surfaces'][0]['components']['photo']['artifact']
        self.c.tools.quality = self.quality
        await self.publish([message('updateComponents', {'components': [
            {'id': 'photo', 'component': 'Image', 'alt': 'Again', 'artifact': artifact}]})])
        self.quality.review.assert_awaited_once()
        self.c.tools.files.write_trusted.assert_awaited_once()
        source = self.quality.review.await_args.args[1]
        self.assertTrue(Path(source).is_relative_to(self.c.tools.workspace / 'artifacts'))

    async def test_incremental_text_only_update_cannot_resend_unreviewed_old_image(self):
        self.c.tools.quality = None
        await self.publish(self.image_messages())
        before = len(self.bus.replay(11))
        self.c.tools.quality = self.quality
        async def review(chat, source, *, snapshot):
            return {'verdict': 'revise', 'summary': 'Image has a visible defect.'}, snapshot
        self.quality.review.side_effect = review
        result = await self.publish([message('updateDataModel', {'path': '/caption', 'value': 'New caption'})])
        self.assertFalse(result['published'])
        self.assertEqual(len(self.bus.replay(11)), before)

    async def test_review_does_not_hold_chat_lock_and_stopped_task_cannot_publish(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def review(chat, source, *, snapshot):
            entered.set()
            await release.wait()
            return {'verdict': 'pass'}, snapshot
        self.quality.review.side_effect = review
        pending = asyncio.create_task(self.publish(self.image_messages()))
        await asyncio.wait_for(entered.wait(), 2)
        try:
            async with asyncio.timeout(1):
                async with self.c._lock(11):
                    self.c.active.pop(11)
        finally:
            release.set()
        with self.assertRaises(StaleSurface):
            await pending
        self.assertEqual(self.bus.replay(11), [])

    async def test_owner_steering_after_review_started_invalidates_publication(self):
        async def review(chat, source, *, snapshot):
            self.c.task_briefs[11] = 'Use a different title now.'
            return {'verdict': 'pass'}, snapshot
        self.quality.review.side_effect = review
        with self.assertRaises(StaleSurface):
            await self.publish(self.image_messages())
        self.assertEqual(self.bus.replay(11), [])


if __name__ == '__main__':
    unittest.main()
