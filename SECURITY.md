# 安全说明

Peach is a single-user, self-hosted application: one deployment serves one person, with no accounts, roles or tenant isolation. The threat model covers one thing only: other devices on the same LAN being let in by accident. Browser access is protected by an optional access password (scrypt-hashed, login attempts limited per client); scripts use an internal token under `<data root>/secrets/`, and `peach serve` refuses to start on a non-loopback address when it has no token. Source deployments serve HTTPS with a self-signed local CA (only the CA certificate is ever distributed); the standalone Windows package serves plain HTTP on the LAN. Do not expose Peach to the public internet through port forwarding or a reverse proxy: there is no audit log and no second factor. The built-in Cloudflare entry refuses to start without an access password and is meant for temporary previews. Report vulnerabilities through the repository's GitHub **Security** tab (**Private vulnerability reporting**), not a public issue. The rest of this document is in Chinese.

## 威胁模型

Peach 是单人自托管应用：一个部署只服务一个人，没有账号体系、没有权限分级，也没有多租户隔离。

Linux 无桌面部署的配置管理接受经过鉴权的回环与私有网络连接，拒绝公网、链路本地、保留地址和携带转发头的管理请求。地址分类使用 Python 标准库。容器端口默认发布到宿主机回环，部署方式见 [Docker 部署](docs/DOCKER.md)。

能打开 Peach 的人，就能看到整个馆藏，也能做任何修改。Peach 只防一件事：同一局域网里的其他设备被无意放进来。多人共用和云端托管不在考虑范围内。

## 访问密码

访问密码决定浏览器打开 Peach 时要不要登录。

- 首次设置页可以设访问密码，也可以留空。之后在配置页「网络与访问 → 访问密码」里修改或关闭。
- **不设密码时，任何能连到 Peach 的设备打开地址就能进入。** 局域网里有其他人时，请务必设置。
- 密码长度 8–256 个字符。修改密码需要输入当前密码；关闭密码还要勾选确认访问范围。
- 登录时勾选「保持登录」可保持 30 天；不勾选只在这次浏览器会话内有效，最长 12 小时。刷新页面不会延长有效期。
- 修改密码后，其他浏览器的登录全部失效，当前设备保持登录。
- 同一来源每分钟最多尝试登录 10 次，超出后要等一分钟。
- 密码以 scrypt 哈希保存在本机 `<Peach 数据目录>/secrets/access.json`，不进数据库，也不参与任何同步。

没有 `access.json` 的已有部署，登录页使用下面的访问口令。

## 访问口令

访问口令供脚本和 API 调用使用，和访问密码是两套凭据。

- 口令保存在 `<Peach 数据目录>/secrets/auth-token`，由 `peach init` 自动生成。
- `peach token` 查看口令，`peach token --rotate` 换一个新的，换完要重启服务。换口令会让旧口令和用旧口令登录的会话失效；用访问密码登录的浏览器不受影响。
- 读取顺序是 `--token` 参数、`PEACH_TOKEN` 环境变量、口令文件。不要长期用 `--token <口令>` 传递，同一台电脑上的其他程序能从进程列表看到命令行。口令也不写进 `config.toml`，因为那份文件可能会被贴进 issue。
- 脚本用 `X-Token` 请求头携带口令。`?t=<口令>` 查询参数也能用，但口令会留在访问日志、代理日志和浏览历史里，只在一次性场合用。
- **`peach serve` 监听非回环地址却没有口令时，会直接拒绝启动**，而不是只打印警告：托盘在后台启动服务，警告没人看得见，服务却已经对整个局域网开放了。只监听 `127.0.0.1` 时可以没有口令。
- 开启两台电脑之间的复制时，两台要用同一份口令文件，复制过去即可。

口令与密码的比对都用常量时间比较。

## 网络暴露

- `peach serve` 默认只监听 `127.0.0.1`，地址和端口在 `<Peach 数据目录>/config.toml` 的 `[server]` 段。只有把 `host` 改成 `0.0.0.0` 或某个局域网地址，同一网段的设备才能访问。独立测试包在首次设置里选「同一局域网的设备」时监听 `0.0.0.0`。
- 源码部署使用 HTTPS，证书由本机自签的 CA 签发，不用公开 CA。`peach init` 在 `<Peach 数据目录>/secrets/tls/` 生成 CA 与服务器证书。服务器证书有效期 397 天（Apple 对 TLS 服务器证书的上限是 398 天）；本机地址变化时 Peach 用同一个 CA 重新签发，已经信任过这个 CA 的设备不用重装。
- 需要访问的设备只安装并信任 `peach-local-ca.crt`，任何私钥都不外发。安装与指纹核对方法见 [运行与配置](docs/OPERATIONS.md)。
- Windows 独立测试包在局域网里使用 HTTP，传输不加密。
- **不要用端口转发或反向代理把 Peach 直接放到公网。** Peach 没有审计日志，也没有第二道认证，这样做等于把整个馆藏和所有修改权限交出去。
- 配置页的「Cloudflare 公网入口」必须先设访问密码才能启动，只适合临时预览。长期从外网访问，请用 VPN 一类的私有通道。
- 每个响应都要求搜索引擎不收录（`X-Robots-Tag: noindex`，`/robots.txt` 禁止抓取），这一项不能关闭。

## 凭据与数据

- 站点 Cookie、令牌、访问密码和 TLS 私钥都放在 `<Peach 数据目录>/secrets/`，不进 Git、不写日志、不出现在任何 API 返回里。备份数据目录就等于备份了这些凭据，请按保管凭据的标准存放备份。
- 数据库 `<Peach 数据目录>/database/ledger.db` 记录了文件路径、演员等资料和观看记录。它和媒体一样不属于代码仓库。提交 issue、日志或截图之前，先去掉路径、局域网地址和账号名。

## 报告漏洞

请用本仓库 GitHub **Security** 标签页里的 **Private vulnerability reporting** 提交，不要开公开 issue。

报告里请写清受影响的版本或提交、复现步骤和实际影响。仓库由一个人维护，响应尽力而为，修复顺序按实际影响排。
