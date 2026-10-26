#!/bin/sh
# 让 http(s)://peach.local/ 不带端口也能打开。
#
# macOS 和其他 Unix 一样，1024 以下的端口只有 root 能监听。Peach 在 macOS 上跑在
# 非特权的 8900（见 peach.tray.MACOS_PORT），所以 `peach.local:8900` 通、
# `peach.local` 不通。
#
# 与其让整个服务以 root 跑，不如让内核把 80 转到 8900：pf 的 rdr 规则，服务本身
# 还是普通用户进程。这是 macOS 上的标准做法，也是唯一不需要提权跑 Python 的做法。
#
# **`rdr-anchor` 必须落在 translation 段**。pf 要求规则严格按
# options → normalization → queueing → translation → filtering 排列，
# 把它追加到 /etc/pf.conf 末尾会落在 `anchor "com.apple/*"`（filtering）之后，
# 报 `Rules must be in order: ...`。所以插在最后一条 rdr-anchor/nat-anchor 之后。
#
# 改 /etc/pf.conf 之前先在临时文件上 `pfctl -n -f` 验证：这个系统文件写坏了，
# 开机时整个包过滤都加载不起来。install 同时也是修复——它会先剥掉本脚本此前写入的行
# 再重建。
#
#   sudo sh scripts/setup_macos_port80.sh install
#   sudo sh scripts/setup_macos_port80.sh uninstall
#   sh scripts/setup_macos_port80.sh check       # 只验证，不需要 root
set -eu

# 和 src/peach/appid.py 的 MACOS_PF_ANCHOR 必须一致；shell 里 import 不了 Python，
# 这两行由 tests/test_tray.py 钉住。
ANCHOR_NAME="io.github.longmeidao.peach"
# 已部署机器上装着的遗留 anchor 名（peach.appid.LEGACY_MACOS_PF_ANCHORS）。留着不清
# 会让 /etc/pf.conf 里多一条指向已删除文件的 load anchor，整份包过滤规则加载不起来。
LEGACY_ANCHOR_NAMES="gg.lmd.peach"
ANCHOR_FILE="/etc/pf.anchors/${ANCHOR_NAME}"
DAEMON="/Library/LaunchDaemons/${ANCHOR_NAME}.pf.plist"
TARGET_PORT="${PEACH_PORT:-8900}"
TLS_PORT="${PEACH_TLS_PORT:-8443}"
ACTION="${1:-install}"
# 默认路由的网卡。转发目标取它的动态地址，所以换 Wi-Fi 也不用重装。
IFACE="${PEACH_IFACE:-$(route -n get default 2>/dev/null | awk '/interface:/{print $2}')}"
IFACE="${IFACE:-en0}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# 去掉本脚本写过的行（含遗留 anchor 名那几行），并把 rdr-anchor 插回 translation 段。
rebuild_conf() {
    awk -v name="$ANCHOR_NAME" -v anchor_file="$ANCHOR_FILE" -v legacy="$LEGACY_ANCHOR_NAMES" '
        BEGIN { split(legacy, stale, " ") }
        index($0, name) { next }                       # 幂等：本脚本写过的先剥掉
        { for (k in stale) if (stale[k] != "" && index($0, stale[k])) next }
        { line[++n] = $0; if ($0 ~ /^[[:space:]]*(rdr|nat)-anchor/) last = n }
        END {
            for (i = 1; i <= n; i++) {
                print line[i]
                if (i == last) printf "rdr-anchor \"%s\"\n", name
            }
            printf "load anchor \"%s\" from \"%s\"\n", name, anchor_file
        }
    ' "$1"
}

# 只剥不加：uninstall 用它把两种 anchor 名的行都从 /etc/pf.conf 里去掉。
strip_conf() {
    awk -v name="$ANCHOR_NAME" -v legacy="$LEGACY_ANCHOR_NAMES" '
        BEGIN { split(legacy, stale, " ") }
        index($0, name) { next }
        { for (k in stale) if (stale[k] != "" && index($0, stale[k])) next }
        { print }
    ' "$1"
}

