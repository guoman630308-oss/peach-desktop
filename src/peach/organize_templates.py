"""整理用的路径模板：占位符取值、可选分组、文件名清洗与逃逸防护（ADR-0039）。

模板只有三种记号：字面量、`{占位符}`、`[可选分组]`。分组里的占位符只要有一个取不到值，
整段就不出现——`{number}[ {title}][ CD{cd}]` 在没有标题时给 `ABC-123`，而不是
`ABC-123  CD2` 这种带着两个空格的名字。

占位符一律从 approved 真相字段取值（见 `organize.placeholder_values`）。候选不参与：
按未复核的猜测改文件名等于把猜测写进文件系统，而那一层没有 `source` 和 `confidence`。

清洗与逃逸防护分两层，缺一层都会出事：

* **取值这一层**只可能带来非法字符（标题里的 `:` 与 `?`、演员名之间的 `/`），所以按
  Windows 的保留字符逐个替换，并挡住 `CON`、`PRN` 这类保留名与结尾的点和空格。
* **模板本身这一层**才可能带来路径逃逸：`..\\..\\Windows`、`C:\\`、`/etc`。取值那层的
  清洗挡不住它——那些字符是用户自己写在模板里的字面量。所以模板在保存和使用前都要过
  `validate_template`，而不是等渲染完再看结果像不像话。
"""
from __future__ import annotations

import ntpath
import re
from dataclasses import dataclass

#: 模板认识的占位符，以及它在界面上的说明。顺序就是界面上列出来的顺序。
PLACEHOLDERS: dict[str, str] = {
    "number": "番号",
    "title": "标题",
    "studio": "厂牌",
    "series": "系列",
    "year": "发行年",
    "actors": "出演者",
    "cd": "分片号",
    "mosaic": "有码无码",
    "definition": "画质",
}

#: 单个路径组件的长度上限。Windows 的组件上限是 255 个字符，但长名字在资源管理器、
#: 压缩包和网盘客户端里到处挨截断，所以留一大截余量；超出的从中间截断而不是末尾——
#: 末尾往往是分片号和画质这些用来区分的部分。
MAX_COMPONENT = 120
#: 整条目标路径的长度上限。Windows 的 `MAX_PATH` 是 260；长路径要注册表开关配合，
#: 而 115 与 PikPak 的挂载层不保证支持，所以按最保守的那一档拦。
MAX_PATH = 240

#: Windows 不收的字符。POSIX 只禁 `/` 和 NUL，但账本路径一律是 Windows 形态
#: （ADR-0017），所以按 Windows 这一套清洗，两个平台上结果一致。
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
#: 保留设备名。带扩展名也算：`CON.mp4` 在 Windows 上同样建不出来。
_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL",
                       *(f"COM{n}" for n in range(1, 10)),
                       *(f"LPT{n}" for n in range(1, 10))})


class TemplateError(ValueError):
    """模板本身不合法。消息直接给用户看，所以写成一句中文。"""


@dataclass(frozen=True)
class _Token:
    kind: str          # "text" | "field" | "group"
    text: str = ""
    parts: tuple = ()


def sanitise_component(value: str) -> str:
    """把一个取值收敛成能当路径组件的样子。

    替换成 `_` 而不是删掉：`ABC-123：完整版` 删掉冒号会变成 `ABC-123完整版`，看不出
    该处有过分隔；`_` 一眼能看出这个位置上有个不能进文件名的符号。
    """
    text = _ILLEGAL.sub("_", str(value or "")).strip()
    # 结尾的点和空格在 Windows 上会被静默吃掉，于是「建出来的名字」和「要求的名字」
    # 不一致，下一轮整理又会认为这一行还没做完。
    text = text.rstrip(". ")
    if not text:
        return ""
    stem = text.split(".", 1)[0].upper()
    if stem in _RESERVED:
        text = "_" + text
    if len(text) > MAX_COMPONENT:
        head = MAX_COMPONENT - 21
        text = text[:head] + "…" + text[-20:]
    return text


