"""Real subprocess fixtures, no provider traffic or account state."""

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from oak.cli_process import PrivateProcess, authorization_url

CANARY = 'synthetic-cli-code-never-public'
VERIFY = {'authenticated': True, 'gpu_types': ['T4'], 'availability': 'unknown'}
CHALLENGE = 'https://accounts.google.com/o/oauth2/v2/auth?state=synthetic-private-state'


class PrivateProcessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    async def process(self, body, seconds=3):
        script = self.root / 'fixed_fixture.py'
        script.write_text('import sys, json, os, time\n' + body)
        process = PrivateProcess((sys.executable, '-I', str(script)), self.root, seconds=seconds)
        self.addAsyncCleanup(process.close)
        await process.start()
        return process

    async def until(self, predicate):
        async with asyncio.timeout(3):
            while not predicate():
                await asyncio.sleep(.005)

    async def test_real_stdin_private_cache_and_verified_metadata(self):
        body = ('print(json.dumps({"challenge": '+repr(CHALLENGE)+'}), flush=True)\n'
                'code = input()\n'
                'assert code == '+repr(CANARY)+'\n'
                'assert "DISPLAY" not in os.environ and "CODEX_HOME" not in os.environ\n'
                'assert os.stat(os.environ["HOME"]).st_mode & 0o777 == 0o700\n'
                'open("private-cache", "w").write(code)\n'
                'print(json.dumps({"verified": '+repr(VERIFY)+'}), flush=True)\n')
        p = await self.process(body)
        await self.until(lambda: p.challenge is not None)
        await p.submit(CANARY)
        with self.assertRaises(ValueError):
            await p.submit(CANARY)
        await p.task
        self.assertEqual(p.state, 'authenticated')
        self.assertEqual(p.result, VERIFY)
        self.assertIsNone(p.challenge)
        directory = p.directory
        self.assertEqual((directory/'private-cache').read_text(), CANARY)
        await p.close()
        self.assertFalse(directory.exists())

    async def test_echo_or_secret_stderr_is_never_exposed_as_result(self):
        p = await self.process('print(json.dumps({"challenge": '+repr(CHALLENGE)+'}), flush=True)\n'
            'code=input()\nprint(code, flush=True)\nprint(code, file=sys.stderr)\n')
        await self.until(lambda: p.challenge is not None)
        await p.submit(CANARY)
        await p.task
        self.assertEqual(p.state, 'uncertain')
        self.assertIsNone(p.result)
        self.assertIsNone(p.challenge)

    async def test_zero_exit_without_verification_is_not_authentication(self):
        p = await self.process('pass\n')
        await p.task
        self.assertEqual(p.state, 'failed')
        self.assertIsNone(p.result)

    async def test_timeout_and_close_reap_the_exact_process(self):
        p = await self.process('time.sleep(60)\n', seconds=.05)
        await p.task
        self.assertEqual(p.state, 'failed')
        self.assertIsNotNone(p.process.returncode)
        with self.assertRaises(ValueError):
            await p.start()

    async def test_cancel_removes_private_cache_and_reaps_process(self):
        p = await self.process('time.sleep(60)\n')
        directory = p.directory
        await p.close()
        self.assertIsNotNone(p.process.returncode)
        self.assertFalse(directory.exists())
        await p.close()

    def test_private_url_never_accepts_an_arbitrary_destination(self):
        self.assertEqual(authorization_url(CHALLENGE), CHALLENGE)
        for bad in ('http://accounts.google.com/o/oauth2/auth',
                    'https://accounts.google.com.evil.test/o/oauth2/auth',
                    'https://accounts.google.com@evil.test/o/oauth2/auth',
                    'https://accounts.google.com/o/oauth2/auth#code'):
            with self.assertRaises(ValueError):
                authorization_url(bad)
