# amane 的 Watcher 双通道设计（文件发现从「拉」改「推」的取证）

- 取证日期：2026-09-22
- 上游：<https://github.com/sqzw-x/amane>，GPL-3.0
- 固定 revision：`79ecfa763cc786318e1964a3d7f4e244a7d5c96d`
- 取证方式：按固定 revision 读 `src/amane/scheduler/`、`src/amane/library/`、`src/amane/api/`
  与 `docs/dev/watcher.md`、`docs/user/libraries.md`；只读源码，**只借设计，不引代码**（GPL 与
  Peach 的 AGPL 不混）

**本文不登记进 `docs/reference-sources.json`**：它是对一个固定 revision 下十来个源码文件的一次性
只读实证，不是需要跟踪漂移的单份可变 Markdown，也没有一份与之一一对应的
`docs/reference-snapshots/upstream/` 原文。同一个 revision 已由 `amane-content-routes` 那一条登记，
要跟踪上游时按它重新克隆即可。

## 一句话

amane 把「文件怎么被发现」拆成两条互斥通道：真实文件系统用 watchdog 事件，CloudDrive 挂载用
CloudDrive2 推来的 HTTP 通知。它的价值在通道划分、云端路径纪律和事件风暴的处理；写完判定与
来源限制这两件事它没做，Peach 要自己补。

## 两条通道按来源二选一

`Library.ingest` 只有 `native` 与 `clouddrive` 两个取值（`src/amane/enums.py`）。`native` 的库挂进
进程内唯一的 `FileWatcher`；`clouddrive` 的库**不挂 Observer**，只在路由表里登记它的云端前缀。

`docs/dev/watcher.md` 给的理由是两条：FUSE 挂载上云端落盘不产生 `FileCreated`，只挂 Observer 会
静默漏；两条都开又会让同一个文件入库两次。

对 Peach 成立的是同一件事：`R:` 是真本地盘，`A:`／`B:` 是 CloudDrive 挂出来的网盘。遍历后两者就是
走网络，把它们挂上递归监视等于让 CloudDrive 把整棵树替我们拉一遍。Peach 的两条通道按 `location`
天然不重叠：本地通道只订阅 `local` 的数据库路径前缀，云端通道只接 `115` 与 `pikpak`。

## 本地通道

- 库是 `watchdog`，上游写的是下限约束 `>=6.0.0`。
- 每个库一个 handler 实例，事件自带归属，不靠路径前缀反推。
- 只实现 `on_created`、`on_deleted`、`on_moved`，**没有 `on_modified`**。
- 递归与否跟着库的 `recursive` 走，默认递归。
- `on_deleted` 不区分文件与目录，一律按路径前缀处理：注释写明原因是 Windows 的删除通知不区分
  这两者。
- 忽略规则是**扩展名白名单**（`MEDIA_EXTENSIONS`）加黑名单正则、预告片正则和体积下限，
  没有 `.part`／`.!qB`／`.crdownload` 这类下载中后缀的显式名单——它靠白名单隐式挡住，
  等下载器改成最终扩展名时的 rename 产生 `on_moved` 才收。
- 正则逐条编译而不是用 `|` 拼接：用户写的全局旗标出现在拼接中间会让整体编译失败。
- `use_polling` 是进程级开关，给 NAS／NFS、macOS 上的 VirtioFS、WSL2 和 inotify watch 超限那几种
  场景；它不能按库混用。

## 去抖与事件风暴

- 窗口默认 3.0 秒，可配区间 `[0.5, 30.0]`。
- 聚合键是**路径字符串**，分放在四张 dict 里（新增、文件删、目录前缀删、移动）。同一路径重复
  事件只刷新时间戳，天然合并。
- 摘取不是定时器，是一条每 1.0 秒醒一次的循环，把超过窗口的条目取出来。
- 目录删除时把该子树下所有待处理的新增、删除、移动、目录删除**整批丢弃**；已有更外层目录待删
  时连这条也不记。这是它对付「删一整个目录产生上万条事件」的办法。
