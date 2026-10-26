"""随仓库分发的法人实体标识。

ADR-0026 从 ADR-0024 的「字节不进 Git」里划出一条窄例外：厂牌和站点的标识类小图标
收进 `resources/marks/`，跟着发行版走。新机器第一次打开厂牌页不再为 198 枚小图标各发
一个请求，断网时那一排也不是灰底首字母。

收进来的不是缓存目录的副本，是它的可审查形态：

* 文件名带真实扩展名（`Attackers.png`），不是本机缓存的 `.img`。GitHub 上能直接预览，
  「仓库里装了什么」这件事用眼睛就能核，这是收录的意义之一。
* provenance 集中在一份 `manifest.json`，不是 198 个边车。边车会让仓库文件数翻三倍，
  而这些字段本来就该一屏扫完。

**本机缓存优先于内置。** 用户自己重探、复核批准装下的那一份是更新的，内置只是兜底。
顺序颠倒过来的话，任何一次复核批准都会被发行版里的旧图盖掉。
"""
from __future__ import annotations

import json
from pathlib import Path

from .settings_file import PROJECT_ROOT

#: `resources/marks/`。自己按 `__file__` 上溯是错的：wheel 把资源装进 `peach/_resources/`、
#: PyInstaller 解到 `_MEIPASS`，两种打包下相对层数都不是源码树那个数，而算错的表现是
#: 内置资源静默失踪——页面退回首字母，没有任何报错。
MARKS_ROOT = PROJECT_ROOT / "resources" / "marks"
MANIFEST = MARKS_ROOT / "manifest.json"
STUDIOS_DIR = MARKS_ROOT / "studios"

#: 扩展名 → MIME。落盘时扩展名由字节嗅探（`follow_assets.sniff`）决定，所以这里反查
#: 得到的类型和字节一致，不是靠文件名猜的。
CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".svg": "image/svg+xml",
}

#: ADR-0026 的收录判据 4 与 5。越过上限说明混进了不该收的东西，不是把上限调高。
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024


def content_type(path: Path) -> str:
    """内置文件的 MIME。认不出的扩展名不该存在于 `resources/marks/`，由测试拦住。"""
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


def find(stem: str, root: Path | None = None) -> Path | None:
    """内置目录里主名为 `stem` 的标识；没有就是 None。

    `stem` 用的是本机缓存那套落盘名去掉 `.img` 之后的部分（`Attackers`、
    `Attackers.icon`），扩展名由内置文件自己带。两边命名规则必须同源，否则
    「内置装了却找不到」不会报错，只会安静地退回网络抓取。

    大小写不敏感：`logo_key` 保留假名和汉字，同一个厂牌在不同来源里的大小写并不稳定，
    而 `_logo_file` 本来就按大小写不敏感找。
    """
    base = STUDIOS_DIR if root is None else root
    if not stem or not base.is_dir():
        return None
    wanted = stem.lower()
    for path in sorted(base.iterdir()):
        if path.is_file() and path.stem.lower() == wanted:
            return path
    return None


def installed_stems(variants: tuple[str, ...], root: Path | None = None) -> frozenset[str]:
    """内置标识覆盖到的厂牌落盘名，casefold 后剥掉变体后缀。

    `web_state.logo_index()` 拿它跟本机目录的扫描结果合并。少了这一步，内置资源取得回
    图、页面却判「这个厂牌没图」，于是永远只显示首字母——装了等于没装，而且不报错。

    `variants` 由调用方传入而不是从 `previews` 导入：`previews` 已经导入本模块，反向
    再导一次就成环。
    """
    base = STUDIOS_DIR if root is None else root
    if not base.is_dir():
        return frozenset()
    stems: set[str] = set()
    for path in base.iterdir():
        if not path.is_file() or path.suffix.lower() not in CONTENT_TYPES:
            continue
        stem = path.stem.casefold()
        for variant in variants:
            if stem.endswith(f".{variant}"):
                stem = stem[:-len(variant) - 1]
                break
        if stem:
            stems.add(stem)
    return frozenset(stems)


def manifest() -> dict:
    """清单内容；没有清单时返回空壳，让没装资源的检出照常跑。"""
    if not MANIFEST.is_file():
        return {"version": 1, "marks": []}
    return json.loads(MANIFEST.read_text(encoding="utf-8"))
