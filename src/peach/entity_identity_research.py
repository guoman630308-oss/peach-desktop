"""公开身份来源与馆藏署名的衔接；冻结计划和逐行回执保持可撤回。

研究清单由人或智能体读页面后写成，不是代码判据：分类断言与身份关联一律落 candidate，
等用户复核后才 approved（ADR-0052 决策一）。
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

from .entities import resolve_entity, upsert_asset_entity
from .entity_classification import trusted_sql, write_claim
from .field_owners import is_protected, owner_of, write_owned_fields

SOURCE = 'script:creator-identity-research'
#: 通用搜索不收录的日本资料站，页名按 EUC-JP 百分号编码直取。
WIKI_ROOTS = {'sougouwiki': 'https://seesaawiki.jp/w/sougouwiki/', 'av_neme': 'https://seesaawiki.jp/av_neme/'}
#: 同名页的作品表至少列出几位出演者，才算厂牌或系列的作品一览。
LABEL_PAGE_CAST = 3


def settled_sql(column='e.id'):
    """身份面上已有可信断言（任何来源、任何值）或已核实是发行厂牌的实体；这些不再去查。"""
    return (f"EXISTS (SELECT 1 FROM entity_classification ec WHERE ec.entity_id={column} "
            f"AND {trusted_sql('ec')} "
            "AND (ec.facet='identity' OR (ec.facet='account_role' AND ec.value='studio')))")


def researchable(name):
    """结构目录与转载站名不是任何人的名字，拿去查页只会撞上同名的无关页。"""
    from .classification import is_repost_creator, is_structural_creator
    return not (is_structural_creator(name) or is_repost_creator(name))


def lookup_keys(name, prefixes=()):
    """资料站页名候选：规范名、去掉番号前缀的名字（`COSH こすっち` → `こすっち`）、文件名里的发行前缀。"""
    keys = [name, re.sub(r'^[A-Z]{2,6}\s+(?=[^\x00-\x7f])', '', name), *prefixes]
    return list(dict.fromkeys(key.strip() for key in keys if key and key.strip()))[:3]


def filename_prefixes(connection, entity_ids):
    """实体 id → 名下视频文件名里的发行前缀（`classification.numbered_cast_shape`），没有就是空表。"""
    from collections import defaultdict
    from .classification import numbered_cast_shape
    from .entities import normalize_entity_name
    ids = list(dict.fromkeys(int(entity_id) for entity_id in entity_ids))
    if not ids:
        return {}
    people = {normalize_entity_name(row[0]) for row in connection.execute(
        "SELECT canonical_name FROM entity WHERE kind='performer' UNION "
        "SELECT a.alias FROM entity_alias a JOIN entity e ON e.id=a.entity_id WHERE e.kind='performer'")}
    files = defaultdict(list)
    for entity_id, _asset_id, name in connection.execute(
            "SELECT DISTINCT ae.entity_id,a.id,a.name FROM asset_entity ae JOIN asset a ON a.id=ae.asset_id "
            "WHERE ae.role='creator' AND a.medium='video' AND ae.entity_id IN (" + ','.join('?' for _ in ids) + ")",
            ids):
        files[entity_id].append(str(name or ''))
    result = {}
    for entity_id in ids:
        shape = numbered_cast_shape(files.get(entity_id, []), people)
        result[entity_id] = [shape['prefix']] if shape and shape['prefix'] else []
    return result


def _label_page(page, source):
    """作品一览页 → (作品数, 出演者数, 最常见番号前缀)；人物页和出演者不足的页交 None。"""
    from collections import Counter
    from .sources.seesaa import person_profile
    if person_profile(page)[0]:
        return None
    rows = source.rows(page)
    cast = {link['name'] for row in rows for link in row.extra['wiki_evidence']['performer_links']}
    if len(cast) < LABEL_PAGE_CAST:
        return None
    prefixes = Counter(re.match(r'[A-Z]*', row.code)[0] for row in rows)
    return len(rows), len(cast), prefixes.most_common(1)[0][0]


def wiki_finding(entity_id, name, keys, pages):
    """按页名直取 sougouwiki 与 av_neme，交回 `plan()` 能读的身份清单项。

    `pages` 是站名到 `performer_alias_followup.WikiSitePages` 的映射：站上没有的页抛
    `LookupError`，其余取页失败抛 `RuntimeError`。同名页是多位出演者的作品一览就记
    `identity=release` 的 observed，并否掉 `identity=unknown`（查创作者身份后继照写；经 `apply()`
    的清单人可以改过，一律降为 candidate）；页都不存在时只更新 unknown 候选
    的证据，写明实际问过哪几页。没有页以外的取页失败是未取得，交 None，不冻成「没有」。
    """
    from urllib.parse import quote
    from .sources.seesaa import WIKI_SOURCES
    queried = []
    for wiki, root in WIKI_ROOTS.items():
        source = WIKI_SOURCES[wiki]()
        for key in keys:
            try:
                url = root + 'd/' + quote(key.encode('euc_jp'))
            except UnicodeEncodeError:
                queried.append(f'{wiki}「{key}」（写不成站上编码）')
                continue
            try:
                page = pages[wiki].get(url)
            except LookupError:
                queried.append(f'{wiki}「{key}」（页不存在）')
                continue
            except RuntimeError:
                return None
            found = _label_page(page, source)
            if not found:
                queried.append(f'{wiki}「{key}」（不是作品一览）')
                continue
            works, cast, prefix = found
            evidence = (f'{wiki}「{key}」页是作品一览：{works} 部、出演者 {cast} 位'
                        + (f'，番号前缀 {prefix}' if prefix else '') + '；名称指向厂牌或系列，不是个人账号')
            return {'entity_id': entity_id, 'name': name, 'claims': [
                {'facet': 'identity', 'value': 'release', 'status': 'observed', 'confidence': 0.8,
                 'source_url': url, 'evidence': evidence},
                {'facet': 'identity', 'value': 'unknown', 'status': 'rejected', 'confidence': 0.8,
                 'source_url': url, 'evidence': evidence}]}
    return {'entity_id': entity_id, 'name': name, 'claims': [
        {'facet': 'identity', 'value': 'unknown', 'status': 'candidate', 'confidence': 0.5,
         'evidence': '公开身份来源未取得；已按页名直取 ' + '、'.join(queried) + '。同名命中不构成唯一身份依据。'}]}


def _rows(connection, table, clause, params):
    cursor = connection.execute(f'SELECT * FROM {table} WHERE {clause} ORDER BY rowid', params)
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns,raw)) for raw in cursor]


def plan(connection, findings):
    result = []
    for finding in findings:
        entity_id = int(finding['entity_id'])
        entity = _rows(connection, 'entity', 'id=?', (entity_id,))
        if not entity or entity[0]['canonical_name'] != finding['name']:
            raise ValueError('身份清单与实体名称不一致')
        relations = _rows(connection, 'asset_entity', "entity_id=? AND role='creator'", (entity_id,)) if finding.get('cast_studios') else []
        cast = []
        for relation in relations:
            asset = _rows(connection, 'asset', 'id=?', (relation['asset_id'],))[0]
            if relation['source'] != 'legacy:asset' or is_protected(owner_of(asset['field_owners'], 'creator')):
                continue
            # 出演者必须有公开身份来源，且发行署名在厂牌上下文中完整匹配。
            if not finding.get('cast_studios') or asset['studio'] not in finding['cast_studios']:
                continue
            if not any(claim['facet']=='occupation' and claim['value'] in {'adult_performer','actor','model'}
                       and claim['status']=='observed' for claim in finding['claims']):
                raise ValueError('出演者迁移需要已取得的公开职业来源')
            name = re.escape(finding['name']).replace(r'\ ', r'[ ._-]+')
            publisher = re.escape(asset['studio']).replace(r'\ ', r'[ ._-]+')
            parts = re.split(r'[\\/]', asset['path'])
            if not any(re.search(r'^' + publisher + r'[ ._-]+.*(?<!\w)' + name + r'(?!\w)', part, re.I) for part in parts):
                continue
            cast.append({'relation':relation, 'asset':asset})
        result.append({'finding':finding, 'entity':entity[0], 'relations':relations,
                       'claims':_rows(connection, 'entity_classification', 'entity_id=?', (entity_id,)), 'cast':cast})
    return result


def apply(connection, frozen):
    if plan(connection, [row['finding'] for row in frozen]) != frozen:
        raise ValueError('身份计划已过期')
    receipt = {'source':SOURCE, 'changes':[], 'created_entities':[]}
    stamp = datetime.now(timezone.utc).isoformat()
    for row in frozen:
        finding = row['finding']; entity_id = int(finding['entity_id'])
        targets = [entity_id]
        performer = None
        if row['cast']:
            performer = resolve_entity(connection, 'performer', finding['name'])
            existed = performer is not None
            if not existed:
                performer_id = upsert_asset_entity(connection, kind='performer', name=finding['name'],
                    asset_id=row['cast'][0]['asset']['id'], role='performer', source=SOURCE,
                    metadata={'source_url':finding['cast_source_url']}, update_entity_metadata=False)
                # 记录来源关系的 before 必须在插入前，所以先撤掉这条临时创建关系。
                connection.execute("DELETE FROM asset_entity WHERE entity_id=? AND source=?", (performer_id,SOURCE))
                receipt['created_entities'].append(_rows(connection,'entity','id=?',(performer_id,))[0])
            else:
                performer_id = performer['id'] if isinstance(performer, sqlite3.Row) else performer[0]
            targets.append(performer_id)
            receipt['changes'].append({'table':'entity_identity_link', 'clause':'left_id=? AND right_id=? AND source=?',
                'params':[entity_id,performer_id,SOURCE], 'before':_rows(connection,'entity_identity_link','left_id=? AND right_id=? AND source=?',(entity_id,performer_id,SOURCE))})
            connection.execute('INSERT OR IGNORE INTO entity_identity_link VALUES(?,?,?,?,?,?,?,?)',
                (entity_id,performer_id,'same_person',SOURCE,finding['cast_source_url'],
                 '发行文件完整署名与公开出演者身份一致','candidate',stamp))
            for cast in row['cast']:
                asset_id = cast['asset']['id']
                receipt['changes'].append({'table':'asset_entity','clause':"asset_id=? AND (entity_id=? OR (entity_id=? AND source=?))",
                    'params':[asset_id,entity_id,performer_id,SOURCE],
                    'before':_rows(connection,'asset_entity',"asset_id=? AND (entity_id=? OR (entity_id=? AND source=?))",(asset_id,entity_id,performer_id,SOURCE))})
                receipt['changes'].append({'table':'asset','clause':'id=?','params':[asset_id],
                    'before':_rows(connection,'asset','id=?',(asset_id,))})
                upsert_asset_entity(connection, kind='performer',name=finding['name'],asset_id=asset_id,
                    role='performer',source=SOURCE,metadata={'source_url':finding['cast_source_url'], 'evidence':'厂牌发行文件完整署名'},update_entity_metadata=False)
                connection.execute("DELETE FROM asset_entity WHERE asset_id=? AND entity_id=? AND role='creator' AND source='legacy:asset'",(asset_id,entity_id))
                survivors = connection.execute("SELECT e.canonical_name FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id WHERE ae.asset_id=? AND ae.role='creator'", (asset_id,)).fetchall()
                if cast['asset']['creator'] == finding['name']:
                    names = {item[0] for item in survivors}
                    if len(names)>1:
                        raise ValueError('作品存在多个发布者，需复核')
                    write_owned_fields(connection,[asset_id],{'creator':next(iter(names),None)},SOURCE,
                                       expected_revision=cast['asset']['mutation_revision'])
        for target in targets:
            receipt['changes'].append({'table':'entity_classification','clause':'entity_id=? AND source=?','params':[target,SOURCE],
                'before':_rows(connection,'entity_classification','entity_id=? AND source=?',(target,SOURCE))})
            for claim in finding['claims']:
                status = 'rejected' if claim.get('status') == 'rejected' else 'candidate'
                write_claim(connection, entity_id=target, source=SOURCE, **{**claim, 'status': status})
    for change in receipt['changes']:
        change['after'] = _rows(connection,change['table'],change['clause'],change['params'])
    return receipt


def restore(connection, receipt):
    """有后续修改就拒绝撤回，不覆盖观看历史或人的判断。"""
    for change in receipt['changes']:
        if _rows(connection,change['table'],change['clause'],change['params']) != change['after']:
            raise ValueError('身份修复记录已发生后续改动')
    for entity in receipt['created_entities']:
        if _rows(connection,'entity','id=?',(entity['id'],)) != [entity]:
            raise ValueError('新身份已发生后续改动')
    for change in reversed(receipt['changes']):
        table = change['table']
        if table == 'asset':
            old = change['before'][0]
            fields = {'creator':old['creator']}
            write_owned_fields(connection,[old['id']],fields,SOURCE)
            connection.execute('UPDATE asset SET field_owners=? WHERE id=?', (old['field_owners'],old['id']))
            continue
        connection.execute(f'DELETE FROM {table} WHERE ' + change['clause'],change['params'])
        for old in change['before']:
            columns = list(old)
            connection.execute(f'INSERT INTO {table} (' + ','.join(columns) + ') VALUES(' + ','.join('?' for _ in columns) + ')',tuple(old.values()))
    for entity in receipt['created_entities']:
        # 新身份的任何其他引用都需要用户保留；只撤回本批创建的空身份。
        references = connection.execute('SELECT count(*) FROM asset_entity WHERE entity_id=?',(entity['id'],)).fetchone()[0]
        if references:
            raise ValueError('新身份出现额外作品关系')
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            if not re.fullmatch(r'[a-zA-Z0-9_]+',table):
                continue
            for foreign in connection.execute(f'PRAGMA foreign_key_list({table})'):
                if foreign[2]=='entity' and connection.execute(f'SELECT 1 FROM {table} WHERE {foreign[3]}=?',(entity['id'],)).fetchone():
                    raise ValueError('新身份出现额外资料')
        connection.execute('DELETE FROM entity WHERE id=?',(entity['id'],))
