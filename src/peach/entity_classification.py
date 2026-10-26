"""实体职业与作品角色分别记录，来源断言可按批撤回。"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

from .entry_links import EXTERNAL_KIND, JAV_DIRECTORIES

VALUES = {
    'identity': {'person':'个人', 'account':'发布账号', 'organization':'机构',
                 'platform':'平台', 'collection':'合集目录', 'release':'作品标识', 'unknown':'身份待核验'},
    'occupation': {'adult_performer':'成人出演者', 'amateur_performer':'非职业出演者', 'model':'模特', 'actor':'演员',
                   'artist':'艺术创作者', 'animator':'动画作者', 'content_creator':'内容创作者'},
    'market': {'japanese_av':'日本 AV', 'western_adult':'西方成人发行'},
    'account_role': {'seller':'卖家', 'publisher':'发布者', 'studio':'发行厂牌'},
}
CATEGORIES = {'japanese_av': '女优', 'amateur': '素人', 'western': '西方',
              'blogger': '网黄博主', 'seller': '卖家', 'animation': '动画作者'}
#: observed 只属于代码判据（ADR-0052 决策一）：`source:` 是抓取器从来源页解析出的事实，
#: `script:fc2-seller@` 由 FC2 作品页快照的卖家字段解析，`script:creator-identity-wiki@` 由查创作者
#: 身份后继按页名直取资料站、按作品表结构判定（ADR-0098）。研究与检索结论只写 candidate。
CODE_SOURCES = ('source:', 'script:fc2-seller@', 'script:creator-identity-wiki@')
#: 视觉模型标签的来源前缀：`vision`、`vision_creator`、`vision_creator_review` 等。
MODEL_TAG_SOURCE_PREFIX = 'vision'


def _code_source_sql(column: str) -> str:
    return '(' + ' OR '.join(f"{column}source LIKE '{prefix}%'" for prefix in CODE_SOURCES) + ')'


def trusted_sql(alias: str = '') -> str:
    """可信断言：用户复核，或代码判据来源的 observed。"""
    column = f'{alias}.' if alias else ''
    return f"({column}status='approved' OR ({column}status='observed' AND {_code_source_sql(column)}))"


_UNTRUSTED_OBSERVED = "status='observed' AND NOT " + _code_source_sql('')
_CLAIM_TABLES = ('entity_classification', 'entity_identity_link')


def untrusted_observed(connection) -> dict[str, list[dict]]:
    """标成 observed 却不出自代码判据的断言与身份关联，即存量降级计划。"""
    plan = {}
    for table in _CLAIM_TABLES:
        cursor = connection.execute(f'SELECT * FROM {table} WHERE {_UNTRUSTED_OBSERVED} ORDER BY rowid')
        columns = [column[0] for column in cursor.description]
        plan[table] = [dict(zip(columns, row)) for row in cursor]
    return plan


def downgrade_untrusted_observed(connection) -> dict[str, int]:
    """这些行降为 candidate 等用户复核；来源、证据、置信度与检查时间原样保留。"""
    return {table: connection.execute(f"UPDATE {table} SET status='candidate' WHERE {_UNTRUSTED_OBSERVED}").rowcount
            for table in _CLAIM_TABLES}


def write_claim(connection, *, entity_id, facet, value, source, evidence,
                source_url='', status='candidate', confidence=0.5, checked_at=None):
    """目录、检索与研究推断保持 candidate，代码判据事实使用 observed，用户复核使用 approved。"""
    if facet not in VALUES or value not in VALUES[facet]:
        raise ValueError('未知身份分类')
    if status not in {'candidate','observed','approved','rejected'}:
        raise ValueError('未知断言状态')
    if not source or not evidence or not 0 <= confidence <= 1:
        raise ValueError('断言缺少来源、证据或有效置信度')
    if status == 'approved' and not source.startswith(('user:', 'review:')):
        raise ValueError('approved 只属于用户复核')
    if status == 'observed' and urlsplit(source_url).scheme != 'https':
        raise ValueError('公开事实需要 HTTPS 来源')
    if status == 'observed' and not source.startswith(CODE_SOURCES):
        raise ValueError('observed 只属于代码判据来源')
    # 脚本不覆盖同来源上的用户复核。
    old = connection.execute('SELECT status FROM entity_classification WHERE entity_id=? AND facet=? AND value=? AND source=?',
                             (entity_id,facet,value,source)).fetchone()
    if old and old[0] == 'approved' and status != 'approved':
        raise ValueError('不能覆盖已复核断言')
    connection.execute('INSERT INTO entity_classification VALUES(?,?,?,?,?,?,?,?,?) '
                       'ON CONFLICT(entity_id,facet,value,source) DO UPDATE SET source_url=excluded.source_url,'
                       'evidence=excluded.evidence,status=excluded.status,confidence=excluded.confidence,checked_at=excluded.checked_at',
                       (entity_id,facet,value,source,source_url,evidence,status,confidence,
                        checked_at or datetime.now(timezone.utc).isoformat()))


def classifications(connection, entity_ids):
    """批量下发公开证据与展示字串；待核验不冒充职业。"""
    ids = list(dict.fromkeys(entity_ids))
    result = {entity_id:[] for entity_id in ids}
    if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='entity_classification'").fetchone():
        return result
    if not ids:
        return result
    cursor = connection.execute('SELECT entity_id,facet,value,source,source_url,evidence,status,confidence,checked_at '
                                'FROM entity_classification WHERE entity_id IN (' + ','.join('?' for _ in ids) + ') '
                                'ORDER BY facet,value,source', ids)
    columns = [column[0] for column in cursor.description]
    for raw in cursor:
        row = dict(zip(columns,raw))
        row['label'] = VALUES.get(row['facet'], {}).get(row['value'], row['value'])
        result[row.pop('entity_id')].append(row)
    return result


def category_predicates(column='e.id', kind_column='e.kind', connection=None):
    """浏览分类分别命中；发行范围与作品类型允许交叉。"""
    providers = ','.join(f"'{provider}'" for provider in sorted(JAV_DIRECTORIES))
    has_claims = connection is None or connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE name='entity_classification'").fetchone()
    def claim(facet, value):
        if not has_claims:
            return '0'
        return (f"EXISTS (SELECT 1 FROM entity_classification ec WHERE ec.entity_id={column} "
                f"AND ec.facet='{facet}' AND ec.value='{value}' AND {trusted_sql('ec')})")
    def work(condition):
        return (f"EXISTS (SELECT 1 FROM asset_entity ca JOIN asset av ON av.id=ca.asset_id "
                f"WHERE ca.entity_id={column} AND ca.role='performer' AND av.medium='video' "
                f"AND COALESCE(av.disposal,'')<>'vanished' AND ({condition}))")
    jav = (f"EXISTS (SELECT 1 FROM entity_external_ref er WHERE er.entity_id={column} "
           f"AND er.external_kind='{EXTERNAL_KIND}' AND er.external_id<>'' AND er.provider IN ({providers})) "
           f"OR {claim('market', 'japanese_av')}")
    west_work = work("av.region='west'")
    western = f"{claim('market', 'western_adult')} OR {west_work}"
    # 素人发行：FC2-PPV 番号，或作品带「素人」标签（标签表与标签实体任一处）。
    # 视觉模型读画面得出的标签（`vision*` 来源，含复核页整批通过的创作者风格标签）只描述观感，
    # 不参与身份判定；刮削、文件名、用户与发行元数据来源照收。
    amateur_release = ("COALESCE(av.code,'') LIKE 'FC2-PPV-%' "
                       f"OR EXISTS (SELECT 1 FROM asset_tag t WHERE t.asset_id=av.id AND t.tag='素人' "
                       f"AND t.source NOT LIKE '{MODEL_TAG_SOURCE_PREFIX}%') "
                       "OR EXISTS (SELECT 1 FROM asset_entity ta JOIN entity te ON te.id=ta.entity_id "
                       "WHERE ta.asset_id=av.id AND te.kind='tag' AND te.canonical_name='素人' "
                       f"AND ta.source NOT LIKE '{MODEL_TAG_SOURCE_PREFIX}%')")
    only_amateur_releases = f"{work('1')} AND NOT {work(f'NOT ({amateur_release})')}"
    amateur = (f"({claim('occupation', 'amateur_performer')} OR ({only_amateur_releases})) "
               f"AND NOT ({jav} OR {claim('occupation', 'adult_performer')} OR {western})")
    seller = claim('account_role', 'seller')
    animation = claim('occupation', 'animator')
    creator_video = (f"EXISTS (SELECT 1 FROM asset_entity ca JOIN asset av ON av.id=ca.asset_id "
                     f"WHERE ca.entity_id={column} AND ca.role='creator' AND av.medium='video' "
                     "AND COALESCE(av.disposal,'')<>'vanished')")
    personal_account = (f"{claim('identity','person')} OR {claim('occupation','content_creator')} "
                        f"OR ({claim('identity','account')} AND {claim('account_role','publisher')})")
    nonpersonal = ' OR '.join(claim('identity', value) for value in
                             ('organization', 'platform', 'collection', 'release'))
    return {
        'japanese_av': f"{kind_column} IN ('performer','creator') AND ({jav})",
        'amateur': f"{kind_column}='performer' AND ({amateur})",
        'western': f"{kind_column}='performer' AND ({western})",
        'blogger': f"{kind_column}='creator' AND ({creator_video}) AND ({personal_account}) AND (NOT ({seller}) OR {claim('identity','person')}) AND NOT ({animation}) AND NOT ({claim('account_role','studio')}) AND NOT ({nonpersonal})",
        'seller': f"{kind_column} IN ('performer','creator') AND ({seller})",
        'animation': f"{kind_column}='creator' AND ({animation})",
    }


def summaries(connection, entity_ids):
    """资料页与分页卡片共用一份浏览分类，不写入账本断言。"""
    ids = list(dict.fromkeys(entity_ids))
    if not ids:
        return {}
    predicates = category_predicates(connection=connection)
    rows = connection.execute('SELECT e.id,' + ','.join(f'({sql})' for sql in predicates.values()) + ' FROM entity e WHERE e.id IN ('
                              + ','.join('?' for _ in ids) + ')', ids)
    result = {}
    for row in rows:
        categories = [key for key, matched in zip(predicates, row[1:]) if matched]
        result[row[0]] = {'identity_categories': categories,
                          'identity_labels': [CATEGORIES[key] for key in categories]}
    return result


def work_portrait_predicate(connection, column='e.id', kind_column='e.kind'):
    """作品画面只用于出演者本人头像；发布账号需要本人身份依据。"""
    if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='entity_classification'").fetchone():
        return f"{kind_column}<>'creator'"
    return (f"({kind_column}<>'creator' OR EXISTS (SELECT 1 FROM entity_classification ec "
            f"WHERE ec.entity_id={column} AND {trusted_sql('ec')} "
            "AND ((ec.facet='identity' AND ec.value='person') "
            "OR (ec.facet='occupation' AND ec.value IN ('adult_performer','model','actor')))) "
            f"AND NOT EXISTS (SELECT 1 FROM entity_classification ec WHERE ec.entity_id={column} AND {trusted_sql('ec')} "
            "AND ((ec.facet='occupation' AND ec.value='animator') "
            "OR (ec.facet='account_role' AND ec.value IN ('seller','studio')) "
            "OR (ec.facet='identity' AND ec.value IN ('organization','platform','collection','release')))))")


def index_scope(connection, kind: str) -> str:
    """艺人名册包含真人账号和动画作者；卖家使用独立名册。"""
    predicates = category_predicates(connection=connection)
    if kind == 'performer':
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='entity_classification'").fetchone():
            return "e.kind IN ('performer','creator')"
        return ("(e.kind='performer' OR (e.kind='creator' AND (NOT (" + predicates['seller'] + ") "
                "OR (" + predicates['blogger'] + ") OR (" + predicates['animation'] + ")) "
                "AND NOT EXISTS (SELECT 1 FROM entity_classification ec WHERE ec.entity_id=e.id "
                "AND " + trusted_sql('ec') + " AND ((ec.facet='account_role' AND ec.value='studio') "
                "OR (ec.facet='identity' AND ec.value IN ('organization','platform','collection','release'))))))")
    if kind == 'creator':
        return '(' + predicates['seller'] + ')'
    return "e.kind='" + kind + "'"


def counts(connection, kind, query, visible_clause):
    """对搜索范围中的有效实体独立计数，不受所选分类及分页限制。"""
    predicates = category_predicates(connection=connection)
    sql = ('SELECT ' + ','.join(f'COALESCE(SUM(({value})),0)' for value in predicates.values()) + ' '
           'FROM entity e WHERE ' + index_scope(connection, kind) + ' AND EXISTS ('
           'SELECT 1 FROM asset_entity ae CROSS JOIN asset a ON a.id=ae.asset_id '
           'WHERE ae.entity_id=e.id AND ' + visible_clause + ') ')
    params = []
    if query:
        sql += 'AND e.canonical_name LIKE ? '
        params.append(f'%{query}%')
    totals = connection.execute(sql,params).fetchone()
    return {key: count for key, count in zip(predicates,totals) if count}


def related_identities(connection, entity_id):
    if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='entity_identity_link'").fetchone():
        return []
    cursor = connection.execute("SELECT e.id,e.kind,e.canonical_name,l.relation,l.source_url,l.evidence "
        "FROM entity_identity_link l JOIN entity e ON e.id=CASE WHEN l.left_id=? THEN l.right_id ELSE l.left_id END "
        "WHERE (l.left_id=? OR l.right_id=?) AND " + trusted_sql('l') + " ORDER BY e.kind,e.canonical_name",
        (entity_id,entity_id,entity_id))
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns,raw)) for raw in cursor]


def filter_sql(category, column='e.id', connection=None):
    """浏览分类在分页前执行，与资料页采用相同来源判据。"""
    if category in {'','all'}:
        return '', []
    if category not in CATEGORIES:
        return ' AND 0 ', []
    return f' AND ({category_predicates(column, connection=connection)[category]}) ', []


def transfer(connection, source_id, target_id):
    """用户执行实体合并时保留所有来源；同来源冲突显式拒绝。"""
    if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='entity_classification'").fetchone():
        return
    for raw in connection.execute('SELECT facet,value,source,source_url,evidence,status,confidence,checked_at FROM entity_classification WHERE entity_id=?', (source_id,)).fetchall():
        row = tuple(raw)
        old = connection.execute('SELECT source_url,evidence,status,confidence,checked_at FROM entity_classification WHERE entity_id=? AND facet=? AND value=? AND source=?',
                                 (target_id,*row[:3])).fetchone()
        if old and tuple(old)[:-1] != tuple(row[3:-1]):
            raise ValueError('实体分类来源冲突，需先复核')
        connection.execute('INSERT OR IGNORE INTO entity_classification VALUES(?,?,?,?,?,?,?,?,?)', (target_id,*row))
        if old and row[-1] > old[-1]:
            connection.execute('UPDATE entity_classification SET checked_at=? '
                               'WHERE entity_id=? AND facet=? AND value=? AND source=?',
                               (row[-1], target_id, *row[:3]))
    for raw in connection.execute('SELECT * FROM entity_identity_link WHERE left_id=? OR right_id=?', (source_id,source_id)).fetchall():
        row = list(raw)
        row[:2] = [target_id if value == source_id else value for value in row[:2]]
        if row[0] == row[1]:
            continue
        old = connection.execute('SELECT * FROM entity_identity_link WHERE left_id=? AND right_id=? AND relation=? AND source=?', row[:4]).fetchone()
        if old and tuple(old) != tuple(row):
            raise ValueError('身份关联来源冲突，需先复核')
        connection.execute('INSERT OR IGNORE INTO entity_identity_link VALUES(?,?,?,?,?,?,?,?)', row)
