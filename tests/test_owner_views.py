"""Owner artifact and activity projections expose only authorized bounded data."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from oak.bus import EventBus
from oak.controller import Controller
from oak.owner_views import OwnerViews


class OwnerViewTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.controller = Controller(AsyncMock(), ':memory:', self.root, AsyncMock())
        self.addCleanup(self.controller.close)
        self.db = self.controller.db
        self.bus = EventBus(self.controller)
        self.views = OwnerViews(self.controller)

    def artifact(self, identifier, scope=7, *, name='звіт.pdf', mime='application/pdf'):
        path = self.root / identifier
        path.write_bytes(b'SYNTHETIC_CONTENT')
        with self.db:
            self.db.execute('INSERT INTO artifacts VALUES (?,?,?,?,?)', (identifier, scope, str(path), name, mime))
        return path

    def test_artifact_gallery_is_scoped_and_contains_no_path_or_content(self):
        self.artifact('mine')
        self.artifact('other', 8, name='OTHER_OWNER')
        result = self.views.artifacts(7, 7)
        self.assertEqual(len(result['items']), 1)
        item = result['items'][0]
        self.assertEqual((item['name'], item['mime'], item['kind']), ('звіт.pdf', 'application/pdf', 'document'))
        self.assertEqual(item['url'], '/api/artifacts/mine')
        self.assertEqual(item['size_bytes'], len(b'SYNTHETIC_CONTENT'))
        for secret in (str(self.root), 'SYNTHETIC_CONTENT', 'OTHER_OWNER'):
            self.assertNotIn(secret, json.dumps(result))
        with self.assertRaises(LookupError):
            self.views.artifacts(8, 7)
        self.controller.client.request.assert_not_called()

    def test_removed_file_has_no_download_url_and_metadata_remains_bounded(self):
        path = self.artifact('missing', name='../private/\x01' + 'x' * 160, mime='text/html; PRIVATE_HEADER')
        path.unlink()
        item = self.views.artifacts(7, 7)['items'][0]
        self.assertFalse(item['available'])
        self.assertIsNone(item['size_bytes'])
        self.assertIsNone(item['url'])
        self.assertEqual(len(item['name']), 120)
        self.assertEqual(item['mime'], 'application/octet-stream')
        self.assertNotIn('/', item['name'])
        self.assertNotIn('PRIVATE_HEADER', json.dumps(item))

    def test_gallery_pagination_and_deleted_topic(self):
        topic = self.controller.sessions.resolve(7, 42)
        for number in range(5):
            self.artifact(str(number), topic, mime='image/png')
        first = self.views.artifacts(7, topic, limit=2)
        self.assertEqual([row['id'] for row in first['items']], ['4', '3'])
        self.artifact('new', topic)
        second = self.views.artifacts(7, topic, before=first['next_cursor'], limit=2)
        self.assertEqual([row['id'] for row in second['items']], ['2', '1'])
        self.assertTrue(all(item['kind'] == 'image' for item in second['items']))
        self.controller.sessions.delete(topic)
        with self.assertRaises(LookupError):
            self.views.artifacts(7, topic)


if __name__ == '__main__':
    unittest.main()
