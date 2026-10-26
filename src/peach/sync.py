"""本地工作副本与专用共享副本之间的单写者复制。

刻意**不是**多主实时同步。SQLite 没有安全的自动三方合并：两台机器各自写过
之后，没有任何规则能把「看过 / 喜欢 / 标签」这类断言合成一份而不丢东西。
这里做的是单用户场景下能做到、且不会悄悄丢数据的那种：

- 共享副本只负责传输，两台机器各持一份本地工作副本；
- 服务启动只观察世代与写入端，**绝不自动 pull/push**；
- `marker.device` 是当前唯一写入端，另一台服务强制只读；
- 复制与写入端切换只能由托盘/CLI 显式执行；
- **两边都动过就拒绝自动合并**，进只读并报冲突，由人来选一边；
- 同步点不可达时本地照常运行，恢复后再同步。

世代号只表示血缘先后，不表示时间：时钟在两台机器上不可靠，尤其 exFAT 只有
2 秒精度的本地时间戳。

复制一律走 SQLite 的 backup API，不复制文件：账本是 WAL 模式，直接拷 `.db`
会漏掉 `-wal` 里已提交但未 checkpoint 的事务，拷完还可能和目标残留的 `-wal`
拼成一个已经损坏的库。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from .fsutil import atomic_write_text
from .platform import root_online


LOGGER = logging.getLogger(__name__)

#: 保留兼容参数；生产服务不再启动定时回写线程。
PUSH_INTERVAL_SECONDS = 60


#: 旧标记没有记 WAL 载荷时的占位值；判定时跳过这一层。
UNKNOWN_WAL = -1

#: WAL 文件头长度。比它短的文件不可能装着帧。
WAL_HEADER_BYTES = 32

WAL_SUFFIX = "-wal"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Marker:
    """一份副本的血缘与指纹。

    `generation` 是血缘，`device` 同时是当前唯一写入端；`size`/`mtime_ns` 是**相邻那个
    库文件**在写下标记那一刻的指纹，用来判断这份副本之后有没有被本机写过。
    """

    generation: int
    device: str
    written_at: str
    size: int
    mtime_ns: int
    #: 主库内容摘要。空串表示旧标记没有记过，判定时退回只看 size/mtime。
    digest: str = ""
    #: 归一化后的 WAL 载荷大小；-1 表示旧标记没有记过。
    wal_size: int = UNKNOWN_WAL

    def as_json(self) -> str:
        return json.dumps(
            {
                "generation": self.generation,
                "device": self.device,
                "written_at": self.written_at,
                "size": self.size,
                "mtime_ns": self.mtime_ns,
                "digest": self.digest,
                "wal_size": self.wal_size,
            },
            ensure_ascii=False,
            indent=2,
        )


@dataclass(frozen=True)
class SyncPlan:
    """启动时的判定结果。`action` 决定接下来做什么，`reason` 是给人看的。"""

    action: str          # disabled / offline / missing / pull / push / local-ahead / in-sync / conflict
    reason: str
    local_generation: int = 0
    shared_generation: int = 0

    @property
    def conflict(self) -> bool:
        return self.action == "conflict"


def marker_path(db_path: Path) -> Path:
    return db_path.with_name(db_path.name + ".sync.json")


def fingerprint(db_path: Path) -> tuple[int, int] | None:
    try:
        stat = db_path.stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def read_marker(db_path: Path) -> Marker | None:
    try:
        raw = json.loads(marker_path(db_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        return Marker(
            generation=int(raw["generation"]),
            device=str(raw["device"]),
            written_at=str(raw.get("written_at", "")),
            size=int(raw.get("size", -1)),
            mtime_ns=int(raw.get("mtime_ns", -1)),
            digest=str(raw.get("digest", "")),
            wal_size=int(raw.get("wal_size", UNKNOWN_WAL)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def write_marker(db_path: Path, marker: Marker, *, with_digest: bool = True) -> Marker:
    """把标记写到库文件旁边，指纹按落盘后的实际状态重算。

    `with_digest=False` 用于共享副本：只有本机副本会被 `local_dirty` 检查，给共享副本
    算摘要等于凭空多读一遍 127 MiB 的 SMB 网络流量，没有任何消费者用得上。
    """
    current = fingerprint(db_path)
    if current is not None:
        marker = replace(
            marker, size=current[0], mtime_ns=current[1],
            digest=digest_database(db_path) if with_digest else "",
            wal_size=wal_payload_size(db_path),
        )
    atomic_write_text(marker_path(db_path), marker.as_json())
    return marker


def device_id(state_dir: Path) -> str:
    """本机标识。只用来说明「这一代是谁写的」，不参与任何权限判断。"""
    path = state_dir / "device-id"
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    generated = f"{os.uname().nodename if hasattr(os, 'uname') else 'host'}-{uuid.uuid4().hex[:8]}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(generated, encoding="utf-8")
    except OSError:
        LOGGER.warning("无法保存 device-id，本次运行使用临时标识")
    return generated


def digest_database(db_path: Path) -> str:
    """主库文件的 SHA-256。127 MiB 实测约 78 ms，只在判据出现分歧时才算。"""
    hasher = hashlib.sha256()
    try:
        with db_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                hasher.update(chunk)
    except OSError:
        return ""
    return hasher.hexdigest()


def wal_payload_size(db_path: Path) -> int:
    """WAL 里有没有帧，用归一化后的大小表示。

    只读连接打开数据库时 SQLite 会建一个 0 字节的 `-wal`，关闭时又可能删掉它。
    「不存在」和「0 字节」因此必须等价，否则光是浏览就会被判成写过。
    小于一个 WAL 头（32 字节）的文件不可能装着帧，一律归零。
    """
    try:
        size = Path(f"{db_path}{WAL_SUFFIX}").stat().st_size
    except OSError:
        return 0
    return size if size >= WAL_HEADER_BYTES else 0


def local_dirty(db_path: Path, marker: Marker | None) -> bool:
    """这份副本在上次标记之后有没有被写过。

    判据分三层，从便宜到贵，每一层都是实测出来的（2026-08-25，见 ADR-0020）：

    1. **WAL 载荷**。只看 `(size, mtime_ns)` 会**漏报**：已提交但还在 WAL 里没回写的
       事务完全不改主库文件，于是 `plan()` 判成可以 pull，而 `copy_database` 会
       `unlink` 掉目标的 `-wal`——那些事务就此永久消失。这是数据丢失，不是误报。
    2. **快路径**。主库指纹和 WAL 载荷都与标记一致就是干净，不做任何哈希。
    3. **内容摘要**。主库大小不变、只有 mtime 变时，`(size, mtime_ns)` 会**误报**：
       内容中性的 checkpoint 或备份工具碰一下时间戳都会让只读端被判成脏，
       于是每次同步都退化成要人工选边。这一层用摘要把「碰过」和「写过」分开。

    旧标记没有 `digest`/`wal_size`，那两层自动跳过，只按第一层判。
    """
    current = fingerprint(db_path)
    if current is None:
        return False
    if marker is None:
        return True
    if marker.wal_size != UNKNOWN_WAL and wal_payload_size(db_path) != marker.wal_size:
        return True
    if current == (marker.size, marker.mtime_ns):
        return False
    if current[0] != marker.size:
        return True
    if marker.digest and digest_database(db_path) == marker.digest:
        return False
    return True


def writer_device(local: Path, shared: Path) -> str | None:
    """返回当前写入端；共享 marker 在同代时优先，避免半次接管产生双写。"""
    local_marker, shared_marker = read_marker(local), read_marker(shared)
    if local_marker is None:
        return shared_marker.device if shared_marker else None
    if shared_marker is None:
        return local_marker.device
    if shared_marker.generation >= local_marker.generation:
        return shared_marker.device
    return local_marker.device


def copy_database(source: Path, target: Path, *, source_immutable: bool = False) -> None:
    """用 backup API 生成一致快照，再原子替换目标。

    不能让 SQLite 直接把目标连接开在 SMB 上：macOS 的 smbfs 目录可普通写入，SQLite
    却可能因锁或事务侧文件失败而报 ``unable to open database file``。先在本机临时目录
    生成已关闭的独立快照，再传到目标旁边原子替换；复制的不是活跃数据库文件，所以不会
    漏掉源库 WAL 中已提交的事务。
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, snapshot_name = tempfile.mkstemp(prefix="peach-ledger-", suffix=".db")
    os.close(descriptor)
    snapshot = Path(snapshot_name)
    transfer = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        immutable = "&immutable=1" if source_immutable else ""
        origin = sqlite3.connect(f"file:{source}?mode=ro{immutable}", uri=True)
        try:
            destination = sqlite3.connect(snapshot)
            try:
                origin.backup(destination)
            finally:
                destination.close()
        finally:
            origin.close()
        shutil.copyfile(snapshot, transfer)
        # Windows 的 FlushFileBuffers 需要可写句柄；只读句柄上调用 fsync 会返回
        # ``OSError: [Errno 9] Bad file descriptor``。这里同步的是刚写完、尚未发布
        # 的临时快照，使用读写句柄不会改变内容。
        with transfer.open("r+b") as handle:
            os.fsync(handle.fileno())
        # 旧副本的 sidecar 不能和新快照拼在一起。同步调用方保证目标没有服务在写。
        for suffix in ("-wal", "-shm", "-journal"):
            Path(f"{target}{suffix}").unlink(missing_ok=True)
        os.replace(transfer, target)
    finally:
        snapshot.unlink(missing_ok=True)
        transfer.unlink(missing_ok=True)