- 同一窗口内刻意按「目录前缀删 → 文件删 → 移动 → 新增」的顺序跑：窗口内被重建的文件要在清索引
  之后重新登记。
- 队列**无上限**，dict 无界。下游抓取任务另有 `asyncio.Semaphore(3)` 限流。

## 写完判定：上游没有

amane 没有做任何写入完成判定——没有大小稳定轮询，没有 `close_write`（watchdog 不跨平台提供），
没有重试。创建事件后等 3 秒防抖就直接登记。

它对慢速网盘的处理不是等待而是换通道：`clouddrive` 的库完全不听文件系统，只信 CloudDrive2 的
通知，因为发通知时落盘已经完成。

**Peach 要自己补这一条。** Peach 的本地通道监视的是真实落盘目录，几十 GB 的文件在写入过程中每一秒
都有事件；3 秒防抖加上没有大小检查，会把一个半截文件按当时的大小登记进账本。Peach 的判据是
「最后一次事件之后静置 N 秒，再隔一个窗口复测大小，两次相同才算写完」。

## 云端通道的请求形态

- 路径 `POST /api/webhooks/clouddrive`，**任何情况都立即返回 204**，处理放后台。
  上游文档写明理由：避免 FUSE 子树扫描堵住 CloudDrive。
- 请求体是 JSON：

  ```json
  {"data": [{"action": "create", "is_dir": "false",
             "source_file": "/115/影视/x.mp4", "destination_file": ""}]}
  ```

- `action` 只有 `create`／`delete`／`rename` 三种，**没有独立的 move**：跨目录移动由 CloudDrive2
  报成 `rename`，靠 `source_file` 与 `destination_file` 分属不同库来识别。
- 容错都写在校验里，这几条值得照做：`action` 先 `strip().lower()`；`is_dir` 同时接受 JSON 布尔与
  字符串（`"true"`／`"1"`／`"yes"` 与 `"false"`／`"0"`／`"no"`／`""`），因为 CloudDrive2 的模板把
  `{is_dir}` 渲染成字符串；顶层未知字段一律忽略（CloudDrive2 还会发 `device_name`、`event_name`、
  `send_time`）；`source_file` 空白的条目直接丢掉。
- 鉴权与它其余 API 相同：`Authorization: Bearer <token>`，`hmac.compare_digest` 常数时间比较。
  **没有独立的共享密钥，没有签名，令牌也不放 query。**

CloudDrive2 那一侧要填的东西（出自上游 `docs/user/libraries.md`，它自己声明与 CloudDrive2 无隶属
关系、菜单名以当前版本为准）：网页「设置 → Webhook → 添加 Webhook」，或改配置目录里的
`webhook.toml` 的 `[file_system_watcher]` 段，`url`／`method`／`enabled`／`body` 四项加一张
`[file_system_watcher.headers]` 自定义头表。**需要 CloudDrive2 会员。**

## 云端路径不进 pathlib

这一段对 Peach 最直接。

- 配置项是 `Library.cloud_path`，`clouddrive` 时必填且不能是 `/`；本地那一侧仍有可扫描的
  `Library.path`。
- 归一化：Unicode NFC → `strip()` → 反斜杠一律换成 `/` → 折叠连续 `//` → 必须以 `/` 开头 →
  拒绝 `.` 与 `..` 段 → 去尾斜杠。空串非法。
- 匹配是「相等或以 `前缀 + "/"` 开头」，多个库取**最长前缀**；创建与更新时拒绝两个库使用相同或
  互为前缀的 `cloud_path`，把冲突挡在配置期。
- 换算成本机路径的写法是：在**纯字符串**层面切出相对段，再 `Path(local_root).joinpath(*rel.split("/"))`。
  全程只有本机根是 `Path`，云端路径是 `str`。

为什么不能用本机 pathlib 解析，上游代码与文档都明说了：`source_file` 是 CloudDrive 的**虚拟路径**
（POSIX，以 `/` 分段），不是宿主挂载路径；Windows 上同样是这套 VFS 字符串，不允许用本机
`pathlib.Path` 去解析。CloudDrive2 模板里另有 `{mount_point}` 表示宿主挂载点，这条通道不读它。

