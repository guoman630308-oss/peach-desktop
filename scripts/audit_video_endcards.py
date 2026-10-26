#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""只读抽取视频首尾帧并生成片尾出处/不完整版候选。

帧和 OCR sidecar 只写 generated/endcard-evidence；ledger、媒体文件、quality goal 与复核决定
都不写。默认必须点名 --asset 或给正数 --limit，避免无意启动全库云盘批处理。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import time
import sys
from datetime import datetime
from typing import Callable
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from peach.config import DATABASE_PATH, FFMPEG_DIR, GENERATED_DIR, STATE_DIR
from peach.scripting import open_readonly
from peach.endcard import POLICY_VERSION, detect_endcard
from peach.ffmpeg import FFmpegResolver
from peach.jobs import (
    DiskGuard,
    JobPolicyError,
    SourceAccessPolicy,
    require_free_space,
    job_main,
)
from peach.media import resolve_case_insensitive
from peach.platform import system_volume
from peach.review_csv import write_rows


CANDIDATE_FIELDS = (
    "candidate_key", "asset_id", "name", "code", "location", "sample_kind",
    "timestamp_seconds", "frame_key", "ocr_text", "verdict", "detected_urls",
    "detected_handles", "confidence", "reason", "policy_version", "status",
)
HEALTH_FIELDS = (
    "source", "policy_version", "attempted", "frames_requested", "frame_cache_reused",
    "captured", "capture_errors", "ocr_cache_reused", "ocr_succeeded", "ocr_errors",
    "candidates", "incomplete_candidates", "source_evidence", "elapsed_ms",
    "last_error_kind", "last_error_message",
)


def build_parser() -> argparse.ArgumentParser:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--asset", type=int, action="append")
    parser.add_argument("--location")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--allow-metered", action="store_true")
    parser.add_argument("--head-offsets", default="0.5,2,5")
    parser.add_argument("--tail-offsets", default="0.5,2,5,10,15")
    parser.add_argument("--min-free", type=float, default=40.0)
    parser.add_argument(
        "--evidence-root", type=Path, default=GENERATED_DIR / "endcard-evidence",
    )
    parser.add_argument(
        "--out", type=Path,
        default=GENERATED_DIR / f"video-endcard-candidate-{stamp}.csv",
    )
    parser.add_argument(
        "--health", type=Path,
        default=GENERATED_DIR / f"video-endcard-health-{stamp}.csv",
    )
    parser.add_argument(
        "--ocr-script", type=Path,
        default=Path(__file__).resolve().with_name("windows_ocr.ps1"),
    )
    parser.add_argument("--lock", type=Path, default=STATE_DIR / ".endcard-audit.lock")
    return parser


def parse_offsets(raw: str) -> tuple[float, ...]:
    try:
        values = tuple(dict.fromkeys(float(value.strip()) for value in raw.split(",")))
    except ValueError as error:
        raise ValueError("抽帧偏移必须是逗号分隔的秒数") from error
    if not values or any(value <= 0 for value in values):
        raise ValueError("抽帧偏移必须全部大于 0")
    return values


def sample_points(
    duration: float, head_offsets: tuple[float, ...], tail_offsets: tuple[float, ...],
) -> list[tuple[str, float]]:
    points: list[tuple[str, float]] = []
    seen: set[int] = set()
    for kind, offsets in (("head", head_offsets), ("tail", tail_offsets)):
        for offset in offsets:
            timestamp = offset if kind == "head" else duration - offset
            millis = round(timestamp * 1000)
            if timestamp <= 0 or timestamp >= duration or millis in seen:
                continue
            seen.add(millis)
            points.append((kind, timestamp))
    return points


def load_assets(args: argparse.Namespace) -> list[dict]:
    if not args.asset and args.limit <= 0:
        raise ValueError("必须点名 --asset，或给正数 --limit 才会启动")
    source_sql, source_parameters = SourceAccessPolicy().sql_filter(
        args.location, args.allow_metered,
    )
    sql = (
        "SELECT id,location,path,name,code,duration,size FROM asset "
        "WHERE medium='video' AND location<>'online' AND duration>2"
        + source_sql
    )
    parameters: tuple[object, ...] = source_parameters
    if args.asset:
        marks = ",".join("?" for _ in args.asset)
        sql += f" AND id IN ({marks})"
        parameters += tuple(args.asset)
    sql += " ORDER BY size DESC,id"
    if args.limit > 0:
        sql += " LIMIT ?"
        parameters += (args.limit,)
    connection = open_readonly(args.db)
    try:
        return [dict(row) for row in connection.execute(sql, parameters)]
    finally:
        connection.close()


