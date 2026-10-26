"""页面全集由 React 路由注册表和覆盖组共同声明。

`managed-routes.tsx` 的页面元素按真实地址注册，目录表展开到 `CATALOG_PATHS`。
详情与队列使用 `OVERLAY_PATHS`；设置偏好单独登记，三组共同覆盖页面全集。
实体资料页另按种类登记模式路径，筛选态沿用 `STATE_ROUTES` 的目录身份。
"""
import pathlib
import re
import unittest

import peach

REPO = pathlib.Path(peach.__file__).resolve().parents[2]
APP = REPO / "web" / "app.js"
FRONTEND_SRC = REPO / "frontend" / "src"
ROUTE_TABLES = FRONTEND_SRC / "react" / "router" / "managed-routes.tsx"
OVERLAY = FRONTEND_SRC / "history" / "overlay.ts"
CORE = FRONTEND_SRC / "core" / "index.ts"
SETTINGS = FRONTEND_SRC / "history" / "settings-route.ts"

#: 以字面量写出的页面路径；`STATE_ROUTES` 那一组筛选态（三个筛选态与垃圾文件）不在此列。资料页由路由树按模式
#: （`TREE_PATTERNS`）登记，同样不在此列。
FROZEN_ROUTES = frozenset({
    "/", "/trash", "/playlists", "/assets/:id", "/posts/:id",
    "/performers", "/creators", "/studios", "/agencies", "/tags", "/stats", "/taste",
    "/review", "/data-cleanup", "/duplicates", "/resource-sync", "/quality-goals",
    "/sources", "/follow", "/subscriptions", "/tasks", "/immerse", "/wall",
    "/settings/diagnostics", "/jav", "/works/:code", "/settings", "/settings/appearance",
    "/settings/playback", "/settings/jav", "/settings/search", "/settings/content",
    "/settings/library", "/settings/subscriptions", "/settings/downloads",
    "/settings/network", "/settings/maintenance",
})
#: 路由树按模式登记的资料页，一个实体种类一条。
TREE_PATTERNS = frozenset({"/performers/*", "/studios/*", "/creators/*", "/series/*", "/agencies/*", "/tags/*"})

#: 字符串排在注释前面：`'/performers/*'` 里的 `/*` 不是注释。
_STRING_OR_COMMENT = re.compile(r"""('(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*")|/\*.*?\*/|//[^\n]*""", re.S)


def without_comments(source: str) -> str:
    return _STRING_OR_COMMENT.sub(lambda hit: hit.group(1) or "", source)


def braced(source: str, start: int) -> str:
    """`source[start]` 是 `{`，取到与它配对的 `}` 为止（含两端）。"""
    depth = 0
    for index in range(start, len(source)):
        depth += {"{": 1, "}": -1}.get(source[index], 0)
        if depth == 0:
            return source[start:index + 1]
    raise ValueError("大括号不配对")


def top_level_entries(body: str) -> dict[str, str]:
    """对象字面量第一层的 `'键': 值`，值是内联对象就取整个对象，是标识符就取标识符。"""
    entries = {}
    inner = body[1:-1]
    depth = 0
    index = 0
    while index < len(inner):
        char = inner[index]
        if char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
        elif depth == 0 and char == "'":
            hit = re.match(r"'([^']*)'\s*:\s*", inner[index:])
            if hit:
                value_at = index + hit.end()
                if inner[value_at] == "{":
                    value = braced(inner, value_at)
                else:
                    value = re.match(r"[\w$]+", inner[value_at:]).group(0)
                entries[hit.group(1)] = value
                index = value_at + len(value)
                continue
        index += 1
    return entries


def quoted_list(source: str, name: str) -> set[str]:
    """`export const NAME = [...] as const` 里的字符串字面量。"""
    return set(re.findall(r"'([^']*)'", source.split(f"export const {name}", 1)[1].split("] as const", 1)[0]))


def tree_routes(source: str) -> set[str]:
    """路由树用元素画的页面：各张 `*_ROUTES` 表里带 `element` 的条目（值是共用常量的，看那个常量）。
    目录表按页面分键，任一页带了元素，页面组就按 `CATALOG_PATHS` 的每一条路径挂它。"""
    source = without_comments(source)
    found = set()
    for table in re.finditer(r"export const (\w+_ROUTES)\b[^=]*=\s*\{", source):
        for path, value in top_level_entries(braced(source, table.end() - 1)).items():
            if not value.startswith("{"):
                shared = re.search(rf"const {re.escape(value)}\b[^=]*=\s*(?:\([^)]*\)[^=]*=>\s*\()?\{{", source)
                if shared is None:
                    raise ValueError(f"无法读取路由声明：{path} -> {value}")
                value = braced(source, shared.end() - 1)
            if re.search(r"\belement\s*:", value):
                found |= quoted_list(source, "CATALOG_PATHS") if table.group(1) == "CATALOG_ROUTES" else {path}
    return found


class LegacyShellRouteTests(unittest.TestCase):
    def setUp(self):
        self.overlay = quoted_list(OVERLAY.read_text(encoding="utf-8"), "OVERLAY_PATHS")
        settings = without_comments(SETTINGS.read_text(encoding="utf-8"))
        start = settings.index('{', settings.index('export const PREFERENCE_SECTIONS'))
        self.preferences = {'/settings'} | {f'/settings/{key}' for key in re.findall(r'(\w+)\s*:', braced(settings, start))}
        tree = tree_routes(ROUTE_TABLES.read_text(encoding="utf-8"))
        self.patterns = {path for path in tree if path.endswith("/*")}
        state_routes = CORE.read_text(encoding="utf-8").split("const STATE_ROUTES", 1)[1].split("};", 1)[0]
        self.tree = tree - self.patterns - set(re.findall(r":'([^']*)'", state_routes))

    def test_the_route_registry_and_overlays_partition_the_frozen_pages(self):
        self.assertEqual(sorted(self.tree & self.overlay), [], "覆盖组的路径不该在页面组里再登记元素")
        union = self.tree | self.overlay | self.preferences
        added = sorted(union - FROZEN_ROUTES)
        self.assertEqual(added, [],
                         "多出了页面；新页面请做成 frontend/ 的 island 或路由树的元素，"
                         "并把它加进 FROZEN_ROUTES：" + "、".join(added))
        gone = sorted(FROZEN_ROUTES - union)
        self.assertEqual(gone, [], "注册表缺少页面：" + "、".join(gone))

    def test_the_tree_registers_exactly_the_entity_patterns(self):
        self.assertEqual(sorted(self.patterns), sorted(TREE_PATTERNS),
                         "路由树按模式登记的页面变了；新增一种同样算加页面")

    def test_the_application_has_no_unbundled_shell_entry(self):
        self.assertFalse(APP.exists(), "主页面入口应为固定的 peach-app 构建产物")


if __name__ == "__main__":
    unittest.main()
