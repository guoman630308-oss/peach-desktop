"""为指定的出演者保留 Babepedia 头像候选和精确匹配的官方作品封面。

头像只在 Babepedia 主图与她单人作品封面比上脸时自动装，账号一律只留候选。
封面边车记 `source` 与 `batch`，`revert_auto_landing.py --source auto:western-artwork` 按批撤回。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from peach import avatar_cover_face, avatar_picker
from peach.avatar_provider import AvatarCandidateCache, install_entity_avatar
from peach.config import DATABASE_PATH, GENERATED_DIR, COVER_DIR, REVIEW_DIR
from peach.cover_artwork import install_cover
from peach.entities import resolve_entity_id
from peach.http import HttpxTransport
from peach.review_csv import read_rows
from peach.scripting import open_readonly
from peach.western_artwork import (SITES, SOURCE, artwork_key, babepedia_portraits, face_matched_portrait,
                                   filename_date, official_scene)


def profile_urls(connection, candidates: Path, ids: list[int]) -> dict[int, str]:
    """按当前实体身份解析候选表中判词为「命中」的 Babepedia 资料地址。"""
    profiles = {}
    for candidate in read_rows(candidates) if candidates.is_file() else ():
        old_id = str(candidate.get('entity_id') or '')
        if old_id.isdigit() and candidate.get('profile_url') and candidate.get('verdict') == '命中':
            resolved = resolve_entity_id(connection, int(old_id))
            if resolved in ids:
                profiles.setdefault(resolved, candidate['profile_url'])
    return profiles


def install_portrait(args, connection, avatar_root: Path, item: dict, fetched: list) -> None:
    """出演者的 Babepedia 主图与她单人作品封面比上脸才装；账号只留候选。"""
    if item['kind'] != 'performer':
        item['avatar'] = '账号不自动装头像，只留候选'
        return
    from peach.avatar_face import FaceProbe
    from peach.face_match import FaceMatcher

    covers = avatar_cover_face.faces(connection, args.covers, item['id'], FaceProbe())
    winner, evidence, reason = face_matched_portrait(fetched, covers, FaceMatcher())
    if winner is None:
        item['avatar'] = reason
        return
    install_entity_avatar(avatar_root, 'performer', item['id'], winner.body, winner.inspected.mime_type,
                          {**winner.origin, 'source_url': winner.origin['upstream_url'],
                           'source_kind': 'gallery_face_matched', 'face_match': evidence})
    item['installed'] = winner.inspected.sha256


def run(args) -> dict:
    """写入仅涉及可重建图片与来源记录，不改 ledger。"""
    providers = args.generated / 'provider-cache' / 'performer-avatars'
    avatar_root = args.generated / 'avatars'
    result = {'apply': args.apply, 'entities': [], 'covers': []}
    http = HttpxTransport()
    connection = open_readonly(args.db)
    try:
        ids = sorted({resolve_entity_id(connection, raw) for raw in args.entity} - {None})
        if len(ids) > 16:
            raise ValueError('每轮至多 16 位')
        profiles = profile_urls(connection, args.generated / 'babepedia-candidates.csv', ids)
        seen_assets = set()
        for entity_id in ids:
            row = connection.execute('SELECT kind,canonical_name FROM entity WHERE id=?', (entity_id,)).fetchone()
            if not row or row[0] not in ('performer', 'creator'):
                continue
            kind, name = row
            item = {'id': entity_id, 'kind': kind, 'name': name, 'portraits': [], 'issues': []}
            result['entities'].append(item)
            aliases = avatar_picker.name_chain(connection, entity_id)
            try:
                portraits = babepedia_portraits(http, name, aliases, profile_url=profiles.get(entity_id, ''))
                fetched = []
                for origin in portraits:
                    try:
                        cache = AvatarCandidateCache(providers / 'babepedia')
                        body = cache.lookup(origin['upstream_url'])
                        if body is None:
                            # 本人档案解析器已限定为 Babepedia 的 HTTPS 图片路径。
                            body = avatar_picker.fetch_image(http, origin['upstream_url'])
                        inspected = avatar_picker.accept_image(body)
                        item['portraits'].append({**origin, 'sha256': inspected.sha256,
                                                 'width': inspected.width, 'height': inspected.height})
                        if origin.get('automatic_install'):
                            fetched.append((origin, body, inspected))
                        if args.apply:
                            avatar_picker.keep(providers, entity_id, body, origin)
                    except Exception as error:
                        item['issues'].append({'source_url': origin['upstream_url'], 'error': str(error)})
                    finally:
                        time.sleep(args.delay)
                if args.apply and fetched and not (avatar_root / f'{kind}-{entity_id}.img').exists():
                    install_portrait(args, connection, avatar_root, item, fetched)
            except Exception as error:
                item['issues'].append(str(error))
            if args.portraits_only:
                continue
            works = connection.execute('SELECT a.id,a.name,a.code,a.studio,a.release_date FROM asset a '
                'JOIN asset_entity ae ON ae.asset_id=a.id WHERE ae.entity_id=? '
                "AND a.medium='video' AND a.disposal IS NULL ORDER BY a.id LIMIT 30", (entity_id,)).fetchall()
            for asset_id, filename, code, studio, released in works:
                site = str(studio or '').casefold().replace(' ', '')
                if code or site not in SITES or asset_id in seen_assets:
                    continue
                seen_assets.add(asset_id)
                released = released or filename_date(filename, site)
                if not released:
                    continue
                record = {'asset_id': asset_id, 'entity_id': entity_id, 'site': site}
                result['covers'].append(record)
                try:
                    scene = official_scene(http, site, name, released)
                    if scene is None:
                        record['issue'] = '官方封面未取得：没有唯一的名字与日期匹配'
                        continue
                    record.update(scene)
                    body = avatar_picker.fetch_image(http, scene['image_url'])
                    inspected = avatar_picker.accept_image(body)
                    record.update({'sha256': inspected.sha256, 'width': inspected.width,
                                   'height': inspected.height})
                    if args.apply:
                        target = args.covers / f'{artwork_key(asset_id, None)}.jpg'
                        if target.exists():
                            record['preserved_existing'] = True
                        else:
                            # 官方横版剧照不使用 JAV 双联封套判据。
                            install_cover(target, '', body, (inspected.width, inspected.height),
                                          evidence={**record, 'provider': 'western-official',
                                                    'source': SOURCE, 'batch': args.batch})
                            record['installed'] = str(target)
                except Exception as error:
                    record['issue'] = str(error)
                finally:
                    time.sleep(args.delay)
    finally:
        connection.close()
        http.close()
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    return result


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DATABASE_PATH)
    parser.add_argument('--generated', type=Path, default=GENERATED_DIR)
    parser.add_argument('--covers', type=Path, default=COVER_DIR)
    parser.add_argument('--out', type=Path, default=REVIEW_DIR / 'western-artwork.json')
    parser.add_argument('--entity', type=int, action='append', required=True)
    parser.add_argument('--delay', type=float, default=3)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--portraits-only', action='store_true')
    parser.add_argument('--batch', default=SOURCE + '@' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
                        help='封面边车记的批次，revert_auto_landing.py 按它整批撤回')
    return parser


if __name__ == '__main__':
    args = build_parser().parse_args()
    if args.delay < 1:
        raise SystemExit('请求间隔至少 1 秒')
    if not args.batch.startswith(SOURCE + '@'):
        raise SystemExit(f'批次需要以 {SOURCE}@ 开头')
    print(json.dumps(run(args), ensure_ascii=False))