def frame_path(root: Path, asset_id: int, kind: str, timestamp: float) -> Path:
    return root / str(asset_id) / f"{kind}-{round(timestamp * 1000):09d}.png"


def capture_frame(ffmpeg: str, source: str, timestamp: float, destination: Path) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp.png")
    command = [
        ffmpeg, "-y", "-v", "error", "-rw_timeout", "8000000",
        "-ss", f"{timestamp:.3f}", "-i", source, "-frames:v", "1",
        "-vf", "scale=1280:-1", str(temporary),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=45)
        if completed.returncode != 0 or not temporary.is_file() or temporary.stat().st_size <= 1024:
            return False
        os.replace(temporary, destination)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        temporary.unlink(missing_ok=True)


class WindowsOcrProvider:
    def __init__(self, script: Path):
        self.script = script
        self.powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / (
            r"System32\WindowsPowerShell\v1.0\powershell.exe"
        )

    def recognize(self, paths: list[Path]) -> dict[str, dict]:
        if os.name != "nt" or not self.powershell.is_file() or not self.script.is_file():
            raise RuntimeError("Windows OCR provider unavailable")
        results: dict[str, dict] = {}
        # Windows PowerShell 5.1 对命令行 string[] 的绑定有歧义：第二个裸路径会被
        # 绑定到后面的 Language。逐张调用固定 -File 虽有少量启动成本，但路径不会
        # 被拼进脚本文本，也不会串到别的参数。
        for path in paths:
            command = [
                str(self.powershell), "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(self.script), "-Paths", str(path.resolve()),
            ]
            completed = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30,
            )
            if completed.returncode != 0:
                raise RuntimeError((completed.stderr or completed.stdout or "OCR failed")[-800:])
            try:
                payload = json.loads(completed.stdout.lstrip("\ufeff"))
            except ValueError as error:
                raise RuntimeError("Windows OCR returned invalid JSON") from error
            for row in payload:
                results[str(Path(row["path"]).resolve())] = row
        return results


def atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic_csv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    write_rows(path, fields, rows, atomic=True, fill_missing=True)


