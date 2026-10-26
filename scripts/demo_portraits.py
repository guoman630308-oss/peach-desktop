"""演示库的人像素材：按钉死的清单取图，再排成封面与头像。

图片字节不进仓库（ADR-0026），进仓库的只有 `demo-portraits.json` 这份清单——去哪取、
取到的该是什么哈希。生成器运行时按清单取一次，缓存在输出目录旁边。

**清单是人工逐张看过后钉死的，不按图库自己的顺序自动取。** 图库把目录前缀当来源优先级
排序，排第一的答的是「先试哪一张」，不是「哪一张能放进公开文档」：实测排在前面的大图
多是片商宣传素材，内衣、泳装乃至露点都在里面。自动取必然翻车，所以这里只认清单。
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.user_agent import USER_AGENT  # noqa: E402

MANIFEST_PATH = Path(__file__).resolve().parent / "demo-portraits.json"
GFRIENDS_RAW = "https://raw.githubusercontent.com/gfriends/gfriends/master/Content/"
#: 单张图的上限。清单里最大的一张 2880x1800 约 2.1 MB，留一倍余量；越界说明取错了东西。
MAX_IMAGE_BYTES = 4 * 1024 * 1024

COVER_LANDSCAPE = (800, 538)
COVER_PORTRAIT = (600, 900)
AVATAR_SIDE = 480

#: 封面与画面上的文字要落到 CJK 字形上。按平台找一份，找不到就不写字——
#: 缺字体的机器上宁可出一张没有标题的封面，也不要满屏豆腐块。
FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
)


@dataclass(frozen=True)
class Portrait:
    """清单里的一条：去哪取、取到的该是什么。"""

    name: str
    key: str
    category: str
    filename: str
    sha256: str
    width: int
    height: int

    @property
    def url(self) -> str:
        return (GFRIENDS_RAW + urllib.parse.quote(self.category)
                + "/" + urllib.parse.quote(self.filename))

    @property
    def cache_name(self) -> str:
        """缓存文件名带哈希前缀：清单换了图，旧缓存不会被当成新的。"""
        return f"{self.sha256[:16]}-{self.category}.jpg"

    @property
    def landscape(self) -> bool:
        return self.width > self.height


def load_manifest(path: Path | None = None) -> list[Portrait]:
    """读清单。顺序就是清单里的顺序，按种子取用由调用方决定。"""
    document = json.loads((path or MANIFEST_PATH).read_text(encoding="utf-8"))
    return [Portrait(**entry) for entry in document["entries"]]


def fetch(portrait: Portrait, cache_dir: Path, *, opener=urllib.request.urlopen) -> Path:
    """取一张图到缓存，校验哈希。缓存命中就不再联网。

    哈希不符是硬错误，不是可以将就的降级：清单的全部意义就是「取到的是我看过的那张」，
    校验放过去等于回到自动取图。
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / portrait.cache_name
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == portrait.sha256:
        return target
    request = urllib.request.Request(portrait.url, headers={"User-Agent": USER_AGENT})
    with opener(request, timeout=60) as response:
        body = response.read(MAX_IMAGE_BYTES + 1)
    if len(body) > MAX_IMAGE_BYTES:
        raise RuntimeError(f"{portrait.name} 的图超过 {MAX_IMAGE_BYTES} 字节，取错了东西")
    digest = hashlib.sha256(body).hexdigest()
    if digest != portrait.sha256:
        raise RuntimeError(
            f"{portrait.name} 的图与清单对不上：清单 {portrait.sha256[:16]}，取到 {digest[:16]}。"
            "图库换了这一张，重新人工看过再更新清单。")
    target.write_bytes(body)
    return target


def load_font(size: int):
    from PIL import ImageFont

    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return None


def _ellipsize(draw, text: str, font, room: int) -> str:
    """放不下就截断加省略号。长标题横着写出画框比截断难看得多。"""
    if draw.textlength(text, font=font) <= room:
        return text
    trimmed = text
    while trimmed and draw.textlength(trimmed + "…", font=font) > room:
        trimmed = trimmed[:-1]
    return (trimmed + "…") if trimmed else ""


