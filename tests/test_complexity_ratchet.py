"""函数复杂度棘轮：函数变复杂必须有人判断过，不许「顺手加一个 if」悄悄堆上去。

2026-09-13 全仓盘点，`src/peach/` 与 `scripts/` 里分支数达到 30 的函数有 42 个，
最大的两个（`library_processing.process_library`、`web_batch.q_ads`）分别是 91 与 85。
它们不是一次写成的，是每次「顺手加一个 if」堆出来的；这个门槛拦的就是那一个 if。

计数是纯 AST 分支数：每个 `if`／`elif`／`for`／`while`／`except`／`with`／`assert`、
每个推导式子句、每个 `and`／`or`、每个 `case` 与条件表达式各计 1，嵌套函数算进
外层。数值只和它自己比，不和别的工具比。

两张表，数都必须与代码一致：

- `BASELINE` 是盘点留下的存量，只能降。函数变简单了就把数改小，降到限值以下就删掉。
- `ACCEPTED` 是判过「就该这么长」的函数，每条带一句理由：顺序流水线、按类型分派、
  逐字段解析这类，拆开只会把一路传下去的状态散进几个只调用一次的函数里。它长了就在
  同一次改动里改数，理由跟着再看一遍还成不成立。

函数超过自己的数时先判断，再二选一。一是拆：拆出去的那块要能独立命名，有自己的前提
和结果，比如 `jav_cover_fetch._download_best`「按档次完整下载，挑第一张静态图」。二是
挪进 `ACCEPTED` 写理由。下面几种不算拆，只是把分支数藏起来，评审一律退回：只转发一个
表达式的包装；把模块函数当参数传给 helper；为了少一个 if 建只有一项的分派表；在注释里
拿这道门槛当设计理由。
"""
import ast
import pathlib
import unittest
from types import MappingProxyType

import peach

REPO = pathlib.Path(peach.__file__).resolve().parents[2]
SOURCE_ROOTS = (REPO / "src" / "peach", REPO / "scripts")

#: 达到这个分支数的函数进入基线；基线之外的函数不许长到这里。
LIMIT = 30

#: 键是 `<仓库相对路径>:<限定名>`，值是当前的分支数，只能往下改。
BASELINE: dict[str, int] = {
    "src/peach/web_batch.py:_scored_junk": 62,
    "scripts/localize_performer_names.py:collect": 63,
    "src/peach/web_resource_sync.py:_resource_orphan_plan": 50,
    "src/peach/web_review.py:_review_rows": 43,
    "src/peach/fanbox.py:normalize_fanbox_post": 59,
    "src/peach/web_follow.py:q_follow": 35,
    "scripts/merge_duplicate_identities.py:collect": 50,
    "src/peach/web_review.py:_attach_review_asset_context": 46,
    "src/peach/web_review.py:w_review_decision": 44,
    "scripts/harvest_social_avatars.py:run": 45,
    "scripts/fetch_studio_avatar_candidates.py:main": 44,
    "src/peach/web_catalog.py:q_item": 44,
    "src/peach/web_follow.py:q_follow_authors": 40,
    "scripts/sheets.py:run": 40,
    "src/peach/taste_history.py:_taste_analysis": 40,
    "src/peach/web_catalog.py:catalog_filter": 40,
    "scripts/audit_domain_codes.py:collect": 39,
    "src/peach/web_catalog.py:q_items": 37,
    "scripts/harvest_social_avatars.py:harvest_entity": 37,
    "scripts/probe.py:run": 36,
    "scripts/merge_duplicate_identities.py:collect_repeated_projections": 34,
    "src/peach/fc2_similarity.py:media_evidence": 34,
    "scripts/clean_names.py:run": 33,
    "src/peach/studio_icons.py:icon_row": 32,
    "scripts/find_ads.py:find_candidates": 31,
    "scripts/rehome_unknown_jav.py:build_plan": 31,
    "src/peach/resource_identification.py:ingest_results": 30,
}

