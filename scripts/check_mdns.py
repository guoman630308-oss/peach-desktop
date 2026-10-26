#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从终端验证 `peach.local` 到底有没有人在网上应答。

**必须从终端跑，不能做成服务自检。** macOS 的「本地网络」隐私门按进程判：终端起的
进程继承终端已获授权的身份，launchd 起的作业是另一个主体且没有弹窗可点，自己发的多播
会被静默丢弃。服务进程去探自己，探针必然收不到回应，会把好的记录判成不可达。

也不要用 `socket.gethostbyname` 代替：它走系统解析器，会命中缓存和 `/etc/hosts`，
实测在这台机器上把 `peach.local` 解析成了另一个网段的旧地址。只有直接对
`224.0.0.251:5353` 发查询、看谁回包，才是「局域网上的设备现在能不能解析到」这个问题的
真实答案。

    python scripts/check_mdns.py
    python scripts/check_mdns.py --name peach --expect 198.51.100.16
"""
from __future__ import annotations

import argparse
import socket
import sys
import time


MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353


def query(hostname: str, seconds: float) -> list[str]:
    """对 mDNS 组播发一次 A 查询，返回回包主机的地址。"""
    packet = b"\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    for label in hostname.split("."):
        packet += bytes([len(label)]) + label.encode()
    packet += b"\x00\x00\x01\x00\x01"

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.settimeout(seconds)
    responders: list[str] = []
    try:
        probe.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        probe.sendto(packet, (MDNS_GROUP, MDNS_PORT))
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                payload, sender = probe.recvfrom(2048)
            except socket.timeout:
                break
            if hostname.split(".")[0].encode() in payload:
                responders.append(sender[0])
    finally:
        probe.close()
    return sorted(set(responders))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="验证 peach.local 在局域网上可解析")
    parser.add_argument("--name", default="peach", help="mDNS 名字，默认 peach")
    parser.add_argument("--expect", help="期望应答的地址；给了就核对")
    parser.add_argument("--seconds", type=float, default=3.0)
    return parser


def run(args: argparse.Namespace) -> int:
    hostname = f"{args.name}.local"
    responders = query(hostname, args.seconds)
    if not responders:
        print(f"{hostname}：无人应答")
        print("  服务在跑却无人应答时，先查「本地网络」权限，或确认走的是 dns-sd 后端")
        return 1
    print(f"{hostname}：{'、'.join(responders)}")
    if len(responders) > 1:
        # 同一个名字两台机器都在广播。不会报错，只是有一边按名字访问不到。
        print("  撞名：多台机器在广播同一个名字，一次只开一台，或用 PEACH_MDNS_NAME 换名")
        return 1
    if args.expect and args.expect not in responders:
        print(f"  期望 {args.expect}，实际不在应答列表里")
        return 1
    return 0


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    sys.exit(main())
