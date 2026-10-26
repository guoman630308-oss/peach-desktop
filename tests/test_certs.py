import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from peach.certs import (
    CERT_DAYS,
    CertificateFiles,
    ensure_certificate,
    not_after,
    reissue,
    reissue_reason,
    subject_alt_names,
)


def make_ca(tls_dir: Path) -> CertificateFiles:
    files = CertificateFiles.under(tls_dir)
    tls_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256",
         "-days", "3650", "-subj", "/CN=Test Local CA/O=Peach",
         "-keyout", str(files.ca_key), "-out", str(files.ca_cert)],
        check=True, capture_output=True)
    return files


class CertificateTests(unittest.TestCase):
    def test_openssl_hides_its_windows_console_and_preserves_output(self):
        from peach import certs
        result = subprocess.CompletedProcess(['openssl'], 0, 'certificate output', '')
        with mock.patch.object(certs.subprocess, 'run', return_value=result) as run:
            self.assertEqual(certs._openssl('version'), 'certificate output')
        expected = subprocess.CREATE_NO_WINDOW if certs.os.name == 'nt' else 0
        self.assertEqual(run.call_args.kwargs['creationflags'], expected)
        result.returncode, result.stderr = 1, 'test failure'
        with mock.patch.object(certs.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(RuntimeError, 'test failure'):
                certs._openssl('version')

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tls = Path(self._tmp.name) / "tls"
        self.files = make_ca(self.tls)
        self.addCleanup(self._tmp.cleanup)

    def test_missing_certificate_needs_reissue(self):
        self.assertEqual(
            reissue_reason(self.files, {"peach.local"}, {"127.0.0.1"}),
            "服务器证书不存在")

    def test_reissue_writes_every_requested_name(self):
        reissue(self.files, "peach.local", {"peach.local", "localhost"},
                {"127.0.0.1", "192.0.2.10"})
        names, addresses = subject_alt_names(self.files.cert)
        self.assertEqual(names, {"peach.local", "localhost"})
        self.assertEqual(addresses, {"127.0.0.1", "192.0.2.10"})
        self.assertIsNone(
            reissue_reason(self.files, {"peach.local"}, {"127.0.0.1", "192.0.2.10"}))

    def test_reissue_stays_under_apples_398_day_ceiling(self):
        """Apple 从 2020-09 起拒绝有效期超过 398 天的 TLS 服务器证书。

        iOS 上即使根证书已被完全信任也照样报「不受信任」，而且错误信息看不出是有效期
        的问题——所以这条必须由测试守住，不能靠记忆。
        """
        reissue(self.files, "peach.local", {"peach.local"}, {"127.0.0.1"})
        expiry = not_after(self.files.cert)
        self.assertIsNotNone(expiry)
        span = (expiry - datetime.now(timezone.utc)).days
        self.assertLessEqual(span, 398)
        self.assertGreater(span, 360)
        self.assertEqual(CERT_DAYS, 397)

    def test_a_new_address_triggers_reissue(self):
        """局域网地址随 DHCP 变化，证书的 SAN 是签死的——这正是要自动化的那一步。"""
        reissue(self.files, "peach.local", {"peach.local"}, {"127.0.0.1", "192.0.2.10"})
        self.assertIsNone(
            reissue_reason(self.files, {"peach.local"}, {"127.0.0.1", "192.0.2.10"}))
        reason = reissue_reason(self.files, {"peach.local"}, {"127.0.0.1", "198.51.100.7"})
        self.assertIn("198.51.100.7", reason or "")

    def test_near_expiry_triggers_reissue(self):
        reissue(self.files, "peach.local", {"peach.local"}, {"127.0.0.1"})
        soon = datetime.now(timezone.utc) + timedelta(days=CERT_DAYS - 5)
        reason = reissue_reason(self.files, {"peach.local"}, {"127.0.0.1"}, now=soon)
        self.assertIn("到期", reason or "")

    def test_ensure_keeps_the_ca_untouched(self):
        """只重签叶子证书。连 CA 一起换的话，每台设备都要重新装一遍信任。"""
        before = self.files.ca_cert.read_bytes()
        ensure_certificate(self.tls, "peach.local", {"192.0.2.10"})
        self.assertEqual(self.files.ca_cert.read_bytes(), before)
        names, addresses = subject_alt_names(self.files.cert)
        self.assertIn("peach.local", names)
        # 回环永远带上：健康检查走它，换网络也不受影响。
        self.assertIn("127.0.0.1", addresses)
        self.assertIn("192.0.2.10", addresses)

    def test_ensure_is_a_no_op_when_the_certificate_already_covers_everything(self):
        ensure_certificate(self.tls, "peach.local", {"192.0.2.10"})
        first = self.files.cert.read_bytes()
        self.assertIsNone(ensure_certificate(self.tls, "peach.local", {"192.0.2.10"}))
        self.assertEqual(self.files.cert.read_bytes(), first)

    def test_ensure_does_nothing_without_a_local_ca(self):
        """没有 CA 就没法签。这不是错误——macOS 那份可以只跑 HTTP。"""
        empty = Path(self._tmp.name) / "empty"
        empty.mkdir()
        self.assertIsNone(ensure_certificate(empty, "peach.local", {"192.0.2.10"}))


if __name__ == "__main__":
    unittest.main()
