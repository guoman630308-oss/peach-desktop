"""用户确认的目录用途、归属预览、来源撤回及新文件继承。"""
from __future__ import annotations

import json
import sqlite3
from hashlib import sha256
from pathlib import PureWindowsPath

from .entities import (_creator_attribution_rejected, canonicalize_entity_name,
                       normalize_entity_name, resolve_entity, resolve_entity_id, upsert_asset_entity)
from .entity_classification import write_claim
from .field_owners import owner_of, write_owned_fields
from .media_folders import path_predicate, within
from .profiles import DEFAULT_PROFILE_ID

KEY = 'folderRules'
KINDS = {'category': '', 'creator': 'creator', 'performer': 'performer', 'seller': 'creator', 'studio': 'studio'}


class FolderRuleConflict(ValueError):
    """目录范围或规则版本与预览不一致。"""


def load(connection) -> tuple[dict, dict]:
    row = connection.execute('SELECT settings_json FROM profile WHERE id=?', (DEFAULT_PROFILE_ID,)).fetchone()
    settings = json.loads(row[0] or '{}') if row else {}
    if not isinstance(settings, dict):
        raise ValueError('用户设置格式不正确')
    data = settings.get(KEY, {'revision': 0, 'rules': []})
    if not isinstance(data, dict) or not isinstance(data.get('rules'), list) or type(data.get('revision')) is not int:
        raise ValueError('目录规则格式不正确')
    for rule in data['rules']:
        if rule.get('entity_id'):
            target = resolve_entity_id(connection, rule['entity_id'])
            row = connection.execute('SELECT id,kind,canonical_name FROM entity WHERE id=?', (target,)).fetchone() if target else None
            if row and row[1] == KINDS[rule['purpose']]:
                rule['entity_id'], rule['name'] = row[0], row[2]
    return settings, data


def source(rule):
    return 'user:directory-' + rule['id']


def effective(rules, location: str, path: str):
    matches = [rule for rule in rules if rule['location'] == location
               and within(path, rule['path'], rule['recursive'])]
    return max(matches, key=lambda rule: len(PureWindowsPath(rule['path']).parts), default=None)


def validate(connection, node, body):
    purpose = body.get('purpose', 'category')
    if purpose not in KINDS:
        raise ValueError('请选择目录用途')
    if not isinstance(body.get('recursive'), bool) or not isinstance(body.get('future'), bool):
        raise ValueError('目录范围与新文件设置须明确选择')
    name = body.get('name', '')
    if not isinstance(name, str) or len(name) > 200:
        raise ValueError('关联名称最多 200 个字符')
    kind = KINDS[purpose]
    canonical = canonicalize_entity_name(kind, name) if kind else ''
    entity_id = None
    if kind:
        if not canonical:
            raise ValueError('请输入关联名称')
        entity = resolve_entity(connection, kind, canonical)
        if entity is None:
            possible = connection.execute(
                'SELECT DISTINCT e.id FROM entity e LEFT JOIN entity_alias al ON al.entity_id=e.id '
                'WHERE e.kind=? AND (e.normalized_name=? OR al.normalized_alias=?)',
                (kind, normalize_entity_name(canonical), normalize_entity_name(canonical))).fetchall()
            if possible:
                raise ValueError('名称存在多个身份或不同写法，请使用资料页的规范名称')
        else:
            entity_id, canonical = entity['id'], entity['canonical_name']
    return {'id': node['id'], 'location': node['location'], 'path': node['path'], 'purpose': purpose,
            'name': canonical, 'entity_id': entity_id, 'recursive': body['recursive'], 'future': body['future']}


def _asset_rows(connection, rule):
    clause, params = path_predicate(rule['location'], rule['path'], rule['recursive'])
    return [dict(row) for row in connection.execute(
        "SELECT a.id,a.location,a.path,a.name,a.creator,a.studio,a.field_owners,a.mutation_revision "
        "FROM asset a WHERE COALESCE(a.disposal,'') NOT IN ('trash','vanished') AND " + clause + ' ORDER BY a.id', params)]


