"""存量身份数据的两项修正，默认只列计划。

- `--downgrade`：标成 observed 却不出自代码判据（`source:`、`script:fc2-seller@`）的分类断言与
  身份关联降为 candidate，等用户复核（ADR-0052 决策一）。来源、证据与检查时间不变。
- `--directories`：某账号在一个目录子树里的每件有效作品都已被逐个否定时，补一条目录级否定，
  以后进入该目录的新文件不再归给这个账号。只留最上层目录，不顺带否定任何未被否定的作品。

两项都不给时两项都列。`--report` 把完整计划写成 JSON；`--apply` 必须同时给 `--backup`，
在一个事务里写完并做完整性与外键自检。

    repair_identity_claims.py --report build/identity-claims.json
    repair_identity_claims.py --downgrade --apply --backup <备份路径>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from peach.entities import apply_directory_rejections, derived_directory_rejections  # noqa: E402
from peach.entity_classification import downgrade_untrusted_observed, untrusted_observed  # noqa: E402
from peach.field_owners import script_owner  # noqa: E402
from peach.fsutil import atomic_write_text  # noqa: E402
from peach.scripting import add_ledger_write_args, counts_of, open_for_write, verify_after_write  # noqa: E402

REVIEWER = script_owner('repair_identity_claims')
EXTRA_COUNTS = {
    'observed_claims': "SELECT count(*) FROM entity_classification WHERE status='observed'",
    'candidate_claims': "SELECT count(*) FROM entity_classification WHERE status='candidate'",
    'attribution_rejections': "SELECT count(*) FROM review_decision WHERE category='creator-attribution' AND status='rejected'",
}


def build_plan(connection, *, downgrade: bool, directories: bool) -> dict:
    plan = {}
    if downgrade:
        plan['downgrade'] = untrusted_observed(connection)
    if directories:
        plan['directories'] = derived_directory_rejections(connection)
    return plan


def summary(plan: dict) -> dict:
    result = {}
    if 'downgrade' in plan:
        result['downgrade'] = {table: _by_source(rows) for table, rows in plan['downgrade'].items()}
    if 'directories' in plan:
        result['directories'] = [{key: row[key] for key in ('name', 'directory', 'subtree_assets', 'name_rejections')}
                                 for row in plan['directories']]
    return result


def _by_source(rows) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row['source']] = counts.get(row['source'], 0) + 1
    return counts


def main(argv=None) -> int:
    parser = add_ledger_write_args(argparse.ArgumentParser(description='存量身份断言降级与目录级否定'))
    parser.add_argument('--downgrade', action='store_true', help='只处理非代码判据的 observed 降级')
    parser.add_argument('--directories', action='store_true', help='只处理由作品级否定派生的目录级否定')
    parser.add_argument('--report', type=Path, help='完整计划写成 JSON')
    args = parser.parse_args(argv)
    both = not (args.downgrade or args.directories)
    selected = {'downgrade': args.downgrade or both, 'directories': args.directories or both}
    connection = open_for_write(args)
    try:
        plan = build_plan(connection, **selected)
        if args.report:
            atomic_write_text(args.report, json.dumps(plan, ensure_ascii=False, indent=2))
        output = {'plan': summary(plan)}
        if args.apply:
            connection.execute('BEGIN IMMEDIATE')
            output['before'] = counts_of(connection, EXTRA_COUNTS)
            _, known_violations = verify_after_write(connection)
            if selected['downgrade']:
                output['downgraded'] = downgrade_untrusted_observed(connection)
            if selected['directories']:
                output['directory_rejections'] = apply_directory_rejections(
                    connection, plan['directories'], reviewer=REVIEWER)
            integrity, violations = verify_after_write(connection)
            if integrity != 'ok' or violations > known_violations:
                connection.rollback()
                print(json.dumps({'error': '完整性或外键自检未通过', 'integrity': integrity,
                                  'foreign_key_violations': violations}, ensure_ascii=False), file=sys.stderr)
                return 1
            connection.commit()
            output['after'] = counts_of(connection, EXTRA_COUNTS)
        else:
            output['dry_run'] = '未写 ledger；加 --apply --backup <路径> 才写入'
        print(json.dumps(output, ensure_ascii=False, indent=2))
    finally:
        connection.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
