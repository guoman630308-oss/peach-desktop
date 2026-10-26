"""PikPak 的同来源官方移动与有界核验。

改名与移动只走 CloudDrive 官方接口：同父改名用 `RenameFile`，保留名称的跨目录移动用
`MoveFile`；目录证据只取 `GetSubFiles(forceRefresh=True)` 的完整成员。HTTPS WebDAV 只用来
`MKCOL` 建父目录。官方接口的回答不能代替目录证据：`RenameFile` 改文件时回 `success=true`，
目录列表却没变。
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from pathlib import PureWindowsPath
import time
from urllib.parse import quote, unquote, urlsplit

import httpx
import grpc
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.empty_pb2 import Empty

from . import organize
from .downloads_clouddrive import CloudDriveClient, SERVICE, discover
from .downloads import CLOUDDRIVE_CREDENTIAL, DownloadSettings
from .follow_secrets import CredentialStore
from .config import DATABASE_PATH, SECRETS_DIR, STATE_DIR
from .scripting import open_readonly

MAX_RESPONSE = 2 * 1024 * 1024
MAX_FILES = 10000
DAV_ORIGIN = 'https://dav.mypikpak.com'
#: 提交结果：官方接口确认执行、确定性拒绝；`None` 表示没取得回答。
ACCEPTED, REJECTED = 200, 403
#: 官方接口对请求本身的拒绝。原样重提只会得到同样的回答，原件仍在原处。
REJECTED_CODES = frozenset({grpc.StatusCode.INVALID_ARGUMENT, grpc.StatusCode.PERMISSION_DENIED,
                            grpc.StatusCode.NOT_FOUND, grpc.StatusCode.ALREADY_EXISTS,
                            grpc.StatusCode.FAILED_PRECONDITION})


class UnconfirmedMove(SystemExit):
    """原件与目标都在或都不在、成员不符或目录读不到：状态未知，保留意图并停止当前进程。"""


class MoveNotExecuted(FileNotFoundError):
    """官方目录确认原件仍在、目标未出现：本条未执行，可记失败后继续下一条。"""


def _submission_status(error: grpc.RpcError):
    code = error.code() if callable(getattr(error, 'code', None)) else None
    return REJECTED if code in REJECTED_CODES else None


def _messages():
    """CloudDrive 官方 GetDownloadUrlPath/GetSubFiles 消息的所需字段。"""
    from . import downloads_clouddrive_pb2 as pb
    proto = descriptor_pb2.FileDescriptorProto(
        name='peach_organize_cloud.proto', package='peach_organize_cloud', syntax='proto3')
    proto.dependency.append(pb.DESCRIPTOR.name)
    request = proto.message_type.add(name='DownloadRequest')
    request.field.add(name='path', number=1, type=9, label=1)
    request.field.add(name='get_direct_url', number=4, type=8, label=1)
    reply = proto.message_type.add(name='DownloadReply')
    reply.field.add(name='directUrl', number=3, type=9, label=1)
    reply.field.add(name='userAgent', number=4, type=9, label=1)
    entry = reply.nested_type.add(name='HeadersEntry')
    entry.options.map_entry = True
    entry.field.add(name='key', number=1, type=9, label=1)
    entry.field.add(name='value', number=2, type=9, label=1)
    reply.field.add(name='additionalHeaders', number=5, type=11, label=3,
                    type_name='.peach_organize_cloud.DownloadReply.HeadersEntry')
    listing = proto.message_type.add(name='ListRequest')
    listing.field.add(name='path', number=1, type=9, label=1)
    listing.field.add(name='forceRefresh', number=2, type=8, label=1)
    result = proto.message_type.add(name='ListReply')
    result.field.add(name='subFiles', number=1, type=11, label=3,
                     type_name='.clouddrive.CloudDriveFile')
    rename = proto.message_type.add(name='RenameRequest')
    rename.field.add(name='path', number=1, type=9, label=1)
    rename.field.add(name='newName', number=2, type=9, label=1)
    move = proto.message_type.add(name='MoveRequest')
    move.field.add(name='theFilePaths', number=1, type=9, label=3)
    move.field.add(name='destPath', number=2, type=9, label=1)
    policy = move.enum_type.add(name='ConflictPolicy')
    for name, number in (('Overwrite', 0), ('Rename', 1), ('Skip', 2)):
        policy.value.add(name=name, number=number)
    move.field.add(name='conflictPolicy', number=3, type=14, label=1,
                   type_name='.peach_organize_cloud.MoveRequest.ConflictPolicy')
    for name, number in (('moveAcrossClouds', 4), ('handleConflictRecursively', 5)):
        index = len(move.oneof_decl)
        move.oneof_decl.add(name='_' + name)
        move.field.add(name=name, number=number, type=8, label=1, proto3_optional=True, oneof_index=index)
    pool = descriptor_pool.DescriptorPool()
    pool.AddSerializedFile(pb.DESCRIPTOR.dependencies[0].serialized_pb)
    pool.AddSerializedFile(pb.DESCRIPTOR.serialized_pb)
    pool.Add(proto)
    return tuple(message_factory.GetMessageClass(pool.FindMessageTypeByName('peach_organize_cloud.' + name))
                 for name in ('DownloadRequest', 'DownloadReply', 'ListRequest', 'ListReply', 'RenameRequest', 'MoveRequest'))


DownloadRequest, DownloadReply, ListRequest, ListReply, RenameRequest, MoveRequest = _messages()


class PikPakDav:
    """A 盘的一次移动：官方接口提交一次，官方完整目录确认结果。

    `listing(path)` 返回官方目录的完整成员；`rename`／`move_remote` 提交同父改名与保留名称的
    跨目录移动，回 `ACCEPTED`、`REJECTED` 或 `None`。
    """

    def __init__(self, http: httpx.Client, root: str, refresh, *, listing, rename, move_remote, confirm_attempts=1):
        self.http, self.root, self.refresh = http, PureWindowsPath(root), refresh
        self.rename = rename
        self.listing = listing
        self.move_remote = move_remote
        if not 1 <= confirm_attempts <= 4:
            raise ValueError('移动确认次数越出预算')
        self.confirm_attempts = confirm_attempts
        if self.root != PureWindowsPath('A:\\'):
            raise ValueError('HTTPS WebDAV 整理只支持已声明的 A 盘根目录')

    def url(self, path: str) -> str:
        item = PureWindowsPath(path)
        if item.drive.casefold() != self.root.drive.casefold() or '..' in item.parts or not item.is_absolute():
            raise ValueError('WebDAV 路径越出已声明来源')
        return DAV_ORIGIN + quote('/' + '/'.join(item.parts[1:]), safe='/@')

    def request(self, method, path, **kwargs):
        try:
            with self.http.stream(method, self.url(path), **kwargs) as response:
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE:
                        raise ValueError('WebDAV 响应超过预算')
                    chunks.append(chunk)
                return response.status_code, b''.join(chunks)
        except httpx.HTTPError:
            raise OSError('WebDAV 网络应答未取得') from None

    def list(self, path):
        self.url(path)
        return self.listing(path)

    def tree(self, path):
        pending, files, visited = [path], [], set()
        while pending:
            parent = pending.pop()
            if parent in visited or len(visited) >= MAX_FILES:
                raise ValueError('WebDAV 目录遍历超过预算或循环')
            visited.add(parent)
            for entry in self.list(parent):
                if entry['directory']:
                    pending.append(entry['path'])
                else:
                    files.append(entry)
            if len(files) > MAX_FILES:
                raise ValueError('WebDAV 文件成员超过预算')
        return files

    def ensure_parent(self, path):
        parent = PureWindowsPath(path).parent
        if parent == self.root:
            return
        self.ensure_parent(str(parent))
        grandparent = str(parent.parent)
        listing = self.list(grandparent)
        matches = [r for r in listing if PureWindowsPath(r['path']) == parent]
        if matches:
            if not matches[0]['directory']:
                raise ValueError('目标父路径不是目录')
            return
        status, _ = self.request('MKCOL', str(parent))
        if status != 201:
            raise OSError('WebDAV 创建父目录失败：HTTP ' + str(status))
        self.refresh(grandparent)

    def submit_move(self, source, target):
        if PureWindowsPath(source).parent == PureWindowsPath(target).parent:
            return self.rename(source, target)
        return self.move_remote(source, target)

    def move(self, source, target):
        self.url(source)
        self.url(target)
        old, new = PureWindowsPath(source), PureWindowsPath(target)
        if old.parent != new.parent and old.name != new.name:
            raise ValueError('官方接口只接受同父改名或保留名称的移动；跨父改名须先拆成两步')
        old_parent, new_parent = str(old.parent), str(new.parent)
        entries = self.list(old_parent)
        found = [r for r in entries if PureWindowsPath(r['path']) == PureWindowsPath(source)]
        if len(found) != 1:
            raise FileNotFoundError('WebDAV 来源未取得')
        before = self.tree(source) if found[0]['directory'] else found
        self.ensure_parent(target)
        if any(PureWindowsPath(r['path']) == PureWindowsPath(target) for r in self.list(new_parent)):
            raise FileExistsError('WebDAV 目标已存在')
        try:
            response_status = self.submit_move(source, target)
        except OSError:
            response_status = None
        for attempt in range(self.confirm_attempts):
            final = attempt + 1 == self.confirm_attempts
            try:
                for parent in {old_parent, new_parent}:
                    self.refresh(parent)
                self.confirm_move(source, target, found[0], before, response_status, final=final)
                return
            except MoveNotExecuted:
                raise
            except (UnconfirmedMove, OSError, ValueError):
                if final:
                    raise UnconfirmedMove('移动状态未确认，保留意图回执并停止；不重复操作、不写数据库') from None
                time.sleep(2)

    def confirm_move(self, source, target, source_entry, before, response_status, *, final=True):
        """官方目录里原件仍在、目标未出现：被拒绝或确认预算用完时判为未执行，否则再等一轮。"""
        old_parent, new_parent = str(PureWindowsPath(source).parent), str(PureWindowsPath(target).parent)
        try:
            after_parent = self.list(new_parent)
            old_listing = self.list(old_parent)
            arrived = [r for r in after_parent if PureWindowsPath(r['path']) == PureWindowsPath(target)]
            still_present = any(PureWindowsPath(r['path']) == PureWindowsPath(source) for r in old_listing)
            if still_present and not arrived:
                if response_status == REJECTED or final:
                    raise MoveNotExecuted('官方接口拒绝或未执行移动，原件已确认留在原处')
                raise ValueError('移动请求仍可能在服务端处理')
            after = self.tree(target) if source_entry['directory'] else arrived
            expected = {(str(PureWindowsPath(target, *PureWindowsPath(r['path']).parts[len(PureWindowsPath(source).parts):])), r['size'])
                        for r in before}
            if still_present or len(arrived) != 1 or arrived[0]['directory'] != source_entry['directory'] or expected != {(r['path'], r['size']) for r in after}:
                raise ValueError('移动状态或完整成员不符')
            for parent in {old_parent, new_parent}:
                self.refresh(parent)
            if source_entry['directory']:
                self.refresh(target)
        except MoveNotExecuted:
            raise
        except Exception:
            raise UnconfirmedMove('移动状态未确认，保留意图回执并停止；不重复操作、不写数据库') from None


def _cloud_listing(cloud, path):
    """逐层完整读取官方目录元数据并校验来源、父目录与重名。"""
    item = PureWindowsPath(path)
    if item.drive.casefold() != 'a:' or not item.is_absolute() or '..' in item.parts or len(item.parts) > 32:
        raise ValueError('官方目录路径越出 A 盘或超过层级预算')
    previous = None
    for parent in (*reversed(item.parents), item):
        if previous is not None and not any(PureWindowsPath(row['path']) == parent and row['directory'] for row in previous):
            raise FileNotFoundError('官方父目录未列出所需目录')
        request = ListRequest(path=('/Pikpak/' + '/'.join(parent.parts[1:])).rstrip('/'), forceRefresh=True)
        call = cloud.channel.unary_stream(SERVICE + 'GetSubFiles',
            request_serializer=ListRequest.SerializeToString, response_deserializer=ListReply.FromString)
        stream = call(request, timeout=15, metadata=(('authorization', 'Bearer ' + cloud.token),))
        rows, seen = [], set()
        try:
            for response in stream:
                for member in response.subFiles:
                    if not member.fullPathName.startswith('/Pikpak/'):
                        raise ValueError('官方目录成员来自其他来源')
                    name = PureWindowsPath('A:\\' + member.fullPathName[len('/Pikpak/'):].replace('/', '\\'))
                    if name.parent != parent or str(name).casefold() in seen or member.size < 0:
                        raise ValueError('官方目录成员越界、重名或体积无效')
                    seen.add(str(name).casefold())
                    rows.append(dict(path=str(name),size=member.size,directory=member.isDirectory))
                if len(rows) > MAX_FILES:
                    raise ValueError('官方目录成员超过预算')
        except grpc.RpcError as error:
            raise OSError('官方完整目录未取得：' + error.code().name) from None
        finally:
            stream.cancel()
        previous = rows
    return previous


def _fallback_samples():
    with closing(open_readonly(DATABASE_PATH)) as db:
        return [row[0] for row in db.execute(
            "SELECT path FROM asset WHERE location='pikpak' AND disposal IS NULL AND medium='image' "
            "AND path LIKE 'A:%' ORDER BY id LIMIT 2")]


def _move_remote(cloud, source, target):
    """官方同来源移动保持原名，显式跳过冲突并关闭跨云及递归合并。"""
    from . import downloads_clouddrive_pb2 as pb
    old, new = PureWindowsPath(source), PureWindowsPath(target)
    if (old.drive.casefold() != 'a:' or new.drive.casefold() != 'a:' or not old.is_absolute()
            or not new.is_absolute() or '..' in old.parts or '..' in new.parts
            or old.name != new.name or old.parent == new.parent):
        raise ValueError('官方移动来源、父目录或文件名不符')
    request = MoveRequest(theFilePaths=['/Pikpak/' + '/'.join(old.parts[1:])],
                          destPath=('/Pikpak/' + '/'.join(new.parent.parts[1:])).rstrip('/'),
                          conflictPolicy=2, moveAcrossClouds=False, handleConflictRecursively=False)
    stub = cloud.channel.unary_unary(SERVICE + 'MoveFile', request_serializer=MoveRequest.SerializeToString,
                                    response_deserializer=pb.FileOperationResult.FromString)
    try:
        reply = stub(request, timeout=15, metadata=(('authorization', 'Bearer ' + cloud.token),))
        return ACCEPTED if reply.success else REJECTED
    except grpc.RpcError as error:
        return _submission_status(error)


def _rename_remote(cloud, source, target):
    """官方同父改名；`success=true` 只是提交结果，生效与否由调用方读官方目录确认。"""
    from . import downloads_clouddrive_pb2 as pb
    old, new = PureWindowsPath(source), PureWindowsPath(target)
    if (old.drive.casefold() != 'a:' or not old.is_absolute() or '..' in old.parts
            or old.parent != new.parent or '..' in new.parts):
        raise ValueError('官方改名只支持 A 盘内的同父目录')
    stub = cloud.channel.unary_unary(SERVICE + 'RenameFile',
        request_serializer=RenameRequest.SerializeToString, response_deserializer=pb.FileOperationResult.FromString)
    try:
        reply = stub(RenameRequest(path='/Pikpak/' + '/'.join(old.parts[1:]), newName=new.name), timeout=15,
                     metadata=(('authorization', 'Bearer ' + cloud.token),))
        return ACCEPTED if reply.success else REJECTED
    except grpc.RpcError as error:
        return _submission_status(error)


def _refresh_listing(cloud, path):
    """限定 A 盘目录缓存过期后按完整祖先链读取实际成员。"""
    from . import downloads_clouddrive_pb2 as pb
    item = PureWindowsPath(path)
    if item.drive.casefold() != 'a:' or not item.is_absolute() or '..' in item.parts:
        raise ValueError('目录缓存范围越出 A 盘')
    stub = cloud.channel.unary_unary(SERVICE + 'ForceExpireDirCache',
            request_serializer=pb.FileRequest.SerializeToString, response_deserializer=Empty.FromString)
    try:
        stub(pb.FileRequest(path=('/Pikpak/' + '/'.join(item.parts[1:])).rstrip('/')),
             timeout=15, metadata=(('authorization', 'Bearer ' + cloud.token),))
    except grpc.RpcError as error:
        if error.code() != grpc.StatusCode.NOT_FOUND:
            raise OSError('目录缓存刷新未取得：' + error.code().name) from None
    return _cloud_listing(cloud, path)


def _download_headers(cloud, sample_path):
    paths = [sample_path]
    stub = cloud.channel.unary_unary(SERVICE + 'GetDownloadUrlPath',
        request_serializer=DownloadRequest.SerializeToString, response_deserializer=DownloadReply.FromString)
    for index in range(3):
        if index >= len(paths):
            break
        path = PureWindowsPath(paths[index])
        if path.drive.casefold() != 'a:' or not path.is_absolute() or '..' in path.parts:
            raise ValueError('认证样本越出 A 盘来源')
        relative = '/' + '/'.join(path.parts[1:])
        try:
            reply = stub(DownloadRequest(path='/Pikpak' + relative, get_direct_url=True), timeout=15,
                         metadata=(('authorization', 'Bearer ' + cloud.token),))
        except grpc.RpcError as error:
            if error.code() != grpc.StatusCode.NOT_FOUND:
                raise ValueError('既有 CloudDrive 直链认证未取得') from None
            if index == 0:
                paths.extend(p for p in _fallback_samples() if p != sample_path)
            continue
        url = urlsplit(reply.directUrl)
        if url.hostname != 'dav.mypikpak.com' or url.scheme not in ('http', 'https') or url.username or url.query or unquote(url.path) != relative:
            raise ValueError('既有直链来源或路径不符')
        headers = dict(reply.additionalHeaders)
        if not any(k.casefold() == 'authorization' for k in headers):
            raise ValueError('既有 WebDAV 认证未取得')
        if reply.userAgent:
            headers['User-Agent'] = reply.userAgent
        return headers
    raise ValueError('既有 CloudDrive 认证样本未取得')


@contextmanager
def pikpak_renames(sample_path: str, roots):
    """仅显式启用的目录批次复用既有 CloudDrive 认证。"""
    if tuple(roots.get('pikpak', ())) != ('A:\\',):
        raise ValueError('PikPak 来源根必须唯一声明为 A 盘')
    credential = CredentialStore(SECRETS_DIR).load(CLOUDDRIVE_CREDENTIAL)
    if not credential or not credential.values.get('token'):
        raise ValueError('既有 CloudDrive 令牌未取得')
    address = DownloadSettings(STATE_DIR).load().clouddrive_address or discover()
    with CloudDriveClient(address, credential.values['token'], timeout=15) as cloud:
        info = cloud.find('/Pikpak')
        if info is None or info.CloudAPI.name.casefold() != 'webdav':
            raise ValueError('既有 PikPak WebDAV 来源未取得')
        headers = _download_headers(cloud, sample_path)
        with httpx.Client(headers=headers, timeout=20, follow_redirects=False) as http:
            dav = PikPakDav(http, 'A:\\', lambda path: _refresh_listing(cloud, path),
                            listing=lambda path: _cloud_listing(cloud, path),
                            rename=lambda source, target: _rename_remote(cloud, source, target),
                            move_remote=lambda source, target: _move_remote(cloud, source, target),
                            confirm_attempts=4)
            with organize.verified_renames('pikpak', dav.move):
                yield dav
