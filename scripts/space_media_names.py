"""恢复英文媒体文件名的单词间隔，保留原字符、序号和扩展名。

只插入空格，不改任何字符。以下几类不拆：撇号缩写（`Couldn't`、`Year's`）、全大写缩写
（`POV`、`CIM`）与紧挨数字的全大写编号（`SPANKPH6`、`ASMR4K`）、网站域名及紧跟的编号
（`xvideos.com_abcdefgh`）、Pornhub viewkey 与十六进制编号（`ph5f8a9bcdef12345`）。
混入西里尔形近字母的词（`withсum`）按拉丁字母算切点，原字符照旧保留。

`--recompute-batch` 按修正后的分词重算一个已执行批次：对日志里每条的原名重新分词，
与现名不同的行写成复核 CSV，交给 `--apply --review-csv` 执行。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from contextlib import closing
from pathlib import Path, PureWindowsPath

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / 'src'))

from peach import organize
from peach.config import GENERATED_DIR
from peach.platform import location_roots, resolve_root, root_online, translate_ledger_path
from peach.review_csv import read_rows, write_rows
from peach.scripting import add_ledger_write_args, open_for_write, open_readonly
from wordninja_enhanced import LanguageModel

NAMING = PROJECT / 'resources' / 'naming'
WORDS = NAMING / 'english_filename_words.txt'
NONWORDS = NAMING / 'english_filename_nonwords.txt'
FIELDS = (*organize.PLAN_FIELDS, 'size', 'original_name', 'new_name')
VIDEO_SUFFIXES = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.webm'}

#: 西里尔形近字母与右单引号，一一映射到拉丁字母与直撇号；只用来算切点，长度不变。
LOOKALIKES = 'аеорсухіјѕАВЕКМНОРСТХІЈЅ'
SHADOW = str.maketrans(LOOKALIKES + '’', 'aeopcyxijsABEKMHOPCTXIJS' + "'")
LETTER = 'A-Za-z' + LOOKALIKES
CHUNK = rf"[{LETTER}]+(?:['’][{LETTER}]+)*"
TLD = 'com|net|org|tv|xxx|xyz|cc|io|top|vip|club|info|live|site|porn|sex|biz'
DOMAIN = rf"(?:[A-Za-z0-9-]+\.)+(?:{TLD})(?![A-Za-z0-9])(?:[_-][A-Za-z0-9]+)?"
HEX_ID = r"(?<![A-Za-z0-9])(?:ph)?(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{8,}(?![A-Za-z0-9])"
VIEWKEY = r"viewkey=?[A-Za-z0-9]+"
TOKEN = re.compile(rf"(?P<keep>{DOMAIN}|{VIEWKEY}|{HEX_ID})|(?P<word>{CHUNK})")


def _entries(path):
    return [line.strip() for line in path.read_text(encoding='utf-8').splitlines()
            if line.strip() and not line.startswith('#')]


def model():
    return LanguageModel(add_words=_entries(WORDS),
                         blacklist=[word.lower() for word in _entries(NONWORDS)],
                         add_to_top=True, overwrite=True)


def _cuts(shadow, language):
    """词段里该插空格的位置：小写接大写处必切，其余由词频模型定；撇号两侧与 n't 之前不切。"""
    cuts, start = set(), 0
    forced = [i for i in range(1, len(shadow)) if shadow[i - 1].islower() and shadow[i].isupper()]
    for end in [*forced, len(shadow)]:
        offset = start
        for character in language.rejoin(shadow[start:end]):
            if character == ' ':
                cuts.add(offset)
            else:
                offset += 1
        cuts.add(end)
        start = end
    return sorted(cut for cut in cuts if 0 < cut < len(shadow)
                  and "'" not in (shadow[cut - 1], shadow[cut])
                  and shadow[cut:cut + 3].lower() != "n't")


def _spaced_chunk(text, before, after, language):
    shadow = text.translate(SHADOW)
    if not re.search('[A-Za-z]', text):
        return text
    letters = shadow.replace("'", '')
    if letters.isupper() and (len(letters) <= 4 or before.isdigit() or after.isdigit()):
        return text
    parts, last = [], 0
    for cut in _cuts(shadow, language):
        parts.append(text[last:cut])
        last = cut
    parts.append(text[last:])
    return ' '.join(parts)


def spaced_name(name, language):
    """仅插入空格，词典不确定性不会改写内容字符。"""
    path = PureWindowsPath(name)
    if path.suffix.casefold() not in VIDEO_SUFFIXES:
        return name
    original = path.stem

    def words(match):
        if match.group('keep'):
            return match.group()
        before = original[match.start() - 1] if match.start() else ''
        after = original[match.end()] if match.end() < len(original) else ''
        return _spaced_chunk(match.group(), before, after, language)
    stem = TOKEN.sub(words, original)
    stem = re.sub(r'([,;!])(?=[A-Za-z])', r'\1 ', stem)
    if re.sub(r'\s', '', stem) != re.sub(r'\s', '', original):
        raise ValueError('英文分词改变了内容字符')
    return stem + path.suffix


def plan(connection, root):
    canonical_root = str(PureWindowsPath(root))
    owner, index, _ = resolve_root(canonical_root + '\\_', location_roots())
    if not owner or index < 0 or not root_online(translate_ledger_path(canonical_root)):
        raise ValueError('目录不在在线馆藏来源内')
    if organize.unverified_location(owner):
        raise ValueError(f'来源 {owner} 的挂载改名无法核验云端结果，不出分词计划')
    language = model()
    result, claimed = [], set()
    for row in connection.execute('SELECT id,path,name,size FROM asset WHERE location=? AND disposal IS NULL AND medium=\'video\' ORDER BY id', (owner,)):
        if str(PureWindowsPath(row['path']).parent).casefold() != canonical_root.casefold():
            continue
        new = spaced_name(row['name'], language)
        if new == row['name']:
            continue
        target = str(PureWindowsPath(row['path']).with_name(new))
        if len(target) > organize.MAX_PATH or target.casefold() in claimed:
            raise ValueError('分词目标重名或路径过长')
        claimed.add(target.casefold())
        result.append(dict(asset_id=row['id'], location=owner, current_path=row['path'], target_path=target,
                           action='rename', reason='', size=row['size'], original_name=row['name'], new_name=new))
    return result


