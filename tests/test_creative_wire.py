"""Creative workflows cross the real client/socket/worker dispatch boundary."""

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from oak.executor import ExecutionClient, decode, encode
from oak.executor_worker import Worker


class CreativeWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_and_animation_reach_worker_but_generation_is_not_permitted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profile = {'workspace': str(root), 'runtime_home': str(root/'runtime'),
                       'worker_socket': str(root/'worker.sock'), 'executor_uid': os.getuid(),
                       'policy_digest': 'synthetic-attestation'}
            worker = Worker(profile)
            received = []
            handlers = set()

            async def handle(reader, writer):
                task = asyncio.current_task()
                handlers.add(task)
                try:
                    while raw := await reader.readline():
                        request = decode(raw)
                        received.append(request['op'])
                        result = await worker.dispatch(request['op'], request['args'])
                        writer.write(encode({'id': request['id'], 'ok': True, 'result': result}))
                        await writer.drain()
                finally:
                    writer.close()
                    await writer.wait_closed()
                    handlers.discard(task)

            server = await asyncio.start_unix_server(handle, path=profile['worker_socket'])
            client = ExecutionClient({'profile': '/synthetic'})
            try:
                # Provider I/O/rendering are separately exercised with actual
                # HTTP/FFmpeg fixtures. Only their leaf work is mocked here.
                with patch('oak.executor.require_boundary', return_value=profile), \
                        patch('oak.hf_spaces.Spaces.search', return_value=[{'id': 'public/space'}]) as search, \
                        patch('oak.hf_spaces.Spaces.inspect', return_value={'generation_available': 'unknown'}) as inspect, \
                        patch('oak.animation_tools.render', return_value={'method': 'procedural_2d', 'review_required': True}) as render:
                    self.assertEqual(await client.call('spaces.search', {'query': 'video', 'limit': 1}), [{'id': 'public/space'}])
                    self.assertEqual(await client.call('spaces.inspect', {'space_id': 'public/space'}), {'generation_available': 'unknown'})
                    result = await client.call('animation.render', {
                        'image_path': 'source.png', 'closed_eyes_path': 'closed.png',
                        'image_sha256': 'a'*64, 'closed_eyes_sha256': 'b'*64,
                        'eye_regions': [[60,70,80,90]], 'hair_polygons': [[[10,10],[50,10],[50,60]]]})
                    self.assertEqual(result, {'method': 'procedural_2d', 'review_required': True})
                    with self.assertRaisesRegex(ValueError, 'Unsupported execution operation'):
                        await client.call('spaces.generate', {})
                    search.assert_called_once_with(query='video', limit=1)
                    inspect.assert_called_once_with(space_id='public/space')
                    self.assertIs(render.call_args.args[0], worker.files)
                    self.assertEqual(received, ['hello', 'spaces.search', 'spaces.inspect', 'animation.render'])
            finally:
                await client.close()
                server.close()
                await server.wait_closed()
                await asyncio.gather(*tuple(handlers), return_exceptions=True)
                await worker.close()


if __name__ == '__main__':
    unittest.main()
