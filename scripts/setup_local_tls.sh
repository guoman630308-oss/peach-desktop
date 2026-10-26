#!/bin/sh
# 用**现有的**本机 CA 重签服务器证书，把缺的名字补进 SAN。
#
# CA 不动，所以已经在 Mac 钥匙串和 iPhone 上信任过的那张根证书继续有效，不用重新装。
# 换掉的只有服务器证书（叶子证书）。
#
# 为什么要重签：原来的 SAN 是 `DNS:peach.local, IP:198.51.100.162`——那个 IP 是
# Windows 那台的。于是
#   * 本机用 `https://127.0.0.1:8443` 做健康检查会因为主机名不匹配失败；
#   * 手机只能用 `https://peach.local/`，用局域网 IP 直接开一定报错。
# 补上 localhost / 127.0.0.1 / 本机当前局域网地址之后两个问题一起消失。
#
#   sh scripts/setup_local_tls.sh
#   PEACH_EXTRA_IP=198.51.100.20 sh scripts/setup_local_tls.sh   # 再多加一个地址
set -eu

# TLS 路径由两种 shell 共用的 Python 入口解析；PEACH_TLS_DIR 可显式覆盖。
PEACH_REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TLS_DIR="${PEACH_TLS_DIR:-}"
if [ -z "$TLS_DIR" ]; then
    PEACH_TLS_PYTHON="$PEACH_REPO_ROOT/.venv/bin/python"
    if [ ! -x "$PEACH_TLS_PYTHON" ]; then
        PEACH_TLS_COMMON_GIT="$(git -C "$PEACH_REPO_ROOT" rev-parse --path-format=absolute --git-common-dir)"
        PEACH_TLS_PYTHON="$(dirname "$PEACH_TLS_COMMON_GIT")/.venv/bin/python"
    fi
    [ -x "$PEACH_TLS_PYTHON" ] || { echo "找不到项目 Python：$PEACH_TLS_PYTHON" >&2; exit 1; }
    TLS_DIR="$("$PEACH_TLS_PYTHON" -X utf8 "$PEACH_REPO_ROOT/scripts/resolve_tls_directory.py")"
fi
CA_CRT="$TLS_DIR/peach-local-ca.crt"
CA_KEY="$TLS_DIR/peach-local-ca.key"
NAME="${PEACH_MDNS_NAME:-peach}.local"
# 局域网地址是**会变的**：换网络或 DHCP 续租之后证书里这条就作废了，届时用
# 局域网 IP 访问会报错（用 peach.local 不受影响）。`PEACH_LAN_IP=none` 可以不写。
LAN_IP="${PEACH_LAN_IP:-$(ipconfig getifaddr "$(route -n get default 2>/dev/null | awk '/interface:/{print $2}')" 2>/dev/null || true)}"
[ "$LAN_IP" = "none" ] && LAN_IP=""
# 397 天，不是随便挑的：Apple 从 2020-09 起拒绝有效期超过 398 天的 TLS 服务器
# 证书，iOS 上即使根证书已被信任也照样报「不受信任」。Windows 那份
# setup_local_tls.ps1 用的也是 397。CA 本身不受这条限制（它是 3650 天）。
DAYS="${PEACH_CERT_DAYS:-397}"

[ -f "$CA_CRT" ] || { echo "找不到 CA 证书：$CA_CRT" >&2; exit 1; }
[ -f "$CA_KEY" ] || { echo "找不到 CA 私钥：$CA_KEY（只有它能重签）" >&2; exit 1; }

SAN="DNS:${NAME},DNS:localhost,IP:127.0.0.1"
[ -n "$LAN_IP" ] && SAN="${SAN},IP:${LAN_IP}"
[ -n "${PEACH_EXTRA_IP:-}" ] && SAN="${SAN},IP:${PEACH_EXTRA_IP}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

cat > "$WORK/san.cnf" <<CNF
[req]
distinguished_name = dn
prompt = no
[dn]
CN = ${NAME}
O = Peach
[ext]
subjectAltName = ${SAN}
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
basicConstraints = critical, CA:FALSE
CNF

openssl req -new -newkey rsa:2048 -nodes \
    -keyout "$WORK/peach.key" -out "$WORK/peach.csr" -config "$WORK/san.cnf" 2>/dev/null
openssl x509 -req -in "$WORK/peach.csr" \
    -CA "$CA_CRT" -CAkey "$CA_KEY" -CAcreateserial \
    -out "$WORK/peach.crt" -days "$DAYS" -sha256 \
    -extfile "$WORK/san.cnf" -extensions ext 2>/dev/null

# 先验证链再落盘，免得把能用的证书换成一张坏的。
openssl verify -CAfile "$CA_CRT" "$WORK/peach.crt" >/dev/null

STAMP="$(date +%Y%m%d-%H%M%S)"
[ -f "$TLS_DIR/peach.crt" ] && cp "$TLS_DIR/peach.crt" "$TLS_DIR/peach.crt.bak.$STAMP"
[ -f "$TLS_DIR/peach.key" ] && cp "$TLS_DIR/peach.key" "$TLS_DIR/peach.key.bak.$STAMP"
cp "$WORK/peach.crt" "$TLS_DIR/peach.crt"
cp "$WORK/peach.key" "$TLS_DIR/peach.key"
chmod 600 "$TLS_DIR/peach.key"

echo "已用现有 CA 重签服务器证书（CA 未改动，已装的信任继续有效）"
echo "  SAN: ${SAN}"
openssl x509 -in "$TLS_DIR/peach.crt" -noout -serial -dates | sed 's/^/  /'
echo "  重启服务后生效：python scripts/install_macos_agent.py install"
[ -n "$LAN_IP" ] && echo "  注意：${LAN_IP} 是当前的局域网地址，换网络后需要重跑本脚本；peach.local 不受影响"
