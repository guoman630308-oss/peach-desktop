import json
import unittest
from unittest.mock import patch

from peach import release_updates
from peach.http import HttpResponse


class NativeUpdateTests(unittest.TestCase):
    def test_window_package_refuses_plain_application_release(self):
        payload = [{'tag_name': 'v9.0.0', 'assets': [{'name': 'Peach-9.0.0-windows-x64.zip',
                    'state': 'uploaded', 'size': 100, 'digest': 'sha256:' + 'a' * 64}]}]
        def transport(*_args):
            return HttpResponse(200, {}, json.dumps(payload).encode(), 'https://api.github.com')
        with patch('peach.distribution.embedded_window', return_value=True):
            result = release_updates.check(transport=transport)
        self.assertEqual(result['state'], 'empty')
        self.assertNotIn('asset_url', result)

    def test_window_package_accepts_matching_variant(self):
        payload = [{'tag_name': 'v9.0.0', 'assets': [{'name': 'PeachDesktop-9.0.0-windows-x64.zip',
                    'state': 'uploaded', 'size': 100, 'digest': 'sha256:' + 'a' * 64}]}]
        def transport(*_args):
            return HttpResponse(200, {}, json.dumps(payload).encode(), 'https://api.github.com')
        with patch('peach.distribution.embedded_window', return_value=True):
            result = release_updates.check(transport=transport)
        self.assertEqual(result['state'], 'available')
        self.assertTrue(result['asset_url'].endswith('/PeachDesktop-9.0.0-windows-x64.zip'))
