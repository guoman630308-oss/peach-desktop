"""复核产物 CSV 的读写口径。

这里守的是编码契约本身。它此前在 46 个读写点各写一遍，而两条要求都属于「写的时候
一切正常、几天后有人用 Excel 打开才发现坏了」那一类，靠人眼复查是拦不住的。
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from peach.review_csv import ENCODING, read_rows, write_rows

FIELDS = ("code", "name")


class ReviewCsvTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_written_file_carries_the_bom_excel_needs(self):
        """没有 BOM，一份中文复核表在 Excel 里就是一屏问号。"""
        target = self.root / "out.csv"
        write_rows(target, FIELDS, [{"code": "ABW-232", "name": "白虎"}])
        self.assertTrue(target.read_bytes().startswith(b"\xef\xbb\xbf"), "缺少 UTF-8 BOM")
        self.assertEqual(ENCODING, "utf-8-sig")

    def test_rows_are_not_separated_by_blank_lines(self):
        """漏掉 newline=""，csv 写的 \\r\\n 会再被文本层翻译一次，行间多出空行。"""
        target = self.root / "out.csv"
        write_rows(target, FIELDS, [{"code": "A", "name": "一"}, {"code": "B", "name": "二"}])
        raw = target.read_bytes().decode("utf-8-sig")
        self.assertNotIn("\r\r\n", raw)
        self.assertEqual([line for line in raw.splitlines() if line],
                         ["code,name", "A,一", "B,二"])

    def test_missing_parent_directory_is_created(self):
        target = self.root / "deep" / "nested" / "out.csv"
        write_rows(target, FIELDS, [{"code": "A", "name": "一"}])
        self.assertTrue(target.is_file())

    def test_round_trip_through_the_shared_reader(self):
        target = self.root / "out.csv"
        rows = [{"code": "ABW-232", "name": "白虎"}, {"code": "FC2-PPV-1", "name": "レキシントンⅡ"}]
        write_rows(target, FIELDS, rows)
        self.assertEqual(read_rows(target), rows)

    def test_a_missing_file_raises_unless_the_caller_opts_out(self):
        """默认抛异常，和直接 open() 一样。

        有些脚本就指望这个异常把「输入没给全」变成一次带健康报告的失败退出。默认
        返回空表会让它们安静地跑完并返回成功——这正是把它设成默认时踩到的坑。
        """
        missing = self.root / "never-written.csv"
        with self.assertRaises(FileNotFoundError):
            read_rows(missing)
        self.assertEqual(read_rows(missing, missing_ok=True), [])

    def test_an_unexpected_field_is_an_error_by_default(self):
        """多余的键几乎总是字段名写错了。

        无条件补齐会把它变成一列静默的空值，等复核的人对着空列发呆时已经查不回来。
        """
        with self.assertRaises(ValueError):
            write_rows(self.root / "out.csv", FIELDS, [{"code": "A", "typo": "x"}])

    def test_fill_missing_pads_absent_columns_when_asked(self):
        target = self.root / "out.csv"
        write_rows(target, FIELDS, [{"code": "A"}], fill_missing=True)
        self.assertEqual(read_rows(target), [{"code": "A", "name": ""}])

    def test_atomic_write_never_leaves_a_half_written_file(self):
        """长跑任务被打断时，读的人要么拿到替换前那份完整文件，要么拿到替换后那份。"""
        target = self.root / "out.csv"
        write_rows(target, FIELDS, [{"code": "old", "name": "旧"}])

        class Boom(RuntimeError):
            pass

        def exploding_rows():
            yield {"code": "new", "name": "新"}
            raise Boom("写到一半断了")

        with self.assertRaises(Boom):
            write_rows(target, FIELDS, exploding_rows(), atomic=True)
        self.assertEqual(read_rows(target), [{"code": "old", "name": "旧"}],
                         "原子写失败后必须保留替换前那份")
        leftovers = [p.name for p in self.root.iterdir() if p.name.startswith(".")]
        self.assertEqual(leftovers, [], f"临时文件没清掉：{leftovers}")

    def test_atomic_write_replaces_the_previous_version(self):
        target = self.root / "out.csv"
        write_rows(target, FIELDS, [{"code": "old", "name": "旧"}], atomic=True)
        write_rows(target, FIELDS, [{"code": "new", "name": "新"}], atomic=True)
        self.assertEqual(read_rows(target), [{"code": "new", "name": "新"}])

    def test_atomic_write_waits_for_a_reader_to_let_go(self):
        """Windows 上复核页正读着这份表时替换被拒；等读者放手再换，处理任务不因此中断。"""
        target = self.root / "out.csv"
        write_rows(target, FIELDS, [{"code": "old", "name": "旧"}], atomic=True)
        real_replace = os.replace
        refusals = [PermissionError(5, "拒绝访问"), PermissionError(5, "拒绝访问")]

        def replace(source, destination):
            if refusals:
                raise refusals.pop()
            real_replace(source, destination)

        with mock.patch("peach.fsutil.os.replace", side_effect=replace), \
                mock.patch("peach.fsutil.time.sleep"):
            write_rows(target, FIELDS, [{"code": "new", "name": "新"}], atomic=True)
        self.assertEqual(read_rows(target), [{"code": "new", "name": "新"}])
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.startswith(".")], [])


if __name__ == "__main__":
    unittest.main()
