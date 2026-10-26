"""Kodi/Jellyfin 影片及单集 NFO 的本地边车适配。"""
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from .catalog_rules import release_code_from_filename, release_code_from_text, same_release_code
from .scan import VIDEO


def directory_files(directory: Path) -> dict[str, Path]:
    """目录里的普通文件，按 casefold 文件名索引。

    走 `os.scandir`：Windows 的目录列表自带类型，`is_file()` 不再逐个 stat。网盘挂载上
    一次 stat 就是一趟往返，几百个文件的文件夹用 `iterdir()` 要付几百趟。
    """
    with os.scandir(directory) as entries:
        return {entry.name.casefold(): Path(entry.path) for entry in entries if entry.is_file()}


POSTER_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.tbn')
_NUMBERED = re.compile(r'(.*\D|)(\d+)(\D*)')


class SidecarIndex:
    """目录的影片、NFO 和连号图片分组；一份索引服务同目录的全部影片。"""

    def __init__(self, files: dict[str, Path]):
        self.files = files
        self.videos = []
        images = []
        self.nfos = []
        for path in files.values():
            suffix = path.suffix.lower()
            if suffix in VIDEO:
                self.videos.append(path)
            elif suffix == '.nfo':
                self.nfos.append((path, release_code_from_filename(path.stem)))
            elif suffix in POSTER_EXTENSIONS:
                images.append(path)
        self.single = len(self.videos) == 1
        stems = {path.stem.casefold() for path in self.videos}
        self.numbered = {}
        for image in images:
            match = _NUMBERED.fullmatch(image.stem.casefold())
            if match is not None and image.stem.casefold() not in stems:
                self.numbered.setdefault(match.group(1, 3), set()).add(match.group(2))
        self.shared = {}
        self.video_codes = None

    def _shared_movie(self, code):
        if self.single:
            return self.files.get('movie.nfo')
        if not code or 'movie.nfo' not in self.files:
            return None
        if self.video_codes is None:
            self.video_codes = [release_code_from_filename(path.name) for path in self.videos]
        if code not in self.shared:
            self.shared[code] = all(same_release_code(code, value) for value in self.video_codes)
        return self.files.get('movie.nfo') if self.shared[code] else None

    def for_video(self, video: Path):
        nfo = self.files.get((video.stem + '.nfo').casefold())
        if nfo is None and self.nfos:
            code = release_code_from_filename(video.name)
            matching = [path for path, value in self.nfos if code and same_release_code(code, value)]
            if len(matching) == 1:
                nfo = matching[0]
            elif not matching:
                nfo = self._shared_movie(code)
        names = [video.stem + '-poster']
        match = _NUMBERED.fullmatch(video.stem.casefold())
        numbered = bool(match and self.numbered.get(match.group(1, 3), set()) - {match.group(2)})
        if not numbered:
            names.append(video.stem)
        if self.single:
            names.extend(['poster', 'folder', 'cover'])
        posters = [self.files[name.casefold() + extension] for name in names
                   for extension in POSTER_EXTENSIONS if name.casefold() + extension in self.files]
        return nfo, posters


def sidecars(video: Path, files: dict[str, Path] | None = None):
    """`video` 旁边的 NFO 与海报候选；`files` 是调用方已列好的同目录索引，省掉再列一遍。"""
    files = directory_files(video.parent) if files is None else files
    return SidecarIndex(files).for_video(video)


def read_nfo(path: Path):
    """保留完整原文；拒绝 DTD，并限制 XML 的输入体积。"""
    with path.open('rb') as handle:
        raw = handle.read(1024 * 1024 + 1)
    # XML 的 UTF-16/32 编码包含零字节；声明检查同样覆盖这些编码。
    declarations = raw.replace(b'\x00', b'').upper()
    if len(raw) > 1024 * 1024 or b'<!DOCTYPE' in declarations or b'<!ENTITY' in declarations:
        raise ValueError('NFO 文件过大或包含实体声明')
    root = ET.fromstring(raw)
    if root.tag not in {'movie', 'episodedetails', 'musicvideo'}:
        raise ValueError('NFO 不是影片或单集资料')
    def text(*keys):
        return next(((root.findtext(key) or '').strip() for key in keys
                     if (root.findtext(key) or '').strip()), '')
    identifiers = [node.text for node in root.findall('uniqueid')
                   if node.get('type', '').lower() in {'javboss', 'jav', 'javid', 'dvdid', 'num'}]
    identifiers.extend([text('num'), text('sorttitle')])
    # 通用 id 常是 IMDb/TMDb；仅明确的番号形状参与 JAV 身份识别。
    for value in [text('id'), *(node.text or '' for node in root.findall('uniqueid'))]:
        if re.fullmatch(r'(?:[A-Za-z]{2,12}[-_]\d{2,8}|FC2[-_](?:PPV[-_])?\d+)', value.strip(), re.I):
            identifiers.append(value)
    codes = {code for value in identifiers if (code := release_code_from_text(value or ''))}
    if len(codes) > 1:
        raise ValueError('NFO 包含多个不同番号')
    genres = list(dict.fromkeys(node.text.strip() for key in ('genre', 'tag')
                  for node in root.findall(key) if node.text and node.text.strip()))
    payload = dict(id=next(iter(codes), ''), title=text('title', 'localtitle', 'name'),
        original_title=text('originaltitle'), maker=text('studio'),
        series=text('set/name', 'set', 'showtitle'),
        release_date=text('premiered', 'releasedate', 'aired'),
        director=text('director'), runtime=text('runtime'),
        actresses=[{'japanese_name': (node.findtext('name') or '').strip()}
                   for node in root.findall('actor')], genres=genres, local_tags=genres,
        nfo_kind=root.tag, source_generator=text('generator'),
        local_art=text('art/poster', 'thumb[@aspect="poster"]', 'thumb'))
    return payload, raw


def local_art(video: Path, payload: dict):
    """仅读取影片所在目录内的本地图片，远端引用保留在原文。"""
    value = payload.get('local_art', '')
    if not value or '://' in value:
        return None
    path = (video.parent / value).resolve()
    if path.is_relative_to(video.parent.resolve()) and path.is_file():
        return path
    return None
