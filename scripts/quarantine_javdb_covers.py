"""将有 JavDB 来源证据的封面移入可恢复备份区，正式封面目录只保留合格图源。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from peach.cover_artwork import has_javdb_source
from peach.fsutil import atomic_write_text


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    root, report = args.data_root.resolve(), args.report.resolve()
    if not report.is_relative_to(root / 'review'):
        parser.error('备份区必须位于当前 Peach 数据目录的 review 内')
    covers = root / 'generated/covers'
    paths = [path for path in sorted(covers.glob('*.jpg')) if has_javdb_source(path)]
    report.mkdir(parents=True, exist_ok=True)
    manifest = report / 'manifest.json'
    history = json.loads(manifest.read_text(encoding='utf-8')).get('items', []) if manifest.exists() else []
    items = {row['code']: row for row in history}
    def save():
        result = dict(matched=len(paths), quarantined=sum(row['status'] == 'quarantined' for row in items.values()),
                      applied=args.apply, items=list(items.values()))
        atomic_write_text(manifest, json.dumps(result, ensure_ascii=False, indent=2))
        return result
    for path in paths:
        moves = []
        for suffix in ('.jpg', '.face.json', '.poster.json', '.scraping.json'):
            source = path.with_suffix(suffix)
            if not source.exists():
                continue
            destination = report / 'originals' / source.name
            moves.append((source, destination))
        if args.apply:
            for _, destination in moves:
                if destination.exists():
                    raise FileExistsError(destination)
            completed = []
            try:
                for source, destination in moves:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    source.rename(destination)
                    completed.append((source, destination))
            except OSError:
                for source, destination in reversed(completed):
                    destination.rename(source)
                raise
        items[path.stem] = dict(code=path.stem, status='quarantined' if args.apply else 'planned',
                               files=[str(destination) for _, destination in moves])
        save()
    result = save()
    print(json.dumps({key: value for key, value in result.items() if key != 'items'}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