def run(
    args: argparse.Namespace,
    *,
    capture: Callable[[str, str, float, Path], bool] = capture_frame,
    ocr=None,
    ffmpeg_path: str | None = None,
) -> int:
    started = time.perf_counter()
    health = {
        "source": "ffmpeg+windows_ocr", "policy_version": POLICY_VERSION,
        "attempted": 0, "frames_requested": 0, "frame_cache_reused": 0,
        "captured": 0, "capture_errors": 0, "ocr_cache_reused": 0,
        "ocr_succeeded": 0, "ocr_errors": 0, "candidates": 0,
        "incomplete_candidates": 0, "source_evidence": 0, "elapsed_ms": 0,
        "last_error_kind": "", "last_error_message": "",
    }
    try:
        assets = load_assets(args)
        head = parse_offsets(args.head_offsets)
        tail = parse_offsets(args.tail_offsets)
        require_free_space(system_volume(), args.min_free)
        guard = DiskGuard(system_volume(), args.min_free, 0)
        if ffmpeg_path is None:
            resolver = FFmpegResolver(FFMPEG_DIR)
            ffmpeg = resolver.ffmpeg()
            if ffmpeg is None:
                raise RuntimeError("ffmpeg unavailable")
            active_ffmpeg = str(ffmpeg.path)
        else:
            active_ffmpeg = ffmpeg_path
        provider = ocr or WindowsOcrProvider(args.ocr_script)
        observations: dict[int, list[dict]] = {int(asset["id"]): [] for asset in assets}
        uncached: list[Path] = []
        frame_context: dict[str, tuple[dict, str, float, Path]] = {}
        for asset in assets:
            health["attempted"] += 1
            for kind, timestamp in sample_points(float(asset["duration"]), head, tail):
                health["frames_requested"] += 1
                guard.check(force=True)
                frame = frame_path(args.evidence_root, int(asset["id"]), kind, timestamp)
                sidecar = frame.with_suffix(".ocr.json")
                if frame.is_file() and frame.stat().st_size > 1024:
                    health["frame_cache_reused"] += 1
                else:
                    ok = capture(
                        active_ffmpeg, resolve_case_insensitive(asset["path"]),
                        timestamp, frame,
                    )
                    if not ok:
                        health["capture_errors"] += 1
                        continue
                    health["captured"] += 1
                key = str(frame.resolve())
                frame_context[key] = (asset, kind, timestamp, sidecar)
                if sidecar.is_file():
                    try:
                        result = json.loads(sidecar.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        uncached.append(frame)
                    else:
                        observations[int(asset["id"])].append({
                            "asset": asset, "kind": kind, "timestamp": timestamp,
                            "frame": frame, **result,
                        })
                        health["ocr_cache_reused"] += 1
                else:
                    uncached.append(frame)
        if uncached:
            results = provider.recognize(uncached)
            for frame in uncached:
                key = str(frame.resolve())
                asset, kind, timestamp, sidecar = frame_context[key]
                result = results.get(key) or {"text": "", "lines": [], "error": "missing OCR row"}
                atomic_json(sidecar, result)
                observations[int(asset["id"])].append({
                    "asset": asset, "kind": kind, "timestamp": timestamp,
                    "frame": frame, **result,
                })
                if result.get("error"):
                    health["ocr_errors"] += 1
                    health["last_error_kind"] = "ocr_item"
                    health["last_error_message"] = str(result["error"])[:500]
                else:
                    health["ocr_succeeded"] += 1
        candidates = []
        priority = {"incomplete_candidate": 2, "source_evidence": 1, "none": 0}
        for asset_id, rows in observations.items():
            classified = [(detect_endcard(row.get("text") or ""), row) for row in rows]
            classified = [item for item in classified if item[0].verdict != "none"]
            if not classified:
                continue
            detection, evidence = max(
                classified,
                key=lambda item: (priority[item[0].verdict], item[0].confidence,
                                  item[1]["kind"] == "tail", item[1]["timestamp"]),
            )
            asset = evidence["asset"]
            candidates.append({
                "candidate_key": str(asset_id), "asset_id": asset_id,
                "name": asset.get("name") or "", "code": asset.get("code") or "",
                "location": asset.get("location") or "", "sample_kind": evidence["kind"],
                "timestamp_seconds": round(float(evidence["timestamp"]), 3),
                "frame_key": str(evidence["frame"].relative_to(args.evidence_root)).replace("\\", "/"),
                "ocr_text": str(evidence.get("text") or "")[:2000],
                "verdict": detection.verdict,
                "detected_urls": " ".join(detection.urls),
                "detected_handles": " ".join(detection.handles),
                "confidence": detection.confidence, "reason": detection.reason,
                "policy_version": POLICY_VERSION, "status": "candidate",
            })
        candidates.sort(key=lambda row: (-float(row["confidence"]), int(row["asset_id"])))
        health["candidates"] = len(candidates)
        health["incomplete_candidates"] = sum(
            row["verdict"] == "incomplete_candidate" for row in candidates
        )
        health["source_evidence"] = sum(
            row["verdict"] == "source_evidence" for row in candidates
        )
        atomic_csv(args.out, CANDIDATE_FIELDS, candidates)
    except (OSError, sqlite3.Error, subprocess.SubprocessError, ValueError, RuntimeError,
            JobPolicyError) as error:
        health["last_error_kind"] = type(error).__name__
        health["last_error_message"] = str(error)[:500]
        health["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        atomic_csv(args.health, HEALTH_FIELDS, [health])
        print(f"首尾帧审计失败：{error}")
        print(f"健康报告 → {args.health}")
        return getattr(error, "exit_code", 2)
    health["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
    atomic_csv(args.health, HEALTH_FIELDS, [health])
    print(f"首尾帧候选 {health['candidates']} 条 → {args.out}")
    print(f"来源健康 → {args.health}")
    print("只生成候选：未修改 ledger、quality goal、媒体文件或复核决定。")
    return 0


def main(argv: list[str] | None = None) -> int:
    return job_main(build_parser, run, argv)


if __name__ == "__main__":
    raise SystemExit(main())
