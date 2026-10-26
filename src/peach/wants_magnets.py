"""想要作品的只读资源查询：串行请求、有限缓存、来源限速与失败冷却。"""
from __future__ import annotations

from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from threading import Lock
from threading import RLock
import json
import math
import time
from datetime import datetime, timezone

from .sources import FailureReason, SourceFailure
from .catalog_rules import normalise_code_key
from .fsutil import atomic_write_text
from .jav_cache import cache_fresh, cache_lifetime as cache_lifetime

MAX_CACHE_BYTES = 16 * 1024 * 1024


def reusable_result(result) -> bool:
    return (isinstance(result, dict) and result.get('state') == 'ready'
            and isinstance(result.get('items'), list)
            and all(isinstance(item, dict) and item.get('protocol') in {'magnet', 'ed2k', 'url'}
                    and isinstance(item.get('uri'), str) and isinstance(item.get('size'), str)
                    and isinstance(item.get('attributes'), list) for item in result['items'])
            and bool(result['items'] or not result.get('warnings')))


def fetch(root: Path, code: str) -> dict:
    from .magnet_chain import search
    from .sources.javdb import merge_resources
    result = search(root, code, stop_on_preferred=False)
    linked = result.pop('linked_resources', [])
    ranked = [{**row, 'id': 'magnet:' + row['info_hash'], 'size': row['size_text']} for row in result['items']]
    return {**result, 'items': merge_resources([*ranked, *linked])[:128]}


class MagnetSearch:
    """一台服务同时只查一部；缓存最多 128 部，失败一分钟后可重试。"""

    def __init__(self, root: Path, *, fetcher=fetch, clock=time.monotonic, sleeper=time.sleep, wallclock=time.time):
        self.root, self.fetcher, self.clock, self.sleeper = root, fetcher, clock, sleeper
        self.wallclock = wallclock
        self.path = Path(root) / 'cache' / 'want-resources.json'
        self.lock = Lock()
        self.cache_lock = RLock()
        self.cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self.failures: dict[str, tuple[float, dict]] = {}
        self.saved_wall: dict[str, float] = {}
        self.next_request = 0.0
        self._load()

    def _load(self):
        try:
            if self.path.stat().st_size > MAX_CACHE_BYTES:
                return
            payload = json.loads(self.path.read_text(encoding='utf-8'))
            rows = payload.get('entries', []) if payload.get('version') == 1 else []
            if not isinstance(rows, list):
                return
            for row in reversed(rows[:128]):
                if not isinstance(row, dict) or not isinstance(row.get('code'), str):
                    continue
                saved = row.get('saved')
                if (type(saved) not in (int, float) or not math.isfinite(saved)
                        or saved > self.wallclock() or not reusable_result(row.get('result'))):
                    continue
                self.cache[normalise_code_key(row['code'])] = (
                    self.clock() - (self.wallclock() - saved), row['result'])
                self.saved_wall[normalise_code_key(row['code'])] = saved
        except (OSError, ValueError, TypeError, AttributeError):
            return

    def _save(self):
        rows = [{'code': code, 'saved': self.saved_wall[code], 'result': result}
                for code, (_, result) in reversed(self.cache.items()) if reusable_result(result)]
        encoded = json.dumps({'version': 1, 'entries': rows}, ensure_ascii=False)
        while len(encoded.encode('utf-8')) > MAX_CACHE_BYTES and rows:
            rows.pop()
            encoded = json.dumps({'version': 1, 'entries': rows}, ensure_ascii=False)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self.path, encoded, mode=0o600)
        except OSError:
            return

    def _cached(self, code, released):
        with self.cache_lock:
            cached = self.cache.get(code)
            saved = self.saved_wall.get(code, 0)
            elapsed = max(0, self.clock() - cached[0]) if cached else 0
            complete = cached and reusable_result(cached[1]) and not cached[1].get('warnings') and not cached[1].get('cache_stale')
            if cached and (cache_fresh(saved, released, max(self.wallclock(), saved + elapsed)) if complete
                           else elapsed < (86400 if reusable_result(cached[1]) else 60)):
                self.cache.move_to_end(code)
                return cached[1]
            failed = self.failures.get(code)
            if failed and failed[0] + 60 > self.clock():
                return failed[1]
        return None

    def known_magnets(self, code, released=None):
        result = self._cached(normalise_code_key(code), released)
        return any(item.get('protocol') == 'magnet' for item in result['items']) if result else None

    def _remember(self, code, result):
        with self.cache_lock:
            previous = self.cache.get(code)
            if (result['state'] == 'error' or result.get('warnings')) and previous and reusable_result(previous[1]):
                from .sources.javdb import merge_resources
                result['items'] = merge_resources([*previous[1]['items'], *result['items']]) if result['items'] else previous[1]['items']
                result['checked_at'] = previous[1].get('checked_at')
                result['cache_stale'] = True
                self.failures[code] = (self.clock(), result)
                return
            self.failures.pop(code, None)
            self.cache[code] = (self.clock(), result)
            self.saved_wall[code] = self.wallclock()
            self.cache.move_to_end(code)
            while len(self.cache) > 128:
                removed, _ = self.cache.popitem(last=False)
                self.failures.pop(removed, None)
                self.saved_wall.pop(removed, None)
            if reusable_result(result):
                self._save()

    def query(self, code: str, *, refresh: bool = False, released: str | None = None) -> dict:
        code = normalise_code_key(code)
        cached = self._cached(code, released)
        if cached and not refresh:
            return cached
        # 等锁的请求不积压为无限队列；前端把忙态留在卡片上，稍后再取。
        if not self.lock.acquire(timeout=0.05):
            return {"ok": True, "state": "busy", "items": cached['items'] if cached else [], "error": "",
                    "checked_at": cached.get('checked_at') if cached else None}
        try:
            cached = self._cached(code, released)
            if not refresh and cached:
                return cached
            delay = self.next_request - self.clock()
            if delay > 0:
                self.sleeper(delay)
            result = {"ok": True, "state": "ready", "items": [], "warnings": [], "error": "",
                      "checked_at": datetime.fromtimestamp(self.wallclock(), timezone.utc).isoformat()}
            try:
                result.update(self.fetcher(self.root, code))
                if result['state'] == 'ready' and not reusable_result(result):
                    result.update(state='error', error='资源链接未取得，请稍后重试或检查来源连接。')
            except SourceFailure as error:
                if error.reason != FailureReason.NOT_FOUND:
                    result.update(state="error", error=str(error))
            except Exception:  # 来源异常只报告查询失败，不泄露请求或凭据
                result.update(state="error", error="磁力抓取链未取得，请稍后重试或检查来源连接。")
            self.next_request = self.clock() + 3
            self._remember(code, result)
            return result
        finally:
            self.lock.release()


_FACTORY_LOCK = Lock()


@lru_cache(maxsize=4)
def _search_for(root: Path) -> MagnetSearch:
    return MagnetSearch(root)


def search_for(root: Path) -> MagnetSearch:
    # lru_cache 允许首次并发调用重复构造；工厂锁让所有卡片共用同一把查询锁。
    with _FACTORY_LOCK:
        return _search_for(Path(root).resolve())
