"""从已有 FC2 来源缓存补齐卖家名册；默认预览，写入须备份。"""
from __future__ import annotations

import argparse
import json
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from peach import fc2_sellers
from peach.config import DATABASE_PATH
from peach.scripting import add_ledger_write_args, counts_of, open_for_write, verify_after_write


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_ledger_write_args(parser)
    parser.add_argument('--cache', type=Path, default=DATABASE_PATH.parent.parent / 'sources' / 'library-metadata')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--batch', default='')
    args = parser.parse_args()
    if args.apply and not args.batch.startswith(fc2_sellers.SOURCE + '@'):
        parser.error('--apply 需要 script:fc2-seller@ 开头的 --batch')
    with closing(open_for_write(args)) as connection, connection:
        before = counts_of(connection, {'asset_tag': 'SELECT count(*) FROM asset_tag'})
        fk_before = [tuple(row) for row in connection.execute('PRAGMA foreign_key_check')]
        frozen = fc2_sellers.collect(connection, args.cache)
        receipt = {'plan': frozen, 'before': before, 'apply': args.apply, 'batch': args.batch}
        if args.apply:
            receipt['installed'] = fc2_sellers.install(connection, frozen, batch=args.batch)
            integrity, _ = verify_after_write(connection)
            fk_after = [tuple(row) for row in connection.execute('PRAGMA foreign_key_check')]
            if integrity != 'ok' or fk_before != fk_after:
                raise ValueError('卖家写入完整性核查失败')
            receipt['integrity'] = integrity
            receipt['foreign_keys'] = len(fk_after)
        receipt['after'] = counts_of(connection, {'asset_tag': 'SELECT count(*) FROM asset_tag'})
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'accounts': len(frozen['accounts']), 'skipped': len(frozen['skipped']), 'apply': args.apply}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
