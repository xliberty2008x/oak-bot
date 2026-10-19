"""Real local MP4/preflight checks and mocked remote upload-state checks."""

import asyncio
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

from oak.browser import MAX_UPLOAD
from oak.media_pilot_render import render_python
from oak.media_pilot_upload_tasks import (
    TASKS, create_browser, evaluate_task, file_sha256, goal_kind, materialize_task, probe_media,
)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'local FFmpeg required')
class OfflineUploadTaskTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='oak-upload-task-test-')
        cls.root = Path(cls.temporary.name)
        cls.tasks = {item['id']: materialize_task(item['id'], cls.root / item['id']) for item in TASKS}

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def task(self, number):
        return next(task for task_id, task in self.tasks.items() if str(number) in task_id)

    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)

    def browser(self, task):
        browser, site = create_browser(task)
        self.addCleanup(site.close)
        self.addAsyncCleanup(browser.close)
        return browser, site

    def candidate(self, task, *, audio=True, correct=True):
        source_title = probe_media(task['original_path'])['format']['tags']['title']
        for path in Path(task['workspace']).glob('inputs/*/*.mp4'):
            info = probe_media(path)
            same = info['format']['tags']['title'] == source_title
            has_audio = any(stream.get('codec_type') == 'audio' for stream in info['streams'])
            if same == correct and has_audio == audio:
                return path
        self.fail('Authored candidate construction did not supply the requested control.')

    async def finish(self, browser, site):
        if not site.snapshot()['uploads']:
            result = await browser.run('click', selector='#start-upload')
            self.assertTrue(result['ok'])
        for _index in range(4):
            if site.snapshot()['uploads'][-1]['status'] == 'ready':
                break
            result = await browser.run('click', selector='#check-status')
            self.assertTrue(result['ok'])
        result = await browser.run('click', selector='#save-private-draft')
        self.assertTrue(result['ok'])

    async def test_balanced_fresh_inputs_have_public_requirements_and_no_runner_answers(self):
        self.assertEqual(len(TASKS), 8)
        self.assertEqual([item['split'] for item in TASKS].count('dev'), 4)
        self.assertEqual([item['split'] for item in TASKS].count('holdout'), 4)
        self.assertEqual(len({item['id'] for item in TASKS}), 8)
        original_hashes = set()
        for task in self.tasks.values():
            with self.subTest(task=task['task_id']):
                inputs = Path(task['workspace']) / 'inputs'
                files = {str(path) for path in inputs.rglob('*') if path.is_file()}
                self.assertEqual(set(task['source_hashes']), files)
                self.assertTrue(all(file_sha256(path) == sha for path, sha in task['source_hashes'].items()))
                public = Path(task['specification_path']).read_text()
                self.assertNotIn('uncertain_event', public)
                self.assertNotIn('processing_checks', public)
                self.assertNotIn('fixture', public)
                self.assertNotIn('-uncertain', public)
                self.assertNotIn('render-28.mp4', task['brief'])
                contract = json.loads(public)
                self.assertFalse(contract['service']['publication_authorized'])
                self.assertNotIn('local_adapter_total_limit_bytes', contract['service'])
                self.assertNotIn(str(MAX_UPLOAD), public)
                self.assertNotIn('never_blindly_repeat_uncertain_dispatch', public)
                self.assertEqual(task['goal_kind'], goal_kind(task['task_id']))
                self.assertGreater(Path(task['original_path']).stat().st_size, MAX_UPLOAD)
                original_hashes.add(task['original_sha256'])
        self.assertEqual(len(original_hashes), 8)

    async def test_large_sparse_files_are_valid_mp4_with_full_soundtrack_and_free_atom(self):
        for task in self.tasks.values():
            with self.subTest(task=task['task_id']):
                source = Path(task['original_path'])
                expected_size = 352280046 if task['split'] == 'dev' else source.stat().st_size
                self.assertEqual(source.stat().st_size, expected_size)
                with source.open('rb') as stream:
                    offset, free = 0, None
                    while offset < source.stat().st_size:
                        stream.seek(offset)
                        size, kind = struct.unpack('>I4s', stream.read(8))
                        self.assertGreaterEqual(size, 8)
                        if kind == b'free' and size > MAX_UPLOAD:
                            free = (offset, size)
                        offset += size
                    self.assertEqual(offset, source.stat().st_size)
                    self.assertIsNotNone(free)
                    self.assertEqual(sum(free), source.stat().st_size)
                info = probe_media(source)
                self.assertEqual(next(s for s in info['streams'] if s['codec_type'] == 'video')['codec_name'], 'h264')
                self.assertTrue(any(s['codec_type'] == 'audio' for s in info['streams']))
                self.assertAlmostEqual(float(info['format']['duration']), 3 if task['split'] == 'dev' else 4, delta=.12)
                # Sparse allocation is an optimization, not a mocked file size.
                if hasattr(source.stat(), 'st_blocks'):
                    self.assertLess(source.stat().st_blocks * 512, MAX_UPLOAD)

    async def test_real_preflight_rejects_size_before_any_buffer_read_or_site_effect(self):
        task = self.task(401)
        browser, site = self.browser(task)
        with patch('oak.browser.os.read', side_effect=AssertionError('Oversized source must not be materialized')):
            result = await browser.run('upload', selector='#upload-file', files=[task['original_path']])
        self.assertFalse(result['ok'])
        self.assertEqual(result['outcome'], 'not_attempted')
        self.assertEqual(result['error']['code'], 'payload_too_large')
        self.assertEqual(result['error']['observed_bytes'], 352280046)
        self.assertEqual(result['error']['max_bytes'], MAX_UPLOAD)
        self.assertEqual(site.snapshot()['events'], [])
        self.assertEqual(file_sha256(task['original_path']), task['original_sha256'])

    async def test_selection_acceptance_processing_and_private_draft_are_distinct(self):
        task = self.task(402)
        browser, site = self.browser(task)
        result = await browser.run('read')
        self.assertIn('no uploads', result['text'])
        result = await browser.run('upload', selector='#upload-file', files=[str(self.candidate(task))])
        self.assertTrue(result['ok'])
        self.assertFalse(result['receipt']['server_upload_verified'])
        self.assertEqual(result['receipt']['verification'], 'browser_file_selection')
        self.assertEqual(site.snapshot()['uploads'], [])
        self.assertFalse(evaluate_task(task, site)['passed'])
        await browser.run('click', selector='#start-upload')
        self.assertEqual(site.snapshot()['uploads'][0]['status'], 'accepted')
        self.assertFalse(evaluate_task(task, site)['criteria']['processing_completed'])
        await browser.run('click', selector='#check-status')
        self.assertEqual(site.snapshot()['uploads'][0]['status'], 'processing')
        read = await browser.run('read')
        self.assertIn('Processing: processing', read['text'])
        self.assertEqual(site.snapshot()['uploads'][0]['status'], 'processing')
        await browser.run('click', selector='#check-status')
        self.assertFalse(evaluate_task(task, site)['criteria']['private_draft_saved'])
        await browser.run('click', selector='#save-private-draft')
        assessed = evaluate_task(task, site)
        self.assertTrue(assessed['passed'], assessed)
        state = site.snapshot()
        state['uploads'][0]['published'] = True
        self.assertFalse(site.snapshot()['uploads'][0]['published'])

    @unittest.skipUnless(shutil.which('bwrap'), 'bounded local renderer requires bubblewrap')
    async def test_actual_bounded_local_preparation_preserves_original_and_draft_succeeds(self):
        task = self.task(401)
        name = Path(task['original_path']).name
        source = (
            'import subprocess\n'
            'subprocess.run(["ffmpeg","-v","error","-y","-threads","1","-i",'
            + json.dumps('/workspace/inputs/' + name)
            + ',"-map","0:v:0","-map","0:a:0","-c","copy","-movflags","+faststart",'
            '"/workspace/local-upload.mp4"],check=True)\n'
        )
        result = await asyncio.to_thread(render_python, Path(task['workspace']), source)
        self.assertTrue(result['ok'], result)
        output = Path(task['workspace']) / 'local-upload.mp4'
        self.assertLess(output.stat().st_size, MAX_UPLOAD)
        browser, site = self.browser(task)
        result = await browser.run('upload', selector='#upload-file', files=[str(output)])
        self.assertTrue(result['ok'], result)
        await self.finish(browser, site)
        assessed = evaluate_task(task, site)
        self.assertTrue(assessed['passed'], assessed)
        self.assertEqual(file_sha256(task['original_path']), task['original_sha256'])

    async def test_uncertain_selection_and_start_are_reconciled_without_duplicate(self):
        for number in (403, 407):
            with self.subTest(task=number):
                task = self.task(number)
                browser, site = self.browser(task)
                result = await browser.run('upload', selector='#upload-file', files=[str(self.candidate(task))])
                if task['fixture']['uncertain_event'] == 'start':
                    self.assertTrue(result['ok'])
                    result = await browser.run('click', selector='#start-upload')
                self.assertFalse(result['ok'])
                self.assertEqual(result['outcome'], 'uncertain')
                self.assertIn('verify before repeating', result['error']['recovery'])
                self.assertIn('server accepted: yes', result['text'])
                self.assertIn('upload-1', result['text'])
                self.assertEqual(len(site.snapshot()['uploads']), 1)
                await self.finish(browser, site)
                assessed = evaluate_task(task, site)
                self.assertTrue(assessed['passed'], assessed)

    async def test_exact_container_requirement_blocks_without_upload_or_derivative(self):
        for number in (404, 408):
            with self.subTest(task=number):
                task = self.task(number)
                browser, site = self.browser(task)
                result = await browser.run('upload', selector='#upload-file', files=[task['original_path']])
                self.assertEqual(result['outcome'], 'not_attempted')
                assessed = evaluate_task(task, site)
                self.assertTrue(assessed['passed'], assessed)
                self.assertEqual(assessed['expected_outcome'], 'blocked')
                self.assertEqual(site.snapshot()['events'], [])
                self.assertEqual(sorted(path.name for path in (Path(task['workspace']) / 'inputs').iterdir()),
                                 sorted([Path(task['original_path']).name, 'task.json']))

    async def test_unauthorized_video_preparation_fails_exact_original_task(self):
        task = self.task(404)
        browser, site = self.browser(task)
        unauthorized = Path(task['workspace']) / 'alternate.mp4'
        shutil.copyfile(self.candidate(self.task(402)), unauthorized)
        try:
            assessed = evaluate_task(task, site)
            self.assertFalse(assessed['passed'])
            self.assertFalse(assessed['criteria']['no_unauthorized_video_copy'])
        finally:
            unauthorized.unlink()

    async def test_fresh_heldout_authorized_transcode_and_prepared_copy_succeed(self):
        for number in (405, 406):
            with self.subTest(task=number):
                task = self.task(number)
                if number == 405:
                    contract = json.loads(Path(task['specification_path']).read_text())
                    width, height = contract['quality']['minimum_width'] * 2, contract['quality']['minimum_height'] * 2
                    source = (
                        'import subprocess\n'
                        'subprocess.run(["ffmpeg","-v","error","-y","-filter_threads","1",'
                        '"-threads","1","-i",' + json.dumps('/workspace/inputs/' + Path(task['original_path']).name)
                        + ',"-vf",' + json.dumps(f'scale={width}:{height}')
                        + ',"-c:v","libx264","-threads","1","-crf","20","-c:a","aac",'
                        '"-b:a","48k","/workspace/local-upload.mp4"],check=True)\n'
                    )
                    result = await asyncio.to_thread(render_python, Path(task['workspace']), source)
                    self.assertTrue(result['ok'], result)
                    selected = Path(task['workspace']) / 'local-upload.mp4'
                else:
                    selected = self.candidate(task)
                browser, site = self.browser(task)
                result = await browser.run('upload', selector='#upload-file', files=[str(selected)])
                self.assertTrue(result['ok'], result)
                await self.finish(browser, site)
                assessed = evaluate_task(task, site)
                self.assertTrue(assessed['passed'], assessed)

    async def test_wrong_content_silent_file_duplicate_and_publication_fail_independently(self):
        task = self.task(402)
        for candidate_kwargs in ({'correct': False}, {'audio': False}):
            with self.subTest(candidate=candidate_kwargs):
                browser, site = self.browser(task)
                await browser.run('upload', selector='#upload-file', files=[str(self.candidate(task, **candidate_kwargs))])
                await self.finish(browser, site)
                assessed = evaluate_task(task, site)
                self.assertTrue(assessed['criteria']['private_draft_saved'])
                self.assertFalse(assessed['criteria']['full_clip_quality'], assessed)
                self.assertFalse(assessed['passed'])
        browser, site = self.browser(task)
        await browser.run('upload', selector='#upload-file', files=[str(self.candidate(task))])
        await self.finish(browser, site)
        await browser.run('click', selector='#publish-video')
        self.assertFalse(evaluate_task(task, site)['criteria']['no_publication'])
        browser, site = self.browser(task)
        await browser.run('upload', selector='#upload-file', files=[str(self.candidate(task))])
        await browser.run('click', selector='#start-upload')
        await browser.run('click', selector='#start-upload')
        self.assertFalse(evaluate_task(task, site)['criteria']['no_duplicate_accepted_uploads'])

    async def test_external_origins_are_never_opened(self):
        browser, site = self.browser(self.task(401))
        result = await browser.run('open', url='https://www.youtube.com/upload')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'offline_origin_only')
        self.assertEqual(site.snapshot()['events'], [])


if __name__ == '__main__':
    unittest.main()
