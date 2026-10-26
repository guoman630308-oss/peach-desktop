"""逐张核对封面尺寸、来源、版次、人脸与正封框，保存可重放的核查清单。"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PIL import Image
from peach import jav_poster_crop as crop
from peach.cover_artwork import FACE_ALGORITHM_VERSION, metadata_snapshots, face_record as detect_face
from peach.community_catalog import origin_of
from peach.fsutil import atomic_write_text
from peach.library_processing import _official_evidence


def read(path):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def snapshots(root, code):
    return metadata_snapshots(root, code)


def current_face(path, size, title, detector):
    """按算法版本与源图尺寸核对缓存，需要时从整图重新检测。"""
    record = read(path.with_suffix('.face.json'))
    if detector is not None and (record.get('version') != FACE_ALGORITHM_VERSION or record.get('px') != list(size)):
        from peach.face_detect import decode
        return detect_face(decode(path.read_bytes()), detector, code=path.stem, title=title) or {}
    return record


def inspect(path, metadata, *, detector=None):
    from peach.library_processing import artwork_title
    with Image.open(path) as image:
        width, height = image.size
        image.verify()
    saved = crop.read_sidecar(path) or {}
    evidence = read(path.with_suffix('.scraping.json'))
    known = snapshots(metadata, path.stem)
    previous_title = str(saved.get('title') or evidence.get('title') or '')
    title = artwork_title([*known, ('installed', {'title': previous_title, 'genres': [previous_title]})])
    if crop.is_vr_title(previous_title):
        title = previous_title
    face_record = current_face(path, (width, height), title, detector)
    face = face_record.get('face') if face_record.get('px') == [width, height] else None
    full = crop.face_crosses_spine(width, height, face)
    layout = 'still' if crop.is_scene_cover(path.stem, title) or face_record.get('layout') == 'still' else ''
    proposed = saved if crop.is_manual(saved) and crop.is_current(saved, width, height) else crop.crop_record(
        path.stem, width, height, crop.file_gradient(path), title=title, face=face, layout=layout)
    if full and proposed['box']['method'] == crop.NONE:
        layout = 'still'
    if layout == 'still' and not crop.is_manual(proposed):
        proposed['box'] = {'x0': 0, 'y0': 0, 'x1': width, 'y1': height, 'method': crop.NONE}
    origin = origin_of(str(evidence.get('source_url') or ''))
    reasons = []
    if origin in ('javdb', 'javbus'):
        reasons.append('community_source')
    if not evidence.get('source_url'):
        reasons.append('missing_provenance')
    if max(width, height) < 1600:
        reasons.append('low_resolution')
    if not crop.is_current(saved, width, height):
        reasons.append('stale_frame')
    if face_record.get('version') != FACE_ALGORITHM_VERSION:
        reasons.append('stale_face')
    if saved.get('box') != proposed.get('box'):
        reasons.append('crop_changed')
    if proposed['box']['method'] == crop.RATIO:
        reasons.append('ratio_fallback')
    if layout != str(face_record.get('layout') or '') and not crop.is_manual(saved):
        reasons.append('layout_changed')
    official = _official_evidence(known)
    row = dict(code=path.stem, width=width, height=height, origin=origin,
               method=proposed['box']['method'], old_x0=(saved.get('box') or {}).get('x0'),
               x0=proposed['box']['x0'], layout=layout, official_candidates=len(official.candidates) if official else 0,
               reasons=';'.join(reasons))
    return row, proposed, face_record, layout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='备份并更新可重建的取景边车')
    parser.add_argument('--refresh-faces', action='store_true', help='按当前人脸算法重算过期缓存')
    args = parser.parse_args()
    args.report.mkdir(parents=True, exist_ok=True)
    rows, errors = [], []
    covers = sorted((args.data_root / 'generated/covers').glob('*.jpg'))
    detector = None
    if args.refresh_faces:
        from peach.face_detect import FaceDetector
        detector = FaceDetector(allow_download=False)
    for index, path in enumerate(covers, 1):
        try:
            row, poster, face, layout = inspect(path, args.data_root / 'sources/library-metadata', detector=detector)
            rows.append(row)
            if args.apply:
                changes = {path.with_suffix('.poster.json'): poster}
                if face and not crop.is_manual(poster):
                    face = {**face}
                    face.pop('layout', None)
                    if layout:
                        face['layout'] = layout
                    changes[path.with_suffix('.face.json')] = face
                for target, data in changes.items():
                    if read(target) == data:
                        continue
                    backup = args.report / 'sidecar-backup' / target.name
                    if target.exists() and not backup.exists():
                        backup.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(target, backup)
                    atomic_write_text(target, json.dumps(data, ensure_ascii=False))
        except (OSError, ValueError, KeyError) as error:
            errors.append({'code': path.stem, 'error': str(error)})
        if index % 100 == 0:
            print(f'{index}/{len(covers)}', flush=True)
    if rows:
        with (args.report / 'covers.csv').open('w', newline='', encoding='utf-8-sig') as output:
            writer = csv.DictWriter(output, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    summary = dict(total=len(covers), decoded=len(rows), errors=errors,
                   origins=dict(Counter(row['origin'] for row in rows)),
                   reasons=dict(Counter(reason for row in rows for reason in row['reasons'].split(';') if reason)),
                   applied=args.apply)
    atomic_write_text(args.report / 'summary.json', json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
