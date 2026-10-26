"""改字工具只保存静态源文案，冲突与代码路径不能误写。"""
from pathlib import Path
import tempfile
import unittest
from peach import dev_copy


class DevCopyTests(unittest.TestCase):
    def test_library_notice_is_editable_and_other_python_files_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            package = root / "src/peach"
            package.mkdir(parents=True)
            notice = package / "library_sharing.py"
            notice.write_text('NOTICE = "上传说明"\n', encoding="utf-8")
            (package / "config.py").write_text('SECRET = "上传说明"\n', encoding="utf-8")
            candidate, = dev_copy.candidates(root, "上传说明")
            self.assertEqual(candidate["file"], "src/peach/library_sharing.py")
            dev_copy.save(root, {"original": "上传说明", "replacement": "新的说明", "candidate": candidate})
            self.assertEqual(notice.read_text(encoding="utf-8"), 'NOTICE = "新的说明"\n')
            self.assertEqual((package / "config.py").read_text(encoding="utf-8"), 'SECRET = "上传说明"\n')

    def test_jsx_text_keeps_braces_literal_and_invalid_values_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "frontend/src").mkdir(parents=True)
            path = root / "frontend/src/example.tsx"
            path.write_text('<p>示例</p>', encoding="utf-8")
            candidate, = dev_copy.candidates(root, "示例")
            dev_copy.save(root, {"original": "示例", "replacement": "文字{原样}", "candidate": candidate})
            self.assertEqual(path.read_text(encoding="utf-8"), '<p>文字&#123;原样&#125;</p>')
            for body in [[], {"original": None, "replacement": "文字"}]:
                with self.assertRaises(ValueError):
                    dev_copy.save(root, body)

    def test_save_preserves_code_and_records_the_original_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "frontend/src/application").mkdir(parents=True)
            path = root / "frontend/src/application/tutorial.js"
            path.write_text("const label='示例文字';", encoding="utf-8")
            candidate, = dev_copy.candidates(root, "示例文字")
            result = dev_copy.save(root, {"original": "示例文字", "replacement": "新的'文字", "candidate": candidate})
            self.assertTrue(result["ok"])
            self.assertTrue(result["needs_build"])
            self.assertEqual(path.read_text(encoding="utf-8"), "const label='新的\\'文字';")
            self.assertEqual(len(list((root / "build/copy-editor/backups").rglob("tutorial.js"))), 1)
            with self.assertRaises(ValueError):
                dev_copy.save(root, {"original": "示例文字", "replacement": "错误", "candidate": candidate})

    def test_ambiguous_labels_require_a_specific_unchanged_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "web").mkdir()
            path = root / "web/index.html"
            path.write_text('<p>示例</p><button>示例</button>', encoding="utf-8")
            choices = dev_copy.candidates(root, "示例")
            self.assertEqual(len(choices), 2)
            dev_copy.save(root, {"original": "示例", "replacement": "按钮", "candidate": choices[1]})
            self.assertEqual(path.read_text(encoding="utf-8"), '<p>示例</p><button>按钮</button>')
            with self.assertRaises(ValueError):
                dev_copy.save(root, {"original": "示例", "replacement": "错误", "candidate": {"file": "../secret"}})