在 Windows 上 `Path("/115/影视/x.mp4")` 会被当成当前盘的根下路径，`\` 也算分隔符；在 macOS 上它
又是一条真实存在语义的绝对路径。两种都能拼出一条看着像样、指向别处的路径，而错误要等到有人点开
那条资产才会暴露。

## 汇合之后做什么

- 文件 `create`：只对这一条路径跑登记，先按路径查重，已存在直接跳过（幂等靠路径唯一索引）。
- 目录 `create` 或目录移入：收集成一批，等防抖窗口过去再对**那一棵子树**跑扫描，不是整库刷新。
  等待的理由是目录事件可能早于目录缓存列出文件。
- 目录 `delete`：按索引前缀删，不遍历磁盘——对网盘挂载这是必须的。
- 目录 `rename`：改写索引里的路径前缀；跨库时按目标库的规则重新判定。
- watcher 层**没有任何重试**：拿不到事件循环就丢事件，路径非法就静默丢弃。重试只存在于下游任务层。
- native Observer 启动失败只停掉文件监视，已登记的云端路由与 webhook 接收照常保留。

## 定期全量扫描：上游没有内建

amane 的 cron 只跑用户自己配的清理、重抓一类例程，不含库扫描；它的兜底说法是人工触发刷新。

第三方项目 `zfhxi/partial-path-scanner` 的 README 记录了 CloudDrive2 v0.8.5 的限制：受 115 限制，
离线下载、重命名文件（重命名文件夹支持）、网盘内拷贝文件（拷贝文件夹支持）不支持跨设备同步；
跨设备文件变更需开启目录缓存本地持久化，且**可能存在遗漏**。另有 `cloud-fs/cloud-fs.github.io#554`
报告 CloudDrive2 网页端的文件操作不触发 Windows 的 `FileSystemWatcher`，与官方功能页的说法相左。

这两条正是 Peach 保留定期全量扫描的硬理由：推送只缩短发现延迟，不承担完整性。

## CloudDrive2 官方 webhook 文档：未取得

2026-09-22 查过官方 <https://www.clouddrive2.com/features.html> 与
<https://www.clouddrive2.com/help.html>。功能页讲的「文件变更通知」是操作系统级事件（让挂载盘也能
被 `FileSystemWatcher`／`FileNotify` 侦测到），不是 HTTP 回调；帮助页的章节里不含 webhook、通知回调
或 `webhook.toml`，其中提到的「文件系统监视器」是备份任务用来侦测本地文件夹变化的。

**官方站点上关于 `webhook.toml`、`[file_system_watcher]` 或 HTTP 回调的地址：未取得。**
现有可复现证据只有 amane 自己的文档与上面那个第三方项目的 README，两者给出的模板一致。

## Peach 取哪几条

| 上游的做法 | Peach 怎么用 |
| --- | --- |
| 按来源二选一，云端库不挂 Observer | 本地通道只订阅 `local` 的数据库路径前缀，`A:`／`B:` 一律不监视 |
| 云端路径全程字符串，最后一步才拼本机路径 | 前缀表加纯字符串切分，逐段拒空串、`.`、`..`、分隔符与控制字符 |
| 最长前缀路由，配置期拒绝互为前缀 | 前缀表保存时核对数据库路径前缀、拒绝重复前缀 |
| webhook 立即回、处理放后台 | 同 |
| 目录删除按索引处理，不遍历磁盘 | 事件一律不删账本行；去留仍由资源同步对账决定 |
| 事件漏发靠人工刷新 | 定期全量扫描保留为兜底，一条不删 |
| 队列无上限 | 去抖队列有待办上限，溢出计数并交回全量扫描 |
| 无写完判定 | 大小稳定两次才登记，超时放弃并交回全量扫描 |
| 无来源限制，只有 Bearer | 只收回环与局域网，独立共享密钥走自定义请求头 |