#: `synchronize_now` 里真的会复制数据库的那几个判定；其余都是「没什么可做」。
COPY_ACTIONS = ("pull", "push", "local-ahead")


def plan(local: Path, shared: Path) -> SyncPlan:
    """只读判定，不碰任何文件。"""
    if local == shared or local.resolve() == shared.resolve():
        return SyncPlan("disabled", "本机直接使用共享副本，不需要复制")
    if not root_online(shared.parent):
        return SyncPlan("offline", "共享副本所在的盘不可达，本地照常读写")

    local_exists, shared_exists = local.is_file(), shared.is_file()
    if not shared_exists and not local_exists:
        return SyncPlan("missing", "两侧都没有数据库")
    if not shared_exists:
        return SyncPlan("push", "共享副本不存在，用本地副本播种")
    if not local_exists:
        return SyncPlan("pull", "本地副本不存在，从共享副本拉取")

    local_marker, shared_marker = read_marker(local), read_marker(shared)
    dirty = local_dirty(local, local_marker)
    local_generation = local_marker.generation if local_marker else 0
    shared_generation = shared_marker.generation if shared_marker else 0

    if local_marker is None or shared_marker is None:
        # 没有血缘可比，两份都有数据。这种情况只能人来选，自动挑一边就是丢数据。
        return SyncPlan(
            "conflict", "缺少同步标记，无法判断两份数据库的先后",
            local_generation, shared_generation,
        )
    if (local_generation == shared_generation
            and local_marker.device != shared_marker.device):
        return SyncPlan(
            "conflict", "同一世代的写入端标记不一致",
            local_generation, shared_generation,
        )
    if shared_generation > local_generation:
        if dirty:
            return SyncPlan(
                "conflict",
                f"共享副本已到第 {shared_generation} 代，本地仍是第 {local_generation} 代"
                f"且有未回写的改动",
                local_generation, shared_generation,
            )
        return SyncPlan("pull", "共享副本更新", local_generation, shared_generation)
    if shared_generation < local_generation:
        return SyncPlan("push", "本地副本更新", local_generation, shared_generation)
    if dirty:
        return SyncPlan("local-ahead", "本地有待回写的改动", local_generation, shared_generation)
    return SyncPlan("in-sync", "两侧一致", local_generation, shared_generation)


