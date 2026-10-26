"""发版前只读检查上游、依赖与参考项目，证据写入 build。"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import shutil
import subprocess
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "scripts"
from . import check_reference_updates as references

ROOT = Path(__file__).resolve().parents[1]


def run(args: list[str], root: Path, *, allowed: tuple[int, ...] = (0,)) -> str:
    result = subprocess.run([shutil.which(args[0]) or args[0], *args[1:]], cwd=root,
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=180)
    if result.returncode not in allowed:
        raise RuntimeError(f"{' '.join(args)}：{result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def collect(root: Path, repo: str) -> dict:
    registry = references.load_registry(root / "docs/reference-sources.json")
    problems = references.validate_registry(root, registry)
    if problems:
        raise ValueError("；".join(problems))
    report = {"checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "commit": run(["git", "rev-parse", "HEAD"], root),
              "checks": [], "complete": True}

    def check(label, action):
        print(f"检查：{label}", flush=True)
        try:
            report["checks"].append({"id": label, "status": "已取得", "result": action()})
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            report["complete"] = False
            report["checks"].append({"id": label, "status": "未取得", "error": str(exc)})

    for label, args, allowed in (
        ("python", ["uv", "tree", "--locked", "--all-groups", "--outdated"], (0,)),
        ("amane-python", ["uv", "tree", "--project", "tools/amane-bridge", "--locked",
                          "--outdated"], (0,)),
        ("npm", ["npm", "outdated", "--json"], (0, 1)),
        ("frontend-npm", ["npm", "outdated", "--prefix", "frontend", "--json"], (0, 1)),
        ("dependabot", ["gh", "api", "--paginate", "--slurp",
                        f"repos/{repo}/pulls?state=open&per_page=100"], (0,)),
    ):
        def inspect_dependency(args=args, allowed=allowed, label=label):
            output = run(args, root, allowed=allowed)
            if label == "dependabot":
                return [{"number": item["number"], "title": item["title"], "url": item["html_url"]}
                        for page in json.loads(output) for item in page
                        if item.get("user", {}).get("login") == "dependabot[bot]"]
            if label.endswith("npm"):
                payload = json.loads(output or "{}")
                if "error" in payload:
                    raise ValueError(str(payload["error"]))
                return payload
            return output
        check(label, inspect_dependency)

    revisions = {}

    def resolve(source):
        git = source.get("git")
        if not git:
            return None
        key = (git["repository"], git["ref"])
        if key not in revisions:
            revisions[key] = references.resolve_git_revision(source)
        return revisions[key]

    for pin in references.selected_sources(registry, None, key="pins"):
        check(pin["id"], lambda pin=pin: references.inspect_pin(root, pin))
        web = pin["repository"].removesuffix(".git")
        if web.startswith("https://github.com/"):
            upstream = web.removeprefix("https://github.com/")
            check(pin["id"] + "-release", lambda upstream=upstream: json.loads(run(
                ["gh", "api", f"repos/{upstream}/releases/latest"], root)))
    for source in references.selected_sources(registry, None):
        def inspect(source=source):
            result = references.inspect_source(root, source, revision_resolver=resolve)
            return {key: value for key, value in result.items() if key != "live"}
        check(source["id"], inspect)
    return report


def review(root: Path, repo: str) -> dict:
    report = collect(root, repo)
    path = root / "build/release-review.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not report["complete"]:
        missing = [item["id"] for item in report["checks"] if item["status"] == "未取得"]
        raise ValueError(f"发版检查未取得：{'、'.join(missing)}；证据见 {path}")
    return {"report": str(path), "checks": len(report["checks"]), "complete": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="peach-mitao/peach")
    args = parser.parse_args()
    try:
        print(json.dumps(review(ROOT, args.repo), ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
