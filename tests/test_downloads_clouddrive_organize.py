"""A 盘官方移动：冲突、成员核验、确定性拒绝与状态未知、路径边界。"""
import unittest
from pathlib import PureWindowsPath
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import unquote, urlsplit

import httpx
import grpc

from peach.organize_clouddrive import (ACCEPTED, DAV_ORIGIN, REJECTED, MoveNotExecuted, PikPakDav,
                                       UnconfirmedMove)
from peach import organize_clouddrive


class Status(grpc.RpcError):
    def __init__(self, code):
        self._code = code

    def code(self):
        return self._code


class OfficialMoves(unittest.TestCase):
    def setUp(self):
        self.entries = {'A:\\old': True, 'A:\\old\\film.mp4': 20, 'A:\\new': True}
        self.requests = []
        self.refresh = Mock()
        self.http = httpx.Client(transport=httpx.MockTransport(self.handle), follow_redirects=False)
        self.addCleanup(self.http.close)
        self.dav = PikPakDav(self.http, 'A:\\', self.refresh, listing=self.listing,
                             rename=Mock(side_effect=self.relocate),
                             move_remote=Mock(side_effect=self.relocate))

    def handle(self, request):
        path = 'A:' + unquote(urlsplit(str(request.url)).path).replace('/', '\\').rstrip('\\')
        self.requests.append((request.method, path))
        if request.method != 'MKCOL':
            return httpx.Response(405)
        self.entries[path] = True
        return httpx.Response(201)

    def listing(self, path):
        if path != 'A:\\' and self.entries.get(path) is not True:
            raise FileNotFoundError('官方父目录未列出所需目录')
        return [dict(path=name, size=0 if value is True else value, directory=value is True)
                for name, value in self.entries.items()
                if name != path and str(PureWindowsPath(name).parent) == path]

    def relocate(self, source, target):
        for name in list(self.entries):
            if name == source or name.startswith(source + '\\'):
                self.entries[target + name[len(source):]] = self.entries.pop(name)
        return ACCEPTED

    def test_official_move_preserves_size_and_refreshes_both_parents(self):
        self.dav.move('A:\\old\\film.mp4', 'A:\\new\\film.mp4')
        self.assertEqual(self.entries['A:\\new\\film.mp4'], 20)
        self.assertNotIn('A:\\old\\film.mp4', self.entries)
        self.dav.move_remote.assert_called_once_with('A:\\old\\film.mp4', 'A:\\new\\film.mp4')
        self.dav.rename.assert_not_called()
        self.assertEqual({c.args[0] for c in self.refresh.call_args_list}, {'A:\\old', 'A:\\new'})

    def test_target_collision_never_submits_move(self):
        self.entries['A:\\new\\film.mp4'] = 40
        with self.assertRaises(FileExistsError):
            self.dav.move('A:\\old\\film.mp4', 'A:\\new\\film.mp4')
        self.dav.move_remote.assert_not_called()
        self.assertEqual(self.entries['A:\\new\\film.mp4'], 40)

    def test_rejected_submission_is_a_failure_with_the_source_in_place(self):
        self.dav.move_remote = Mock(return_value=REJECTED)
        self.dav.confirm_attempts = 4
        with patch.object(organize_clouddrive.time, 'sleep') as sleep, self.assertRaises(MoveNotExecuted):
            self.dav.move('A:\\old\\film.mp4', 'A:\\new\\film.mp4')
        self.dav.move_remote.assert_called_once()
        sleep.assert_not_called()
        self.assertEqual(self.entries['A:\\old\\film.mp4'], 20)

    def test_unchanged_source_after_the_confirmation_budget_is_a_failure(self):
        for status in (None, ACCEPTED):
            with self.subTest(status=status):
                self.dav.move_remote = Mock(return_value=status)
                self.dav.confirm_attempts = 4
                with patch.object(organize_clouddrive.time, 'sleep') as sleep, \
                        self.assertRaises(MoveNotExecuted):
                    self.dav.move('A:\\old\\film.mp4', 'A:\\new\\film.mp4')
                self.dav.move_remote.assert_called_once()
                self.assertEqual(sleep.call_count, 3)
                self.assertEqual(self.entries['A:\\old\\film.mp4'], 20)

    def test_rename_acknowledgement_without_path_change_is_a_failure(self):
        self.dav.rename = Mock(return_value=ACCEPTED)
        with self.assertRaises(MoveNotExecuted):
            self.dav.move('A:\\old\\film.mp4', 'A:\\old\\title.mp4')
        self.dav.rename.assert_called_once()
        self.assertEqual(self.entries['A:\\old\\film.mp4'], 20)

    def test_same_parent_rename_uses_the_official_rename(self):
        self.dav.move('A:\\old\\film.mp4', 'A:\\old\\title.mp4')
        self.dav.rename.assert_called_once_with('A:\\old\\film.mp4', 'A:\\old\\title.mp4')
        self.dav.move_remote.assert_not_called()
        self.assertEqual(self.entries['A:\\old\\title.mp4'], 20)

    def test_collection_move_checks_all_members(self):
        self.dav.move('A:\\old', 'A:\\new\\old')
        self.assertEqual(self.entries['A:\\new\\old\\film.mp4'], 20)
        self.assertNotIn('A:\\old', self.entries)

    def test_incomplete_collection_stops_without_repeating_move(self):
        def lossy(source, target):
            self.relocate(source, target)
            self.entries.pop(target + '\\film.mp4')
            return ACCEPTED
        self.dav.move_remote = Mock(side_effect=lossy)
        with self.assertRaises(UnconfirmedMove):
            self.dav.move('A:\\old', 'A:\\new\\old')
        self.dav.move_remote.assert_called_once()

    def test_source_and_target_both_present_stop_the_batch(self):
        def copy(source, target):
            self.entries[target] = self.entries[source]
            return ACCEPTED
        self.dav.move_remote = Mock(side_effect=copy)
        with self.assertRaises(UnconfirmedMove):
            self.dav.move('A:\\old\\film.mp4', 'A:\\new\\film.mp4')
        self.dav.move_remote.assert_called_once()

    def test_source_and_target_both_absent_stop_the_batch(self):
        def lose(source, target):
            self.entries.pop(source)
            return None
        self.dav.move_remote = Mock(side_effect=lose)
        with self.assertRaises(UnconfirmedMove):
            self.dav.move('A:\\old\\film.mp4', 'A:\\new\\film.mp4')
        self.dav.move_remote.assert_called_once()

    def test_empty_collection_requires_destination_entry(self):
        del self.entries['A:\\old\\film.mp4']
        self.dav.move('A:\\old', 'A:\\new\\old')
        self.assertIs(self.entries['A:\\new\\old'], True)

    def test_nested_parent_is_created_in_order(self):
        self.dav.move('A:\\old\\film.mp4', 'A:\\deep\\nested\\film.mp4')
        self.assertEqual([r[1] for r in self.requests if r[0] == 'MKCOL'], ['A:\\deep', 'A:\\deep\\nested'])

    def test_cross_drive_is_rejected_before_network(self):
        with self.assertRaises(ValueError):
            self.dav.move('A:\\old\\film.mp4', 'R:\\film.mp4')
        self.assertFalse(self.requests)

    def test_cross_parent_rename_is_rejected_before_submission(self):
        with self.assertRaises(ValueError):
            self.dav.move('A:\\old\\film.mp4', 'A:\\new\\title.mp4')
        self.dav.move_remote.assert_not_called()
        self.dav.rename.assert_not_called()
        self.assertFalse(self.requests)

    def test_unicode_path_is_utf8_percent_encoded(self):
        self.assertEqual(self.dav.url('A:\\创作者\\a b.mp4'), DAV_ORIGIN + '/%E5%88%9B%E4%BD%9C%E8%80%85/a%20b.mp4')

    def test_creator_handle_keeps_the_literal_at_sign(self):
        self.assertEqual(self.dav.url('A:\\old\\creator@handle'), DAV_ORIGIN + '/old/creator@handle')

    def test_delayed_listing_only_rechecks_the_single_submitted_move(self):
        self.dav.move_remote = Mock(return_value=ACCEPTED)
        self.dav.confirm_attempts = 4
        reads = 0
        def refresh(path):
            nonlocal reads
            reads += 1
            if reads == 3:
                self.entries['A:\\new\\film.mp4'] = self.entries.pop('A:\\old\\film.mp4')
        self.dav.refresh = refresh
        with patch.object(organize_clouddrive.time, 'sleep'):
            self.dav.move('A:\\old\\film.mp4', 'A:\\new\\film.mp4')
        self.dav.move_remote.assert_called_once()
        self.assertEqual(self.entries['A:\\new\\film.mp4'], 20)

    def test_cache_failure_after_submission_preserves_intent(self):
        self.dav.move_remote = Mock(return_value=ACCEPTED)
        self.dav.refresh = Mock(side_effect=OSError('未取得'))
        with self.assertRaises(UnconfirmedMove):
            self.dav.move('A:\\old\\film.mp4', 'A:\\new\\film.mp4')
        self.dav.move_remote.assert_called_once()


