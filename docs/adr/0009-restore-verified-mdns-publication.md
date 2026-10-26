# ADR-0009：恢复已验证的 mDNS 发布语义

- 状态：Accepted（2026-08-21 补充双机命名与 macOS 系统发布器）
- 日期：2026-08-14
- 取代：ADR-0004 中 Windows 使用 `DnsServiceRegister` 的局部决策

## 背景

迁移前的 `rm-web.py` 使用无参数 `Zeroconf()`，在全部合格网卡发布
`peach._http._tcp.local`，其主机为 `peach.local`。真实 LAN 客户端曾成功访问。

FastAPI 迁移先把发布器改成 `Zeroconf(interfaces=[address])`，但没有针对这一网络语义
变化做客户端回归。一次客户端解析失败后，又因 Steam、CrossPaste 等进程同时监听 UDP
5353，直接将共享端口认定为根因，改用 Windows `DnsServiceRegister`。该实现的注册回调和
health 均成功，但它只能可靠发布服务及真实计算机 SRV 主机，不能提供产品需要的
`peach.local` 主机记录。

## 决策

- 恢复迁移前的 `Zeroconf()` 全合格网卡监听与 `allow_name_change=True` 发布语义。
- Windows/Linux 使用 Python zeroconf 全合格网卡发布器；macOS 由系统 `dns-sd -P` / mDNSResponder
  代发，避开 launchd 主体的本地网络权限门。删除 Windows ctypes DNS-SD 分支和重复的别名响应器。
- macOS 的正式 LAN 入口为 `peach.local`，Windows 为 `peach-writer.local`；计算机名只作为诊断信息，
  不进入产品导航。
- 修改 mDNS 时必须对照已成功基线，一次只改变一个网络变量。
- 验收必须包含单元测试、运行态 health、DNS-SD 服务发现、主机名解析和另一台 LAN 客户端。
  端口枚举、成功回调、同机 health 都只能作为局部证据。

## 已拒绝方案

- **Windows `DnsServiceRegister`**：适合服务发现，但不能满足品牌主机名契约。
- **Peach 自研 UDP 5353 别名响应器**：重复 zeroconf 已成熟实现，增加协议和共存风险。
- **路由器静态 DNS**：会引入设备配置，并且把 `.local` 的 mDNS 语义混入单播 DNS。

## 后果

实现更短且恢复已知可用行为；多网卡主机可能发布到多个合格接口，但这正是本项目已验证的
Windows 运行方式。若未来需要收窄接口，必须先新增真实客户端回归测试，不能由代码整洁性
推导网络可用性。
