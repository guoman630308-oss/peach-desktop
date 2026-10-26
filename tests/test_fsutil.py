import os
import tempfile
import unittest
from pathlib import Path

from peach.fsutil import atomic_path, atomic_write_bytes, atomic_write_text


class AtomicWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def leftovers(self) -> list[str]:
        return sorted(path.name for path in self.root.rglob("*.tmp*"))

    def test_a_write_lands_and_leaves_nothing_behind(self):
        target = self.root / "nested" / "marker.json"
        returned = atomic_write_text(target, '{"a": 1}')
        self.assertEqual(returned, target)
        self.assertEqual(target.read_text(encoding="utf-8"), '{"a": 1}')
        self.assertEqual(self.leftovers(), [])

    def test_a_failed_write_keeps_the_old_content_and_drops_the_staging_file(self):
        target = self.root / "marker.json"
        atomic_write_text(target, "old")
        with self.assertRaises(RuntimeError):
            with atomic_path(target) as temporary:
                temporary.write_text("new", encoding="utf-8")
                raise RuntimeError("boom")
        # 关键断言：读的人只能看到替换前那份，而不是写了一半的新内容。
        self.assertEqual(target.read_text(encoding="utf-8"), "old")
        self.assertEqual(self.leftovers(), [])

    def test_the_staging_file_sits_next_to_the_target(self):
        # 跨卷的 os.replace 不是原子的，所以临时文件必须和目标同目录。
        target = self.root / "deep" / "marker.json"
        with atomic_path(target) as temporary:
            temporary.write_text("x", encoding="utf-8")
            self.assertEqual(temporary.parent, target.parent)

    def test_the_staging_file_keeps_the_target_extension(self):
        # FFmpeg 和 PIL 按扩展名决定输出格式，`x.tmp` 会让它们直接失败。
        seen: list[str] = []
        with atomic_path(self.root / "5_4.jpg") as temporary:
            seen.append(temporary.suffix)
            temporary.write_bytes(b"\xff\xd8")
        with atomic_path(self.root / "state", suffix=".json") as temporary:
            seen.append(temporary.suffix)
            temporary.write_bytes(b"{}")
        self.assertEqual(seen, [".jpg", ".json"])

    def test_two_writers_to_one_target_do_not_share_a_staging_name(self):
        target = self.root / "cover.jpg"
        with atomic_path(target) as first:
            with atomic_path(target) as second:
                self.assertNotEqual(first, second)
                second.write_bytes(b"second")
            first.write_bytes(b"first")
        self.assertEqual(target.read_bytes(), b"first")
        self.assertEqual(self.leftovers(), [])

    def test_bytes_can_be_written_with_a_tightened_mode(self):
        target = self.root / "review-cache.json"
        atomic_write_bytes(target, b"payload", mode=0o600)
        self.assertEqual(target.read_bytes(), b"payload")
        if os.name != "nt":
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