#: 判过就该这么长的函数：`(分支数, 为什么拆开更差)`。键与 `BASELINE` 同形、不重叠。
ACCEPTED: dict[str, tuple[int, str]] = {
    "src/peach/api.py:create_app": (
        42, "应用工厂：按启动顺序构造数据库、各项后台服务（含可选分享）与路由并接成一个 app，每个可注入的"
            "依赖各带一个缺省；挂载探测按配置状态启动。拆开会把同一条构造顺序分散到几处"),
    "src/peach/library_processing.py:process_library": (
        47, "顺序流水线：本地资料、本地预览、联网采集、写候选、收尾共用一份进度状态"
            "与问题记录；来源扫描、资产查询与外部会话分别归 `_scan_sources`、"
            "`_collection_rows`、`_RemoteSession`，剩下的是任务编排与逐项目资料处理"),
    "src/peach/jav_cover_fetch.py:run": (
        50, "批处理的主循环：每个番号依次量本机尺寸、择优、比较、落盘、记日志，统计、日志行"
            "和出错后换新的连接池在整轮里共用，逐段拆开只是把这几样来回传"),
}

#: 一句理由至少要说清「是哪种形状、拆开差在哪」，短于这个长度的多半只是占位。
MIN_REASON = 20

BRANCHES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler,
            ast.With, ast.AsyncWith, ast.Assert, ast.IfExp, ast.match_case)


def complexity(node: ast.AST) -> int:
    """`node` 的分支数加一：一条直线的函数是 1。"""
    total = 1
    for child in ast.walk(node):
        if isinstance(child, BRANCHES):
            total += 1
        elif isinstance(child, ast.comprehension):
            total += 1 + len(child.ifs)
        elif isinstance(child, ast.BoolOp):
            total += len(child.values) - 1
    return total


def functions(path: pathlib.Path):
    """`path` 里每个函数与方法的 `(限定名, 分支数)`；嵌套函数并进外层，不单列。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def walk(body, prefix):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield prefix + node.name, complexity(node)
            elif isinstance(node, ast.ClassDef):
                yield from walk(node.body, prefix + node.name + ".")

    yield from walk(tree.body, "")


def survey() -> dict[str, int]:
    """当前树里每个函数的分支数，键与 `BASELINE` 同形。"""
    found = {}
    for root in SOURCE_ROOTS:
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(REPO).as_posix()
            for name, value in functions(path):
                found[f"{relative}:{name}"] = value
    return found


def recorded() -> dict[str, int]:
    """两张表里记下的数合在一起。"""
    return {**BASELINE, **{key: value for key, (value, _reason) in ACCEPTED.items()}}


class ComplexityRatchetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.current = MappingProxyType(survey())

    def test_the_counter_counts_the_shapes_it_names(self):
        source = (
            "def f(items):\n"
            "    for item in items:\n"
            "        if item and item.ok or item.forced:\n"
            "            try:\n"
            "                pass\n"
            "            except ValueError:\n"
            "                pass\n"
            "    return [x for x in items if x]\n"
        )
        node = ast.parse(source).body[0]
        # 1 + for + if + and + or + except + 推导式 + 推导式的 if
        self.assertEqual(complexity(node), 8)

    def test_no_function_grew_past_its_recorded_count(self):
        current, limits = self.current, recorded()
        grown = sorted(
            f"{key}: {value}（记录 {limits.get(key, LIMIT - 1)}）"
            for key, value in current.items()
            if value > limits.get(key, LIMIT - 1))
        self.assertEqual(grown, [],
                         "这些函数的分支数超过了记录。拆出能独立命名的一块，或判过它就该"
                         "这么长后挪进 ACCEPTED 写理由；转发包装、把函数当参数传、单项分派表"
                         "都不算拆：\n  " + "\n  ".join(grown))

    def test_the_recorded_counts_match_the_code(self):
        current = self.current
        stale = sorted(
            f"{key}: 现在 {current.get(key, '不存在')}，记录 {value}"
            for key, value in recorded().items()
            if key not in current or current[key] < value)
        self.assertEqual(stale, [],
                         "记录落后于代码：函数变简单或删掉了就把这里改小或删掉，"
                         "记录不能替将来的增长留余量：\n  " + "\n  ".join(stale))

    def test_the_tables_hold_only_functions_at_or_over_the_limit(self):
        below = sorted(key for key, value in recorded().items() if value < LIMIT)
        self.assertEqual(below, [], f"低于 {LIMIT} 的函数不该进表：{below}")

    def test_an_accepted_function_says_why_splitting_is_worse(self):
        self.assertEqual(sorted(set(BASELINE) & set(ACCEPTED)), [], "一个函数只能在一张表里")
        thin = sorted(key for key, (_value, reason) in ACCEPTED.items()
                      if len(reason.strip()) < MIN_REASON)
        self.assertEqual(thin, [], f"这些条目的理由不足 {MIN_REASON} 字：{thin}")


if __name__ == "__main__":
    unittest.main()