def _parse(template: str) -> tuple[_Token, ...]:
    """把模板切成记号。`{{`、`}}`、`[[`、`]]` 是对应符号的字面量写法。"""
    tokens: list[_Token] = []
    buffer: list[str] = []
    stack: list[list[_Token]] = []
    target = tokens

    def flush() -> None:
        if buffer:
            target.append(_Token("text", "".join(buffer)))
            buffer.clear()

    index = 0
    while index < len(template):
        char = template[index]
        pair = template[index:index + 2]
        if pair in {"{{", "}}", "[[", "]]"}:
            buffer.append(char)
            index += 2
            continue
        if char == "{":
            end = template.find("}", index)
            if end < 0:
                raise TemplateError("占位符的 `{` 没有配对的 `}`")
            name = template[index + 1:end].strip()
            if name not in PLACEHOLDERS:
                raise TemplateError(
                    f"不认识的占位符 `{{{name}}}`；可用的是 "
                    + "、".join(f"{{{key}}}" for key in PLACEHOLDERS))
            flush()
            target.append(_Token("field", name))
            index = end + 1
            continue
        if char == "[":
            flush()
            stack.append(target)
            target = []
            index += 1
            continue
        if char == "]":
            if not stack:
                raise TemplateError("可选分组的 `]` 没有配对的 `[`")
            flush()
            group = _Token("group", parts=tuple(target))
            target = stack.pop()
            target.append(group)
            index += 1
            continue
        buffer.append(char)
        index += 1
    if stack:
        raise TemplateError("可选分组的 `[` 没有配对的 `]`")
    flush()
    return tuple(tokens)


def _render_tokens(tokens: tuple[_Token, ...], values: dict[str, str]) -> tuple[str, bool]:
    """返回渲染结果，以及这一段里的占位符是不是都有值。"""
    out: list[str] = []
    filled = True
    for token in tokens:
        if token.kind == "text":
            out.append(token.text)
        elif token.kind == "field":
            value = sanitise_component(values.get(token.text, ""))
            if not value:
                filled = False
            out.append(value)
        else:
            text, group_filled = _render_tokens(token.parts, values)
            # 分组里有占位符没取到值就整段省略。方括号本身永远不出现在结果里；
            # 要一对字面的方括号写 `[[` 和 `]]`。一个占位符都没有的分组等于一段
            # 普通字面量，照常输出，而不是一段永远不出现的字。
            if group_filled or not _fields_in(token.parts):
                out.append(text)
    return "".join(out), filled


def _fields_in(tokens: tuple[_Token, ...]) -> bool:
    return any(token.kind == "field" or (token.kind == "group" and _fields_in(token.parts))
               for token in tokens)


def template_fields(template: str) -> frozenset[str]:
    """模板用到的占位符名字。界面据此说明「缺哪个字段这一行就整理不了」。"""
    def walk(tokens: tuple[_Token, ...]) -> set[str]:
        found: set[str] = set()
        for token in tokens:
            if token.kind == "field":
                found.add(token.text)
            elif token.kind == "group":
                found |= walk(token.parts)
        return found
    return frozenset(walk(_parse(template)))


def required_fields(template: str) -> frozenset[str]:
    """不在可选分组里的占位符：取不到值这一行就没法整理，只能跳过。"""
    def walk(tokens: tuple[_Token, ...]) -> set[str]:
        return {token.text for token in tokens if token.kind == "field"}
    return frozenset(walk(_parse(template)))


def validate_template(template: str, *, directory: bool = False) -> str:
    """校验并返回规范化的模板；不合法抛 `TemplateError`。

    目录模板可以带 `/` 或 `\\` 分层，文件名模板不行——文件名里的分隔符意味着模板在
    悄悄多建一层目录，而用户以为自己只是在给文件取名。
    """
    text = str(template or "").strip()
    if not text:
        return ""
    if len(text) > 300:
        raise TemplateError("模板太长")
    _parse(text)  # 记号配对与占位符名字先过一遍
    if ntpath.splitdrive(text)[0]:
        raise TemplateError("模板不能带盘符；它只描述来源根下面的相对位置")
    normalised = text.replace("\\", "/")
    if normalised.startswith("/"):
        raise TemplateError("模板不能以分隔符开头；它只描述来源根下面的相对位置")
    if not directory and "/" in normalised:
        raise TemplateError("文件名模板不能带目录分隔符；分层写在目录模板里")
    for component in normalised.split("/"):
        stripped = component.strip()
        if stripped in {".", ".."}:
            raise TemplateError("模板不能包含 `.` 或 `..`")
        if directory and not stripped and normalised.strip("/") != "":
            raise TemplateError("目录模板里有空的一层")
    return normalised.strip("/") if directory else normalised


def render(template: str, values: dict[str, str], *,
           directory: bool = False) -> tuple[str, frozenset[str]]:
    """渲染模板，返回 `(结果, 缺失的必填占位符)`。

    缺失的占位符一起返回而不是抛异常：调用方要把「这一行为什么没整理」写进计划 CSV，
    一次说清楚缺哪几个字段，比让用户改一次模板试一次强。
    """
    normalised = validate_template(template, directory=directory)
    if not normalised:
        return "", frozenset()
    missing = {name for name in required_fields(normalised)
               if not sanitise_component(values.get(name, ""))}
    text, _filled = _render_tokens(_parse(normalised), values)
    components = [sanitise_component(part) for part in text.split("/")] if directory \
        else [sanitise_component(text)]
    components = [part for part in components if part]
    return "/".join(components), frozenset(missing)
