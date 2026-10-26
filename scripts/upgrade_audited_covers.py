"""按核查清单重探官方高清候选，逐番号保留备份和处理结果。"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from audit_cover_artwork import read, snapshots
from peach.cover_artwork import install_cover
from peach.fsutil import atomic_write_text
from peach.images import measure_image_file
from peach.jav_cover_fetch import (HostLimitedTransport, NotFound, Unavailable, _download_best,
                                   _measure, _no_usable_official, _usable_candidates, best_cover)
from peach.library_processing import _official_evidence, artwork_title
from peach.scraping_access import SourceTransport
from peach.community_catalog import origin_of


def upgrade(path, known, transport, report, *, live=False, metadata_root=None):
    evidence = _official_evidence(known)
    if not live and (not evidence or not evidence.candidates):
        return {'code': path.stem, 'status': 'no_official_snapshot'}
    diagnostics = {}
    def record(key):
        diagnostics[key] = diagnostics.get(key, 0) + 1
    deadline = time.monotonic() + 90
    if live:
        candidate, size, data = best_cover(transport, path.stem, 0, known=evidence,
            metadata_root=metadata_root, minimum_width=240, deadline=deadline, diagnostics=diagnostics)
    else:
        candidates = _usable_candidates(path.stem, evidence.candidates, {}, 0, 0)
        measured = _measure(transport, candidates, record,
                            minimum_width=240, delay=0, deadline=deadline)
        if not measured:
            kind = Unavailable if diagnostics.get('unanswered') else NotFound
            raise kind(_no_usable_official(diagnostics))
        candidate, size, data = _download_best(transport, measured, record,
            minimum_width=240, minimum_quality=0, minimum_pixels=0, deadline=deadline)
    previous = read(path.with_suffix('.scraping.json'))
    old_size = measure_image_file(path)
    community = origin_of(str(previous.get('source_url') or '')) in ('javdb', 'javbus')
    result = dict(code=path.stem, old_size=old_size, size=size, source=candidate.source,
                  source_url=candidate.url, diagnostics=diagnostics)
    if old_size and size[0] * size[1] <= old_size[0] * old_size[1] * 1.01 and not community:
        return {**result, 'status': 'kept'}
    backup = report / 'originals'
    backup.mkdir(parents=True, exist_ok=True)
    for suffix in ('.jpg', '.face.json', '.poster.json', '.scraping.json'):
        original = path.with_suffix(suffix)
        saved = backup / original.name
        if original.exists() and not saved.exists():
            shutil.copy2(original, saved)
    digest = hashlib.sha256(data).hexdigest()
    install_cover(path, path.stem, data, size, evidence=dict(source=candidate.source,
        source_url=candidate.url, title=artwork_title(known) or previous.get('title', ''), width=size[0], height=size[1],
        raw_sha256=digest, installed_sha256=digest, checked_at=time.time(),
        resolver='official-live-upgrade-v1' if live else 'official-snapshot-upgrade-v1', verified_by=[]))
    return {**result, 'status': 'installed'}


def failure_result(code, error, transport):
    """暂时故障重建连接池，来源明确缺失保留原有连接；逐番号记录可重试性。"""
    from peach.library_processing import describe_failure, is_missing
    retryable = not is_missing(error)
    if retryable:
        transport.renew()
    return dict(code=code, status='failed', error=describe_failure(error),
                error_type=type(error).__name__, retryable=retryable)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--code', action='append', default=[])
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--retry-failures', action='store_true')
    parser.add_argument('--live', action='store_true', help='通过现有官方链在线补查，无需已有快照')
    args = parser.parse_args()
    root = args.data_root / 'generated/covers'
    paths = [root / (code + '.jpg') for code in args.code] if args.code else sorted(root.glob('*.jpg'))
    args.report.mkdir(parents=True, exist_ok=True)
    transport = HostLimitedTransport(SourceTransport(args.data_root / 'secrets'), 1.0)
    results = []
    try:
        for index, path in enumerate(paths, 1):
            checkpoint = args.report / (path.stem + '.json')
            if checkpoint.exists() and not (args.retry_failures and read(checkpoint).get('status') == 'failed'):
                results.append(read(checkpoint))
                continue
            known = snapshots(args.data_root / 'sources/library-metadata', path.stem)
            size = measure_image_file(path)
            evidence = read(path.with_suffix('.scraping.json'))
            eligible = bool(args.code) or (size and (max(size) < 1600 or origin_of(
                str(evidence.get('source_url') or '')) in ('javdb', 'javbus')))
            if not eligible or (not args.live and not _official_evidence(known)):
                continue
            if not args.apply:
                print(json.dumps({'code': path.stem, 'size': size, 'eligible': True}), flush=True)
                continue
            try:
                result = upgrade(path, known, transport, args.report, live=args.live,
                                 metadata_root=args.data_root / 'sources/metadata/javinizer-go')
            except Exception as error:
                result = failure_result(path.stem, error, transport)
            results.append(result)
            atomic_write_text(checkpoint, json.dumps(result, ensure_ascii=False, indent=2))
            print(json.dumps({'index': index, **result}, ensure_ascii=False), flush=True)
    finally:
        transport.close()
    atomic_write_text(args.report / 'results.json', json.dumps(results, ensure_ascii=False, indent=2))
    print(json.dumps({'processed': len(results), 'installed': sum(r.get('status') == 'installed' for r in results),
                      'failed': sum(r.get('status') == 'failed' for r in results)}, ensure_ascii=False), flush=True)
    return 1 if any(r.get('status') == 'failed' for r in results) else 0


if __name__ == '__main__':
    raise SystemExit(main())
