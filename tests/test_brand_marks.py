"""随仓库分发的实体标识：收录判据与运行时回退（ADR-0026）。

这里守两件事。一是**收进来的东西必须是可以收的**：ADR-0026 那五条判据逐条检查，越界
的不是调高上限而是不收。二是**收进来的东西必须真的被用上**：取图和「这个厂牌有没有图」
两处判据必须同时认得内置资源，只改一处的后果不是报错，是页面安静地少显示一批图。
"""
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

import peach
from peach import brand_marks
from peach.previews import LOGO_VARIANTS, PreviewService

REPO = pathlib.Path(peach.__file__).resolve().parents[2]


class ManifestTests(unittest.TestCase):
    """清单与目录内容必须互为真相，谁也不能单方面漂移。"""

    @classmethod
    def setUpClass(cls):
        cls.marks = brand_marks.manifest()["marks"]

    def test_every_mark_has_a_source_url(self):
        """判据 1。没有来源证据的不收，不靠文件名推断归属。"""
        missing = [row["file"] for row in self.marks if not row.get("source_url")]
        self.assertEqual([], missing)

    def test_variants_are_mark_shaped(self):
        """判据 3。作品封面、剧照不是标识；变体只有 icon、logo 和裸文件三种。"""
        allowed = set(LOGO_VARIANTS) | {""}
        unexpected = sorted({row.get("variant", "") for row in self.marks} - allowed)
        self.assertEqual([], unexpected)

    def test_each_file_within_budget(self):
        """判据 4。超过 256 KB 的多半不是小图标。"""
        oversize = [(row["file"], row["bytes"]) for row in self.marks
                    if row["bytes"] > brand_marks.MAX_FILE_BYTES]
        self.assertEqual([], oversize)

    def test_total_within_budget(self):
        """判据 5。越过上限说明混进了别的东西，不是把上限调高。"""
        total = sum(row["bytes"] for row in self.marks)
        self.assertLessEqual(total, brand_marks.MAX_TOTAL_BYTES)

    def test_manifest_and_directory_agree(self):
        """清单登记的和目录里的必须一一对应，两边都不许有对方没有的。"""
        listed = {row["file"] for row in self.marks}
        on_disk = {path.name for path in brand_marks.STUDIOS_DIR.iterdir()
                   if path.is_file()}
        self.assertEqual(listed, on_disk)

    def test_bytes_match_recorded_digest(self):
        """摘要对不上说明字节被改过——`.gitattributes` 的换行改写就是一种改法。"""
        wrong = []
        for row in self.marks:
            data = (brand_marks.STUDIOS_DIR / row["file"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != row["sha256"] or len(data) != row["bytes"]:
                wrong.append(row["file"])
        self.assertEqual([], wrong)

    def test_extensions_are_resolvable(self):
        """认不出扩展名的文件运行时会以 octet-stream 发出去，浏览器不显示。"""
        unknown = [row["file"] for row in self.marks
                   if pathlib.Path(row["file"]).suffix.lower()
                   not in brand_marks.CONTENT_TYPES]
        self.assertEqual([], unknown)

    def test_no_person_marks(self):
        """判据 2。自然人肖像不进仓库，`avatars/` 那套命名不该出现在这里。"""
        person = [row["file"] for row in self.marks
                  if row["file"].startswith(("performer-", "creator-"))]
        self.assertEqual([], person)


class LookupTests(unittest.TestCase):
    """内置资源要被找得到，但不能盖住用户自己那份。"""

    def test_find_matches_cache_stem(self):
        """缓存名去掉 `.img` 就该命中内置文件，两边命名规则同源。"""
        sample = brand_marks.manifest()["marks"][0]
        stem = pathlib.Path(sample["file"]).stem
        self.assertEqual(brand_marks.STUDIOS_DIR / sample["file"],
                         brand_marks.find(stem))

    def test_find_is_case_insensitive(self):
        sample = brand_marks.manifest()["marks"][0]
        stem = pathlib.Path(sample["file"]).stem
        self.assertIsNotNone(brand_marks.find(stem.upper()))
        self.assertIsNotNone(brand_marks.find(stem.lower()))

    def test_unknown_stem_is_none(self):
        self.assertIsNone(brand_marks.find("no-such-studio-4f2a"))
        self.assertIsNone(brand_marks.find(""))

    def test_installed_stems_drop_variant_suffix(self):
        """变体后缀要剥掉，否则 `X.icon` 会被当成另一个厂牌，页面判 X 没有图。"""
        stems = brand_marks.installed_stems(LOGO_VARIANTS)
        self.assertTrue(stems)
        for variant in LOGO_VARIANTS:
            self.assertFalse([stem for stem in stems if stem.endswith(f".{variant}")])

    def test_local_cache_wins_over_bundled(self):
        """用户复核批准装下的那份更新；顺序反了，每次发版都会盖掉他的批准结果。"""
        sample = brand_marks.manifest()["marks"][0]
        stem = pathlib.Path(sample["file"]).stem
        with tempfile.TemporaryDirectory() as raw:
            logo_root = pathlib.Path(raw)
            local = logo_root / f"{stem}.img"
            local.write_bytes(b"\x89PNG\r\n\x1a\n local")
            service = PreviewService.__new__(PreviewService)
            service.logo_root = logo_root.resolve()
            # 这两个用例要的正是「内置资源真的接上了」，所以给真实的 `STUDIOS_DIR`。
            service.marks_root = brand_marks.STUDIOS_DIR
            found = service._logo_file(list(logo_root.iterdir()), f"{stem}.img")
            self.assertEqual(local.resolve(), found.resolve())

    def test_bundled_used_when_cache_empty(self):
        """干净数据目录正是内置资源要顶上的场景。"""
        sample = brand_marks.manifest()["marks"][0]
        stem = pathlib.Path(sample["file"]).stem
        with tempfile.TemporaryDirectory() as raw:
            logo_root = pathlib.Path(raw)
            service = PreviewService.__new__(PreviewService)
            service.logo_root = logo_root.resolve()
            # 这两个用例要的正是「内置资源真的接上了」，所以给真实的 `STUDIOS_DIR`。
            service.marks_root = brand_marks.STUDIOS_DIR
            found = service._logo_file([], f"{stem}.img")
            self.assertEqual(brand_marks.STUDIOS_DIR / sample["file"], found)
            self.assertEqual(sample["content_type"], service._logo_content_type(found))


class SyncScriptTests(unittest.TestCase):
    """采集脚本：判据落选要能解释，重复执行不能产生改动。"""

    def _run(self, source, target, *extra):
        script = REPO / "scripts" / "sync_brand_marks.py"
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", str(script),
             "--source", str(source), "--target", str(target), *extra],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(0, proc.returncode, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def _fixture(self, root, name, data, meta):
        (root / f"{name}.img").write_bytes(data)
        if meta is not None:
            (root / f"{name}.img.provenance.json").write_text(
                json.dumps(meta), encoding="utf-8")

    def test_filters_and_repeats_cleanly(self):
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
        with tempfile.TemporaryDirectory() as raw:
            base = pathlib.Path(raw)
            source, target = base / "logos", base / "marks"
            source.mkdir()
            self._fixture(source, "Good", png, {"source_url": "https://x.test/a.png"})
            self._fixture(source, "Good.icon", png + b"1", {"source_url": "https://x.test/b.png"})
            self._fixture(source, "NoSource", png, None)
            self._fixture(source, "Oversize", png + b"0" * brand_marks.MAX_FILE_BYTES,
                          {"source_url": "https://x.test/c.png"})
            self._fixture(source, "NotAnImage", b"<html>nope</html>",
                          {"source_url": "https://x.test/d.png"})

            first = self._run(source, target, "--apply")
            self.assertEqual(2, first["taken"])
            self.assertEqual(3, first["skipped"])
            self.assertEqual(["no_source_url", "too_large", "unknown_format"],
                             first["reasons"])
            self.assertEqual(2, first["written"])
            self.assertEqual({"Good.png", "Good.icon.png"},
                             {p.name for p in (target / "studios").iterdir()})

            second = self._run(source, target, "--apply")
            self.assertEqual(0, second["written"], "重复执行不该重写任何文件")
            self.assertEqual([], second["removed"])

    def test_drops_files_outside_the_manifest(self):
        """目录里只留清单登记的那些：厂牌改名后旧名字那份不清掉就会永远留在仓库里。"""
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
        with tempfile.TemporaryDirectory() as raw:
            base = pathlib.Path(raw)
            source, target = base / "logos", base / "marks"
            source.mkdir()
            self._fixture(source, "OldName", png, {"source_url": "https://x.test/a.png"})
            self._run(source, target, "--apply")
            (source / "OldName.img").unlink()
            self._fixture(source, "NewName", png, {"source_url": "https://x.test/a.png"})
            result = self._run(source, target, "--apply")
            self.assertEqual(["OldName.png"], result["removed"])
            self.assertEqual({"NewName.png"},
                             {p.name for p in (target / "studios").iterdir()})

    def test_a_variant_with_the_bare_files_bytes_is_not_shipped_twice(self):
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
        with tempfile.TemporaryDirectory() as raw:
            base = pathlib.Path(raw)
            source, target = base / "logos", base / "marks"
            source.mkdir()
            for name in ("Baked", "Baked.icon", "Baked.logo"):
                self._fixture(source, name, png, {"source_url": "https://x.test/a.png"})
            result = self._run(source, target, "--apply")
            self.assertEqual(["same_as_base"], result["reasons"])
            self.assertEqual({"Baked.png"}, {p.name for p in (target / "studios").iterdir()})

            service = PreviewService.__new__(PreviewService)
            service.logo_root = base / "empty"
            service.marks_root = target / "studios"
            for variant in LOGO_VARIANTS:
                found, _type = service.logo("Baked", variant)
                self.assertEqual(target / "studios" / "Baked.png", found)

    def test_dry_run_writes_nothing(self):
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
        with tempfile.TemporaryDirectory() as raw:
            base = pathlib.Path(raw)
            source, target = base / "logos", base / "marks"
            source.mkdir()
            self._fixture(source, "Good", png, {"source_url": "https://x.test/a.png"})
            result = self._run(source, target)
            self.assertEqual(1, result["taken"])
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