# 卸掉遗留 anchor 名的 LaunchDaemon 并删掉它的 anchor 文件。那个 daemon 每次开机都
# 重新加载一份指向它自己的规则，留着就和新的抢同一个 80/443 转发。没有就静默跳过。
remove_legacy() {
    for stale_name in $LEGACY_ANCHOR_NAMES; do
        stale_daemon="/Library/LaunchDaemons/${stale_name}.pf.plist"
        launchctl bootout system "$stale_daemon" 2>/dev/null || true
        rm -f "$stale_daemon" "/etc/pf.anchors/${stale_name}"
    done
}

if [ "$ACTION" = "check" ]; then
    rebuild_conf /etc/pf.conf > "$WORK/pf.conf"
    if pfctl -n -f "$WORK/pf.conf" 2>"$WORK/err"; then
        echo "语法检查通过；install 会把它写入 /etc/pf.conf"
    else
        echo "语法检查失败：" >&2; cat "$WORK/err" >&2; exit 1
    fi
    exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "需要 root：sudo sh $0 $ACTION" >&2
    exit 1
fi

if [ "$ACTION" = "uninstall" ]; then
    launchctl bootout system "$DAEMON" 2>/dev/null || true
    rm -f "$DAEMON" "$ANCHOR_FILE"
    remove_legacy
    strip_conf /etc/pf.conf > "$WORK/pf.conf"
    cp "$WORK/pf.conf" /etc/pf.conf
    pfctl -f /etc/pf.conf 2>/dev/null || true
    echo "已移除 80/443 的重定向"
    exit 0
fi

remove_legacy

cat > "$ANCHOR_FILE" <<RULES
# 由 scripts/setup_macos_port80.sh 生成，勿手改。
#
# 转发目标是网卡地址而不是 127.0.0.1。转到回环地址时：外部设备（手机）的回包源地址
# 是回环地址、路由不回去，连不上；本机直连高位端口也会被 lo0 上残留的 rdr 状态反向
# 翻译回 :80，TCP 连得上但 HTTP 永远收不到响应。写成 (网卡) 让 pf 动态取当前地址，
# 换网络或换 DHCP 地址都不用重装。服务监听的是 0.0.0.0，收得到。
#
# translation 规则是第一条命中就生效（和 filter 的最后一条命中相反），所以 no rdr
# 的例外必须写在前面。
no rdr on lo0 proto tcp from any to any port ${TARGET_PORT}
no rdr on lo0 proto tcp from any to any port ${TLS_PORT}
# 本机自己访问走 lo0，其他设备走真实网卡，两条都要。
rdr pass on lo0 inet proto tcp from any to any port 80 -> (${IFACE}) port ${TARGET_PORT}
rdr pass on ${IFACE} inet proto tcp from any to any port 80 -> (${IFACE}) port ${TARGET_PORT}
rdr pass on lo0 inet proto tcp from any to any port 443 -> (${IFACE}) port ${TLS_PORT}
rdr pass on ${IFACE} inet proto tcp from any to any port 443 -> (${IFACE}) port ${TLS_PORT}
RULES

# 先在临时文件上验证，通过了才动系统文件。
rebuild_conf /etc/pf.conf > "$WORK/pf.conf"
if ! pfctl -n -f "$WORK/pf.conf" 2>"$WORK/err"; then
    echo "生成的 pf.conf 语法不合法，未改动系统文件：" >&2
    cat "$WORK/err" >&2
    exit 1
fi
cp /etc/pf.conf "/etc/pf.conf.peach.bak.$(date +%Y%m%d-%H%M%S)"
cp "$WORK/pf.conf" /etc/pf.conf

cat > "$DAEMON" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>${ANCHOR_NAME}.pf</string>
  <key>ProgramArguments</key>
  <array><string>/sbin/pfctl</string><string>-e</string><string>-f</string><string>/etc/pf.conf</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
</dict></plist>
PLIST

pfctl -e 2>/dev/null || true
pfctl -f /etc/pf.conf
launchctl bootout system "$DAEMON" 2>/dev/null || true
launchctl bootstrap system "$DAEMON"

echo "已把 80 -> ${TARGET_PORT}、443 -> ${TLS_PORT}（经 ${IFACE}）"
echo "  验证：curl -s --noproxy '*' -o /dev/null -w '%{http_code}\\n' http://peach.local/healthz"
echo "        curl -s --noproxy '*' -o /dev/null -w '%{http_code}\\n' https://peach.local/healthz"
echo "  移除：sudo sh $0 uninstall"
