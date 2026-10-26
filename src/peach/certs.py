"""本机 CA 签发的服务器证书：检查覆盖面，必要时用同一个 CA 重签。

局域网地址会随 DHCP 和换网络变化，而证书的 SAN 是签死的。地址一变，用 IP 访问就报
「证书无效」——手动重跑脚本能修，但那件事没有任何理由让人来记。托盘在发现本机地址
变化时调这里，重签完重启 HTTPS 服务即可。

两条硬约束：

- **CA 不动。** 只重签叶子证书，根证书的指纹不变，Mac 钥匙串和 iPhone 上已经装过的
  信任继续有效。要是连 CA 一起换，每台设备都得重新装一遍。
- **有效期必须 ≤ 398 天。** Apple 从 2020-09 起拒绝更长的 TLS 服务器证书，iOS 上即使
  根证书已完全信任也照样报「不受信任」，而且错误信息完全看不出是有效期的问题。
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


LOGGER = logging.getLogger(__name__)

#: Apple 的上限是 398 天，取 397 留一天余量（Windows 侧 setup_local_tls.ps1 同值）。
CERT_DAYS = 397
#: 快到期就提前重签，别等真的过期。
RENEW_BEFORE_DAYS = 30
#: 根证书十年有效期。它只在首次运行时生成一次，之后指纹不能变。
CA_DAYS = 3650

_SAN_DNS = re.compile(r"DNS:([^,\s]+)")
_SAN_IP = re.compile(r"IP Address:([^,\s]+)")


@dataclass(frozen=True)
class CertificateFiles:
    ca_cert: Path
    ca_key: Path
    cert: Path
    key: Path

    @classmethod
    def under(cls, tls_dir: Path) -> "CertificateFiles":
        return cls(
            tls_dir / "peach-local-ca.crt", tls_dir / "peach-local-ca.key",
            tls_dir / "peach.crt", tls_dir / "peach.key",
        )

    @property
    def complete(self) -> bool:
        return all(path.is_file() for path in (self.ca_cert, self.ca_key))


def _openssl(*args: str, stdin: bytes | None = None) -> str:
    binary = shutil.which("openssl") or "/usr/bin/openssl"
    result = subprocess.run(
        [binary, *args], input=stdin, capture_output=True,
        # 显式给 encoding：text=True 会用平台默认编码，中文输出会静默丢成空 stdout。
        text=stdin is None, encoding=None if stdin is not None else "utf-8",
        errors=None if stdin is not None else "replace",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode != 0:
        detail = result.stderr if isinstance(result.stderr, str) else result.stderr.decode(
            "utf-8", "replace")
        raise RuntimeError(f"openssl {args[0]} 失败：{detail.strip()[:200]}")
    return result.stdout if isinstance(result.stdout, str) else result.stdout.decode(
        "utf-8", "replace")


def subject_alt_names(cert: Path) -> tuple[set[str], set[str]]:
    """返回 (DNS 名字集合, IP 集合)。读不出来就当成空，调用方会据此重签。"""
    try:
        text = _openssl("x509", "-in", str(cert), "-noout", "-ext", "subjectAltName")
    except (RuntimeError, OSError):
        return set(), set()
    return set(_SAN_DNS.findall(text)), set(_SAN_IP.findall(text))


def not_after(cert: Path) -> datetime | None:
    try:
        text = _openssl("x509", "-in", str(cert), "-noout", "-enddate")
    except (RuntimeError, OSError):
        return None
    _, _, value = text.partition("=")
    try:
        return datetime.strptime(value.strip(), "%b %d %H:%M:%S %Y %Z").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def reissue_reason(
    files: CertificateFiles, names: set[str], addresses: set[str], now: datetime | None = None,
) -> str | None:
    """需要重签就返回原因，不需要返回 None。"""
    if not files.cert.is_file() or not files.key.is_file():
        return "服务器证书不存在"
    have_names, have_addresses = subject_alt_names(files.cert)
    missing = (names - have_names) | (addresses - have_addresses)
    if missing:
        return "证书未覆盖：" + "、".join(sorted(missing))
    expiry = not_after(files.cert)
    if expiry is None:
        return "读不出证书有效期"
    stamp = now or datetime.now(timezone.utc)
    remaining = (expiry - stamp).days
    if remaining <= RENEW_BEFORE_DAYS:
        return f"证书还有 {remaining} 天到期"
    return None


def reissue(
    files: CertificateFiles, common_name: str, names: set[str], addresses: set[str],
    days: int = CERT_DAYS,
) -> Path:
    """用现有 CA 重签叶子证书。链验证通过之后才覆盖旧文件。"""
    san = ",".join(
        [f"DNS:{name}" for name in sorted(names)]
        + [f"IP:{address}" for address in sorted(addresses)]
    )
    config = (
        "[req]\ndistinguished_name = dn\nprompt = no\n"
        f"[dn]\nCN = {common_name}\nO = Peach\n"
        f"[ext]\nsubjectAltName = {san}\n"
        "keyUsage = critical, digitalSignature, keyEncipherment\n"
        "extendedKeyUsage = serverAuth\n"
        "basicConstraints = critical, CA:FALSE\n"
    )
    with tempfile.TemporaryDirectory() as raw:
        work = Path(raw)
        (work / "san.cnf").write_text(config, encoding="utf-8")
        _openssl("req", "-new", "-newkey", "rsa:2048", "-nodes",
                 "-keyout", str(work / "peach.key"), "-out", str(work / "peach.csr"),
                 "-config", str(work / "san.cnf"))
        _openssl("x509", "-req", "-in", str(work / "peach.csr"),
                 "-CA", str(files.ca_cert), "-CAkey", str(files.ca_key), "-CAcreateserial",
                 "-out", str(work / "peach.crt"), "-days", str(days), "-sha256",
                 "-extfile", str(work / "san.cnf"), "-extensions", "ext")
        _openssl("verify", "-CAfile", str(files.ca_cert), str(work / "peach.crt"))

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        for source, target in ((files.cert, "crt"), (files.key, "key")):
            if source.is_file():
                shutil.copy2(source, source.with_suffix(f".{target}.bak.{stamp}"))
        shutil.copy2(work / "peach.crt", files.cert)
        shutil.copy2(work / "peach.key", files.key)
        files.key.chmod(0o600)
    return files.cert


def create_local_ca(files: CertificateFiles, days: int = CA_DAYS) -> Path:
    """建一个本机 CA。**已经有 CA 就原样返回，绝不重建。**

    参数与 `scripts/setup_local_tls.ps1` 里那套保持一致（3072 位、pathlen:0、只用于签发
    证书）；换掉任何一项都会让已经装过信任的设备需要重新装一遍。
    """
    if files.ca_cert.is_file() and files.ca_key.is_file():
        return files.ca_cert
    files.ca_cert.parent.mkdir(parents=True, exist_ok=True)
    _openssl("genrsa", "-out", str(files.ca_key), "3072")
    files.ca_key.chmod(0o600)
    _openssl("req", "-x509", "-new", "-sha256", "-key", str(files.ca_key),
             "-out", str(files.ca_cert), "-days", str(days),
             "-subj", "/CN=Peach Local CA/O=Peach",
             "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
             "-addext", "keyUsage=critical,keyCertSign,cRLSign",
             "-addext", "subjectKeyIdentifier=hash")
    LOGGER.info("已生成本机 CA：%s", files.ca_cert)
    return files.ca_cert


def bootstrap_certificates(
    tls_dir: Path, common_name: str, addresses: set[str] = frozenset(),
    extra_names: set[str] = frozenset(),
) -> CertificateFiles:
    """首次运行用：CA 缺就建，服务器证书缺或没覆盖当前地址就签。

    和 `ensure_certificate` 的分工是「谁负责 CA」：日常运行期只重签叶子证书（没有 CA
    就什么都不做，绝不悄悄换根），只有 `peach init` 允许创建那个 CA。
    """
    files = CertificateFiles.under(tls_dir)
    create_local_ca(files)
    names = {common_name, "localhost"} | set(extra_names)
    wanted = {"127.0.0.1"} | {
        address for address in addresses
        if address and not ipaddress.ip_address(address).is_loopback
    }
    if reissue_reason(files, names, wanted) is not None:
        reissue(files, common_name, names, wanted)
    return files


def ensure_certificate(
    tls_dir: Path, common_name: str, addresses: set[str], extra_names: set[str] = frozenset(),
) -> str | None:
    """证书没覆盖当前地址（或快到期）就重签。返回重签原因，没动返回 None。"""
    files = CertificateFiles.under(tls_dir)
    if not files.complete:
        return None
    names = {common_name, "localhost"} | set(extra_names)
    # 回环地址永远带上：健康检查走它，换网络也不受影响。
    wanted = {"127.0.0.1"} | {
        address for address in addresses
        if address and not ipaddress.ip_address(address).is_loopback
    }
    reason = reissue_reason(files, names, wanted)
    if reason is None:
        return None
    LOGGER.info("重签服务器证书：%s", reason)
    reissue(files, common_name, names, wanted)
    return reason
