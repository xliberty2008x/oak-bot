"""Controller initialization validates identifiers without querying another chat."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from oak.harness_versions import HarnessVersions
from oak.controller import Controller


class ControllerInitializationScopeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.path = self.root / 'versions.sqlite'
        self.store = HarnessVersions(self.path)
        self.addCleanup(self.store.close)
        self.scope = ('selected-project', 7, 'media-depth')
        self.selected = self.store.bootstrap_current(*self.scope, 'SELECTED_CHAT7_PROCEDURE')
        foreign = self.store.bootstrap_current('selected-project', 1, 'media-depth', 'PRIVATE_CHAT1_CANARY')
        # A corrupt unrelated record must have no effect on this conversation.
        with self.store.db:
            self.store.db.execute('UPDATE harness_versions SET procedure=? WHERE id=?',
                                  ('CORRUPT_PRIVATE_CHAT1_CANARY', foreign['id']))
        self.config = {'harness_activation': {'store_path': str(self.path),
                       'project_id': self.scope[0], 'domain': self.scope[2]}}

    def test_constructor_does_not_resolve_a_dummy_conversation(self):
        client = SimpleNamespace(request=AsyncMock())
        with patch.object(HarnessVersions, 'resolve', side_effect=AssertionError('Unexpected conversation read.')) as resolve:
            controller = Controller(client, self.root / 'controller.sqlite', self.root, AsyncMock(),
                                    config=self.config)
            resolve.assert_not_called()
        self.addCleanup(controller.close)
        inputs, application = controller.prepare_harness_input(7, 'selected-request',
                                                               [{'type': 'text', 'text': 'Create an image.'}])
        self.assertEqual(application['version_id'], self.selected['id'])
        self.assertIn('SELECTED_CHAT7_PROCEDURE', str(inputs))
        self.assertNotIn('CHAT1_CANARY', str(inputs))
        self.assertFalse(client.request.called)

    def test_invalid_identifiers_still_fail_before_store_or_native_input(self):
        for key, value in (('project_id', ''), ('project_id', 1), ('domain', '../escape'), ('domain', '')):
            config = {'harness_activation': {**self.config['harness_activation'], key: value,
                       'store_path': str(self.root / 'must-not-create.sqlite')}}
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                Controller(SimpleNamespace(request=AsyncMock()), self.root / 'bad-controller.sqlite',
                           self.root, AsyncMock(), config=config)
            self.assertFalse((self.root / 'must-not-create.sqlite').exists())


if __name__ == '__main__':
    unittest.main()
