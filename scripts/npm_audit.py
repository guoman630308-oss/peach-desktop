"""按漏洞严重性判定 npm 审计，仅为临时网络错误退避重试。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import time

TRANSIENT = {"EAI_AGAIN", "ECONNRESET", "ECONNREFUSED", "ETIMEDOUT", "ENOTFOUND",
             "E429", "E500", "E502", "E503", "E504"}


def classify(payload: dict, returncode: int) -> str:
    error = payload.get("error") or {}
    if not isinstance(error, dict):
        return "error"
    if error:
        return "retry" if error.get("code") in TRANSIENT else "error"
    counts = payload.get("metadata", {}).get("vulnerabilities")
    if not isinstance(counts, dict) or not {"high", "critical"}.issubset(counts):
        return "error"
    if counts["high"] or counts["critical"]:
        return "vulnerable"
    return "clean" if returncode == 0 else "error"


def audit(project: Path, *, runner=subprocess.run, sleep=time.sleep) -> int:
    for attempt in range(3):
        try:
            result = runner([shutil.which("npm") or "npm", "audit", "--json", "--audit-level=high"],
                            cwd=project, capture_output=True, text=True, encoding="utf-8", timeout=90)
            try:
                payload = json.loads(result.stdout)
                if not isinstance(payload, dict):
                    raise ValueError("审计结果不是 JSON 对象")
            except (ValueError, TypeError):
                print(result.stdout or result.stderr)
                return 1
            decision = classify(payload, result.returncode)
            print(json.dumps({"attempt": attempt + 1, "project": str(project),
                              "decision": decision, "audit": payload}, ensure_ascii=False), flush=True)
        except subprocess.TimeoutExpired:
            decision = "retry"
            print(f"npm audit 第 {attempt + 1} 次超时", flush=True)
        if decision == "clean":
            return 0
        if decision != "retry" or attempt == 2:
            return 1
        sleep(15 * (2 ** attempt))
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=Path("."))
    return audit(parser.parse_args().prefix.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