class OfficialMoveProtocol(unittest.TestCase):
    def test_conflict_skip_and_false_optional_flags_are_encoded(self):
        cloud = Mock(token='test-token')
        cloud.channel.unary_unary.return_value.return_value = SimpleNamespace(success=True)
        self.assertEqual(organize_clouddrive._move_remote(cloud, 'A:\\old\\film.mp4', 'A:\\new\\film.mp4'), ACCEPTED)
        request = cloud.channel.unary_unary.return_value.call_args.args[0]
        self.assertEqual(list(request.theFilePaths), ['/Pikpak/old/film.mp4'])
        self.assertEqual(request.destPath, '/Pikpak/new')
        self.assertEqual(request.conflictPolicy, 2)
        for flag in ('moveAcrossClouds', 'handleConflictRecursively'):
            self.assertTrue(request.HasField(flag))
            self.assertFalse(getattr(request, flag))

    def test_path_boundaries_stop_before_rpc(self):
        cloud = Mock(token='test-token')
        for target in ('R:\\film.mp4', 'A:\\new\\other.mp4', 'A:\\old\\film.mp4', 'A:\\new\\..\\film.mp4'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                organize_clouddrive._move_remote(cloud, 'A:\\old\\film.mp4', target)
        for target in ('A:\\new\\title.mp4', 'R:\\old\\title.mp4'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                organize_clouddrive._rename_remote(cloud, 'A:\\old\\film.mp4', target)
        cloud.channel.unary_unary.assert_not_called()

    def test_rpc_timeout_has_no_automatic_resubmission(self):
        for submit in (organize_clouddrive._move_remote, organize_clouddrive._rename_remote):
            with self.subTest(submit=submit.__name__):
                cloud = Mock(token='test-token')
                cloud.channel.unary_unary.return_value.side_effect = Status(grpc.StatusCode.DEADLINE_EXCEEDED)
                target = 'A:\\new\\film.mp4' if submit is organize_clouddrive._move_remote else 'A:\\old\\title.mp4'
                self.assertIsNone(submit(cloud, 'A:\\old\\film.mp4', target))
                cloud.channel.unary_unary.return_value.assert_called_once()

    def test_request_rejections_are_deterministic(self):
        for code in (grpc.StatusCode.INVALID_ARGUMENT, grpc.StatusCode.PERMISSION_DENIED):
            for submit, target in ((organize_clouddrive._move_remote, 'A:\\new\\film.mp4'),
                                   (organize_clouddrive._rename_remote, 'A:\\old\\title.mp4')):
                with self.subTest(code=code, submit=submit.__name__):
                    cloud = Mock(token='test-token')
                    cloud.channel.unary_unary.return_value.side_effect = Status(code)
                    self.assertEqual(submit(cloud, 'A:\\old\\film.mp4', target), REJECTED)

    def test_rename_reply_maps_to_submission_status(self):
        for success, expected in ((True, ACCEPTED), (False, REJECTED)):
            with self.subTest(success=success):
                cloud = Mock(token='test-token')
                cloud.channel.unary_unary.return_value.return_value = SimpleNamespace(success=success)
                self.assertEqual(organize_clouddrive._rename_remote(cloud, 'A:\\old\\film.mp4', 'A:\\old\\title.mp4'), expected)
                request = cloud.channel.unary_unary.return_value.call_args.args[0]
                self.assertEqual((request.path, request.newName), ('/Pikpak/old/film.mp4', 'title.mp4'))

    def test_cache_scope_rejects_another_drive(self):
        cloud = Mock(token='test-token')
        with self.assertRaises(ValueError):
            organize_clouddrive._refresh_listing(cloud, 'B:\\MVP')
        cloud.channel.unary_unary.assert_not_called()


class OfficialDirectoryEvidence(unittest.TestCase):
    class Stream:
        def __init__(self, rows):
            self.rows, self.cancelled = rows, False
        def __iter__(self):
            yield SimpleNamespace(subFiles=self.rows)
        def cancel(self):
            self.cancelled = True

    def setUp(self):
        self.paths, self.streams = [], []
        self.entries = {'/Pikpak': [('old', True, 0)], '/Pikpak/old': [('creator@handle', True, 0)],
                        '/Pikpak/old/creator@handle': [('film.mp4', False, 20)]}
        def read(request, **kwargs):
            self.paths.append((request.path, request.forceRefresh))
            rows = [SimpleNamespace(fullPathName=request.path + '/' + name, isDirectory=directory, size=size)
                    for name, directory, size in self.entries[request.path]]
            stream = self.Stream(rows)
            self.streams.append(stream)
            return stream
        self.cloud = Mock(token='test-token')
        self.cloud.channel.unary_stream.return_value = read

    def test_ancestor_listings_confirm_the_complete_unicode_directory(self):
        rows = organize_clouddrive._cloud_listing(self.cloud, 'A:\\old\\creator@handle')
        self.assertEqual(rows, [dict(path='A:\\old\\creator@handle\\film.mp4', directory=False, size=20)])
        self.assertEqual(self.paths, [('/Pikpak', True), ('/Pikpak/old', True), ('/Pikpak/old/creator@handle', True)])
        self.assertTrue(all(stream.cancelled for stream in self.streams))

    def test_missing_ancestor_does_not_read_an_unverified_child(self):
        self.entries['/Pikpak/old'] = []
        with self.assertRaises(FileNotFoundError):
            organize_clouddrive._cloud_listing(self.cloud, 'A:\\old\\creator@handle')
        self.assertEqual(len(self.paths), 2)

    def test_foreign_drive_never_uses_the_existing_credential(self):
        with self.assertRaises(ValueError):
            organize_clouddrive._cloud_listing(self.cloud, 'R:\\Media')
        self.assertFalse(self.paths)

    def test_duplicate_members_stop_and_cancel_the_stream(self):
        self.entries['/Pikpak/old/creator@handle'] *= 2
        with self.assertRaises(ValueError):
            organize_clouddrive._cloud_listing(self.cloud, 'A:\\old\\creator@handle')
        self.assertTrue(all(stream.cancelled for stream in self.streams))


class AuthenticationSamples(unittest.TestCase):
    class Missing(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.NOT_FOUND

    def test_moved_sample_uses_existing_catalog_sample(self):
        cloud = Mock(token='test-token')
        reply = SimpleNamespace(directUrl=DAV_ORIGIN + '/available/image.jpg', userAgent='', additionalHeaders={'Authorization': 'test-credential'})
        stub = cloud.channel.unary_unary.return_value
        stub.side_effect = [self.Missing(), reply]
        with patch.object(organize_clouddrive, '_fallback_samples', return_value=['A:\\available\\image.jpg']):
            headers = organize_clouddrive._download_headers(cloud, 'A:\\moved\\image.jpg')
        self.assertEqual(headers, {'Authorization': 'test-credential'})
        self.assertEqual([c.args[0].path for c in stub.call_args_list], ['/Pikpak/moved/image.jpg', '/Pikpak/available/image.jpg'])

    def test_missing_samples_stop_after_three_calls(self):
        cloud = Mock(token='test-token')
        stub = cloud.channel.unary_unary.return_value
        stub.side_effect = self.Missing()
        with patch.object(organize_clouddrive, '_fallback_samples', return_value=['A:\\first.jpg', 'A:\\second.jpg']), self.assertRaises(ValueError):
            organize_clouddrive._download_headers(cloud, 'A:\\moved.jpg')
        self.assertEqual(stub.call_count, 3)

    def test_foreign_download_source_is_rejected(self):
        cloud = Mock(token='test-token')
        cloud.channel.unary_unary.return_value.return_value = SimpleNamespace(directUrl='https://example.test/image.jpg', additionalHeaders={}, userAgent='')
        with self.assertRaises(ValueError):
            organize_clouddrive._download_headers(cloud, 'A:\\image.jpg')
