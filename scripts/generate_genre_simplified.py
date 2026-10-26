"""为已登记中文来源词生成有限简体别名；运行时不做语言转换。"""
from pathlib import Path
import argparse
import json
import re
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / 'src'
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.genre_taxonomy import CONTENT_GENRES, NON_CONTENT_GENRES, normalise_genre


def aliases():
    from opencc import OpenCC

    converter = OpenCC('t2s')
    content = {}
    excluded = set()
    for raw, label in CONTENT_GENRES.items():
        if re.search(r'[ぁ-ヿ]', raw):
            continue
        simple = converter.convert(raw)
        if simple == raw:
            continue
        key = normalise_genre(simple)
        if key in content and content[key] != label:
            raise ValueError(f'简体别名冲突：{simple}')
        content[key] = label
    for raw in NON_CONTENT_GENRES:
        if not re.search(r'[ぁ-ヿ]', raw):
            simple = converter.convert(raw)
            if simple != raw:
                excluded.add(normalise_genre(simple))
    collisions = excluded & content.keys()
    if collisions:
        raise ValueError(f'内容与非内容别名冲突：{sorted(collisions)}')
    return dict(sorted(content.items())), sorted(excluded)


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    content, excluded = aliases()
    target = PROJECT_ROOT / 'src/peach/genre_taxonomy.py'
    block = ('# BEGIN GENERATED_SIMPLIFIED_GENRES\nCONTENT_ALIASES = '
             + json.dumps(content, ensure_ascii=False, indent=4)
             + '\nEXCLUDED_ALIASES = frozenset(' + json.dumps(excluded, ensure_ascii=False, indent=4)
             + ')\n# END GENERATED_SIMPLIFIED_GENRES')
    source, count = re.subn(r'# BEGIN GENERATED_SIMPLIFIED_GENRES.*?# END GENERATED_SIMPLIFIED_GENRES',
                           lambda _match: block, target.read_text(encoding='utf-8'), flags=re.S)
    if count != 1:
        raise ValueError('词表需要唯一的简体别名区块')
    target.write_text(source, encoding='utf-8', newline='\n')
    print(f'内容别名 {len(content)} 项，非内容别名 {len(excluded)} 项')


if __name__ == '__main__':
    main()
