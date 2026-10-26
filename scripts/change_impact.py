"""比较检查输入的有效内容；解析失败与执行权限变化保留完整验证。"""
from __future__ import annotations

import ast
from functools import lru_cache
import hashlib
import json
from pathlib import PurePosixPath
import tomllib


PACKAGE_FILES = frozenset({"package.json", "frontend/package.json"})
DISPLAY_FIELDS = frozenset({"description", "keywords"})


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def dependency_inputs(source: str) -> dict:
    """提取 Python 构建、解释器与依赖输入。"""
    data = tomllib.loads(source)
    project = data.get("project", {})
    uv = dict(data.get("tool", {}).get("uv", {}))
    uv.pop("required-version", None)
    return {"build": data.get("build-system"), "requires-python": project.get("requires-python"),
            "dependencies": project.get("dependencies"), "extras": project.get("optional-dependencies"),
            "groups": data.get("dependency-groups"), "uv": uv}


@lru_cache(maxsize=2048)
def signatures(path: str, source: str) -> dict[str, str]:
    """返回可比较的内容指纹；docstring、类型注释和运行时字串参与比较。"""
    try:
        if path.endswith(".py"):
            tree = ast.parse(source, type_comments=True)
            return {"behavior": digest(ast.dump(tree, include_attributes=False))}
        if path in PACKAGE_FILES:
            data = json.loads(source)
            if not isinstance(data, dict):
                return {}
            return {"behavior": digest({key: value for key, value in data.items() if key not in DISPLAY_FIELDS})}
        if path == "pyproject.toml":
            return {"behavior": digest(tomllib.loads(source)), "dependencies": digest(dependency_inputs(source))}
    except (ValueError, TypeError, SyntaxError, RecursionError):
        return {}
    return {}


def kind(path: str, before: str | None, after: str | None) -> str:
    if before is None or after is None:
        return "unsafe"
    return signature_kind(signatures(path, before), signatures(path, after))


def signature_kind(before: dict, after: dict) -> str:
    if before.get("behavior") and before.get("behavior") == after.get("behavior"):
        return "prose"
    if before.get("dependencies") and before.get("dependencies") == after.get("dependencies"):
        return "metadata"
    return "logic"


def delta(before: dict, after: dict) -> tuple[list[str], dict[str, str]]:
    """比较两个完整快照，并保留文件执行模式与删除信息。"""
    old, new = before["files"], after["files"]
    paths = sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))
    kinds = {}
    for name in paths:
        first, second = old.get(name), new.get(name)
        if isinstance(first, list) and isinstance(second, list) and first[0] == second[0] and first[2:] == second[2:]:
            kinds[name] = signature_kind(before.get("signatures", {}).get(name, {}),
                                         after.get("signatures", {}).get(name, {}))
        elif isinstance(first, list) and isinstance(second, list):
            kinds[name] = "unsafe"
    if "src/peach/__init__.py" in paths and version_only(before, after):
        kinds["src/peach/__init__.py"] = "version"
    return paths, kinds


def version_only(before: dict, after: dict) -> bool:
    path = "src/peach/__init__.py"
    first, second = before["files"].get(path), after["files"].get(path)
    return isinstance(first, list) and isinstance(second, list) and first[0] == second[0] and first[2:] == second[2:] \
        and before.get("version_body") is not None and before.get("version_body") == after.get("version_body")


def documentation(path: str) -> bool:
    return PurePosixPath(path).suffix == ".md"