def _relations(connection, asset_id):
    return [dict(row) for row in connection.execute(
        'SELECT ae.entity_id,ae.role,ae.source,e.kind,e.canonical_name FROM asset_entity ae '
        'JOIN entity e ON e.id=ae.entity_id WHERE ae.asset_id=? ORDER BY ae.entity_id,ae.role,ae.source', (asset_id,))]


def _conflict(connection, asset, rule, relations):
    kind = KINDS[rule['purpose']]
    if not kind:
        return ''
    field = kind if kind in {'creator', 'studio'} else ''
    own = source(rule)
    if field and asset[field] and asset[field] != rule['name'] and owner_of(asset['field_owners'], field) != own:
        return '已有不同的归属'
    if field and owner_of(asset['field_owners'], field).startswith(('user:', 'review:')) and owner_of(asset['field_owners'], field) != source(rule):
        return '已有逐件确认'
    if any(row['kind'] == kind and row['canonical_name'] != rule['name'] and row['source'] != own for row in relations):
        return '已有不同的身份关系'
    if kind == 'creator' and _creator_attribution_rejected(connection, [rule['name']], asset['id'], asset['path']):
        return '已有归属否定'
    return ''


def plan(connection, rule, data):
    """预览绑定资产、关系和规则版本，提交时重算并拒收过期计划。"""
    rules = [row for row in data['rules'] if row['id'] != rule['id']] + [rule]
    rows = []
    for asset in _asset_rows(connection, rule):
        chosen = effective(rules, asset['location'], asset['path'])
        relations = _relations(connection, asset['id'])
        reason = '由子目录规则管理' if chosen and chosen['id'] != rule['id'] else _conflict(connection, asset, rule, relations)
        rows.append({**asset, 'reason': reason, 'relations': relations})
    old = next((item for item in data['rules'] if item['id'] == rule['id']), None)
    changing = old and any(old.get(key) != rule[key] for key in ('purpose', 'name', 'entity_id', 'recursive'))
    previous = [dict(row) for row in connection.execute(
        'SELECT a.id,a.path,a.creator,a.studio,a.field_owners,a.mutation_revision,ae.entity_id,ae.role,ae.metadata_json '
        'FROM asset_entity ae JOIN asset a ON a.id=ae.asset_id WHERE ae.source=? ORDER BY a.id,ae.entity_id,ae.role',
        (source(rule),))] if changing else []
    signature = sha256(json.dumps([rule, data['revision'], rows, previous], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {'plan_version': signature, 'total': len(rows), 'withdrawn': len({row['id'] for row in previous}),
            'applicable': sum(not row['reason'] for row in rows),
            'conflicts': [{'id': row['id'], 'name': row['name'], 'reason': row['reason']} for row in rows if row['reason']],
            'sample': [{'id': row['id'], 'name': row['name']} for row in rows[:6]], 'rows': rows}


def _link(connection, asset, rule):
    kind = KINDS[rule['purpose']]
    if not kind:
        return False
    own = source(rule)
    if connection.execute('SELECT 1 FROM asset_entity WHERE asset_id=? AND source=?', (asset['id'], own)).fetchone():
        return False
    metadata = {'directory': rule['path'], 'rule_id': rule['id'], 'purpose': rule['purpose']}
    field = kind if kind in {'creator', 'studio'} else ''
    if field:
        metadata['projection'] = {'field': field, 'value': asset[field], 'owner': owner_of(asset['field_owners'], field)}
    entity_id = upsert_asset_entity(connection, kind=kind, name=rule['name'], asset_id=asset['id'], role=kind,
                                    source=own, metadata=metadata, update_entity_metadata=False)
    rule['entity_id'] = entity_id
    if field and not asset[field]:
        write_owned_fields(connection, [asset['id']], {field: rule['name']}, own, require_empty=True)
    if kind == 'performer':
        connection.execute('INSERT OR IGNORE INTO asset_tag(asset_id,tag,confidence,source) VALUES(?,?,1,?)',
                           (asset['id'], '演员:' + rule['name'], own))
    if rule['purpose'] == 'seller':
        write_claim(connection, entity_id=entity_id, facet='account_role', value='seller', source=own,
                    evidence='用户确认目录用途：' + rule['path'], status='approved', confidence=1)
    return True


def withdraw(connection, rule):
    """只撤回本规则的关系；后来由其他操作接管的字段保持原值。"""
    own = source(rule)
    rows = connection.execute(
        'SELECT ae.asset_id,ae.metadata_json,e.kind,e.canonical_name FROM asset_entity ae '
        'JOIN entity e ON e.id=ae.entity_id WHERE ae.source=?', (own,)).fetchall()
    connection.execute('DELETE FROM asset_entity WHERE source=?', (own,))
    connection.execute('DELETE FROM asset_tag WHERE source=?', (own,))
    connection.execute('DELETE FROM entity_classification WHERE source=?', (own,))
    for row in rows:
        projection = json.loads(row['metadata_json'] or '{}').get('projection')
        if not projection:
            continue
        field = projection['field']
        asset = connection.execute(f'SELECT {field},field_owners FROM asset WHERE id=?', (row['asset_id'],)).fetchone()
        if not asset or owner_of(asset['field_owners'], field) != own or asset[0] != row['canonical_name']:
            continue
        remaining = connection.execute(
            'SELECT e.canonical_name FROM asset_entity ae JOIN entity e ON e.id=ae.entity_id '
            'WHERE ae.asset_id=? AND e.kind=? ORDER BY e.id LIMIT 1', (row['asset_id'], row['kind'])).fetchone()
        value = remaining[0] if remaining else projection['value']
        write_owned_fields(connection, [row['asset_id']], {field: value}, own)
        owner_path = '$.' + field
        if projection['owner']:
            connection.execute("UPDATE asset SET field_owners=json_set(COALESCE(field_owners,'{}'),?,?) WHERE id=?",
                               (owner_path, projection['owner'], row['asset_id']))
        else:
            connection.execute("UPDATE asset SET field_owners=json_remove(COALESCE(field_owners,'{}'),?) WHERE id=?",
                               (owner_path, row['asset_id']))
    return len(rows)


def save(connection, settings, data, rule, preview, expected):
    if preview['plan_version'] != expected:
        raise FolderRuleConflict('目录内容或归属已变化，请重新预览')
    old = next((row for row in data['rules'] if row['id'] == rule['id']), None)
    if old and any(old.get(key) != rule[key] for key in ('purpose', 'name', 'entity_id', 'recursive')):
        withdraw(connection, old)
        preview = plan(connection, rule, data)
    applied = sum(_link(connection, row, rule) for row in preview['rows'] if not row['reason'])
    settings[KEY] = {'revision': data['revision'] + 1,
                     'rules': [row for row in data['rules'] if row['id'] != rule['id']] + [rule]}
    _store(connection, settings)
    return applied


def _store(connection, settings):
    changed = connection.execute('UPDATE profile SET settings_json=? WHERE id=?',
                                 (json.dumps(settings, ensure_ascii=False), DEFAULT_PROFILE_ID)).rowcount
    if not changed:
        raise ValueError('当前用户设置不存在')


def remove(connection, settings, data, key):
    old = next((row for row in data['rules'] if row['id'] == key), None)
    removed = withdraw(connection, old) if old else 0
    settings[KEY] = {'revision': data['revision'] + 1, 'rules': [row for row in data['rules'] if row['id'] != key]}
    _store(connection, settings)
    return removed


def apply_new(connection, asset_ids):
    """扫描登记的新资产只沿用用户明确启用的最近一层目录规则。"""
    if not asset_ids or not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='profile'").fetchone():
        return 0
    previous = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        settings, data = load(connection)
        if not data['rules']:
            return 0
        count = 0
        for asset_id in asset_ids:
            row = connection.execute('SELECT id,location,path,name,creator,studio,field_owners,mutation_revision '
                                     'FROM asset WHERE id=?', (asset_id,)).fetchone()
            if row is None:
                continue
            asset = dict(row)
            rule = effective(data['rules'], asset['location'], asset['path'])
            if not rule or not rule['future'] or not KINDS[rule['purpose']]:
                continue
            if not _conflict(connection, asset, rule, _relations(connection, asset_id)):
                count += _link(connection, asset, rule)
        if count:
            data['revision'] += 1
            settings[KEY] = data
            _store(connection, settings)
        return count
    finally:
        connection.row_factory = previous