def recompute_batch(connection, root, log_path):
    """按修正后的分词重算已执行批次：原名重新分词，与现名不同的行出改名计划，只读。

    批次之后文件可能已被目录整理搬走，所以按 asset_id 找账本里的当前行，只要求文件名
    仍是批次写下的那个名字、所在目录是 `root`。名字已被改过、行已不在库或不在 `root`
    的条目不出计划，放进第二个返回值。
    """
    canonical_root = str(PureWindowsPath(root))
    entries = json.loads(Path(log_path).read_text(encoding='utf-8')).get('entries') or []
    language = model()
    result, skipped, claimed = [], [], set()
    for entry in entries:
        original, current = PureWindowsPath(entry['old_path']), PureWindowsPath(entry['new_path'])
        new = spaced_name(original.name, language)
        if new == current.name:
            continue
        row = connection.execute('SELECT location,path,size FROM asset WHERE id=? AND disposal IS NULL',
                                 (int(entry['asset_id']),)).fetchone()
        held = PureWindowsPath(row['path']) if row else None
        reason = ('已不在库' if held is None
                  else '文件名已不是批次写下的名字' if held.name != current.name
                  else '不在 --root 指定的目录' if str(held.parent).casefold() != canonical_root.casefold()
                  else '')
        if reason:
            skipped.append(dict(asset_id=int(entry['asset_id']), path=str(held or current), reason=reason))
            continue
        target = str(held.with_name(new))
        if len(target) > organize.MAX_PATH or target.casefold() in claimed:
            raise ValueError('分词目标重名或路径过长')
        claimed.add(target.casefold())
        result.append(dict(asset_id=int(entry['asset_id']), location=row['location'],
                           current_path=row['path'], target_path=target, action='rename',
                           reason='按修正后的分词重算', size=row['size'],
                           original_name=held.name, new_name=new))
    return result, skipped


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_ledger_write_args(parser)
    parser.add_argument('--root', required=True)
    parser.add_argument('--review-csv', type=Path)
    parser.add_argument('--recompute-batch', type=Path,
                        help='已执行的分词批次日志；只出重算计划，不能与 --apply 同用')
    parser.add_argument('--out', type=Path, default=GENERATED_DIR / 'english-name-spacing.csv')
    args = parser.parse_args(argv)
    if args.recompute_batch:
        if args.apply:
            parser.error('--recompute-batch 只出计划；执行用 --apply --review-csv')
        with closing(open_readonly(args.db)) as connection:
            rows, skipped = recompute_batch(connection, args.root, args.recompute_batch)
        write_rows(args.out, FIELDS, rows, atomic=True)
        print(json.dumps(dict(planned=len(rows), skipped=skipped, review=str(args.out)),
                         ensure_ascii=False))
        return 0
    if not args.apply:
        with closing(open_readonly(args.db)) as connection:
            rows = plan(connection, args.root)
        write_rows(args.out, FIELDS, rows, atomic=True)
        print(json.dumps(dict(planned=len(rows), review=str(args.out)), ensure_ascii=False))
        return 0
    if not args.review_csv:
        parser.error('--apply 必须给 --review-csv，消费已冻结的原名与目标名')
    rows = read_rows(args.review_csv)
    seen = set()
    with closing(open_readonly(args.db)) as connection:
        for row in rows:
            asset_id = int(row['asset_id'])
            held = connection.execute('SELECT path,size FROM asset WHERE id=? AND disposal IS NULL', (asset_id,)).fetchone()
            source = PureWindowsPath(row['current_path'])
            target = PureWindowsPath(row['target_path'])
            location, index, _ = resolve_root(str(source), location_roots())
            if location != row['location'] or index < 0 or not root_online(translate_ledger_path(str(source.parent))):
                raise ValueError('英文文件名不在在线馆藏来源内')
            if organize.unverified_location(location):
                raise ValueError(f'来源 {location} 的挂载改名无法核验云端结果，整批未执行')
            if asset_id in seen or held is None or tuple(held) != (str(source), int(row['size'])):
                raise ValueError('英文文件名复核已失效')
            if str(source.parent).casefold() != str(PureWindowsPath(args.root)).casefold() or source.parent != target.parent:
                raise ValueError('英文分词只能在指定目录内改名')
            if row['action'] != 'rename' or re.sub(r'\s', '', source.name) != re.sub(r'\s', '', target.name):
                raise ValueError('英文文件名复核改变了内容字符')
            native = translate_ledger_path(str(source))
            if native.is_symlink() or not native.is_file() or native.stat().st_size != int(row['size']):
                raise ValueError('英文文件体积或类型变化')
            seen.add(asset_id)
    connection = open_for_write(args)
    try:
        baseline = {tuple(r) for r in connection.execute('PRAGMA foreign_key_check')}
        count = connection.execute('SELECT count(*) FROM asset').fetchone()[0]
        result = organize.apply_plan(connection, rows, generated_root=GENERATED_DIR)
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or {tuple(r) for r in connection.execute('PRAGMA foreign_key_check')} - baseline or connection.execute('SELECT count(*) FROM asset').fetchone()[0] != count:
            raise RuntimeError('英文文件名核验失败')
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result['failed'] else 0
    finally:
        connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