def resolve(local: Path, shared: Path, device: str) -> SyncPlan:
    """`synchronize_now` 将要照着做的那个判定，但不碰任何文件。

    比 `plan()` 多的一层是写入端：本机不是写入端时，`push` / `local-ahead` 会被拒，
    真跑一次的结果只能是 conflict。调用方要先知道「这次到底会不会复制」才能决定要不要
    停服务——共享盘没挂、两侧已经一致、或本机根本推不动的时候，停一遍服务再启回来
    是白白一次停机，还会把「没什么可做」报成同步失败。
    """
    decision = plan(local, shared)
    owner = writer_device(local, shared)
    if (decision.action in ("push", "local-ahead")
            and owner is not None and owner != device):
        return SyncPlan(
            "conflict", f"当前写入端是 {owner}，本机不能推送",
            decision.local_generation, decision.shared_generation,
        )
    return decision


class LedgerSync:
    """启动判定、周期回写和只读闸门。"""

    def __init__(
        self,
        local: Path,
        shared: Path,
        device: str,
        interval: float = PUSH_INTERVAL_SECONDS,
    ):
        self.local = local
        self.shared = shared
        self.device = device
        self.interval = interval
        self.status = "unstarted"
        self.detail = ""

    @property
    def read_only(self) -> bool:
        """只有 marker 指定的唯一写入端能写；其他服务保持可读。"""
        return self.status == "conflict" or writer_device(self.local, self.shared) != self.device

    @property
    def read_only_message(self) -> str:
        """409 时给界面的解释：发生了什么、影响、怎么恢复。"""
        if self.status == "conflict":
            return (
                f"数据库处于冲突状态（{self.detail}），本机暂时只读。"
                "请在托盘执行「同步数据库」；若提示仍冲突，需按日志人工处理。"
            )
        owner = writer_device(self.local, self.shared) or "另一台机器"
        return (
            f"数据库由 {owner} 负责写入，本机当前只能浏览。"
            "请改在该机器上操作，或在托盘执行「由本机接管数据库写入」"
            "（要求两侧数据一致）后再试。"
        )

    # ── 单次动作 ────────────────────────────────────────────────────────────
    def pull(self) -> None:
        shared_marker = read_marker(self.shared)
        # 共享副本由 push 以关闭后的 SQLite 快照原子发布。macOS smbfs 不支持 SQLite
        # 普通只读连接的锁/sidecar 语义，但 immutable 读取这种已关闭快照是安全的。
        copy_database(self.shared, self.local, source_immutable=True)
        generation = shared_marker.generation if shared_marker else 1
        origin = shared_marker.device if shared_marker else self.device
        write_marker(self.local, Marker(generation, origin, _now(), 0, 0))

    def push(self) -> None:
        shared_marker = read_marker(self.shared)
        local_marker = read_marker(self.local)
        base = max(
            shared_marker.generation if shared_marker else 0,
            local_marker.generation if local_marker else 0,
        )
        generation = base + 1
        copy_database(self.local, self.shared)
        stamp = _now()
        write_marker(self.shared, Marker(generation, self.device, stamp, 0, 0),
                     with_digest=False)
        write_marker(self.local, Marker(generation, self.device, stamp, 0, 0))

    def observe(self) -> SyncPlan:
        """只读刷新当前角色与同步状态，不碰 DB 或 marker。"""
        decision = plan(self.local, self.shared)
        owner = writer_device(self.local, self.shared)
        if decision.conflict:
            self.status, self.detail = "conflict", decision.reason
        elif owner == self.device:
            self.status, self.detail = "writer", f"本机是写入端；{decision.reason}"
        elif owner:
            self.status, self.detail = "reader", f"写入端是 {owner}；{decision.reason}"
        else:
            self.status, self.detail = "conflict", "没有可验证的写入端"
        return decision

    def synchronize_now(self) -> SyncPlan:
        """重新判定并完成一次安全同步，供服务停机后的手动同步使用。"""
        decision = resolve(self.local, self.shared, self.device)
        self.status, self.detail = decision.action, decision.reason
        if decision.action == "pull":
            self.pull()
        elif decision.action in ("push", "local-ahead"):
            self.push()
        self.observe()
        return decision

    def take_ownership(self) -> SyncPlan:
        """在两份完全一致时显式切换唯一写入端，不复制数据库内容。"""
        decision = plan(self.local, self.shared)
        if decision.action != "in-sync":
            self.status, self.detail = "conflict", (
                f"接管前必须两侧一致；当前是 {decision.action}：{decision.reason}"
            )
            return SyncPlan(
                "conflict", self.detail,
                decision.local_generation, decision.shared_generation,
            )
        local_marker, shared_marker = read_marker(self.local), read_marker(self.shared)
        generation = max(
            local_marker.generation if local_marker else 0,
            shared_marker.generation if shared_marker else 0,
        ) + 1
        stamp = _now()
        write_marker(self.shared, Marker(generation, self.device, stamp, 0, 0),
                     with_digest=False)
        write_marker(self.local, Marker(generation, self.device, stamp, 0, 0))
        self.observe()
        return SyncPlan("take-ownership", "本机已成为唯一写入端", generation, generation)

    def push_if_needed(self) -> str:
        """周期回写。别人抢先推过就转冲突，绝不覆盖。"""
        if self.read_only:
            return "conflict"
        decision = plan(self.local, self.shared)
        if decision.action in ("local-ahead", "push"):
            self.push()
            self.status = "in-sync"
            return "pushed"
        if decision.action == "conflict":
            self.status, self.detail = "conflict", decision.reason
            LOGGER.error("数据库同步冲突：%s", decision.reason)
            return "conflict"
        self.status = decision.action
        return decision.action