def _cover_box(image, size: tuple[int, int], anchor: float = 0.15):
    """放大裁切填满画框，取景偏上以保住脸。"""
    from PIL import Image

    scale = max(size[0] / image.width, size[1] / image.height)
    big = image.resize((round(image.width * scale), round(image.height * scale)),
                       Image.LANCZOS)
    left = max(0, (big.width - size[0]) // 2)
    top = max(0, min(int((big.height - size[1]) * anchor), big.height - size[1]))
    return big.crop((left, top, left + size[0], top + size[1]))


def draw_cover(source: Path, destination: Path, *, code: str, title: str,
               performer: str, studio: str, year: str, orientation: str) -> None:
    """封面：人像占一侧，另一侧排番号、标题与出演者。

    横屏作品用横版封面，这也是番号作品封面的实际形状；竖屏作品用竖版，文字压在底部。
    人像不拉伸、不靠模糊背景撑满——那两种做法出来都是「头像撑成海报」的样子。
    """
    from PIL import Image, ImageDraw

    image = Image.open(source).convert("RGB")
    # 四行文字：番号、标题、出演者、年份与厂牌。字号与行距一起定，逐行累加落位，
    # 不按固定偏移量硬写——竖版的文字块比横版矮，写死偏移会溢出画面底边。
    rows = [row for row in ((code, 26, "#8a919c", 0), (title, 42, "#f3f5f8", 14),
                            (performer, 24, "#c5cad1", 18),
                            (f"{year} · {studio}", 21, "#8a919c", 26)) if row[0]]
    fonts = [load_font(size) for _, size, _, _ in rows]
    block = sum(size + lead for (_, size, _, lead) in rows)

    if orientation == "竖屏":
        canvas = _cover_box(image, COVER_PORTRAIT).convert("RGB")
        width, height = COVER_PORTRAIT
        # 上面一段做渐变过渡，文字全部落在下面的实心段——纯渐变带的顶部太淡，
        # 第一行番号压在画面亮部时基本看不见。
        fade, solid = 72, block + 56
        overlay = Image.new("RGBA", (width, fade + solid), (10, 11, 14, 220))
        painter = ImageDraw.Draw(overlay)
        for row in range(fade):
            painter.line([(0, row), (width, row)],
                         fill=(10, 11, 14, int(220 * (row / fade) ** 1.2)))
        canvas.paste(overlay, (0, height - fade - solid), overlay)
        left, cursor = 34, height - solid + 24
    else:
        canvas = Image.new("RGB", COVER_LANDSCAPE, "#14161a")
        panel = round(COVER_LANDSCAPE[0] * 0.44)
        canvas.paste(_cover_box(image, (panel, COVER_LANDSCAPE[1]), anchor=0.12), (0, 0))
        left = panel + 44
        cursor = (COVER_LANDSCAPE[1] - block) // 2

    draw = ImageDraw.Draw(canvas, "RGBA")
    if fonts[0] is None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(destination,
                    format="PNG" if destination.suffix.lower() == ".png" else "JPEG",
                    quality=90)
        return
    room = canvas.width - left - 30
    for (text, size, colour, lead), font in zip(rows, fonts):
        cursor += lead
        draw.text((left, cursor), _ellipsize(draw, text, font, room), font=font, fill=colour)
        cursor += size
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, format="PNG" if destination.suffix.lower() == ".png" else "JPEG",
                quality=90)


def draw_avatar(source: Path, destination: Path) -> None:
    """头像：按短边取正方形，偏上取景。"""
    from PIL import Image

    image = Image.open(source).convert("RGB")
    side = min(image.width, image.height)
    left = (image.width - side) // 2
    top = int((image.height - side) * 0.12)
    square = image.crop((left, top, left + side, top + side))
    destination.parent.mkdir(parents=True, exist_ok=True)
    square.resize((AVATAR_SIDE, AVATAR_SIDE), Image.LANCZOS).save(destination, quality=90)
