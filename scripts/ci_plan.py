"""按提交影响生成 CI 矩阵；主线全量、Windows 系统回归与独立安装共同验收。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "scripts"
ROOT = Path(__file__).resolve().parents[1]


def browser_matrix(root: Path = ROOT, count: int = 6) -> dict:
    """以实测秒数分配文件；每个文件只属于一个独立 runner。"""
    timing_file = root / "scripts/ci-browser-timings.json"
    timings = json.loads(timing_file.read_text(encoding="utf-8"))["seconds"] if timing_file.is_file() else {}
    files = [path.relative_to(root).as_posix() for path in (root / "frontend/e2e").rglob("*.test.ts")]
    if not files or count < 1:
        raise ValueError("浏览器矩阵须包含用例且分片数为正整数")
    groups = [[] for _ in range(min(count, len(files)))]
    totals = [0.0 for _ in groups]
    for name in sorted(files, key=lambda name: (-timings.get(name, 30.0), name)):
        slot = min(range(len(groups)), key=lambda index: (totals[index], index))
        groups[slot].append(name)
        totals[slot] += timings.get(name, 30.0)
    return {"include": [{"index": index, "files": json.dumps(sorted(group)),
                         "estimated_seconds": round(totals[index])}
                        for index, group in enumerate(groups)]}


def validate_results(results: dict, required: list[str], *, phase: str = "complete") -> None:
    """所选任务全部成功；未选择的任务可以跳过，不能隐藏失败。"""
    if phase == "frontend":
        required = [name for name in required if name in {"plan", "web-bundle", "web-vendor"}]
        mandatory = {"plan"}
    elif phase == "complete":
        mandatory = {"plan", "python", "workflow-lint", "frontend-ready"}
    else:
        raise ValueError(f"未知验证阶段：{phase}")
    if not mandatory.issubset(required):
        raise ValueError("验证计划缺少必需任务")
    bad = {name: results.get(name, {}).get("result", "missing")
           for name in required if results.get(name, {}).get("result") != "success"}
    bad.update({name: item["result"] for name, item in results.items()
                if item["result"] not in {"success", "skipped"}})
    if bad:
        raise ValueError(f"验证任务未通过：{bad}")


def runtime_paths(paths: list[str], contents: dict | None) -> list[str]:
    from . import change_impact
    return [path for path in paths if not change_impact.documentation(path)
            and not (contents and path in contents
                     and change_impact.kind(path, *contents[path]) == "prose")]


def browser_input(path: str) -> bool:
    from . import test_runner as runner
    if path == "tests/test_web_e2e.py" or path.startswith("frontend/e2e/"):
        return True
    if path.startswith(("tests/", "frontend/test/", "frontend/scripts/", "scripts/")):
        return False
    return path in {"src/peach/auth.py", "src/peach/server.py"} \
        or path.startswith(("src/peach/web_", "src/peach/routes_")) \
        or "web" in runner.scopes_for_changes([path])[0]


def plan(event: str, paths: list[str], contents: dict | None = None) -> dict:
    from . import test_runner as runner
    scopes, _ = runner.scopes_for_changes(paths, contents=contents)
    wide = event in {"push", "workflow_dispatch"} or "full" in scopes
    rows = []
    for system in ("macos-latest", "windows-latest"):
        scope = "full" if wide else "auto"
        # 独立 runner 承担分片，避免共享文件、端口和全量证据锁。
        shards = (4 if system == "windows-latest" else 2) if scope == "full" else 1
        for index in range(shards):
            rows.append({"os": system, "python": "3.14", "scope": scope,
                         "shard_index": index, "shard_count": shards})
    wheels = [{"os": "windows-latest", "python": "3.14"},
              {"os": "macos-latest", "python": "3.14"}]
    shared = wide
    effective = runtime_paths(paths, contents)
    browser = shared or any(browser_input(path) for path in effective)
    frontend = shared or any(path.startswith(("frontend/", "web/dist/", ".github/workflows/test.yml"))
                             or path == "scripts/copy-rules.json" for path in effective)
    vendor = shared or any(path.startswith(("web/vendor/", ".github/workflows/test.yml"))
                           or path in {"package.json", "package-lock.json", "web/index.html",
                                       "scripts/vendor_web_dependencies.mjs"} for path in effective)
    wheel = shared or "packaging" in scopes or any(path.startswith(("src/", "migrations/", "web/", "resources/"))
                                                 for path in effective)
    required = ["plan", "python", "workflow-lint", "frontend-ready"]
    required += [name for enabled, names in ((browser, ["web-e2e"]), (frontend, ["web-bundle"]),
                                            (vendor, ["web-vendor"]), (wheel, ["wheel-build", "wheel-smoke"]))
                 if enabled for name in names]
    return {"matrix": {"include": rows}, "wheel_matrix": {"include": wheels}, "wide": wide,
            "browser_matrix": browser_matrix(), "browser": browser, "frontend": frontend,
            "vendor": vendor, "wheel": wheel, "required_jobs": required}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", choices=("push", "pull_request", "workflow_dispatch"))
    parser.add_argument("--base")
    parser.add_argument("--verify-results", action="store_true")
    parser.add_argument("--phase", choices=("complete", "frontend"), default="complete")
    args = parser.parse_args()
    if args.verify_results:
        validate_results(json.loads(os.environ["RESULTS"]), json.loads(os.environ["REQUIRED_JOBS"]),
                         phase=args.phase)
        return
    if not args.event or not args.base:
        parser.error("生成计划需要 --event 与 --base")
    from . import test_runner as runner
    root = Path(__file__).resolve().parents[1]
    try:
        paths = runner.changed_files(root, args.base)
        result = plan(args.event, paths, runner.changed_contents(root, args.base, paths))
    except subprocess.CalledProcessError:
        # 首次推送、浅克隆或已删除基线：扩大验证，不能把无 diff 当成无需测试。
        result = plan("workflow_dispatch", [])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as stream:
            for key in result:
                stream.write(f"{key}={json.dumps(result[key])}\n")


if __name__ == "__main__":
    main()
