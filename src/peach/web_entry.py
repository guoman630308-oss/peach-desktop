"""入口页共用件：首启、登录与错误三页的薄壳，首启与配置页共用的运行信息。

三页都由独立页面包 `/dist/peach-pages.js` 画（ADR-0094），服务端只吐同一张薄壳，要画的变量写在
挂载点的 `data-*` 上。运行信息则被 `/api/setup` 和 `/api/configuration` 共用。三个路由模块都要，
就不能住在其中任何一个里：那会让路由层互相导入成环。放在 web 层，路由模块单向依赖它，
`tests/test_module_layering.py` 守着这个方向。
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from html import escape

#: 三页共用的文档头。`robots` 那一行管只读 HTML 的爬虫，响应头 `X-Robots-Tag` 管所有响应：
#: 公网入口在跑的时候登录页就在互联网上，不希望它进任何搜索结果。图标声明和主站同一份，
#: 没有会话时登录页就是书签实际停在的地方。
_PAGE_HEAD = ('<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
              '<meta name="viewport" content="width=device-width,initial-scale=1">'
              '<meta name="color-scheme" content="light dark">'
              '<meta name="robots" content="noindex, nofollow">'
              '<link rel="icon" href="/favicon.ico" type="image/x-icon">')

#: 手动选的深浅压过系统偏好，必须在第一次绘制前定下来，所以排在任何样式之前：`dark` 类给
#: BoardUI 的色板，`data-theme` 记手动那一档，判据同 `web/index.html` 的预读脚本。
_THEME_SCRIPT = ('<script>(()=>{try{'
                 'const c=JSON.parse(localStorage.getItem("peach.settings.v1")||"{}").theme;'
                 'document.documentElement.classList.toggle("dark",c==="dark"||(c!=="light"&&matchMedia("(prefers-color-scheme: dark)").matches));'
                 'if(c==="light"||c==="dark")document.documentElement.dataset.theme=c;'
                 '}catch(e){}})();</script>')

_DATA_KEY = re.compile(r"[a-z][a-z0-9-]*")


def page_shell(title: str, page: str, data: Mapping[str, str] | None = None, *, symbols: str = "") -> str:
    """入口页的薄壳：主题预读、Inter、页面包的样式与脚本，以及挂载点 `#peach-page`。

    `page` 写进 `data-page`，页面包按它挑哪一页；`data` 的每一项写成挂载点上的 `data-<键>`，
    值一律经 HTML 转义。骨架文案归页面包，这里只放服务端才知道的变量。`symbols` 是页面要借的
    雪碧图字形，原样放进一张不占位的 `<svg>`。
    """
    attributes = ""
    for key, value in (data or {}).items():
        if not _DATA_KEY.fullmatch(key):
            raise ValueError(f"data-* 键名不合法：{key!r}")
        attributes += f' data-{key}="{escape(value, quote=True)}"'
    sprite = f'<svg width="0" height="0" aria-hidden="true" style="position:absolute">{symbols}</svg>' if symbols else ""
    return (f'{_PAGE_HEAD}<title>{escape(title)}</title>{_THEME_SCRIPT}'
            '<link rel="stylesheet" href="/vendor/inter/5.3.0/index.css">'
            '<link rel="stylesheet" href="/dist/peach-pages.css">'
            '<script type="module" src="/dist/peach-pages.js"></script></head>'
            f'<body class="peach-react">{sprite}'
            f'<div id="peach-page" data-page="{escape(page, quote=True)}"{attributes}></div></body></html>\n')


def runtime_facts(config) -> tuple[tuple[str, str], ...]:
    """这台机器上 Peach 的位置与版本：设置完成页和 `/api/configuration` 共用同一份。"""
    from . import __version__
    import platform as system_platform
    from .ffmpeg import FFmpegResolver
    from .mp4recover import untrunc_path

    available = FFmpegResolver(config.directory("tools") / "ffmpeg").ffmpeg() is not None
    ffmpeg = "可用" if available else "未安装；MP4 可直接播放，转码和缩略图需要安装 FFmpeg。"
    untrunc = "可用" if untrunc_path(config.directory("tools")) else "未安装；缺索引的 MP4 需要它才修得了。"
    return (
        ("版本", __version__),
        ("操作系统", system_platform.system()),
        ("数据目录", str(config.data_root)),
        ("设置文件", str(config.path)),
        ("日志目录", str(config.directory("logs"))),
        ("FFmpeg", ffmpeg),
        ("untrunc", untrunc),
    )


def runtime_fact_entries(config) -> list[dict[str, str]]:
    """运行信息中的缺失依赖附带官方下载入口；依赖可用时不占一行。"""
    from .ffmpeg import FFmpegResolver
    entries = [{"term": term, "value": value} for term, value in runtime_facts(config)]
    resolver = FFmpegResolver(config.directory("tools") / "ffmpeg")
    missing = [name for name, choice in (("FFmpeg", resolver.ffmpeg()), ("ffprobe", resolver.ffprobe()))
               if choice is None]
    if missing:
        entry = next(row for row in entries if row["term"] == "FFmpeg")
        entry.update(value="未找到 " + "、".join(missing) + "；转码、媒体信息与缩略图需要 FFmpeg 工具包。",
                     download_url="https://ffmpeg.org/download.html", download_label="下载 FFmpeg")
    untrunc = next(row for row in entries if row["term"] == "untrunc")
    if untrunc["value"] != "可用":
        untrunc.update(value=untrunc["value"] + f"解压到 {config.directory('tools') / 'untrunc'}。",
                       download_url="https://github.com/anthwlock/untrunc/releases",
                       download_label="下载 untrunc")
    return [row for row in entries if not (row["term"] in ("FFmpeg", "untrunc") and row["value"] == "可用")]
