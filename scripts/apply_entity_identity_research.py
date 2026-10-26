"""身份来源清单的冻结预览、可撤回写入与恢复。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from peach import entity_identity_research as research
from peach.fsutil import atomic_write_text
from peach.metadata_creator_attributions import fingerprint
from peach.scripting import add_ledger_write_args, counts_of, open_for_write, open_readonly


def main():
    parser = add_ledger_write_args(argparse.ArgumentParser(description='身份来源分类与出演署名衔接'))
    parser.add_argument('--findings', type=Path)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--restore', type=Path)
    args = parser.parse_args()
    if not args.apply:
        if not args.findings or not args.plan:
            parser.error('预览需要 --findings 和 --plan')
        connection = open_readonly(args.db)
        try:
            rows = research.plan(connection,json.loads(args.findings.read_text(encoding='utf-8')))
            atomic_write_text(args.plan,json.dumps({'rows':rows,'sha256':fingerprint(rows)},ensure_ascii=False,indent=2))
            print(json.dumps({'entities':len(rows),'cast_relations':sum(len(row['cast']) for row in rows)},ensure_ascii=False))
        finally:
            connection.close()
        return 0
    if not args.backup or not args.manifest or not (args.plan or args.restore):
        parser.error('写入需要 --backup --manifest 和 --plan 或 --restore')
    if args.manifest.exists():
        parser.error('回执路径已经存在')
    payload = json.loads((args.restore or args.plan).read_text(encoding='utf-8'))
    if not args.restore and fingerprint(payload['rows']) != payload['sha256']:
        parser.error('计划摘要不匹配')
    connection = open_for_write(args)
    try:
        before = counts_of(connection)
        keys = sorted((tuple(row) for row in connection.execute('PRAGMA foreign_key_check')),key=repr)
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('账本完整性检查未通过')
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('BEGIN IMMEDIATE')
        if args.restore:
            research.restore(connection,payload)
            receipt = {'restored':True}
        else:
            receipt = research.apply(connection,payload['rows'])
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or keys != sorted((tuple(row) for row in connection.execute('PRAGMA foreign_key_check')),key=repr):
            raise ValueError('身份写入产生了完整性或外键变化')
        receipt.update(before_counts=before,after_counts=counts_of(connection),status='prepared')
        args.manifest.parent.mkdir(parents=True,exist_ok=True)
        with args.manifest.open('x',encoding='utf-8') as handle:
            json.dump(receipt,handle,ensure_ascii=False,indent=2)
        connection.commit()
        receipt['status']='committed'
        atomic_write_text(args.manifest,json.dumps(receipt,ensure_ascii=False,indent=2))
        print(json.dumps({key:receipt[key] for key in ['before_counts','after_counts','status']},ensure_ascii=False))
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return 0


if __name__=='__main__':
    raise SystemExit(main())
