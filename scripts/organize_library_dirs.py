"""馆藏目录分类：冻结整包计划、同盘移动、逐目录回执与失败恢复。"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
from collections import Counter, defaultdict
from contextlib import closing
from functools import cache
from pathlib import Path, PureWindowsPath

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / 'src'))
from peach import organize
from peach.catalog_rules import is_jav_code, is_korean_mib_code, normalise_code_key, promo_free_key, western_release_identity, release_code_from_filename, same_release_code, strip_promo_markers
from peach.classification import is_structural_creator
from peach.config import DATABASE_PATH, GENERATED_DIR
from peach.entity_classification import category_predicates
from peach.jobs import PidFileLock
from peach.media_sources import SCANNABLE_LOCATIONS
from peach.migrations import sqlite_backup
from peach.organize_clouddrive import UnconfirmedMove
from peach.organize_templates import MAX_PATH, sanitise_component
from peach.platform import location_roots, resolve_root, root_online, translate_ledger_path
from peach.review_csv import write_rows
from peach.scan import ARTWORK_DIRS, is_sidecar, video_stems
from peach.regions import infer_region
from peach.scripting import open_readonly
from peach.sources.onepondo import STUDIO as ONEPONDO_STUDIO, movie_id
from peach.web_catalog import effective_region_sql

OUT = GENERATED_DIR / 'library-organize'
_spec = importlib.util.spec_from_file_location('directory_flatten', Path(__file__).with_name('flatten_release_dirs.py'))
flatten = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(flatten)
FIELDS = ('id', 'path', 'name', 'medium', 'disposal', 'code', 'studio', 'creator', 'mutation_revision')
#: 本脚本写出的分类目录；`label()` 与 `classification_inputs()` 给出的就是这几个。
CATEGORY_DIRS = frozenset({'创作者', '网黄博主', '卖家', '动画作者', '日本', '韩国', '西方', 'FC2'})
CONTAINER_WORDS_FILE = PROJECT / 'resources' / 'naming' / 'container_directory_words.txt'

@cache
def container_names():
    """不整体改名到某个归属下的目录名：分类目录加词表里的容器目录。"""
    words = (line.split('#', 1)[0].strip() for line in CONTAINER_WORDS_FILE.read_text(encoding='utf-8').splitlines())
    return frozenset(name.casefold() for name in (*CATEGORY_DIRS, *words) if name)

def save(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    temporary.replace(path)

def signature(row):
    return {key: row[key] for key in FIELDS}

def beneath(path, root):
    p, r = PureWindowsPath(path).parts, PureWindowsPath(root).parts
    return len(p) > len(r) and tuple(x.casefold() for x in p[:len(r)]) == tuple(x.casefold() for x in r)

def rewrite(path, source, target):
    return str(PureWindowsPath(target, *PureWindowsPath(path).parts[len(PureWindowsPath(source).parts):]))

def snapshots(db, source, ids=None):
    if ids is not None:
        return [signature(r) for r in db.execute('SELECT '+','.join(FIELDS)+' FROM asset WHERE id IN (' + ','.join('?' for _ in ids) + ') ORDER BY id', ids)]
    prefix = str(source).rstrip('\\') + '\\'
    escaped = prefix.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
    return [signature(r) for r in db.execute('SELECT '+','.join(FIELDS)+' FROM asset WHERE path LIKE ? ESCAPE ? ORDER BY id', (escaped, '!'))]

def subtitles(db, source):
    prefix = str(source).rstrip('\\') + '\\'
    escaped = prefix.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
    return [dict(r) for r in db.execute('SELECT id,asset_id,location,path FROM asset_subtitle WHERE path LIKE ? ESCAPE ? ORDER BY id', (escaped, '!'))]

def update_paths(db, op):
    for table, rows in (('asset', op['rows']), ('asset_subtitle', op['subtitles'])):
        for row in rows:
            changed = db.execute('UPDATE '+table+' SET path=? WHERE id=? AND path=?',
                                 (rewrite(row['path'], op['source'], op['target']), row['id'], row['path'])).rowcount
            if changed != 1:
                raise ValueError('账本路径更新失败：'+table)

def operation(kind, source, target, rows, reason):
    key = hashlib.sha256((kind + '\0' + source + '\0' + target).encode()).hexdigest()[:20]
    return dict(key=key, kind=kind, source=source, target=target, rows=rows, reason=reason)

def entity_guard(db, source, ids=None):
    prefix = str(source).rstrip('\\') + '\\'
    escaped = prefix.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
    where = "a.path LIKE ? ESCAPE '!'" if ids is None else 'a.id IN (' + ','.join('?' for _ in ids) + ')'
    params = (escaped,) if ids is None else ids
    entities = [dict(r) for r in db.execute("SELECT DISTINCT ae.asset_id,ae.role,e.id,e.canonical_name,e.region FROM asset a JOIN asset_entity ae ON ae.asset_id=a.id JOIN entity e ON e.id=ae.entity_id WHERE " + where + " AND e.kind IN ('creator','studio') ORDER BY ae.asset_id,e.id,ae.role", params)]
    eids = {r['id'] for r in entities}
    facts = [dict(r) for r in db.execute('SELECT entity_id,facet,value,status,source FROM entity_classification ORDER BY entity_id,facet,value,status,source') if r['entity_id'] in eids]
    regions = [dict(r) for r in db.execute('SELECT a.id,a.region FROM asset a WHERE ' + where + ' ORDER BY a.id', params)]
    return dict(entities=entities, facts=facts, regions=regions)

def entity_guard_index(db):
    """冻结计划一次读取身份关系，逐目录只按资产 ID 取证。"""
    entities, facts = defaultdict(list), defaultdict(list)
    for row in db.execute("SELECT DISTINCT ae.asset_id,ae.role,e.id,e.canonical_name,e.region FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id WHERE e.kind IN ('creator','studio') ORDER BY ae.asset_id,e.id,ae.role"):
        entities[row['asset_id']].append(dict(row))
    for row in db.execute('SELECT entity_id,facet,value,status,source FROM entity_classification ORDER BY entity_id,facet,value,status,source'):
        facts[row['entity_id']].append(dict(row))
    return entities, facts

def indexed_guard(rows, regions, index):
    entities, facts = index
    linked = [entry for row in rows for entry in entities.get(row['id'], [])]
    identities = sorted({entry['id'] for entry in linked})
    return dict(entities=linked, facts=[entry for eid in identities for entry in facts.get(eid, [])],
                regions=[dict(id=row['id'], region=regions[row['id']]) for row in rows])

def release_code(row):
    """已有编号优先；FC 简写分片同时核对文件与父目录编号。"""
    code = normalise_code_key(row['code'])
    if code:
        return code
    studio = re.sub(r'[-_ ]', '', row['studio'] or '').upper()
    if studio not in {'', 'FC2', 'FC2PPV'}:
        return ''
    explicit = re.match(r'^@?FC2(?:[-_ ]?PPV)?[-_ ]*(\d{5,})(?!\d)',
                        strip_promo_markers(row['name']), re.I)
    if explicit:
        return normalise_code_key('FC2-' + explicit.group(1))
    platform_parent = re.fullmatch(r'FC2(?:[-_ ]?PPV)?(?:[-_ ]*(\d{5,}))?',
                                  PureWindowsPath(row.get('path', '')).parent.name, re.I)
    platform_part = re.match(r'^(\d{5,})(?=早期購入|本編)', row['name'])
    if (platform_parent and platform_part
            and platform_parent.group(1) in {None, platform_part.group(1)}):
        return normalise_code_key('FC2-' + platform_part.group(1))
    short = re.match(r'^@?FC[-_ ]*(\d{5,})(?=[_.-]|$)', row['name'], re.I)
    parent = re.match(r'^FC(?:2)?(?:[-_ ]?PPV)?[-_ ]*(\d{5,})(?!\d)',
                      PureWindowsPath(row.get('path', '')).parent.name, re.I)
    if short and parent and short.group(1) == parent.group(1):
        return normalise_code_key('FC2-' + short.group(1))
    return ''

def label(row, creators, categories, unverified):
    if row['disposal'] is not None or row['medium'] not in {'video', 'image', 'audio', 'archive'}:
        return None
    code = release_code(row)
    region = row.get('effective_region', '')
    if row['studio'] == 'MIB' and region in ('', 'kr'):
        return ('publisher', '韩国', 'MIB')
    if is_jav_code(code) and region in ('', 'jp') and not is_korean_mib_code(code):
        if code.startswith('FC2'):
            return ('release', '日本', 'FC2', code)
        if row['studio']:
            return ('release', '日本', row['studio'], code)
    owners = creators.get(row['id'], [])
    if len(owners) == 1:
        entity_id, name = owners[0]
        if not is_structural_creator(name):
            matched = categories.get(entity_id, [])
            if len(matched) == 1 and entity_id not in unverified:
                return ('creator', matched[0], name)
            if not matched or entity_id in unverified:
                return ('creator', '创作者', name)
    if row['studio'] and region in ('', 'west') and western_release_identity(row['name']):
        return ('western', '西方', row['studio'])
    return None

def release_directory_matches(name, owner):
    """整包目录可含发行站、画质与分片标记；日期编号保留片商边界。"""
    if owner[3].startswith('FC2-PPV-'):
        head = re.match(r'^FC(?:2)?(?:[-_ ]?PPV)?[-_ ]*(\d{5,})(?!\d)', name, re.I)
        if head:
            return normalise_code_key('FC2-' + head.group(1)) == owner[3]
    parsed = release_code_from_filename(name)
    if not parsed:
        return promo_free_key(name) == promo_free_key(owner[3])
    if same_release_code(parsed, owner[3]):
        return True
    return (owner[2] == ONEPONDO_STUDIO and bool(movie_id(parsed))
            and movie_id(parsed) == movie_id(owner[3]))

def compatible_sidecar(row, owner, creators, allowed_creators=()):
    """配套图只随确定的整包移动，不给图片补身份字段。"""
    if row['medium'] != 'image' or row['disposal'] is not None:
        return False
    if owner[0] == 'creator':
        return (not row.get('code') and not row.get('studio')
                and (not row.get('creator') or row['creator'] == owner[2])
                and all(name == owner[2] for _, name in creators.get(row['id'], [])))
    if owner[0] == 'publisher':
        return (row.get('effective_region', '') in ('', 'kr')
                and (not row.get('studio') or row['studio'] == owner[2])
                and not row.get('creator') and not creators.get(row['id'])
                and (not row.get('code') or is_korean_mib_code(row['code'])))
    if ((row.get('creator') and row['creator'] not in allowed_creators)
            or any(name not in allowed_creators for _, name in creators.get(row['id'], []))):
        return False
    if owner[0] != 'release' or row.get('effective_region', '') not in ('', 'jp'):
        return False
    if row.get('studio') and row['studio'] != owner[2]:
        return False
    code = normalise_code_key(row.get('code'))
    return not code or code == owner[3] or (
        owner[2] == ONEPONDO_STUDIO and bool(movie_id(code))
        and movie_id(code) == movie_id(owner[3]))

def classification_inputs(db):
    predicates = category_predicates(connection=db)
    categories = {}
    names = {'blogger': '网黄博主', 'seller': '卖家', 'animation': '动画作者'}
    for entity_id, name, *matched in db.execute('SELECT e.id,e.canonical_name,' + ','.join(
            '(' + predicates[k] + ')' for k in names) + " FROM entity e WHERE e.kind='creator'"):
        categories[entity_id] = [name for name, value in zip(names.values(), matched) if value]
    unverified = {r[0] for r in db.execute("SELECT entity_id FROM entity_classification WHERE facet='identity' AND value='unknown' AND status='candidate'")}
    creators = defaultdict(list)
    for asset_id, entity_id, name in db.execute("SELECT DISTINCT ae.asset_id,e.id,e.canonical_name FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id WHERE e.kind='creator' AND ae.role='creator'"):
        creators[asset_id].append((entity_id, name))
    return creators, categories, unverified

def catalog_nodes(all_rows, roots, creators, categories, unverified, skipped):
    nodes = {}
    for row in all_rows:
        location, index, _ = resolve_root(row['path'], roots)
        if location != row['location'] or index < 0:
            skipped.append(dict(source=row['path'], reason='不在来源根内'))
            continue
        root = roots[location][index]
        owner = label(row, creators, categories, unverified)
        if owner and filename_code_conflicts(row, owner):
            # 账本番号与文件名自带的编号指向两部作品：哪个对未取得，按归属未知留在原处。
            owner, row['_code_conflict'] = None, True
        row['_owner'], row['_root'] = owner, root
        parent = PureWindowsPath(row['path']).parent
        while str(parent).casefold() != str(PureWindowsPath(root)).casefold():
            key = str(parent)
            node = nodes.setdefault(key, dict(rows=[], labels=set(), root=root))
            node['rows'].append(signature(row))
            if owner:
                node['labels'].add(owner)
            if parent.parent == parent:
                raise ValueError('目录不在声明来源根内')
            parent = parent.parent
    return nodes

def directory_owner(source, node, rows_by_id, creators):
    path = PureWindowsPath(source)
    primary = [rows_by_id[r['id']] for r in node['rows']
               if r['medium'] in {'video', 'audio', 'archive'} and r['disposal'] is None]
    labels = {r['_owner'] for r in primary if r['_owner']} or node['labels']
    if path.name.casefold() in container_names() or len(labels) != 1:
        return None
    owner = next(iter(labels))
    allowed_creators = {name for r in primary for _, name in creators.get(r['id'], [])}
    allowed_creators.update(r['creator'] for r in primary if r.get('creator'))
    conflicting = [rows_by_id[r['id']] for r in node['rows']
                   if rows_by_id[r['id']]['_owner'] != owner and r['disposal'] is None
                   and r['medium'] in {'video', 'image', 'audio', 'archive'}]
    if any(not compatible_sidecar(row, owner, creators, allowed_creators) for row in conflicting):
        return None
    for row in node['rows']:
        if rows_by_id[row['id']]['_owner'] is None:
            rows_by_id[row['id']]['_owner'] = owner
    return owner

def filename_code_conflicts(row, owner):
    """发行类视频的文件名自带编号，且与账本番号不是同一部作品。"""
    return (owner[0] == 'release' and row['medium'] == 'video'
            and bool(release_code_from_filename(row['name']))
            and not release_directory_matches(row['name'], owner))

def release_directory_index(all_rows):
    """已有的番号目录：`(来源根, 分类, 厂牌)` → 该层下按规范写法命名的目录名。"""
    index = defaultdict(set)
    for row in all_rows:
        root = row.get('_root')
        if not root or row['disposal'] is not None or not beneath(row['path'], root):
            continue
        parts = PureWindowsPath(row['path']).parts[len(PureWindowsPath(root).parts):]
        if len(parts) >= 4 and normalise_code_key(parts[2]) == parts[2]:
            index[(root.casefold(), parts[0], parts[1])].add(parts[2])
    return index

def release_directory_name(root, owner, existing):
    """同一作品只落一个目录：有等价的已有目录就并过去，几处都有时一本道取官网作品号写法。"""
    if existing is None:
        return owner[3]
    names = existing[(root.casefold(), owner[1], sanitise_component(owner[2]))]
    matches = sorted(name for name in names if release_directory_matches(name, owner))
    official = movie_id(owner[3]) if owner[2] == ONEPONDO_STUDIO else None
    chosen = next((name for name in (official, owner[3]) if name and name in matches),
                  matches[0] if matches else owner[3])
    names.add(chosen)
    return chosen

def owner_directory(root, path, owner, existing=None):
    """归属的规范目录。创作者类只保留规范名那一层之下的结构，规范名之上的容器目录都丢掉；
    路径里没有规范名那一层时只带源目录自己的名字。"""
    if owner[0] == 'release':
        parts = (owner[1], owner[2], release_directory_name(root, owner, existing))
    else:
        category, canonical = owner[1:]
        relative = path.relative_to(PureWindowsPath(root)).parts
        layers = [i for i, part in enumerate(relative) if promo_free_key(part) == promo_free_key(canonical)]
        tail = relative[layers[-1] + 1:] if layers else tuple(
            part for part in relative[-1:] if part.casefold() not in container_names())
        parts = (category, canonical, *tail)
    return str(PureWindowsPath(root, *(sanitise_component(part) for part in parts)))

def above_canonical(source, node, owner):
    """路径里还没到规范名那一层、而下面有这一层：它只是容器，交给规范名那层整体移动。"""
    key, depth = promo_free_key(owner[2]), len(PureWindowsPath(source).parts)
    if any(promo_free_key(part) == key for part in PureWindowsPath(source).parts):
        return False
    return any(promo_free_key(part) == key
               for row in node['rows'] for part in PureWindowsPath(row['path']).parts[depth:-1])

def directory_target(source, node, owner, rows_by_id, skipped, existing=None):
    path = PureWindowsPath(source)
    if owner[0] == 'release':
        if not release_directory_matches(path.name, owner):
            return None
        for row in node['rows']:
            rows_by_id[row['id']]['_owner'] = owner
    elif above_canonical(source, node, owner):
        return None
    target = owner_directory(node['root'], path, owner, existing)
    if target.casefold() == source.casefold():
        return None
    if beneath(target, source) or beneath(source, target):
        skipped.append(dict(source=source, reason='目标与来源相互包含'))
        return None
    if any(len(rewrite(r['path'], source, target).encode('utf-16-le')) // 2 > MAX_PATH for r in node['rows']):
        skipped.append(dict(source=source, reason='路径过长'))
        return None
    return target

def directory_operations(nodes, rows_by_id, creators, skipped, existing=None):
    operations, selected = [], set()
    for source, node in sorted(nodes.items(), key=lambda pair: (len(PureWindowsPath(pair[0]).parts), pair[0])):
        owner = directory_owner(source, node, rows_by_id, creators)
        if owner is None:
            continue
        target = directory_target(source, node, owner, rows_by_id, skipped, existing)
        if target is None:
            continue
        path = PureWindowsPath(source)
        if any(str(parent).casefold() in selected for parent in (path, *path.parents)):
            continue
        selected.add(source.casefold())
        operations.append(operation('rename', source, target, sorted(node['rows'], key=lambda r:r['id']),
                                    '账本规范归属：' + '/'.join(owner[1:])))
    return operations

def file_operations(db, all_rows, rows_by_path, covered, existing=None):
    operations = []
    file_groups = defaultdict(list)
    for row in all_rows:
        owner, root = row.get('_owner'), row.get('_root')
        if row['id'] in covered or not root or not owner or row['disposal'] is not None:
            continue
        parent = str(PureWindowsPath(row['path']).parent)
        target = owner_directory(root, PureWindowsPath(parent), owner, existing)
        if beneath(row['path'], target):
            continue
        if len(str(PureWindowsPath(target, PureWindowsPath(row['path']).name)).encode('utf-16-le')) // 2 > MAX_PATH:
            continue
        file_groups[(parent, target, owner)].append(signature(row))
    for (source, target, owner), rows in sorted(file_groups.items()):
        aids = {r['id'] for r in rows}
        for subtitle in subtitles(db, source):
            extra = rows_by_path.get(subtitle['path'].casefold())
            if subtitle['asset_id'] in aids and extra and extra['id'] not in aids:
                rows.append(signature(extra))
                aids.add(extra['id'])
        rows.sort(key=lambda r: r['id'])
        operations.append(operation('files', source, target, rows,
                                    '混合目录内按已知归属分组：' + '/'.join(owner[1:])))
        covered.update(r['id'] for r in rows)
    return operations

def canonical_directory(root, owner, existing=None):
    if owner[0] == 'release':
        return owner_directory(root, PureWindowsPath(root), owner, existing)
    return str(PureWindowsPath(root, *(sanitise_component(p) for p in owner[1:3])))

def pending_paths(all_rows, mounts, covered, existing=None):
    skipped = []
    for row in all_rows:
        if row['disposal'] is None and row['id'] not in covered:
            owner, root = row.get('_owner'), row.get('_root')
            if root and not mounts[root]:
                reason = '来源离线，保留原位置'
            elif row.get('_code_conflict'):
                reason = '数据库番号与文件名编号不一致，待确认'
            elif owner and beneath(row['path'], canonical_directory(root, owner, existing)):
                reason = '已在规范分类目录'
            elif row['medium'] == 'other':
                reason = '附带文件缺少明确作品目录，保留原位置'
            elif owner:
                reason = '归属明确，但目录混合、命名不足或路径过长，保留原位置'
            else:
                reason = '归属未确认，保留原位置'
            skipped.append(dict(source=row['path'], reason=reason))
    return skipped

def attach_plan_guards(db, operations, stage, all_rows):
    if stage == 'rehome':
        guard_index = entity_guard_index(db)
        regions = {row['id']:row['region'] for row in all_rows}
        ids = [row['id'] for op in operations for row in op['rows']]
        if len(ids) != len(set(ids)):
            raise ValueError('目录计划重复覆盖资产，请复核字幕或整包归属')
    for op in operations:
        op['subtitles'] = subtitles(db, op['source'])
        if op['kind'] == 'files':
            ids = {row['id'] for row in op['rows']}
            op['subtitles'] = [row for row in op['subtitles'] if row['asset_id'] in ids]
        if stage == 'rehome':
            op['entities'] = indexed_guard(op['rows'], regions, guard_index)

def flatten_operations(db, stem):
    operations, skipped = [], []
    rows = flatten.plan_operations(db)
    for row in rows:
        row['verified'] = flatten.verify(row, translate_ledger_path)
        if row['verified'] == 'ok':
            operations.append(operation(row['kind'],row['ledger_dir'],row['target_dir'],
                                        snapshots(db,row['ledger_dir']),'冗余目录层或广告标记'))
        else:
            skipped.append(dict(source=row['ledger_dir'],reason=row['verified']))
    write_rows(OUT/f'{stem}-plan.csv',flatten.PLAN_FIELDS,rows)
    return operations, skipped

def write_plan(payload, stem):
    operations, skipped = payload['operations'], payload['skipped']
    save(OUT/f'{stem}-manifest.json',payload)
    write_rows(OUT/f'{stem}-directories.csv',['key','kind','source','target','assets','reason'],
               [dict(key=op['key'],kind=op['kind'],source=op['source'],target=op['target'],assets=len(op['rows']),reason=op['reason']) for op in operations])
    write_rows(OUT/f'{stem}-pending.csv',['source','reason'],skipped)
    print(json.dumps(dict(stage=payload['stage'],directories=len(operations),assets=len({r['id'] for op in operations for r in op['rows']}),pending=len(skipped)),ensure_ascii=False))

def build_plan(stage, batch=None):
    stem = stage + ('-' + batch if batch else '')
    receipt = OUT/f'{stem}-receipt.json'
    if receipt.exists() and json.loads(receipt.read_text(encoding='utf-8')).get('entries'):
        raise ValueError('已有执行回执，请使用新的批次编号')
    roots = location_roots()
    all_rows = []
    with closing(open_readonly(DATABASE_PATH)) as db:
        db.execute('BEGIN')
        if stage == 'flatten':
            operations, skipped = flatten_operations(db,stem)
        else:
            creators, categories, unverified = classification_inputs(db)
            db.create_function('infer_region',2,infer_region,deterministic=True)
            marks = ','.join('?' for _ in SCANNABLE_LOCATIONS)
            all_rows = [dict(r) for r in db.execute(
                "SELECT a.*," + effective_region_sql() + " AS effective_region FROM asset a "
                f"WHERE a.location IN ({marks}) AND a.path IS NOT NULL AND trim(a.path)<>'' ORDER BY a.id",
                SCANNABLE_LOCATIONS)]
            by_id = {row['id']:row for row in all_rows}
            by_path = {row['path'].casefold():row for row in all_rows}
            mounts = {root:root_online(translate_ledger_path(root)) for values in roots.values() for root in values}
            skipped = []
            nodes = catalog_nodes(all_rows,roots,creators,categories,unverified,skipped)
            existing = release_directory_index(all_rows)
            operations = directory_operations(nodes,by_id,creators,skipped,existing)
            covered = {r['id'] for op in operations for r in op['rows']}
            operations.extend(file_operations(db,all_rows,by_path,covered,existing))
            skipped.extend(pending_paths(all_rows,mounts,covered,existing))
        attach_plan_guards(db,operations,stage,all_rows)
        payload = dict(format=1,stage=stage,roots=roots,operations=operations,skipped=skipped)
    write_plan(payload,stem)

def expand(source, target, *, deadline, count=None):
    count = [0] if count is None else count
    if time.monotonic() > deadline:
        raise TimeoutError('目录预检超时')
    if source.is_symlink() or target.is_symlink():
        raise ValueError('拒绝符号链接')
    if not source.exists():
        raise FileNotFoundError('源目录不存在')
    if not target.exists():
        return [(str(source), str(target))], []
    if not source.is_dir() or not target.is_dir():
        raise FileExistsError('目标同名文件已存在')
    entries = list(source.iterdir())
    count[0] += len(entries)
    if count[0] > 100000:
        raise ValueError('目录项超过本批预算')
    pairs, empty = [], [str(source)]
    for entry in entries:
        child_pairs, child_empty = expand(entry, target / entry.name, deadline=deadline, count=count)
        pairs.extend(child_pairs)
        empty.extend(child_empty)
    return pairs, empty

def restore(pairs):
    failures = []
    for index in range(len(pairs)-1, -1, -1):
        source, target = pairs[index]
        old, new = translate_ledger_path(source), translate_ledger_path(target)
        if not old.exists() and not new.exists():
            ancestor, present = source, False
            for earlier_source, earlier_target in reversed(pairs[:index]):
                if ancestor == earlier_target:
                    ancestor = earlier_source
                    if translate_ledger_path(ancestor).exists():
                        present = True
                        break
            if present:
                continue
        if old.exists() and not new.exists():
            continue
        if not old.exists() and new.exists():
            try:
                organize._rename(target, source)
            except OSError as error:
                failures.append(dict(source=source, target=target, error=str(error)))
        else:
            failures.append(dict(source=source, target=target, error='恢复路径状态冲突'))
    return failures

def staging_name(source):
    prefix = 'peach-organize-' if PureWindowsPath(source).drive.casefold() == 'a:' else '.peach-organize-'
    return prefix + hashlib.sha256(source.encode()).hexdigest()[:20]


def collapse_moves(source, target):
    parent = translate_ledger_path(target)
    temporary = parent.with_name(staging_name(source))
    if temporary.exists() or temporary.is_symlink():
        raise FileExistsError('临时目录已存在')
    child = temporary / translate_ledger_path(source).name
    state = dict(parent=str(parent), temporary=str(temporary), child=str(child), original=source)
    return [(str(parent), str(temporary)), (str(child), str(parent))], state

def restore_entry(entry):
    shared = entry.get('shared_stage')
    if shared:
        temporary = translate_ledger_path(shared['temporary'])
        if not temporary.exists() and not entry.get('shared_started'):
            return []
        failures = restore(entry['moves'][1:])
        if failures:
            return failures
        try:
            organize._rename(str(temporary), shared['original'])
            return []
        except OSError as error:
            return [dict(error=str(error), **shared)]
    state = entry.get('collapse')
    if not state:
        return restore(entry['moves'])
    parent, temporary, child = (translate_ledger_path(state[k]) for k in ('parent','temporary','child'))
    try:
        if not temporary.exists():
            if translate_ledger_path(state['original']).is_dir():
                return []
            if not parent.is_dir():
                raise RuntimeError('恢复所需目录未取得')
            temporary.mkdir()
        if child.exists() and not parent.exists():
            organize._rename(str(temporary), str(parent))
        elif not child.exists() and parent.exists():
            organize._rename(str(parent), str(child))
            organize._rename(str(temporary), str(parent))
        else:
            raise RuntimeError('恢复路径状态冲突')
        return []
    except Exception as error:
        return [dict(error=str(error), **state)]

def cleanup_collapse(entry):
    state = entry.get('collapse') or entry.get('shared_stage')
    if state:
        temporary = translate_ledger_path(state['temporary'])
        if temporary.exists():
            try:
                temporary.rmdir()
                entry.pop('cleanup_error', None)
            except OSError as error:
                entry['cleanup_error'] = str(error)

def recover(db, payload, receipt, path):
    operations = {op['key']: op for op in payload['operations']}
    for entry in receipt['entries']:
        if entry['status'] not in {'intent', 'filesystem'}:
            if entry['status'] == 'rollback_failed':
                raise RuntimeError('上一批存在未恢复的文件')
            continue
        op = operations[entry['key']]
        expected = [dict(r, path=rewrite(r['path'], op['source'], op['target'])) for r in op['rows']]
        current = []
        for row in op['rows']:
            present = db.execute('SELECT * FROM asset WHERE id=?', (row['id'],)).fetchone()
            current.append(signature(present) if present else None)
        current_subs = [dict(db.execute('SELECT id,asset_id,location,path FROM asset_subtitle WHERE id=?', (r['id'],)).fetchone()) for r in op['subtitles']]
        expected_subs = [dict(r, path=rewrite(r['path'], op['source'], op['target'])) for r in op['subtitles']]
        if current == expected and current_subs == expected_subs:
            targets = ([entry['target']] if entry.get('collapse') else
                       [new for old, new in entry['moves'][1:]] if entry.get('shared_stage') else
                       [new for old, new in entry['moves']])
            if any(not translate_ledger_path(new).exists() for new in targets):
                raise RuntimeError('已提交批次的目标文件未取得')
            entry['status'] = 'committed'
            cleanup_collapse(entry)
        elif current == op['rows'] and current_subs == op['subtitles']:
            failures = restore_entry(entry)
            entry['status'] = 'rollback_failed' if failures else 'restored'
            if failures:
                save(path, receipt)
                raise RuntimeError('中断批次的文件恢复失败')
        else:
            raise RuntimeError('中断批次的账本已经发生其他变化')
        save(path, receipt)

class OfflineSource(ValueError):
    pass

def operation_paths(op, roots):
    exclusive_parent = False
    source, target = op['source'], op['target']
    old_location, old_index, _ = resolve_root(source + '\\_', roots)
    new_location, new_index, _ = resolve_root(target + '\\_', roots)
    if old_location != new_location or old_index != new_index or old_index < 0:
        raise ValueError('来源根不一致')
    root = roots[old_location][old_index]
    if not beneath(source, root) or not beneath(target, root) or not root_online(translate_ledger_path(root)):
        raise OfflineSource('来源未挂载或操作越出根目录')
    if op['kind'] != 'files' and (beneath(target, source) or (beneath(source, target) and op['kind'] != 'collapse')):
        raise ValueError('来源与目标相互包含')
    local_source, local_target = translate_ledger_path(source), translate_ledger_path(target)
    if not local_source.is_dir():
        raise ValueError('源路径不是目录或未挂载')
    if local_source.is_symlink() or local_target.is_symlink():
        raise ValueError('拒绝符号链接目录')
    if op['kind'] == 'collapse':
        if PureWindowsPath(source).parent != PureWindowsPath(target):
            raise ValueError('合并目标不是直接父目录')
        if flatten.directory_key(PureWindowsPath(source).name) != flatten.directory_key(PureWindowsPath(target).name):
            raise ValueError('目录名不冗余')
        exclusive_parent = sorted(p.name for p in local_target.iterdir()) == [local_source.name]
    return local_source, local_target, root, exclusive_parent

def verify_frozen_operation(db, op, stage):
    source = op['source']
    row_ids = [r['id'] for r in op['rows']] if op['kind'] == 'files' else None
    if snapshots(db, source, row_ids) != op['rows']:
        raise ValueError('冻结计划已失效：路径或元数据发生变化')
    current_subtitles = subtitles(db, source)
    if row_ids is not None:
        current_subtitles = [r for r in current_subtitles if r['asset_id'] in row_ids]
    if current_subtitles != op['subtitles']:
        raise ValueError('冻结计划已失效：字幕路径发生变化')
    if stage == 'rehome' and entity_guard(db, source, row_ids) != op['entities']:
        raise ValueError('冻结计划已失效：创作者身份发生变化')

def sidecar_owner(name, videos):
    """带片名的附属文件属于哪部视频：取最长的、后面紧跟分隔符的片名前缀。"""
    stem = os.path.splitext(name)[0].casefold()
    owners = [video for video in videos
              if stem == video or (stem.startswith(video) and stem[len(video)] in '-._ ')]
    return max(owners, key=len) if owners else None

def sidecar_moves(op, moving, registered):
    """同目录里随本组视频走的附属文件，判据取 `scan.is_sidecar`。

    带片名的附属（`<片名>-poster.jpg`、`<片名>.nfo`）跟着它的视频走；裸 `poster.jpg`、
    `movie.nfo` 与 `extrafanart` 这类目录级附属，只在本组带走了源目录全部视频时才走。
    账本登记过的文件、工具隔离文件与目标已有同名项的都留在原处。
    """
    source, target = op['source'], op['target']
    entries = list(translate_ledger_path(source).iterdir())
    videos = video_stems(entry.name for entry in entries if entry.is_file())
    moved = video_stems(moving)
    whole = bool(videos) and videos <= moved
    pairs = []
    for entry in entries:
        old, new = str(PureWindowsPath(source, entry.name)), str(PureWindowsPath(target, entry.name))
        if (entry.is_symlink() or entry.name in moving or old.casefold() in registered
                or entry.name.casefold().endswith('.peach-quarantine')):
            continue
        if entry.is_dir():
            inside = old.casefold() + '\\'
            belongs = (whole and entry.name.casefold() in ARTWORK_DIRS
                       and not any(path.startswith(inside) for path in registered))
        elif is_sidecar(entry.name, videos):
            owner = sidecar_owner(entry.name, videos)
            belongs = owner in moved if owner else whole
        else:
            belongs = False
        if belongs and not translate_ledger_path(new).exists():
            pairs.append((old, new))
    return pairs

def file_group_moves(op, registered=frozenset()):
    source, target = op['source'], op['target']
    physical_pairs = [(r['path'], rewrite(r['path'], source, target))
                      for r in (*op['rows'], *op['subtitles'])]
    physical_pairs = list(dict.fromkeys(physical_pairs))
    for old, new in physical_pairs:
        old, new = translate_ledger_path(old), translate_ledger_path(new)
        if old.is_symlink() or new.is_symlink() or not old.is_file() or new.exists():
            raise ValueError('分组文件未取得、符号链接或目标冲突')
    moving = {PureWindowsPath(old).name for old, _ in physical_pairs}
    physical_pairs += sidecar_moves(op, moving, registered)
    empty = [source]
    return physical_pairs, empty

def shared_collapse_moves(local_source, local_target, root, source):
    temporary = local_source.with_name(staging_name(source))
    if temporary.exists() or temporary.is_symlink() or not beneath(str(temporary), root):
        raise ValueError('暂存目录冲突或越出根目录')
    entries = list(local_source.iterdir())
    for entry in entries:
        if entry.is_symlink() or (entry.name.casefold() != local_source.name.casefold()
                                 and (local_target / entry.name).exists()):
            raise ValueError('目标条目冲突或符号链接')
    shared_stage = dict(original=source, temporary=str(temporary))
    physical_pairs = [(str(local_source), str(temporary))] + [
        (str(temporary / p.name), str(local_target / p.name)) for p in entries]
    empty = [str(temporary)]
    return physical_pairs, empty, shared_stage

def prepare_moves(op, local_source, local_target, root, exclusive_parent, deadline, registered=frozenset()):
    source, target = op['source'], op['target']
    collapse, shared_stage = None, None
    if op['kind'] == 'files':
        pairs, empty = file_group_moves(op, registered)
    elif op['kind'] == 'collapse' and exclusive_parent:
        pairs, collapse = collapse_moves(source,target)
        if not beneath(pairs[0][1],root):
            raise ValueError('临时目录越出根目录')
        empty = []
    elif op['kind'] == 'collapse' and any(p.name.casefold()==local_source.name.casefold() for p in local_source.iterdir()):
        pairs, empty, shared_stage = shared_collapse_moves(local_source,local_target,root,source)
    else:
        pairs, empty = expand(local_source,local_target,deadline=min(deadline,time.monotonic()+90))
    return [(str(PureWindowsPath(a)),str(PureWindowsPath(b))) for a,b in pairs], empty, collapse, shared_stage

def move_files(journal, receipt, receipt_path):
    for index,(old,new) in enumerate(journal['moves']):
        organize._rename(old,new)
        if not translate_ledger_path(new).exists():
            raise ValueError('移动后目标未取得')
        if journal['shared_stage'] and index==0:
            journal['shared_started'] = True
            save(receipt_path,receipt)
    journal['status'] = 'filesystem'
    save(receipt_path,receipt)

def commit_paths(db, op):
    update_paths(db,op)
    for table,rows in (('asset',op['rows']),('asset_subtitle',op['subtitles'])):
        for row in rows:
            present = db.execute(f'SELECT path FROM {table} WHERE id=?',(row['id'],)).fetchone()
            if not present or present[0]!=rewrite(row['path'],op['source'],op['target']):
                raise ValueError('目标账本路径核对失败')
    db.commit()

def cleanup_empty_paths(paths):
    """删掉移空的源目录；删不掉的连同里面剩下的名字一起返回，写进回执。"""
    failures = []
    for path in sorted(paths,key=lambda p:len(Path(p).parts),reverse=True):
        local = translate_ledger_path(path)
        try:
            local.rmdir()
        except FileNotFoundError:
            continue
        except OSError as error:
            try:
                remaining = sorted(entry.name for entry in local.iterdir())
            except OSError:
                remaining = []
            failures.append(dict(path=path, error=str(error), count=len(remaining), remaining=remaining[:10]))
    return failures

def provider_moves(pairs, root, allow_intermediate=True):
    planned = {str(PureWindowsPath(path)).casefold() for pair in pairs for path in pair}
    result = []
    for old, new in pairs:
        source, target = PureWindowsPath(old), PureWindowsPath(new)
        if source.drive.casefold() == 'a:' and source.parent != target.parent and source.name != target.name:
            if not allow_intermediate:
                raise ValueError('目录折叠的改名中间路径需单独复核')
            intermediate = str(source.with_name(target.name))
            local = translate_ledger_path(intermediate)
            if not beneath(intermediate, root) or intermediate.casefold() in planned or local.exists() or local.is_symlink():
                raise FileExistsError('同父改名的中间路径冲突或越界')
            planned.add(intermediate.casefold())
            result.extend(((old, intermediate), (intermediate, new)))
        else:
            result.append((old, new))
    return result


def record_cleanup(journal, empty, receipt, receipt_path):
    """账本已提交之后收尾：删不掉的源目录记进本条回执，收尾本身出错也只记录不抛。"""
    try:
        errors = cleanup_empty_paths(empty)
    except Exception as error:
        errors = [dict(path=journal['source'], error=str(error), count=None, remaining=[])]
    if errors:
        journal['cleanup_errors'] = errors
        save(receipt_path, receipt)


def record_unconfirmed(db, op, receipt, receipt_path, error):
    """状态未知：不猜着搬回，账本事务回滚，意图留在回执里交给续跑的 `recover()` 判定。"""
    db.rollback()
    receipt['failures'].append(dict(key=op['key'], source=op['source'], error=str(error), unconfirmed=True))
    save(receipt_path, receipt)


def execute_operation(db, payload, op, stage, receipt, receipt_path, deadline, retry_failed, pikpak_webdav=False):
    """一条目录操作。确定性失败记入 `failures` 并恢复本条后返回错误；状态未知时记录后停止本批。"""
    journal, committed = None, False
    try:
        local_source,local_target,root,exclusive = operation_paths(op,payload['roots'])
        db.execute('BEGIN IMMEDIATE')
        verify_frozen_operation(db,op,stage)
        registered = {row['path'].casefold() for row in (*snapshots(db,op['source']),*subtitles(db,op['source']))}
        pairs,empty,collapse,shared_stage = prepare_moves(op,local_source,local_target,root,exclusive,deadline,registered)
        if pikpak_webdav:
            pairs = provider_moves(pairs, root, allow_intermediate=not collapse and not shared_stage)
        journal = dict(key=op['key'],status='intent',source=op['source'],target=op['target'],moves=pairs,empty=empty,
                       assets=len(op['rows']),collapse=collapse,shared_stage=shared_stage,retry_failed=retry_failed)
        receipt['entries'].append(journal)
        save(receipt_path,receipt)
        move_files(journal,receipt,receipt_path)
        commit_paths(db,op)
        committed = True
        journal['status'] = 'committed'
        cleanup_collapse(journal)
        save(receipt_path,receipt)
        record_cleanup(journal, empty, receipt, receipt_path)
    except UnconfirmedMove as error:
        record_unconfirmed(db, op, receipt, receipt_path, error)
        raise
    except Exception as error:
        if committed:
            raise RuntimeError('账本已提交，回执保存失败；保留文件位置供续跑核对') from error
        db.rollback()
        try:
            failures = restore_entry(journal) if journal is not None else []
        except UnconfirmedMove as unknown:
            record_unconfirmed(db, op, receipt, receipt_path, unknown)
            raise
        if journal is not None:
            journal['status'] = 'rollback_failed' if failures else 'restored'
        receipt['failures'].append(dict(key=op['key'],source=op['source'],error=str(error),rollback_failures=failures))
        save(receipt_path,receipt)
        if failures:
            raise RuntimeError('文件恢复未完成，停止本批') from error
        return error
    return None

def pending_operations(payload, receipt, retry_failed, skip_collapses, location):
    done = {r['key'] for r in receipt['entries'] if r['status'] in {'committed','rolled_back'}}
    failed = set() if retry_failed else {r['key'] for r in receipt['failures']}
    for op in payload['operations']:
        if (skip_collapses and op['kind']=='collapse') or op['key'] in done or op['key'] in failed:
            continue
        owner,_,_ = resolve_root(op['source'] + '\\_',payload['roots'])
        if not location or owner==location:
            yield op,owner

def verify_result(db, assets, subtitle_count, baseline):
    result = dict(asset_before=assets,asset_after=db.execute('SELECT count(*) FROM asset').fetchone()[0],
                  subtitles_before=subtitle_count,subtitles_after=db.execute('SELECT count(*) FROM asset_subtitle').fetchone()[0],
                  integrity=db.execute('PRAGMA integrity_check').fetchone()[0],existing_foreign_keys=len(baseline),
                  new_foreign_keys=list({tuple(r) for r in db.execute('PRAGMA foreign_key_check')}-baseline))
    if result['asset_before']!=result['asset_after'] or result['subtitles_before']!=result['subtitles_after'] or result['integrity']!='ok' or result['new_foreign_keys']:
        raise RuntimeError('目录执行后的账本计数或完整性核验失败')
    return result

def run_apply(stage, backup, limit, seconds, location=None, retry_failed=False, skip_collapses=False, batch=None, pikpak_webdav=False):
    if backup is None:
        raise ValueError('--apply 必须同时给 --backup')
    stem = stage + ('-' + batch if batch else '')
    payload = json.loads((OUT/f'{stem}-manifest.json').read_text(encoding='utf-8'))
    if payload.get('format')!=1 or payload.get('stage')!=stage:
        raise ValueError('计划格式不受支持，请使用新的批次编号冻结计划')
    receipt_path = OUT/f'{stem}-receipt.json'
    receipt = json.loads(receipt_path.read_text(encoding='utf-8')) if receipt_path.exists() else dict(entries=[],failures=[])
    blocked = sorted({owner for _,owner in pending_operations(payload,receipt,retry_failed,skip_collapses,location)
                      if organize.unverified_location(owner)})
    if blocked:
        raise ValueError('冻结批次含 ' + '、'.join(blocked) + ' 来源的操作，挂载改名无法核验云端结果；'
                         'A 盘须用 --location pikpak --pikpak-webdav 经官方通道执行，其他来源用 --location 限定')
    lock = PidFileLock(OUT/'directory-job.lock')
    lock.acquire()
    db = None
    try:
        sqlite_backup(DATABASE_PATH,backup)
        receipt.setdefault('backups',[]).append(str(backup))
        db = sqlite3.connect(DATABASE_PATH,timeout=15)
        db.row_factory = sqlite3.Row
        recover(db,payload,receipt,receipt_path)
        baseline = {tuple(r) for r in db.execute('PRAGMA foreign_key_check')}
        assets = db.execute('SELECT count(*) FROM asset').fetchone()[0]
        subtitle_count = db.execute('SELECT count(*) FROM asset_subtitle').fetchone()[0]
        deadline,checked = time.monotonic()+seconds,0
        unavailable,provider_errors = set(),Counter()
        for op,owner in pending_operations(payload,receipt,retry_failed,skip_collapses,location):
            if checked>=limit or time.monotonic()>=deadline:
                break
            if owner in unavailable:
                continue
            checked += 1
            provider_options = {'pikpak_webdav': True} if pikpak_webdav else {}
            error = execute_operation(db,payload,op,stage,receipt,receipt_path,deadline,retry_failed,**provider_options)
            if isinstance(error,OfflineSource):
                unavailable.add(owner)
            if error is None:
                provider_errors[owner] = 0
            if isinstance(error,OSError) and getattr(error,'winerror',None) in {50,1}:
                provider_errors[owner] += 1
                if provider_errors[owner]>=3:
                    unavailable.add(owner)
            if checked%10==0:
                print(json.dumps(dict(checked=checked,committed=sum(r['status']=='committed' for r in receipt['entries']),failed=len(receipt['failures'])),ensure_ascii=False),flush=True)
        receipt['verification'] = verify_result(db,assets,subtitle_count,baseline)
        save(receipt_path,receipt)
        print(json.dumps(dict(stage=stage,checked=checked,committed=sum(r['status']=='committed' for r in receipt['entries']),failed=len(receipt['failures']),
                              cleanup_errors=sum(len(r.get('cleanup_errors') or ()) for r in receipt['entries']),verification=receipt['verification']),ensure_ascii=False))
    finally:
        if db is not None:
            db.close()
        lock.release()

def changed_since_commit(db, op):
    """已提交的一条里，账本路径已不在移动后位置上的行与字幕：被人或别的流程改过，不能撤回。"""
    changed = []
    for table, rows in (('asset', op['rows']), ('asset_subtitle', op['subtitles'])):
        for row in rows:
            expected = rewrite(row['path'], op['source'], op['target'])
            present = db.execute(f'SELECT path FROM {table} WHERE id=?', (row['id'],)).fetchone()
            if not present or present[0] != expected:
                changed.append(dict(table=table, id=row['id'], expected=expected, current=present[0] if present else None))
    return changed

def reverse_operation(op):
    """撤回用的反向操作：行与字幕取移动后的路径，来源与目标对调。"""
    moved = lambda rows: [dict(row, path=rewrite(row['path'], op['source'], op['target'])) for row in rows]
    return dict(op, source=op['target'], target=op['source'], rows=moved(op['rows']), subtitles=moved(op['subtitles']))

def prune_empty_parents(path, root):
    """撤回后目标那一侧移空的目录逐层删到来源根为止，遇到非空就停。"""
    current = PureWindowsPath(path)
    while beneath(str(current), root):
        try:
            translate_ledger_path(str(current)).rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            return
        current = current.parent

def rollback_candidates(payload, receipt, location):
    """回执里已提交的条目，按执行顺序倒过来。"""
    operations = {op['key']: op for op in payload['operations']}
    for entry in reversed(receipt['entries']):
        if entry['status'] == 'committed':
            op = operations[entry['key']]
            owner, index, _ = resolve_root(op['source'] + '\\_', payload['roots'])
            if not location or owner == location:
                yield entry, op, owner, payload['roots'][owner][index]

def rollback_entry(db, entry, op, root, receipt, receipt_path):
    """撤回一条：账本核对 → 文件按回执逆序搬回 → 账本写入移动前的路径。文件搬不回就停在这里。"""
    db.execute('BEGIN IMMEDIATE')
    changed = changed_since_commit(db, op)
    if changed:
        db.rollback()
        return dict(key=entry['key'], source=op['target'], status='changed', changed=changed)
    try:
        failures = restore_entry(entry)
    except UnconfirmedMove as error:
        failures = [dict(error=str(error), unconfirmed=True)]
    if failures:
        db.rollback()
        entry['status'] = 'rollback_failed'
        receipt['rollbacks'].append(dict(key=entry['key'], source=op['target'], status='rollback_failed', failures=failures))
        save(receipt_path, receipt)
        raise RuntimeError('撤回时文件未能全部搬回，停止本批；回执里这一条标为 rollback_failed')
    back = reverse_operation(op)
    try:
        commit_paths(db, back)
    except Exception as error:
        db.rollback()
        entry['status'] = 'rollback_failed'
        receipt['rollbacks'].append(dict(key=entry['key'], source=op['target'], status='rollback_failed', error=str(error)))
        save(receipt_path, receipt)
        raise RuntimeError('文件已搬回，账本路径写入失败，停止本批') from error
    entry['status'] = 'rolled_back'
    prune_empty_parents(op['target'], root)
    return dict(key=entry['key'], source=op['target'], target=op['source'], status='rolled_back', assets=len(op['rows']))

def run_rollback(stage, batch=None, backup=None, location=None, apply=False):
    """整批撤回已提交的目录操作。默认只列计划；`apply` 时先备份账本再逐条撤回。"""
    stem = stage + ('-' + batch if batch else '')
    payload = json.loads((OUT/f'{stem}-manifest.json').read_text(encoding='utf-8'))
    receipt_path = OUT/f'{stem}-receipt.json'
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    candidates = list(rollback_candidates(payload, receipt, location))
    if not apply:
        with closing(open_readonly(DATABASE_PATH)) as db:
            plan = [dict(key=entry['key'], source=op['target'], target=op['source'], assets=len(op['rows']),
                         changed=changed_since_commit(db, op)) for entry, op, _, _ in candidates]
        print(json.dumps(dict(stage=stage, batch=batch, rollback=len(plan), changed=sum(bool(p['changed']) for p in plan),
                              operations=plan), ensure_ascii=False, indent=1))
        return plan
    if backup is None:
        raise ValueError('--apply 必须同时给 --backup')
    blocked = sorted({owner for *_, owner, _ in candidates if organize.unverified_location(owner)})
    if blocked:
        raise ValueError('撤回含 ' + '、'.join(blocked) + ' 来源的操作，挂载改名无法核验云端结果；'
                         'A 盘须用 --location pikpak --pikpak-webdav 经官方通道撤回')
    lock = PidFileLock(OUT/'directory-job.lock')
    lock.acquire()
    db = None
    try:
        sqlite_backup(DATABASE_PATH, backup)
        receipt.setdefault('backups', []).append(str(backup))
        receipt.setdefault('rollbacks', [])
        db = sqlite3.connect(DATABASE_PATH, timeout=15)
        db.row_factory = sqlite3.Row
        recover(db, payload, receipt, receipt_path)
        baseline = {tuple(r) for r in db.execute('PRAGMA foreign_key_check')}
        assets = db.execute('SELECT count(*) FROM asset').fetchone()[0]
        subtitle_count = db.execute('SELECT count(*) FROM asset_subtitle').fetchone()[0]
        results = []
        for entry, op, _, root in rollback_candidates(payload, receipt, location):
            results.append(rollback_entry(db, entry, op, root, receipt, receipt_path))
            receipt['rollbacks'].append(results[-1])
            save(receipt_path, receipt)
        receipt['rollback_verification'] = verify_result(db, assets, subtitle_count, baseline)
        save(receipt_path, receipt)
        print(json.dumps(dict(stage=stage, batch=batch, rolled_back=sum(r['status'] == 'rolled_back' for r in results),
                              changed=[r for r in results if r['status'] == 'changed'],
                              verification=receipt['rollback_verification']), ensure_ascii=False))
        return results
    finally:
        if db is not None:
            db.close()
        lock.release()

def pikpak_channel(stage, batch):
    """A 盘批次的官方核验改名通道。"""
    from peach.organize_clouddrive import pikpak_renames
    payload = json.loads((OUT/f'{stage}-{batch}-manifest.json').read_text(encoding='utf-8'))
    rows = [row for op in payload['operations'] for row in op['rows'] if row['path'].startswith('A:')]
    if not rows:
        raise ValueError('冻结批次没有 A 盘资源')
    return pikpak_renames(rows[0]['path'], payload['roots'])

def self_check():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        wrapper = root / 'wrapper.mp4'
        wrapper.mkdir()
        (wrapper / 'wrapper.mp4').write_bytes(b'wrapped media')
        temporary = root / '.peach-organize-proof'
        pairs = [(str(wrapper), str(temporary)), (str(temporary / 'wrapper.mp4'), str(wrapper))]
        for steps in (0, 1, 2):
            entry = dict(moves=pairs, shared_stage=dict(original=str(wrapper), temporary=str(temporary)),
                         shared_started=steps > 0)
            for old, new in pairs[:steps]:
                organize._rename(old, new)
            if steps == 2:
                temporary.rmdir()
            assert not restore_entry(entry)
            assert (wrapper / 'wrapper.mp4').read_bytes() == b'wrapped media'
            assert not temporary.exists()
        original = root / 'same' / 'same'
        original.mkdir(parents=True)
        (original / 'proof.jpg').write_bytes(b'proof')
        for steps in (0, 1, 2):
            moves, state = collapse_moves(str(original), str(original.parent))
            entry = dict(moves=moves, collapse=state)
            for old, new in moves[:steps]:
                organize._rename(old, new)
            assert not restore_entry(entry)
            assert (original / 'proof.jpg').read_bytes() == b'proof'
            assert not Path(state['temporary']).exists()
        source, target = root / 'source', root / 'target'
        source.mkdir(); target.mkdir()
        (source / 'film.mp4').write_bytes(b'video')
        (source / 'film.srt').write_bytes(b'subtitle')
        (source / 'poster.jpg').write_bytes(b'cover')
        pairs, _ = expand(source, target, deadline=time.monotonic()+10)
        assert len(pairs) == 3
        for old, new in pairs:
            organize._rename(old, new)
        assert (target / 'film.srt').read_bytes() == b'subtitle'
        assert (target / 'poster.jpg').read_bytes() == b'cover'
        assert not restore(pairs)
        assert (source / 'film.mp4').read_bytes() == b'video'
        (target / 'film.mp4').write_bytes(b'existing')
        try:
            expand(source, target, deadline=time.monotonic()+10)
        except FileExistsError:
            pass
        else:
            raise AssertionError('同名文件必须拒绝')
        assert (target / 'film.mp4').read_bytes() == b'existing'
        assert beneath('B:\\日本\\片商\\ABC-123', 'B:\\')
        assert not beneath('B:\\MediaOther\\a', 'B:\\Media')
        assert rewrite('B:\\old\\sub\\film.mp4','B:\\old','B:\\new') == 'B:\\new\\sub\\film.mp4'
        db = sqlite3.connect(':memory:'); db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE asset(id INTEGER PRIMARY KEY,path TEXT UNIQUE)')
        db.execute('CREATE TABLE asset_subtitle(id INTEGER PRIMARY KEY,asset_id INTEGER,location TEXT,path TEXT UNIQUE)')
        db.execute('INSERT INTO asset VALUES(1,?)', ('B:\\old\\film.mp4',))
        db.execute('INSERT INTO asset_subtitle VALUES(1,1,?,?)', ('115','B:\\old\\film.srt'))
        db.commit()
        op = dict(source='B:\\old', target='B:\\new', rows=[dict(id=1,path='B:\\old\\film.mp4')], subtitles=subtitles(db,'B:\\old'))
        update_paths(db, op)
        assert db.execute('SELECT path FROM asset_subtitle').fetchone()[0] == 'B:\\new\\film.srt'
        db.rollback()
        assert db.execute('SELECT path FROM asset').fetchone()[0] == 'B:\\old\\film.mp4'
        assert db.execute('SELECT path FROM asset_subtitle').fetchone()[0] == 'B:\\old\\film.srt'
        db.close()
    print('临时目录实证通过：封面、字幕、恢复、重名拒绝和路径边界。')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', choices=('flatten', 'rehome'), default='rehome')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--limit', type=int, default=1000)
    parser.add_argument('--seconds', type=int, default=1200)
    parser.add_argument('--self-check', action='store_true')
    parser.add_argument('--location', choices=SCANNABLE_LOCATIONS)
    parser.add_argument('--retry-failed', action='store_true')
    parser.add_argument('--skip-collapses', action='store_true')
    parser.add_argument('--pikpak-webdav', action='store_true',
                        help='A 盘批次经 CloudDrive 官方改名与移动执行，并以官方完整目录核验；A 盘操作必须带它')
    parser.add_argument('--batch', type=lambda value: value if re.fullmatch(r'[a-zA-Z0-9_-]+', value)
                        else parser.error('批次编号只能包含字母、数字、下划线与连字号'))
    parser.add_argument('--rollback', action='store_true',
                        help='按回执逆序撤回这一批已提交的操作；不带 --apply 只列计划')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.pikpak_webdav and args.apply and (args.location != 'pikpak' or not args.batch):
        parser.error('--pikpak-webdav 需要 --location pikpak 和明确冻结批次 --batch')
    if args.self_check:
        self_check()
    elif args.rollback and not args.apply:
        run_rollback(args.stage, args.batch, location=args.location)
    elif args.rollback and args.pikpak_webdav:
        with pikpak_channel(args.stage, args.batch):
            run_rollback(args.stage, args.batch, args.backup, args.location, apply=True)
    elif args.rollback:
        run_rollback(args.stage, args.batch, args.backup, args.location, apply=True)
    elif args.apply and args.pikpak_webdav:
        with pikpak_channel(args.stage, args.batch):
            run_apply(args.stage, args.backup, args.limit, args.seconds, args.location, args.retry_failed, args.skip_collapses, args.batch, pikpak_webdav=True)
    elif args.apply:
        run_apply(args.stage, args.backup, args.limit, args.seconds, args.location, args.retry_failed, args.skip_collapses, args.batch)
    else:
        build_plan(args.stage, args.batch)
