"""同一发行的播放版本：版次、规格与分卷，全部由查询派生。"""
from __future__ import annotations

import re
from pathlib import PureWindowsPath

from .catalog_rules import (
    names_without_shared_part_tail, normalise_code_key, ordered_multipart_items,
    _jav_code_pattern, duration_clusters, part_marker, release_code_from_filename,
)
from .field_owners import owner_of
from .metadata import identifies_code


def resolution_label(row: dict) -> str:
    """探测尺寸优先；没有尺寸时读取显式的清晰度标记。"""
    width, height = int(row.get('width') or 0), int(row.get('height') or 0)
    short = min(width, height)
    if short > 0:
        return next((label for floor, label in ((2000, '4K'), (1400, '1440p'),
                    (1000, '1080p'), (700, '720p')) if short >= floor), '480p')
    match = re.search(r'(?i)(?<![a-z0-9])(2160p|4k|1080p|720p|480p)(?!\d)',
                      str(row.get('name') or ''))
    return ('4K' if match[1].lower() in ('2160p', '4k') else match[1].lower()) if match else ''


def version_signature(row: dict) -> tuple[str, str, str]:
    name = str(row.get('name') or '')
    processing = '修复' if '修复' in name else '美颜' if '美颜' in name else ''
    return str(row.get('edition') or '有码'), resolution_label(row), processing


def filename_matches_code(row: dict) -> bool:
    """核对显式文件号；通用文件名使用已刮削标题与所属番号目录。"""
    code = normalise_code_key(row.get('code'))
    name = str(row.get('name') or '')
    extracted = release_code_from_filename(name)
    if extracted:
        return identifies_code(code, {'id': extracted})
    if identifies_code(code, {'content_id': PureWindowsPath(name).stem}):
        return True
    pattern = _jav_code_pattern(code)
    if pattern and re.search(rf'(?<![A-Z0-9]){pattern}(?!\d)', name, re.I):
        return True
    owner = owner_of(row.get('field_owners'), 'catalog_title')
    scraped = bool(row.get('catalog_title') and owner.startswith(('auto:', 'review:', 'user:')))
    return scraped and any(identifies_code(code, {'id': component})
                           for component in PureWindowsPath(str(row.get('path') or '')).parts[:-1])


def _version(items: list[dict], *, multipart: bool = False) -> dict:
    durations = [max(0, float(row.get('duration') or 0)) for row in items]
    sizes = [max(0, int(row.get('size') or 0)) for row in items]
    return {'id': items[0]['id'], 'signature': version_signature(items[0]), 'items': items,
            'multipart': multipart, 'duration': sum(durations) if multipart else max(durations),
            'size': sum(sizes) if multipart else max(sizes)}


def partition_versions(items: list[dict]) -> list[dict]:
    """卷号有唯一解释时成套；同规格的整片与显式分卷分别保留。"""
    ordered = ordered_multipart_items(items)
    if ordered:
        return [_version(ordered, multipart=True)]
    signatures = {version_signature(row) for row in items}
    buckets: dict[tuple, list[dict]] = {}
    for item in sorted(items, key=lambda row: int(row['id'])):
        buckets.setdefault(version_signature(item), []).append(item)
    if (any(not signature[1] for signature in signatures) and len(signatures) > 1
            and len({(signature[0], signature[2]) for signature in signatures}) == 1
            and not all(ordered_multipart_items(bucket) for bucket in buckets.values())):
        return [_version([row]) for row in items]
    versions = []
    for bucket in buckets.values():
        ordered = ordered_multipart_items(bucket)
        if ordered:
            versions.append(_version(ordered, multipart=True))
            continue
        stripped = names_without_shared_part_tail(bucket)
        marked = [row for index, row in enumerate(bucket)
                  if part_marker(str(row.get('name') or ''))
                  or (stripped and part_marker(stripped[index]))]
        ordered = ordered_multipart_items(marked)
        # 多份同一卷有歧义，交给重复文件复核；不挑一份冒充完整的分卷集。
        if ordered:
            versions.append(_version(ordered, multipart=True))
        claimed = {row['id'] for row in ordered}
        remaining = [row for row in bucket if row['id'] not in claimed]
        versions.extend(_version(cluster) for cluster in duration_clusters(remaining))
    return sorted(versions, key=lambda version: int(version['id']))


def foldable_versions(versions: list[dict]) -> bool:
    """版次差异可直接折叠；规格与整片/分卷差异须有文件番号和时长证据。"""
    if len(versions) < 2:
        return False
    if len({version['signature'][0] for version in versions}) > 1:
        return True
    signatures = {version['signature'] for version in versions}
    if (any(not signature[1] for signature in signatures) and len(signatures) > 1
            and len({(signature[0], signature[2]) for signature in signatures}) == 1):
        return False
    if (len({version['signature'] for version in versions}) < 2
            and len({version['multipart'] for version in versions}) < 2):
        return False
    rows = [row for version in versions for row in version['items']]
    if not all(filename_matches_code(row) for row in rows):
        return False
    if any(float(row.get('duration') or 0) <= 0 for row in rows):
        return False
    durations = [version['duration'] for version in versions]
    return max(durations) - min(durations) <= max(15, max(durations) * .10)


def version_labels(versions: list[dict]) -> list[str]:
    """只在组内需要区分的维度上显示规格，卷数单独承载。"""
    show_spec = len({version['signature'] for version in versions}) > 1
    same_editions = len({version['signature'][0] for version in versions}) == 1
    forms = len({version['multipart'] for version in versions}) > 1
    labels = []
    for version in versions:
        edition, resolution, processing = version['signature']
        spec = (resolution + (' ' if resolution and processing else '') + processing).strip()
        label = spec if show_spec and same_editions and spec else edition
        if show_spec and not same_editions and spec:
            label += ' · ' + spec
        if forms and len({version['signature'] for version in versions}) == 1:
            label += ' · ' + ('分卷版' if version['multipart'] else '完整文件')
        labels.append(label)
    return labels
