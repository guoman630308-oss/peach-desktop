import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx

from peach.follow_stream import FollowProxyError, ResolvedFollowMedia
from peach.preview_transport import open_preview_upstream


class PreviewTransportTests(unittest.TestCase):
    def setUp(self):
        self.native = SimpleNamespace(status_code=206,
            headers={'content-type': 'video/mp4', 'content-length': '8', 'content-range': 'bytes 0-7/100'},
            iter_content=mock.Mock(return_value=iter([b'0123', b'4567'])), close=mock.Mock())
        self.session = mock.Mock()
        self.session.request.return_value = self.native
        self.factory = mock.patch('peach.preview_transport.requests.Session', return_value=self.session)
        self.factory.start()
        self.network = mock.patch('peach.preview_transport.peach_proxy.client_options', return_value={'trust_env': False})
        self.network.start()
        self.addCleanup(self.factory.stop)
        self.addCleanup(self.network.stop)
        self.target = ResolvedFollowMedia('https://www.prestige-av.com/api/media/preview.mp4',
                                         allowed_hosts=('www.prestige-av.com',), public_hosts=True)

    def open(self, client, method='GET'):
        return open_preview_upstream(client, method, self.target, incoming={'range': 'bytes=0-'},
                                     secrets_root=Path('unused'))

    def test_tls_failure_streams_browser_response_and_closes_its_owner(self):
        with httpx.Client(transport=httpx.MockTransport(lambda request: self._tls_failure(request))) as client:
            response = self.open(client)
            self.session.close.assert_not_called()
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.headers['content-range'], 'bytes 0-7/100')
            self.assertEqual(b''.join(response.iter_raw()), b'01234567')
            response.close()
            self.native.close.assert_called_once()
            self.session.close.assert_called_once()
            request = self.session.request.call_args.kwargs
            self.assertEqual(request['headers']['range'], 'bytes=0-')
            self.assertTrue(request['stream'])
            self.assertFalse(request['allow_redirects'])
            self.assertIsNone(request['accept_encoding'])

    def test_http_refusal_never_switches_transport(self):
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(403, request=request))) as client:
            with self.assertRaises(FollowProxyError):
                self.open(client)
        self.session.request.assert_not_called()

    def test_browser_redirect_outside_public_boundary_closes_without_second_request(self):
        self.native.status_code = 302
        self.native.headers = {'location': 'https://private.example/video.mp4'}
        with httpx.Client(transport=httpx.MockTransport(self._tls_failure)) as client, \
                mock.patch('peach.follow_stream.resolves_publicly', return_value=False):
            with self.assertRaises(FollowProxyError):
                self.open(client)
        self.session.request.assert_called_once()
        self.native.close.assert_called_once()
        self.session.close.assert_called_once()

    def test_head_and_early_stop_release_the_browser_stream_without_reading_body(self):
        with httpx.Client(transport=httpx.MockTransport(self._tls_failure)) as client:
            response = self.open(client, 'HEAD')
            response.close()
        self.native.iter_content.assert_not_called()
        self.native.close.assert_called_once()
        self.session.close.assert_called_once()

    @staticmethod
    def _tls_failure(request):
        raise httpx.ConnectError('TLS connection interrupted', request=request)
