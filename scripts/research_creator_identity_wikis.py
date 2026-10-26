"""身份待核验的创作者按页名查 sougouwiki 与 av_neme，产出身份清单；账本只读。

清单交给 `apply_entity_identity_research.py --findings` 预览、冻结和写入。取页失败的实体
不进清单，打印成未取得，下一批再问。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from peach import entity_identity_research as research  # noqa: E402
from peach.config import DATABASE_PATH, GENERATED_DIR, SECRETS_DIR  # noqa: E402
from peach.fsutil import atomic_write_text  # noqa: E402
from peach.performer_alias_followup import WikiSitePages  # noqa: E402
from peach.scripting import open_readonly  # noqa: E402
from peach.sources.seesaa import AV_NEME, SEESAA  # noqa: E402


def targets(connection, entity_ids):
    """(实体 id, 规范名, 文件名前缀)；默认是本来源记为身份待核验候选、身份面还没有可信断言的创作者。"""
    if entity_ids:
        marks = ','.join('?' for _ in entity_ids)
        rows = connection.execute(f"SELECT id,canonical_name FROM entity WHERE kind='creator' AND id IN ({marks})",
                                  entity_ids).fetchall()
    else:
        rows = connection.execute(
            "SELECT e.id,e.canonical_name FROM entity e JOIN entity_classification ec ON ec.entity_id=e.id "
            "WHERE e.kind='creator' AND ec.facet='identity' AND ec.value='unknown' AND ec.source=? "
            "AND ec.status='candidate' AND NOT " + research.settled_sql() + " ORDER BY e.id",
            (research.SOURCE,)).fetchall()
    rows = [(entity_id, name) for entity_id, name in rows if research.researchable(name)]
    prefixes = research.filename_prefixes(connection, [entity_id for entity_id, _name in rows])
    return [(entity_id, name, prefixes[entity_id]) for entity_id, name in rows]


def main():
    parser = argparse.ArgumentParser(description='按页名查日本资料站，产出创作者身份清单')
    parser.add_argument('--db', type=Path, default=DATABASE_PATH)
    parser.add_argument('--output', type=Path, required=True, help='身份清单 JSON')
    parser.add_argument('--entity', type=int, action='append', default=[], help='只查这些实体，可重复')
    parser.add_argument('--cache-dir', type=Path, default=GENERATED_DIR/'provider-cache'/'seesaa-pages',
                        help='页缓存，缺省与补别名、查创作者身份两条后继共用')
    parser.add_argument('--cooldown-root', type=Path, default=SECRETS_DIR, help='来源冷却记录，缺省与服务共用')
    parser.add_argument('--max-requests', type=int, default=80, help='每站本批请求上限')
    args = parser.parse_args()
    connection = open_readonly(args.db)
    try:
        todo = targets(connection, args.entity)
    finally:
        connection.close()
    # 两站同一主机，取页器共用补别名后继那一个主机间隔；撞墙记进来源冷却，服务那边也停。
    pages = {config.name: WikiSitePages(args.cache_dir, args.cooldown_root, config=config,
                                        max_requests=args.max_requests) for config in (SEESAA, AV_NEME)}
    findings, unfetched = [], []
    try:
        for entity_id, name, prefixes in todo:
            finding = research.wiki_finding(entity_id, name, research.lookup_keys(name, prefixes), pages)
            if finding is None:
                unfetched.append(name)
            else:
                findings.append(finding)
    finally:
        for wiki in pages.values():
            wiki.close()
    atomic_write_text(args.output, json.dumps(findings, ensure_ascii=False, indent=2))
    hits = [row['name'] for row in findings if any(claim['value'] == 'release' for claim in row['claims'])]
    print(json.dumps({'queried': len(todo), 'findings': len(findings), 'release_pages': hits,
                      'unfetched': unfetched}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
