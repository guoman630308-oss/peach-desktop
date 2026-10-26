"""读取指定艺人与真人账号的 Babepedia 资料，生成计划或备份后补空落库。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from peach.config import REVIEW_DIR
from peach.http import HttpxTransport
from peach.scripting import (HostLimiter, add_ledger_write_args, open_for_write, open_readonly, counts_of,
                             verify_after_write)
from peach.western_profiles import profile, land


def run(args):
    if args.apply:
        if not args.plan:
            raise ValueError('执行需要已生成的 --plan')
        records = json.loads(args.plan.read_text(encoding='utf8'))
        connection = open_for_write(args)
        before = counts_of(connection)
        baseline = {tuple(row) for row in connection.execute('PRAGMA foreign_key_check')}
        result = {'before':before,'entities':[],'batch':'auto:babepedia-profile@'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}
        try:
            with connection:
                for record in records:
                    if 'issue' in record:
                        result['entities'].append({'entity_id':record['entity_id'],'skipped':record['issue']})
                        continue
                    result['entities'].append(land(connection, record['entity_id'], record['name'], record, batch=result['batch']))
                integrity, violations = verify_after_write(connection)
                if integrity != 'ok' or {tuple(row) for row in connection.execute('PRAGMA foreign_key_check')} != baseline:
                    raise ValueError('资料写入产生完整性或外键变化')
                result.update(after=counts_of(connection),integrity=integrity,foreign_keys=violations)
        finally:
            connection.close()
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        return result
    if not args.entity or len(set(args.entity)) > 16 or args.delay < 1:
        raise ValueError('每轮 1–16 位艺人，请求间隔至少 1 秒')
    return collect(args, HttpxTransport(), HostLimiter({}, default_interval=args.delay))


def collect(args, http, limiter) -> dict:
    """逐位取资料；一位失败记成问题行，已取得的照常写进计划，每位取完就落盘。"""
    connection = open_readonly(args.db)
    records = []
    destination = args.plan or args.out
    destination.parent.mkdir(parents=True,exist_ok=True)
    try:
        for entity_id in sorted(set(args.entity)):
            row = connection.execute("SELECT canonical_name FROM entity WHERE id=? AND kind IN ('performer','creator')",(entity_id,)).fetchone()
            if not row:
                records.append({'entity_id':entity_id, 'issue':'艺人不存在或不是出演者、账号'})
            else:
                aliases = [r[0] for r in connection.execute('SELECT alias FROM entity_alias WHERE entity_id=?',(entity_id,))]
                url = dict(args.profile_url or []).get(str(entity_id), '')
                limiter.wait(url or 'https://www.babepedia.com/')
                try:
                    records.append({'entity_id':entity_id, 'name':row[0], **profile(http,row[0],aliases,profile_url=url)})
                except Exception as error:
                    records.append({'entity_id':entity_id, 'name':row[0], 'issue':f'{type(error).__name__}: {str(error)[:200]}'})
            destination.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf8')
    finally:
        connection.close()
        http.close()
    return {'profiles':sum('issue' not in record for record in records),
            'issues':sum('issue' in record for record in records),'plan':str(destination)}


if __name__ == '__main__':
    parser = add_ledger_write_args(argparse.ArgumentParser(description=__doc__))
    parser.add_argument('--entity',type=int,action='append')
    parser.add_argument('--profile-url',nargs=2,action='append',metavar=('ENTITY_ID','URL'))
    parser.add_argument('--plan',type=Path)
    parser.add_argument('--out',type=Path,default=REVIEW_DIR/'western-profiles-receipt.json')
    parser.add_argument('--delay',type=float,default=1)
    print(json.dumps(run(parser.parse_args()),ensure_ascii=False))
