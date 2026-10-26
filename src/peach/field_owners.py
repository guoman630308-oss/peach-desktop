"""`asset` 真相字段的字段级归属与乐观并发。

账本里一个真相字段现在的值是谁写的，只有这一列答得出。ADR-0005 与 ADR-0018 都要求
事后必须答得出「这个值是谁写的、凭什么」，而留痕一直只落在 `review_decision` 上：
那张表按 `<番号>:<字段>` 记决定，对得上算幸运，对不上就没有第二个入口。多值字段那边
的教训摆在眼前：`asset_tag` 有 `source` 列却没有写入时间也没有删除留痕，一行标签少了，
没有任何一个查询指得出是谁写的、谁删的。

归属串的形状是 `<写入者类别>:<标识>`，类别决定谁能覆盖谁：

- `user:manual`      用户在界面上直接编辑的取值；
- `review:<来源>`    用户在 `/review` 批准了某个来源的候选；
- `auto:<来源>`      免复核自动落库（ADR-0018／0025）；
- `scan:filename`    扫描与本地资料从文件名推导；
- `script:<脚本名>`  维护脚本批量写入。

覆盖规则只有一条：`user:*` 与 `review:*` 承载用户的判断，自动写入者一概不许改；
无主字段与 `auto:*`、`scan:*`、`script:*` 写的字段则谁都可以写。这条规则由
`write_owned_fields` 生成的 SQL 强制，不靠调用方自觉。

写入是一条 UPDATE，逐字段 `CASE WHEN` 决定写不写。按字段判而不是按行判是要点：
一次写入里某个字段被用户接管，不该连累同一批里其它无主字段也写不进去。
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

#: 受归属保护的 `asset` 列，也就是 AGENTS.md 术语表说的真相字段。
#: 逻辑名就是列名，别处不得另立一套映射——两套名字迟早对不上，而对不上的表现是
#: 归属写在一个谁也不会去读的键上。
OWNED_FIELDS: tuple[str, ...] = (
    "catalog_title", "original_title", "release_date", "studio", "series",
    "creator", "code", "region",
)

#: 用户在界面上直接编辑。这是最高一级，任何自动写入者都覆盖不掉它。
USER_MANUAL = "user:manual"

#: 扫描与本地资料从文件名推导出的取值。
SCAN_FILENAME = "scan:filename"

#: 归属串里标识那一段的合法形状。来源名与脚本名都来自代码里的封闭清单，
#: 但它们会被拼进 JSON 写库，所以在入口处就把形状钉死。
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9_-]*")

#: 用户的判断，自动写入者不得覆盖。
PROTECTED_PREFIXES: tuple[str, ...] = ("user:", "review:")

#: 乐观并发：写入端点用这个请求体字段带上「我读到的 `mutation_revision`」。
#: 用请求体而不是 `If-Match`，因为稳定 JSON 契约的派发只把 body 交给处理器
#: （`web_router.dispatch_api_post`），请求头到不了那一层。
EXPECTED_REVISION_FIELD = "expected_revision"


def review_owner(provider: str) -> str:
    """用户在 `/review` 批准了 `provider` 这个来源的候选。"""
    return "review:" + _identifier(provider)


def auto_owner(provider: str) -> str:
    """ADR-0018／0025 的免复核落库，取值来自 `provider`。"""
    return "auto:" + _identifier(provider)


def script_owner(name: str) -> str:
    """维护脚本批量写入；`name` 是脚本名，不带 `.py`。"""
    return "script:" + _identifier(name)


def _identifier(value: str) -> str:
    text = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(text):
        raise ValueError(f"归属标识只能是小写字母、数字、下划线和连字符：{value!r}")
    return text


def is_protected(owner: str | None) -> bool:
    """这个归属是用户的判断吗；无主和自动写入者都不是。"""
    return str(owner or "").startswith(PROTECTED_PREFIXES)


def parse_owners(value: object) -> dict[str, str]:
    """把 `asset.field_owners` 这一列解析成 `{列名: 归属串}`。

    解析不了就当无主。这一列是留痕，读它的地方全是展示与判断谁能写，
    为一行坏 JSON 让详情页整页 500 不划算。
    """
    if isinstance(value, Mapping):
        parsed: object = value
    else:
        try:
            parsed = json.loads(str(value or "{}"))
        except (TypeError, ValueError):
            return {}
    if not isinstance(parsed, Mapping):
        return {}
    return {str(key): str(owner) for key, owner in parsed.items()
            if key in OWNED_FIELDS and isinstance(owner, str) and owner}


def owner_of(value: object, field: str | None) -> str:
    """某一个字段现在的归属；无主返回空串。"""
    if not field:
        return ""
    return parse_owners(value).get(field, "")


def owner_label(owner: str | None) -> str:
    """归属串给人看的写法；无主返回空串。

    页面上要回答的是「这个值我还要不要管」，所以标签按写入者的身份说话，
    不重复一遍归属串的机器写法。
    """
    kind, _, identifier = str(owner or "").partition(":")
    if kind == "user":
        return "手动填写"
    if kind == "review":
        return f"复核批准 {identifier}"
    if kind == "auto":
        return f"{identifier} 免复核落库"
    if kind == "scan":
        return "文件名推导"
    if kind == "script":
        return f"维护脚本 {identifier}"
    return ""


class RevisionConflict(ValueError):
    """期望的 `mutation_revision` 与账本现值不符，写入一个字段都没有发生。

    继承 `ValueError` 是为了兜底：没有专门处理它的派发路径会把它当成请求错误
    回 400，而不是漏成 500。HTTP 出口（`routes_api`）先认这一类并回 409。
    """

    def __init__(self, expected: int, revisions: Mapping[int, int]):
        self.expected = int(expected)
        self.revisions = {int(key): int(value) for key, value in revisions.items()}
        current = "、".join(f"{key}={value}" for key, value in sorted(self.revisions.items()))
        super().__init__(f"资产已被改过：期望 revision {expected}，数据库现值 {current}")


@dataclass(frozen=True)
class FieldWrite:
    """一次归属写入的结果。"""

    #: WHERE 命中的资产条数。
    assets: int
    #: 至少在一条资产上真的改了取值的字段。
    written: tuple[str, ...]
    #: 至少在一条资产上因为归属或非空而被跳过的字段。
    refused: tuple[str, ...]
    #: 写入后各资产的 `mutation_revision`。
    revisions: dict[int, int]


def check_revision(connection, asset_ids: Sequence[int], expected: int | None) -> None:
    """所有目标资产的 `mutation_revision` 都必须等于 `expected`，否则抛 `RevisionConflict`。

    写多值字段（演员、标签）的路径也要过这一道：那些取值落在 `asset_tag` 与
    `asset_entity` 上，`write_owned_fields` 管不到，但用户点「通过」时看到的是同一
    张卡，凭据必须同样有效。
    """
    if expected is None:
        return
    ids = sorted({int(asset_id) for asset_id in asset_ids})
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    stale = {int(row[0]): int(row[1]) for row in connection.execute(
        f"SELECT id,mutation_revision FROM asset WHERE id IN ({marks})", ids)
        if int(row[1]) != int(expected)}
    if stale:
        raise RevisionConflict(int(expected), stale)


def _writable_sql(column: str, owner: str, require_empty: bool) -> str:
    """这个字段在这一行写得写不得，写成一段不带占位符的 SQL 条件。"""
    extracted = f"json_extract(COALESCE(field_owners,'{{}}'),'$.\"{column}\"')"
    if is_protected(owner):
        condition = "1"
    else:
        condition = (f"({extracted} IS NULL OR NOT ("
                     f"{extracted} LIKE 'user:%' OR {extracted} LIKE 'review:%'))")
    if require_empty:
        condition = f"({condition} AND trim(COALESCE({column},''))='')"
    return condition


def write_owned_fields(
    connection, asset_ids: Sequence[int], values: Mapping[str, object], owner: str, *,
    expected_revision: int | None = None, require_empty: bool = False,
) -> FieldWrite:
    """按字段归属写 `asset` 的真相字段，这是这几列唯一的写入入口。

    `owner` 是本次写入者的归属串。`user:*` 与 `review:*` 可以覆盖任何归属；其余
    只写无主字段和同为自动写入者写下的字段。`require_empty` 再加一道「当前值必须为
    空」，给补空专用的写入路径用。

    `expected_revision` 是乐观并发：给了它，所有目标资产的 `mutation_revision`
    都必须等于它，否则抛 `RevisionConflict` 且一个字段都不写。

    取值写入与归属登记在同一条 UPDATE 里完成。`mutation_revision` 只在取值真的变了
    时加一：它是给并发用的凭据，登记一次归属不该让别人手上的 revision 作废。
    """
    ids = sorted({int(asset_id) for asset_id in asset_ids})
    if not ids:
        return FieldWrite(0, (), (), {})
    fields = list(values)
    unknown = [name for name in fields if name not in OWNED_FIELDS]
    if unknown:
        raise ValueError(f"不是受归属保护的真相字段：{sorted(unknown)}")
    if not fields:
        raise ValueError("要写的字段是空的")
    kind, _, identifier = str(owner or "").partition(":")
    if kind not in {"user", "review", "auto", "scan", "script"}:
        raise ValueError(f"归属串的类别不在清单里：{owner!r}")
    _identifier(identifier)

    marks = ",".join("?" * len(ids))
    columns = ",".join(fields)
    before = {
        int(row[0]): (row[1], int(row[2]), row[3:])
        for row in connection.execute(
            f"SELECT id,field_owners,mutation_revision,{columns} FROM asset "
            f"WHERE id IN ({marks})", ids)
    }
    if expected_revision is not None:
        stale = {key: value[1] for key, value in before.items()
                 if value[1] != int(expected_revision)}
        if stale:
            raise RevisionConflict(int(expected_revision), stale)

    # 同一条判据在 SQL 里再写一遍是刻意的：SQL 那份保证写入本身正确，这一份只用来
    # 回报「哪几个字段被挡住了」，调用方据此决定要不要记决定、要不要交回人工。
    written: list[str] = []
    refused: list[str] = []
    for index, name in enumerate(fields):
        blocked = [
            (not is_protected(owner) and is_protected(owner_of(row[0], name)))
            or (require_empty and bool(str(row[2][index] or "").strip()))
            for row in before.values()
        ]
        if any(blocked):
            refused.append(name)
        if any(not block and row[2][index] != values[name]
               for block, row in zip(blocked, before.values())):
            written.append(name)

    assignments = []
    owner_expression = "COALESCE(field_owners,'{}')"
    changed = []
    assignment_params: list[object] = []
    owner_params: list[object] = []
    changed_params: list[object] = []
    for name in fields:
        condition = _writable_sql(name, owner, require_empty)
        assignments.append(f"{name}=CASE WHEN {condition} THEN ? ELSE {name} END")
        assignment_params.append(values[name])
        # `json_patch` 的任一参数是 NULL 时整个结果就是 NULL，所以基值走 COALESCE，
        # 写不得的字段给 `'{}'` 而不是 NULL——一个 NULL 会把整行的归属抹掉。
        owner_expression = (f"json_patch({owner_expression},"
                            f"CASE WHEN {condition} THEN json_object('{name}',?) "
                            f"ELSE '{{}}' END)")
        owner_params.append(owner)
        changed.append(f"({condition} AND {name} IS NOT ?)")
        changed_params.append(values[name])
    sql = (
        f"UPDATE asset SET {','.join(assignments)},field_owners={owner_expression},"
        f"mutation_revision=mutation_revision+(CASE WHEN {' OR '.join(changed)} "
        f"THEN 1 ELSE 0 END) WHERE id IN ({marks})"
    )
    cursor = connection.execute(
        sql, [*assignment_params, *owner_params, *changed_params, *ids])
    revisions = {int(row[0]): int(row[1]) for row in connection.execute(
        f"SELECT id,mutation_revision FROM asset WHERE id IN ({marks})", ids)}
    return FieldWrite(cursor.rowcount, tuple(written), tuple(refused), revisions)
