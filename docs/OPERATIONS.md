# 运行与配置

本文讲在自己电脑上运行 Peach 时要知道的事：第一次怎么设置、配置页在哪、怎么从其他设备访问、怎么装 FFmpeg、哪些操作会真的删除文件。

用 Windows 测试包的，先看 [Windows 测试版](TESTING_DESKTOP.md)；从源码运行的，按下面的步骤安装。文末「维护者参考」是给在源码上开发、打包和发布的人看的，普通使用可以跳过。

## 从源码运行（Windows、macOS）

需要 Git 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。Python 下限为 3.14，项目由 `.python-version` 固定运行版本，uv 会自动下载缺少的解释器。

在 PowerShell 7（Windows）或 zsh（macOS）中依次执行：

1. 克隆仓库：`git clone https://github.com/peach-mitao/peach.git peach-app`。
2. 进入项目目录：`cd peach-app`。后续命令均在此目录执行。

Windows 安装依赖：`uv sync --locked --python 3.14`。完成后启动托盘：`& .\.venv\Scripts\peach-tray.exe`。

macOS 安装依赖：`uv sync --locked --python 3.14 --extra macos`。完成后启动托盘：`./.venv/bin/peach-tray`。

托盘会打开浏览器中的首次设置页。源码运行不需要下载 Windows 安装包。

## macOS 独立测试包

在 macOS 的项目目录安装构建依赖：

```sh
MACOSX_DEPLOYMENT_TARGET=11.0 uv sync --locked --extra build --extra macos
```

构建包含 Python、页面、迁移和资源的应用与 DMG：

```sh
./.venv/bin/python -X utf8 scripts/build_macos_standalone.py
```

产物在 `dist/macos.noindex/<架构>/`，构建副本避开 Spotlight 索引；日常使用只保留 Applications 里的应用，测试结束卸载已挂载的安装镜像。Apple Silicon 使用 `arm64`，Intel 使用 `x86_64`；分别用对应架构的 Python 与依赖构建。`--target-arch` 显式指定目标，解释器架构不匹配会停止，两个包不混合原生扩展，也不直接合并成 universal2。Intel 构建可在 Intel Mac 或 Rosetta 下的 x86_64 Python 环境完成；Rosetta 验收不能代替 Intel 真机和较旧 macOS 的验收。

源码开发外壳 `build_macos_app.py` 默认输出到 `dist/macos-source.noindex/`，依赖本机 venv 与 LaunchAgent；不要把它当作独立应用安装。

构建脚本读取随包 Mach-O 的系统版本下限，填入 `LSMinimumSystemVersion` 并重新签名。构建解释器也必须支持目标系统；Homebrew Python 可能要求构建机的系统版本。`watchdog` 等本机编译扩展若命中旧缓存，需带部署目标和对应的 `ARCHFLAGS`，通过 `uv sync --locked --extra build --extra macos --no-cache --reinstall-package watchdog --no-binary-package watchdog` 重编，再核对产物报告的 `minimum_macos`。

打开匹配架构的 DMG，把 `Peach.app` 拖入 Applications，再打开应用完成首次设置。菜单栏提供打开页面、重启和退出入口；数据默认保存在 `~/Library/Application Support/Peach/peach-data`，可用 `PEACH_DATA_ROOT` 指定独立目录。

安装镜像使用浅桃色背景，固定左侧 Peach、右侧 Applications 的拖放布局，隐藏工具栏与边栏，背景同时提供标准与 Retina 分辨率。许可证保留在应用的 `Contents/Resources/LICENSE`，安装窗口只显示两个入口。

版本标签触发 Release 工作流，在 Apple Silicon 与 Intel 原生 runner 分别构建，再由干净 runner 下载并挂载 DMG，验证签名、架构和独立运行。两种 DMG 与 Windows 安装包全部通过后，才一同上传到 GitHub 预发布，统一生成 SHA-256 清单。手动运行工作流只验收候选制品，不创建 Release；推送普通提交不会触发安装包构建。发版检查见[测试与依赖](TESTING.md#每次发版检查)，打标签统一使用 `scripts/release_tag.py`。

Mac 与 Windows 托盘的首层只显示简短状态与提醒数量，展开状态子菜单按行查看服务和媒体来源。长地址与路径会缩略，子菜单中的「查看完整配置」保留完整设置入口；提醒消失后会随下一轮健康检查更新。

单机设置默认关闭账本复制，不需要 Windows、SMB、LaunchAgent 或系统端口转发。各台电脑各自保存数据；共享馆藏仍按下文的[双机复制](#两台电脑之间的复制)配置，离线修改不能自动合并。已有数据目录不会自动导入。

这条构建路径使用本地 ad-hoc 签名，未完成 Developer ID 签名和 Apple 公证，不作为已签名发行包发布。DMG 只分发应用，不安装系统服务；需要集中部署系统组件时再评估 PKG。转码与抽帧使用本机 FFmpeg，本测试包未内置媒体工具、cloudflared 或 macOS 在线更新安装器。

## 首次运行与设置文件

第一次打开 Peach 有两种方式，结果相同：

- **托盘（推荐）**：运行托盘程序后，浏览器会打开首次设置页。
- **终端**：运行 `peach init`，按问答完成设置。

两种方式都会创建数据目录、准备数据库、生成本机证书和访问口令，并写出设置文件。

### 首次设置页

首次设置页会问：

1. 媒体目录：可以添加多个，旁边的「选择文件夹」会弹出系统的文件夹选择框。
2. 谁可以访问：「只有这台电脑」或「同一局域网的设备」。
3. 端口。
4. 访问密码（可选）：留空时，能连到 Peach 的设备不用登录就能进入。
5. 是否立即扫描。

提交后进入 Peach，右下角会出现一份安装教程，列出馆藏、采集凭证、浏览器历史、关注、关注凭证和复核几项。每一项都可以跳过（短时间内能撤销），全部完成或跳过后教程自动消失。教程进度只保存在当前浏览器里。选了导入浏览器历史的，会先打开口味页。

首次设置页由一条只监听本机回环地址的临时服务提供（默认端口 8900），提交成功后托盘自动切换到正式服务，不用重启托盘。它的端口、返回码和切换过程见「维护者参考」里的「首次设置的内部流程」。

### 终端 `peach init`

在终端里直接运行 `peach init`（不带参数）会进入问答：

1. 数据目录。
2. 一个或多个本地媒体目录：答完一个会接着问「再加一个」，直接回车结束。重复或互相包含的文件夹会被退回。
3. 谁可以访问（只有这台电脑／同一局域网的设备）。
4. 端口。
5. 局域网访问地址。

每一题直接回车就用默认值。连续三次输入无效会退出，并且不写任何文件。答完后会问「现在扫描 <目录>？」（默认是），最后打印下一步和扫描摘要。

终端问答只能添加本地文件夹。115 和 PikPak 网盘请在首次设置页或配置页添加，见下面的「CloudDrive 配置」。

给了任何参数、加了 `--no-input`，或者不是在终端里运行时，`peach init` 不问问题，直接按默认值和 `--data-root`、`--host`、`--port`、`--mdns-name`、`--mount local=/mnt/media` 这些参数生成。这样写出的设置文件带 `local`、`115`、`pikpak` 三个示例来源。已经有数据库时它不会重建，会提示改用 `--from-existing`。

### 设置文件在哪

设置文件固定是 `<Peach 数据目录>/config.toml`。Peach 数据目录（Peach 存放数据的目录）按下面的顺序找：

1. 环境变量 `PEACH_DATA_ROOT`。
2. 项目目录旁边的 `peach-data/`（最多向上找四层，所以主检出、工作树和打包出的 `dist/Peach` 都能找到同一个）。
3. 都没有，就是「未配置」。

Windows 测试包默认使用 `%LOCALAPPDATA%\Peach\peach-data`。首次设置选择其他数据目录时，默认数据目录的 `state/data-root.txt` 会记录所选绝对路径；托盘、子服务和再次启动均沿此记录读取设置。该文件只保存本机路径，Peach 数据库和媒体留在各自目录。记录不可读、路径无效或循环时会报错。

已有配置却出现首次设置页时，选择「使用已有数据目录」，填入原数据目录，例如 `E:\Peach\peach-data`。此目录须包含有效的 `config.toml` 和 Peach 数据库；连接成功后，普通启动也沿用该位置。没有位置记录的任意自选目录无法在升级时自动发现，需要选择一次；不必重新初始化或删除数据。

已经通过 `--data-root` 进入馆藏的部署，在线更新的新版本会将正在使用的目录记入空的默认默认数据目录，随后可以普通启动。默认数据目录已有配置、Peach 数据库或目录记录时，继续保留其选择。

恢复接口 `POST /api/setup/restore` 接收 `{"data_root":"绝对路径"}`，沿用首启的本机和同源守卫。成功返回完成页字段及 `restored=true`；失败返回 400，原因位于 `errors.data_root`。

同一个设置项的优先级：环境变量 > 设置文件 > 内置默认值。

### 设置文件出问题时

- **已经有设置文件**：`peach init` 会拒绝运行并返回 3，加 `--force` 才会覆盖。覆盖前先复制一份 `config.toml.bak`，它是唯一记着这台电脑配置的文件。
- **设置文件有语法错误**：`peach serve`、`peach migrate`、`peach status` 会拒绝运行，并报出文件路径和出错的行，不会悄悄用默认值跑出一个错误的状态。这时 `peach init --force` 仍然能用，是唯一的自救入口。
- **没有设置文件**：`peach serve` 照常启动，页面会提示先运行 `peach init`，`/healthz` 返回 `configured=false`。
- **`[media]` 下直接写盘符键**（例如 `R = '...'`）：`peach serve` 会拒绝启动，并提示改写成 `[media.mounts]`。

### 页面返回

侧栏媒体库名称右侧的「返回上一页」回到上一个页面。艺人、卖家、厂牌、事务所和标签名册返回时保留筛选、已载入内容及滚动位置；没有上一页时按钮不可用。

返回按钮采用无边框图标，与侧栏开合按钮共用文字色和悬停色，键盘聚焦时显示焦点环。侧栏灰色高亮标记当前选中的菜单；鼠标悬停其他菜单时保持原位，点击切换页面时对齐当前项。

### 扫描一个目录

在「管理 → 扫描与采集」点击「添加单个视频」，浏览选择或粘贴一个视频文件的完整路径，再点「添加并采集」。文件须位于已配置的媒体目录内；登记后只对该视频读取媒体属性、按设置生成预览并采集资料。处理进度与失败重试沿用「扫描与采集」任务。再次选择同一路径会复用原影片记录。

`peach scan <来源ID> [根目录]` 把一个目录里的文件登记进数据库。它只新增记录、刷新大小和修改时间，不改人工确认过的信息，也不删除记录。

目录已设置归属用途且开启「新文件沿用此规则」时，新登记的文件按最近一层规则关联；已有冲突保留，见[目录浏览](DIRECTORY_BROWSING.md)。

- 不给根目录时，扫描这个来源在 `[media.locations]` 里声明的全部目录。
- 给了根目录时，它必须在某个声明的目录之内（macOS 上是挂载点之内），否则会被拒绝，因为登记进去的路径无法换算回本机路径。
- 扫描跳过 `#recycle` 和名称以 `@` 开头的 NAS 系统目录；同名前缀的普通文件仍按文件类型处理。

### 长时任务断点续跑

后台任务把任务标识、处理范围、完成集合和计数保存在本机。服务重启时接回同一条任务记录，继续未完成的部分。用户暂停的任务保持暂停；明确停止会取消恢复。失败、限流或未取得的结果保留原有重试与冷却规则，不作为成功项目跳过。

| 任务 | 保存与恢复的单位 |
| --- | --- |
| 扫描文件 | 来源根、待遍历目录和目录内的已提交文件批次；保留文件数、字节数与字幕配对资料 |
| 读取配套资料、采集元数据与预览 | 已读取的本地证据、已落盘候选与已登记预览；保留完成影片和问题记录 |
| 拉取关注更新、历史回抓 | 固定来源集合、成功来源与已提交页码；从头回抓任务沿用本次保存的页码 |
| 新作订阅、想要资料、发现卡资料 | 已完成来源或番号、已保存资料与封面；发现卡同时保留已消耗请求与流量预算 |
| 后继任务、下载后处理 | 原有持久队列与同一条任务记录；批量资料与样张保存原始番号集合和成功项目 |
| 时间轴缩略图、媒体修复 | 已核验产物、带文件身份的检查结论与累计计数 |
| 链接检查、关注来源查找、口味读取 | 已完成链接、检索行或浏览器资料；保留结果和处理范围 |
| 文件整理与回滚 | 固定整理计划、同一批次的逐项日志与待回滚记录 |
| 资源检查、资源清理、链接清理 | 已完成来源、已处理记录与清理回执；删除前仍复核当前状态 |
| amane 桥环境准备 | `uv` 缓存和已安装依赖；中断的安装命令重新核对锁文件 |

检查点在结果成功保存后提交。中断时尚未提交的文件批次、来源页、单部影片或写入事务可以重做，已提交结果继续复用。外部请求本身无法从半个响应恢复。检查点损坏或结果文件未取得时会报告问题；此前没有保存的进度无法追溯恢复。

命令行扫描中断后，用相同来源和根目录再次运行即可接回断点；完整扫描结束会清除该断点，下一次扫描重新检查目录。`scripts/scrape_codes.py` 默认续跑同一批次，候选 CSV 和旁边的 `.resume.json` 一起保留；指定 `--out` 时沿用原输出路径。批次参数必须一致，`--no-resume` 用于开始新批次。其它探测、拼图和封面批处理继续按已保存的测量值、产物或成功日志选取待办。

### 媒体来源状态

服务在后台读取挂载目录的首个条目，空目录可读也算在线。正常来源每 600 秒检测，异常来源每 60 秒检测；首次失败隔一秒复查，连续两次失败才确认故障。提示分别说明没有权限读取、目录不存在、目标不是目录或目录读取失败。

`/api/sources` 返回周期快照和检测时间；正在检测或探测未返回时，`online` 为 `null`，页面保留来源可用状态。故障面板的「刷新状态」读取最新快照，后台按上述间隔重新探测。实际播放和扫描仍即时检查目录。`/healthz` 的 `media_mounts` 摘要与托盘显示来源名和故障原因；挂载故障不触发正常服务重启。探测线程最多四个，同一目录有未返回的探测时不重复启动。

### 在 macOS 上对应 Windows 路径：`[media.mounts]`

在首次设置或配置页中添加媒体目录，填写这台设备能读取的目录，例如 macOS 的 `/Volumes/Media`、Linux 的 `/mnt/media` 或 Docker 容器内的 `/media`。Peach 自动保存数据库所需的路径配置。已有目录改位置时，修改这一行的媒体目录；内部路径的对应关系由 Peach 保留。

以下字段说明供手动编辑配置或在多台 Peach 之间对应已有媒体目录时参考。

Peach 数据库中每部影片的 `path` 字段都使用 Windows 格式，例如 `R:\media\x`，即使服务运行在 macOS 或 Linux 上。这个值是数据库里的字符串；本机没有对应盘符时，Peach 需要用以下两项配置找到实际文件。Docker 配置见 [Docker 部署](DOCKER.md#配置文件与路径)。

- 两项使用相同的媒体来源键，例如 `local`、`115`。
- `[media.locations] local = 'R:\media'` 表示数据库中的文件路径以 `R:\media` 开头，不要求 macOS 或 Linux 存在这个目录。
- `[media.mounts] local = '/Volumes/<卷名>/media'` 表示本机实际读取的目录。数据库中的 `R:\media\x` 会转换成 `/Volumes/<卷名>/media/x`。
- 一个来源有多个目录时，两项都写数组，目录数量和顺序必须相同。

Windows 直接使用 `[media.locations]` 中的路径，`[media.mounts]` 可以留空。macOS 和 Linux 仍然使用两项配置；缺少对应实际目录时，该来源显示离线，媒体记录保留，但无法读取文件。把 `[media.locations]` 的值改成 POSIX 路径不会同步修改已有影片记录，也无法替代这一步转换。媒体来源键写错或写成盘符时，配置会被拒绝。

临时诊断时可以用环境变量覆盖，例如 `PEACH_MEDIA_MOUNTS=local=/mnt/res,115=/mnt/115`。

### 两台电脑之间的复制

`[replication] enabled` 决定这台电脑是否参与两台电脑之间的数据库复制，默认 `false`。只有一台电脑时保持关闭即可，这时所有功能都可写，`/healthz` 的 `ledger_sync` 显示 `disabled`。开启后的行为见「维护者参考」里的「两台机器之间的同步」。

### 已在运行的电脑补写设置文件

已经在运行、但还没有设置文件的电脑，用 `peach init --from-existing`：

- 它只写设置文件，不建数据库，也不动 `peach-data/` 里已有的任何文件。
- 它把当前实际生效的配置写进设置文件。推断不出的项（局域网 writer 地址、SMB 主机与账号）会留空并逐条打印出来，请用 `--writer-origin`、`--smb-host`、`--smb-user` 等参数在同一条命令里一次给全，不要事后手改。
- `--from-existing` 写出的复制开关是 `true`，全新 `init` 写的是 `false`。
- 用 `init --from-existing --force` 重写一份读不出来的设置文件时，它写的是内置默认值加环境变量，不继承旧文件的内容。命令会先打印这一点；旧文件里自定义过的 mDNS 名、writer 地址和 SMB 信息，要在同一条命令里重新给一遍。

不运行 `--from-existing` 就会使用内置默认值，至少这些会变：mDNS 名、macOS 的来源挂载表、reader 的 writer 地址与代理、SMB 主机与账号、复制开关（默认关）。Peach 数据目录、数据库路径、Windows 盘符和监听端口不变。

### 配置页

Peach 配置页地址是 `/configuration`。从管理菜单或设置弹窗的「Peach 配置」，或托盘菜单「配置 Peach」进入。页面分成「媒体库与入库」「来源与订阅」「下载渠道」「网络与访问」「应用与维护」五组。来源凭据、作品资料站点与资源搜索索引器放在「来源与订阅」，该分区也可进入采集来源与登录状态；本机端口放在「网络与访问」。

- 只有在运行 Peach 的这台电脑上、并且服务由托盘管理时才能修改。其他设备的管理菜单里没有这一项，设置里会写明要去哪台电脑改。
- 这台电脑可以用配置的 `.local` 名字、回环地址或服务绑定的 IP 打开配置页。Peach 按连接两端的 IP 判断是不是本机。
- 「选择文件夹」会在运行 Peach 的这台电脑上弹出系统的文件夹选择框（Windows 资源管理器、macOS Finder），同样只对本机开放。
- 保存后托盘会重新载入设置，本机服务会重启。
- 源码部署的托盘服务保持固定的 HTTPS 地址和端口；Windows 测试包可以在配置页修改端口。

媒体修复在「数据管理」页，订阅源在「关注管理」页的「订阅源」页签（分工见 [ADR-0050](adr/0050-settings-modal-holds-values-pages-hold-work.md)）。

常用的本机设置：

- 「通用 → 开机自启」：开机后启动 Peach、静默启动（开机后只显示托盘图标，不打开网页）、在桌面创建快捷方式。
- 「网络与访问 → Peach 代理」：选系统代理、直连或自定义地址。采集来源选「Peach 代理」时共用这里的设置，选「直接连接」则不走代理。保存后，新的采集按新设置运行。

### CloudDrive 配置

115 和 PikPak 网盘由 CloudDrive2 挂成本机的盘，Peach 把它当普通文件夹读取。挂载步骤、来源 ID、macOS 上的路径对应、缓存与读取设置，以及新文件自动入库，都在 [CloudDrive 配置与调优](CLOUDDRIVE.md)。配置页里的「CloudDrive 缓存建议」是它的摘要。

### 卸载

Windows 测试包在 Peach 配置「应用与维护 → 卸载 Peach」里卸载：

1. 先选择范围。默认保留数据；勾选「完全卸载」会同时删除设置、本地数据库、观看记录、凭据和缓存。
2. 确认后，Peach 会退出托盘、关闭自己的服务、结束仍在程序目录里运行的残留进程，再移除开机自启和整个程序目录。删除会重试三次，仍失败才报错。

哪些会保留：

- 原始媒体文件，任何情况下都保留。
- 手工复制的 `config.toml.bak` 和其他 Peach 不认识的文件。
- 完全卸载删除的是设置文件、Peach 自己生成的设置备份（`config.previous.toml`，以及 `config.toml.<说明>-<日期>-<时刻>` 形式的备份）和列出的数据库、缓存、凭据等目录。数据目录放在别处、用了目录链接，或者和媒体目录重叠时，需要自己手动检查和清理。

文件被占用而删除失败时，弹窗会给出日志位置：临时目录里的 `peach-uninstall.log`，里面写着原因和失败的路径。

源码安装不能自动卸载：先在配置页关闭开机自启、退出托盘，再按页面列出的路径手动删除。Git 工作区不会被自动删除。

### 健康检查

`/healthz` 用来确认服务是否在运行：

- 不带参数时报告服务存活，并提供数据库、迁移、配置、工具、挂载与访问安全的分项状态；挂载异常属于警告，服务仍返回 200。
- `/healthz?ready=1` 还会检查配置、页面、数据库查询和数据库迁移校验，没准备好时返回 503。
- 返回内容里的 `configured` 表示这台电脑有没有设置文件，`ledger_sync` 表示这台电脑在两机复制里的角色（`writer`、`reader` 或 `disabled`）。

源码部署的托盘（Windows，以及配置了 TLS 的 macOS）同时运行一个 HTTP 服务和一个 HTTPS 服务。HTTP 那个只负责把浏览器引到 HTTPS 地址，所以 HTTP 口的 `/healthz` 正常只能说明它自己活着。

托盘菜单的状态行会逐个列出每个服务，例如 `HTTP 正常 · HTTPS 异常（状态码 503）`，异常时附上最近一次失败的原因。

### 本机诊断

托盘「导出诊断报告」和系统诊断页「下载诊断报告」提供脱敏 ZIP。托盘导出完成后可打开目录、打开文件或关闭窗口；启动失败时，弹窗给出自动保存的报告位置。报告包含版本、系统信息、选定日志的末尾和可取得的系统检查；配置文件、凭据、Peach 数据库和媒体不进入报告。日志中的网址、本机路径、IP、邮箱和常见凭据字段会脱敏，发送前仍应检查报告内容。

独立包支持 `Peach.exe --diagnostic-report`，无需启动服务即可保存报告；指定目标文件时放在该参数后，已有文件不会覆盖。源码环境可用 `python -m peach.support_bundle` 取得同类报告。默认报告与错误日志保存在数据目录的 `logs`，目录不可写时使用系统临时目录下的 `Peach/logs`。

主线程、后台线程、未处理协程和本机浏览器错误写入 `errors-<PID>.log`；Python 可捕获的原生崩溃栈写入 `crash-<PID>.log`。Windows 独立启动失败的记录包含失败 API 名称和系统错误码。操作系统在 Python 启动前阻止程序、进程被直接终止或磁盘完全不可写时，程序自身可能无法留证，应保留系统提示和完整弹窗。

本机下载接口为 `/api/diagnostics/bundle`，需通过现有登录检查；浏览器错误接口 `/api/diagnostics/client-error` 仅接受本机同源请求，支持首启与登录页，每分钟最多记录 20 次，每次请求最多 16 KiB。

在安装 Peach 的电脑上运行 `peach doctor`，逐项查看版本、配置、Peach 数据库迁移、工具路径、数据目录写入、媒体挂载、端口和最近一次任务失败。`peach doctor --json` 输出同一份脱敏报告，供本机诊断 API `/api/diagnostics` 使用。

Peach 配置「应用与维护 → 运行信息」可打开 `/diagnostics`。库健康提供识别失败、补资料失败、缺封面、缺时长计数，展开后可打开作品、复制当前清单。每类最多返回 500 条，保留总数；没有番号的作品使用编号。失败来自最近处理任务的问题记录，暂停与未命中记录不计为失败；记录损坏时显示未取得。

minnano-av 与 javdb 使用实际解析并核对身份后落库的记录，显示上次成功时间与冷却截止。一天内有成功证据且未在冷却时显示正常；旧记录保留时间，当前状态为未取得。打开诊断不发起来源抓取。

独立包默认诊断配置端口；源码环境有完整 TLS 材料时诊断托盘的 HTTPS 入口（Windows 443、macOS 8443）。直接启动服务时，可用 `--host` 与 `--port` 指定实际监听地址。

`failed` 表示需要处理，命令退出码为 1；`warning` 或 `unknown` 保留具体原因，退出码为 0。诊断只在数据目录创建并清理临时写入探针，不执行迁移或修复。端口正在使用时只报告占用状态，进程归属需另行核对。公开 `/healthz` 只提供状态与原因，完整工具路径只在本机诊断中显示。

## 访问地址、密码与证书

### 从哪个地址打开

- **Windows 测试包**：选「只有这台电脑」时打开 `http://127.0.0.1:<端口>`；选「同一局域网的设备」时，其他设备打开 `http://<mdns_name>.local:<端口>`。测试包用 HTTP，不加密。
- **源码部署**：托盘在 80 端口提供 HTTP、在本机局域网 IPv4 地址的 443 端口提供 HTTPS，浏览器打开 `https://<mdns_name>.local`。局域网地址默认自动选择，也可以用启动参数或 `PEACH_LAN_ADDRESS` 指定。

`<mdns_name>` 是设置文件 `[server].mdns_name` 的值，Peach 会在局域网里广播这个名字。

### 访问密码与登录

- 访问密码在首次设置页或配置页「网络与访问 → 访问密码」里设置，可以留空。留空时，能连到 Peach 的设备不用登录就能进入。终端 `peach init` 默认不设访问密码。
- 已有部署如果没有访问密码设置，登录页继续使用访问口令。已登录的本机浏览器可以在配置页设置自己的密码，或明确关闭登录要求。
- 修改密码需要当前密码；关闭密码还要勾选确认访问范围。修改或关闭立即生效，其他浏览器的登录会失效，当前设备保持登录 30 天。
- 登录页勾选「保持登录」保持 30 天；不勾选只在这次浏览器会话内有效，最长 12 小时。刷新页面不会延长有效期。
- 密码以 scrypt 哈希保存在本机 `<Peach 数据目录>/secrets/access.json`，不进数据库，也不参与同步。

安全模型的完整说明见 [安全说明](../SECURITY.md)。

### 访问口令

脚本和 API 调用使用访问口令，放在 `X-Token` 请求头里。

- 非回环地址上启动服务必须有访问口令，首次初始化会自动生成。
- `peach token` 查看口令（文件在 `<Peach 数据目录>/secrets/auth-token`），`peach token --rotate` 换一个新的，换完要重启服务。
- 换口令会让旧口令和用旧口令登录的会话失效；用访问密码登录的浏览器不受影响。修改访问密码也不会影响访问口令。
- 登录由 `/login` 表单换成 HttpOnly cookie。`?t=<口令>` 查询参数只在一次性场合用，它会把口令留在访问日志和浏览历史里。

### 让其他设备信任 HTTPS 证书

源码部署用本机自签的 CA 签发 HTTPS 证书（`.local` 名字无法使用 Let's Encrypt 这类公开证书）。证书和私钥都在本机 `<Peach 数据目录>/secrets/tls/`。

1. 把 `peach-local-ca.crt` 复制到要访问的设备上。只复制这一个文件，任何私钥都不要外发。
2. 在设备上安装并信任它。iPhone／iPad 装好描述文件后，还要在「设置 → 通用 → 关于本机 → 证书信任设置」里打开完全信任。
3. 用 `openssl x509 -noout -fingerprint` 核对设备上的证书指纹与本机一致。

每台运行 Peach 的电脑有自己独立的 CA。换了一台电脑提供服务，设备上要装那台电脑的 `peach-local-ca.crt`。

Windows 上 TLS 私钥禁用了权限继承，只允许实际运行服务的账户、SYSTEM 和 Administrators 读取。

### macOS 打不开 `.local` 地址

浏览器打开 `.local` 地址返回 503、终端直连却正常时，通常是系统代理把请求截走了：代理解析不了 mDNS 名字。

在 macOS 系统代理的例外列表里加上 `*.local`、`localhost`、`127.0.0.1` 和本机局域网网段：

- 设置：`networksetup -setproxybypassdomains`
- 检查：`scutil --proxy` 输出里的 `ExceptionsList`

代理客户端重新设置系统代理后，这个列表可能被清空，需要再加一次。

## 从外网访问：Cloudflare 公网入口

配置页「网络与访问」里的「Cloudflare 公网入口」默认关闭。**必须先设置访问密码才能启动**；没有密码时它会被停掉并保持关闭。

### 临时链接

1. 设好访问密码后，点「启动公网入口」。
2. 页面显示 cloudflared 生成的随机地址 `https://<id>.trycloudflare.com`，用它访问。`https://try.cloudflare.com/` 只是介绍页。
3. 停止公网入口或退出 Peach 后，这个地址失效；下次启动会换一个新地址。

需要注意：

- 这只适合临时预览，不是稳定域名，也没有团队账号或 Cloudflare Access 这一层。
- 随机地址只保存在数据目录的状态文件里。不要把它存进书签、文档或公开配置。
- cloudflared 需要能出站访问 TCP/UDP `7844` 端口。被防火墙或代理挡住时页面会显示失败；已经显示了随机地址，不代表连接已经成功。
- Windows 测试包自带经过 SHA-256 校验的 `cloudflared.exe`。源码运行需要自己安装官方 cloudflared，Peach 按设置文件的 `tunnel.binary`、环境变量 `PEACH_CLOUDFLARED`、PATH 的顺序查找。
- 源码部署时，Cloudflare 连的是本机实际的 HTTPS 服务，并带上项目 CA 和 mDNS 名字做校验；测试包连的是本机回环的 HTTP 端口。

### 命名隧道（仅源码部署）

命名隧道的地址固定，重启后不变。Windows 测试包不支持这种方式，配置页也不会显示这一块。

1. 在 Cloudflare Zero Trust 的 Networks → Tunnels 里新建一条 Cloudflared 隧道，复制它的隧道令牌（Token）。
2. 给这条隧道加一个 Public hostname，主机名用自己的域名。Service 填本机的 HTTPS 地址：源码部署是 `https://<局域网 IP>:443`（macOS 按实际 TLS 端口）。再在 Additional settings 里把 Origin Server Name 填成 `<mdns_name>.local`，并把项目 CA 填进 CA Pool，否则本机自签证书过不了校验。
3. 在配置页「Cloudflare 公网入口」里切到「命名隧道」，填公开主机名和隧道令牌，点「保存配置」。令牌只写进设置文件的 `[tunnel] token`，页面不会再显示它，数据库、日志和 `/healthz` 里也没有它。换令牌时填新的；留空表示沿用已保存的那一份。
4. 建议在 Cloudflare Access 里给这个主机名配一条身份策略。这一层完全在 Cloudflare 后台配置，Peach 不读取也不管理；Peach 自己的访问密码仍然是启动隧道的前提，两道门同时存在。

整个站点都拒绝搜索引擎收录：每个响应都带 `X-Robots-Tag: noindex, nofollow, noarchive`，`/robots.txt` 不用登录也能读到 `Disallow: /`，主页面和登录页另有 `<meta name="robots">`。这一项不能关闭。

## 播放与转码

浏览器能直接播放的格式（例如常见的 MP4）不需要额外软件。Windows 安装版和绿色版携带 FFmpeg、ffprobe 与修复损坏 MP4 索引用的 untrunc。源码部署需要自行提供这些工具；配置页「运行信息」缺少它们时会给出下载入口。首次设置缺少 OpenSSL 时也会给出下载入口。

Peach 按下面的顺序找 FFmpeg 和 ffprobe：

1. 显式设置的环境变量。
2. 独立程序目录下的 `tools/ffmpeg/bin`。
3. 数据目录下的 `tools/ffmpeg/bin`。
4. PATH。

untrunc 按相同顺序查找，对应目录为 `tools/untrunc`。独立程序优先使用随包验证的版本；环境变量仍可显式指定工具路径。

播放时：

- 浏览器不支持的 AVI、MKV 等格式，Peach 会转成兼容的 MP4 缓存起来再播放，**不会改动原始文件**。第一次播放前会先探测文件，最长 20 秒。
- 能保留的码流尽量保留：H.264（`yuv420p`／`yuvj420p`）视频不重新编码，AAC 音频直接复制，其他音频转成 AAC。需要重新编码视频时，Windows 优先用 NVIDIA 显卡（`scale_cuda` + NVENC），失败才改用 CPU（`libx264 veryfast`）；macOS 直接用 CPU。
- 同时最多进行 2 个转码或探测，单次最长 1 小时，关掉播放页会取消对应的任务。
- 115 和 PikPak 的视频通过 CloudDrive2 挂载的 `B:`、`A:` 播放。能不能播，以在 Peach 里实际打开一个已知视频为准。

## 资源同步与删除

哪些操作会删除文件、哪些不会，请在动手前看清楚。

### 资源同步

在 CloudDrive2 或文件管理器里整理过网盘目录之后，要在「管理 → 资源同步」里手动对账一次。Peach 不会在后台悄悄删除任何记录。

- 对账分两步：先「检查文件」，再「清理失效条目」。检查只读盘、不改任何东西，每个来源报出文件已不在盘上的记录、空文件夹和读不了的目录，另外报出不再被引用的缓存。
- 对账会核对已配置的本地和网盘来源。离线的来源整个跳过；某个目录读不了时，保留它的记录并在结果里报告。
- 馆藏很大时扫描要很久，所以它作为后台任务运行，并逐个来源报告进度。
- **确认清理后，文件已不在盘上的记录分两档。** 在库条目里带个人记录（喜欢、打分、稍后看、播放列表、观看历史、寻找更好版本）的标「已消失」并保留，新版本入库时按位置、番号或「创作者 + 文件名 + 时长」接回这些记录；**其余的永久删除，含回收站里的，不进回收站，无法恢复。** 检查报告分开报两档计数，确认框写明哪一档不可撤销（ADR-0087）。删除前逐条重新确认文件确实不在，文件又出现了的记录保留并在结果里列出。
- 数据管理页的孤儿记录列表列出全部已消失条目和它们带的个人记录，可以手动接到某个文件，也可以彻底删除；彻底删除不可撤销。
- 清理只认最近一次检查的结果：之后又检查过一次，或者服务重启过，就要重新检查。
- 空文件夹自底向上删除，每个来源的根目录保留；CloudDrive 上列不出内容的空目录同样算空。
- 对账只清理没有馆藏再引用的生成文件：快照、海报、照片缩略图、转码缓存、播放分段、按文件生成的头像，以及共享的番号封面。演员等资料的头像和 Logo、采集证据、复核用的 CSV 不在自动清理范围内。
- 只清理当前数据库所在数据目录里的缓存，范围之外的一律跳过。
- 每次清理在任务中心留下一条「资源同步清理」记录。
- 条目详情里的「同步删除」只核对这一个目录：缺失的记录里带个人记录的标为已消失，其余移入回收站，两档都能在 8 秒内撤销。

### 回收站

- 移入回收站的条目可以在回收站里「还原」。
- **在回收站里「彻底删除」选中项，或者「清空回收站」，都会永久删除媒体文件和对应的馆藏记录，无法恢复。**

### 垃圾文件

「垃圾文件」复核覆盖 `local`、`115`、`pikpak` 全部来源的各种文件类型，在线网址不在其中。Windows 的 `.url` 快捷方式直接交给人工判断。

- 判断为垃圾的文件只会被移入回收站，不会直接删除。
- 点「不是垃圾」会记下这个文件的判断（可以撤销），它会出现在「已排除」视图里。这个判断只针对这一个文件，不会把整个域名或来源列入白名单。

维护脚本 `scripts/trash_junk.py` 默认生成候选清单；永久删除使用 `--purge --review-csv`，
复核 CSV 每行包含 `id`、`path`、`size`、`decision`、`confidence`、`why`。
只有 `decision=delete` 且置信度至少 0.95 的行进入删除，执行还需 `--apply --backup`。
评分只决定复核顺序，不能单独决定永久删除；文件的路径或体积变化时整批拒绝。
安装附件、网页脚本和未探测时长的推广短片也进入复核；字幕与 NFO 不进入垃圾队列。
剧情词不构成广告证据，种子、作品图集与创作者资料须按文件内容判断。

### 缓存目录

`generated/avatar-thumbs` 是头像的缩小版（长边 640），每台电脑自己生成，不参与同步也不需要备份。整个目录删掉只会让下一次打开索引页慢一些，缺失时页面显示原图。导入一批新头像后，可以运行 `scripts/build_entity_thumbs.py` 提前生成。

## 维护者参考

以下内容给在源码上开发、打包、发布和维护两台机器的人看。

### 首次设置的内部流程

- 托盘启动时读一次设置文件，由 `tray.needs_setup()` 判断：**没有 `config.toml` 且没有数据库**才算需要首次设置。不用 `PeachConfig.configured` 判断：托盘的单实例锁一启动就会在 Peach 数据目录下建出 `state/`，一次失败的启动就可能让下一次误判成已配置；反过来，没有 `config.toml` 的老部署数据库还在，不能被拖进首次设置。
- 需要设置时，托盘不构建正常的服务规格（全新机器上 `build_service_specs()` 会因为缺 TLS 材料抛 `FileNotFoundError`），而是启动 `peach serve --setup --host 127.0.0.1 --port <设置里的端口，默认 8900> --no-mdns --no-ledger-sync`，再把浏览器打开到 `http://127.0.0.1:<端口>/`。这条引导服务没有 TLS，口令强制为空，安全边界就是回环绑定地址。
- 首页是首次设置表单：`GET /` 回一张薄壳（`routes_pages.setup_shell()`），表单由页面包 `/dist/peach-pages.js` 画出，题目与提交都走下面两条 JSON 接口。完成态的入口带 `/?onboarding=1`；选了导入浏览器历史则先去 `/taste?onboarding=1`。安装教程通过专用状态接口读取各项的实际状态。
- 两条接口共用守卫（`routes_pages._setup_guard`）：已配置 404，非回环来源 403，独立包地址栏主机名不是回环写法 403；提交另查 Origin（403）与设置文件已存在（409）。两条都在 `/api/` 下，错误一律回 JSON。
  - `GET /api/setup/questions` 给出画表单要的数据：`questions` 按 `onboarding.questions()` 的顺序列出每题的 `key`、`label`、`help`（说明行列表）、`default`、`required`、`advanced`（是否折进「高级设置」）、`input`（`text`、`number`、`choice`、`folders`）、`prefix`、`suffix`，`host` 另带按页面顺序排好的 `options`，`mdns_name` 另带 `visible_when: {"host": "2"}`；另有 `media_sources`、`media_source_default`、`media_root`（非 Windows 的对应路径格，Windows 为 `null`）、`cloud`（提示、帮助链接与缺失的挂载软件 `dependencies`）、`windows`、`standalone`，以及三个开关的初始值 `access_enabled`（host 默认是局域网就开）、`scan_now`、`history_guide`。
  - `POST /api/setup` 收同名字段：`media_dir` 是 `{path, location, root}` 行列表（`location` 缺省为 `local`），`access_enabled`、`scan_now`、`history_guide` 是布尔，选了「只有这台电脑」可不传 `mdns_name`。400 回 `{"error", "errors"}`：`errors` 按题目 key，访问密码在 `access_password`，`media_dir` 与提交的行一一对应、没错的行是空串；整表不成立（超过 100 行）时只有一句，长度与行数不等。`apply` 失败回 400，错误在 `data_root`。200 回 `url`、`scan_requested`、`history_guide`、`standalone`、`redirect`（独立包为入口地址，其余为 `null`，跳转时长由前端定）与运行信息 `facts`（同 `/api/configuration` 的 `facts`）。
- 建目录（`database`、`generated`、`sources`、`state`、`secrets`、`logs`、`tools`、`review`）、迁移数据库、生成 CA 与口令、写设置文件，都由 `onboarding.apply()` 完成；CLI 和设置页调用同一个函数，只是输出方式不同。问答的题目、默认值和写文件逻辑在 `src/peach/onboarding.py`。
- 网页不展示数据库路径输入，`media_root` 为 `null`。`media_dir` 行中的 `root` 可省略或留空，服务端按媒体目录补齐；已有行提交原 `root` 时保留它。媒体目录与已有配置唯一匹配时复用原前缀，新目录自动生成前缀，并用目录名作为媒体库名称。
- Windows 上 `peach init` 写出的 `[media.locations] local` 直接是那些目录，`[media.mounts]` 为空；macOS 上配置的目录是 `R:\media`（第二个起 `R:\media2`……），本机目录按同样顺序写进 `[media.mounts] local`。复制、writer 镜像、SMB 一律不问，保持关闭或留空。
- 切换由 `tray.SetupGate` 负责，Windows 托盘与 macOS 菜单栏共用。它挂在健康轮询里（Windows 10 秒、macOS 5 秒），每轮重新 `settings_file.load_config()`；不再需要设置时，停掉引导服务，按新 Peach 数据目录构建正常规格并启动，托盘进程本身不重启。
- 独立测试包按设置在高位端口提供 HTTP：本机模式绑定 `127.0.0.1` 且不发布 mDNS，局域网模式绑定 `0.0.0.0` 并发布 `<mdns_name>.local:<端口>`；托盘打开页面和健康检查始终走回环。源码部署由 `build_service_specs(tls_dir=..., mdns_hostname=...)` 构建 HTTP 跳转与 HTTPS 入口，TLS 材料没齐就等下一轮。mDNS 名由这里传进规格，不读 import 时的值。
- 首次扫描不在引导服务里跑，因为那个进程在切换时就会被停掉。表单勾了「现在扫描」只写一个一次性标记 `<Peach 数据目录>/state/first-scan.request`（内容是来源 ID），托盘切换完成后读取并删除它，用子进程跑 `peach scan <来源>`，输出在 `<Peach 数据目录>/logs/tray-scan.out.log`。配置页保存时勾选扫描也走这个标记。
- 配置页的数据走 `/api/configuration`，保存时先把旧文件复制成 `config.previous.toml`，再写入并留重载标记，由托盘接手。文件夹选择走 `/api/pick-folder`。
- wheel 把 `web`、`migrations`、`resources` 装进 `peach/_resources`。`scripts/smoke_wheel.py` 在仓库外用安装了 wheel 的解释器运行；CI 的消费任务只下载制品，不检出源码。测试只用临时 Peach 数据目录。
- Win32 窗口创建前必须启用 Per-Monitor V2 DPI；正常操作不弹模态 MessageBox，更新检查在后台线程执行，并用 pystray 原生的非模态通知反馈。

### 桌面入口与发布

- Windows 日常入口是当前用户「启动」文件夹里唯一的 `Peach.lnk`，指向项目内的 `dist\Peach\Peach.exe`。桌面快捷方式和开机自启在配置页「开机自启」一组里开关，由 `src/peach/desktop_startup.py` 写入；`scripts/manage_tray_startup.ps1` 只用于发布包排障。
- `dist/Peach/Peach.exe` 是本机打包入口，不是可以拷走的独立发行版：托盘只打包了自己，服务进程仍由项目 venv 的 `peach.exe` 运行（`_peach_executable()` 从 exe 所在位置逐级向上找 `.venv\Scripts\peach.exe`），不要把它描述成绿色版。
- macOS 日常入口是 LaunchAgent `io.github.longmeidao.peach.tray`，由 `python scripts/install_macos_agent.py`（`install`／`status`／`uninstall`）管理。`.app` 外壳的 bundle ID 是 `io.github.longmeidao.peach.app`，80/443 的转发在 pf anchor `io.github.longmeidao.peach`。三个标识都取自 `src/peach/appid.py`；`setup_macos_port80.sh` 里的 shell 字面量由 `tests/test_tray.py` 保证一致。macOS 源码菜单栏的构建入口是 `build_macos_app.py`。
- 标识变更要在 Mac 上跑一遍下面的命令才生效，遗留的 LaunchAgent 与 pf anchor 由 `install` 自己清掉：

```bash
launchctl bootout gui/$(id -u)/gg.lmd.peach.tray || true
python scripts/install_macos_agent.py install
sudo sh scripts/setup_macos_port80.sh install
python scripts/install_macos_agent.py status
launchctl print gui/$(id -u)/io.github.longmeidao.peach.tray | grep -E '^\s+pid'
launchctl print gui/$(id -u)/gg.lmd.peach.tray            # 期望「Could not find service」
sudo pfctl -a io.github.longmeidao.peach -s nat
curl -s --noproxy '*' -o /dev/null -w '%{http_code}\n' http://peach.local/healthz
curl -s --noproxy '*' -o /dev/null -w '%{http_code}\n' https://peach.local/healthz
```

  验收四项：菜单栏只有一个 Peach 图标，`status` 报「已加载」；`launchctl print` 的 pid 就是那个菜单栏进程；`pfctl -s nat` 列出 80 → 8900、443 → 8443 两条 rdr；两条 `/healthz` 都返回 200。`peach.local` 换成本机 `[server].mdns_name` 的值。

- 构建入口是 `scripts/build_windows.ps1`：先用 `scripts/generate_brand_assets.py` 生成方形 Logo 与多尺寸 `.ico`，再构建单一的 `dist/Peach/Peach.exe`。不带参数运行是托盘，`serve`／`migrate` 运行 CLI。
- 对外测试包由 `.github/workflows/release.yml` 构建：`build_windows.ps1 -Standalone` 用 PyInstaller onedir 生成完整的 Windows 程序目录，压缩后由不检出源码的消费任务运行 `scripts/smoke_desktop.py`。冒烟测试会完成首次设置、以 `0.0.0.0` 重启、从本机局域网地址读 `/healthz`、确认 mDNS 已装配，并用访问密码登录。`v<__version__>` 标签与版本不一致会失败；制品验收通过后才创建 GitHub 预发布并附 SHA256。`workflow_dispatch` 只生成和验收制品。
- 安装包由 `build_windows.ps1 -Installer` 在同一个程序目录上调 Inno Setup 6 的 `ISCC` 编译 `scripts/installer/peach.iss`，产物是 `dist/Peach-<版本>-windows-x64-setup.exe`；找 `ISCC` 的顺序是 `PEACH_ISCC`、`PATH`、两个默认安装位置。默认安装到当前用户的 `%LOCALAPPDATA%\Programs\Peach`，可选择独立、可写且不与数据目录重叠的程序目录。覆盖安装前由随包临时运行时执行 `--installer-prepare`，通知采集任务保存进度并暂停，随后停止托盘，覆盖程序并迁移实际数据目录中的已有 Peach 数据库，最后释放安装保护。安装期间的新采集请求进入等待；安装完成或取消后，仍在运行的 Peach 会续跑，已退出则在下次启动时恢复。卸载走 `--installer-uninstall`，撤销启动项与桌面图标并保留数据。约定与 `AppId` 在 `src/peach/desktop_installer.py`。
- 独立包构建会把固定版本的官方 `cloudflared-windows-amd64.exe` 放在 `Peach.exe` 旁边，并用 `scripts/cloudflared-windows.json` 里的 SHA-256 校验。构建前运行 `scripts/fetch_cloudflared.ps1`，或给 `build_windows.ps1 -CloudflaredPath` 一个哈希相同的文件。源码运行不携带这个二进制。
- 每个包都带构建身份：`build_windows.ps1` 在调用 PyInstaller 前把 `{commit, version, built_at}` 写进 `build/windows/build-info.json`，再用 `--add-data` 放到包根，本机托盘与独立测试包共用。构建机没有 git 或源码不是检出时 `commit` 为 `null`，构建照常。`peach.buildinfo.frozen_build()` 只在 `sys.frozen` 时读它；文件缺失或格式不对返回 `None`，托盘照常启动，只是认为自己身份未取得。
- PyInstaller 的资源直接在 `sys._MEIPASS` 下，没有源码树的 `src/` 这一层；打包后的 `migrate`、Web 与品牌资源都要从这里解析，不能在 `config.py` 里固定取 `parents[2]`。
- 本机托盘会自己发现「比检出旧」并重建。判据是构建提交与本地检出的差距，不是与 GitHub 的差距：这台机器的提交先落在本地再推远端，「落后远端」永远不成立。
  - `VersionManager.build_age()` 用 `rev-list --count <构建提交>..HEAD` 数出落后多少，版本菜单显示成 `master@<HEAD> · 托盘构建 <构建提交>，落后 N 个提交`。数不出来（身份未取得、提交不在本检出历史里）按陈旧处理，重建范围收窄到 `src/peach/`。
  - 「同步开发进度」在 `ahead`／`current`／`error`／`unconfigured` 下走本地重建；健康轮询另外每 5 分钟读一次本地 HEAD 自动触发，同一个 HEAD 只自动试一次，首次设置没完成时不触发。
  - 重建流程是：完整测试 → 暂存构建 → 检查打包的迁移资源 → 备份 → 替换。任一步失败只发通知，旧托盘和服务继续运行，通知里写明卡在哪一步，日志在 `<Peach 数据目录>/logs/windows-source-sync.log`。
  - 独立测试包和源码运行的托盘不走这条路：测试包用配置页的在线更新，源码入口提示源码已是最新。
- 发布流程（定版、变更日志、打标签和什么时候该发）见「版本、更新与自我重启」。

#### 刷新源码运行态：`restart_windows_tray.py`

Windows 托盘独立于启动它的终端和智能体运行。启动、重启与异常恢复由当前会话的 Windows 桌面进程承接父身份；关闭 Codex、Claude 或终端不会停止托盘。入口先只读探测当前 Peach 数据目录的单实例锁；已有托盘时按启动参数打开页面或静默退出。首次启动从 Job 中交接时，父进程不占用单实例锁，新托盘取得锁后启动子服务。Peach 数据目录、源码路径和启动参数保持一致；未取得桌面进程或 Windows 拒绝创建时明确报错，不在启动器的私有 Job 中继续运行。

桌面进程及托盘可以保留系统管理的共享 Job。子进程由 `PROC_THREAD_ATTRIBUTE_PARENT_PROCESS` 指定的桌面进程提供 Job 继承关系，独立性按关闭启动器私有 Job 后仍存活来验收。一次性交接标记由托盘入口消费，适用于源码、目录包及 onefile 引导进程；子服务不继承该标记。创建被拒绝时仅重试一次显式脱离，错误保留 API 与系统错误码。退出 Windows 会话及系统 Job 自身结束属于系统生命周期。

独立启动取证记录调用者、桌面与子进程 PID 及 Job 成员状态、调用进程直接 Job 的原始限制值与已识别标志。失败上下文进入脱敏诊断报告；无法取得的 Job 句柄链和归属明确标为未取得，不推断 Job 所有者，也不要求管理员权限枚举系统句柄。

Windows 托盘在取得单实例锁后启动独立监视器。监视器用 Win32 进程句柄等待托盘退出，核对进程创建时间，避免 PID 复用；意外退出（包括未登记退出意图的零退出码）按 2、5、10 秒延迟恢复，5 分钟内至多 3 次。恢复保持当前 Peach 数据目录和源码入口，以静默模式启动。新托盘的监视器就绪后完成交接；导入或启动失败计入同一重试预算。手动启动的新实例会取消旧实例尚未执行的恢复。达到上限后保持停止，需检查日志后手动启动。

菜单退出、更新、卸载、外部停止消息和系统会话结束都会登记退出意图并停止监视器。正常重启由重启脚本负责拉起，监视器不参与依赖同步与换包。`tray-lifecycle.log` 每行是一条 JSON，包含 UTC 时间、实例标识、托盘及监视器 PID、启动父进程、退出意图、外部消息发送者 PID、异常退出码和恢复结果。强制结束进程时，记录的操作者为 `unknown`，不能从退出码判断是谁关闭了它。

监视器与托盘共用当前用户权限，不注册计划任务或 Windows 服务。监视器自身被结束、整棵进程树被结束或系统关机时不能自动恢复。监视器启动失败写入 `tray.log`；日志不可写时保留退出和服务清理行为。macOS 沿用 LaunchAgent 的启动行为。

适用于代码已经在检出里、只需要让运行中的托盘重新加载、不换二进制的情况。

- 不要用 Computer Use 点托盘。`python scripts/restart_windows_tray.py` 按精确的 EXE 路径找到 pystray 隐藏窗口，发送正常停止消息，等托盘自己关掉子服务，再静默启动新托盘，并确认新托盘接管了两个服务。
- 找不到唯一的窗口或退出超时就拒绝执行，绝不强杀后另起。旧托盘退出后，要等它名下的子服务退干净才起新托盘。端口被不归旧托盘管的 `serve` 进程占着时，开工前就拒绝并报出它们的 PID，因为换托盘换不掉它们。
- 托盘把自己拉起的子服务放进 kill-on-close Job：托盘被 `Stop-Process -Force` 之类强杀时，整棵 `serve` 子树一起退出，不会留下孤儿进程继续占着 80/443 跑旧代码。托盘每轮健康检查发现端口没响应会补拉；看到健康但不归自己的服务只记 warning，不接管。启动与补拉记录在 `<Peach 数据目录>/logs/tray.log`。
- `--swap-from <暂存包>` 在旧托盘退出后、新托盘启动前顺手换掉二进制，这是整个换包过程中唯一一个目标文件没有被进程占用的时机。换生产二进制请用 `deploy_windows_tray.py`，不要单独调用它。
- 找不到打包托盘的窗口时，命令自动改走源码托盘：`find_source_tray_windows()` 按同一窗口类名加「命令行含 `peach.tray`」认出源码托盘，`restart_source_tray()` 照原命令行（含 `--show`／Peach 数据目录）在托盘退出后重新启动，不换包也不备份。`--source` 显式跳过打包入口。
- 重启自动准备运行环境（ADR-0091），不用手工补依赖、跑迁移：
  - **任务闸门**：开工前只读查本机 `task_run` 里还在跑的行。追更任务（`followup_key` 非空）和定时任务重启后会续跑，放行；手动、CLI 等续跑不了的任务在跑时拒绝重启，`--force` 越过。进程已死的行只列出，不拦。
  - **依赖**：源码部署（有 `uv.lock` 与 `.venv`、能找到 uv）在旧托盘和子服务退净之后、新托盘启动之前跑 `uv sync --locked --all-extras --inexact --no-install-project`，已一致时不动。同步前还有别的进程在用项目 venv（例如 8099 调试 serve）就拒绝重启并报出 PID：Windows 上被加载的 `.pyd` 换不掉。
  - **迁移**：新托盘在第一次拉起子服务之前，对本机写者的 Peach 数据库跑 `peach migrate upgrade --yes`，`migrate` 自己先留 `ledger.pre-migrate-<时间>.db`。读者、Peach 数据库不存在、首次设置未完成时不迁移。
  - **就绪**：子服务起来后托盘再问 `/healthz?ready=1`，Peach 数据库结构与迁移不一致等未就绪项写进日志和启动通知。
  - 新托盘把这几步写进 `<Peach 数据目录>/state/runtime-prepare.json`，命令读回来一起报告。stdout 是一行 JSON，`ok` 为 false 时退出码 1：被闸门拒绝、依赖同步失败、迁移失败或服务未就绪都算。
- 托盘菜单的「重启服务」：依赖一致时只重启子服务，在子服务停着时迁移；依赖与 `uv.lock` 不一致时分离地拉起 `restart_windows_tray.py --source --force` 整体重启托盘，输出追加在 `<Peach 数据目录>/logs/tray-restart.out.log`。脚本拒绝或没起来时通知写明原因，退回只重启子服务。

#### 源码环境改字

页面地址加 `?edit` 开启文字编辑，悬停文字显示编辑范围，点文字在原位修改，回车或点别处保存、Esc 取消。
按钮和链接的文字使用贴在文字位置的输入框。「暂停编辑」可继续操作页面。
静态文案写回 `web/index.html` 或 `frontend/src` 中选定的来源；同一文字有多个位置时需选择来源。
保存前校验源文件摘要，备份位于 `build/copy-editor/backups/`。React 文案重建前端后在普通模式生效。
修改记录可从工具栏导出。界面里的馆藏数据与动态计数没有静态文案来源，不能借此修改 Peach 数据库。

工具脚本位于 `scripts/dev/`，不作为网页资产分发；冻结的发布包不提供改字端点。
演示站的 `?edit` 只保存当前浏览器的文案草稿，可导出，普通访问不应用草稿。

#### 换掉生产托盘二进制：`deploy_windows_tray.py`

适用于生产入口的 EXE 本身要换成新构建的情况。换生产入口是发布动作，执行前需要人工确认，脚本不代替这次确认。

- 在主检出里用项目 venv 的 Python 运行 `python scripts/deploy_windows_tray.py`。步骤：拒绝脏检出 → 按 HEAD 提交号在 `<Peach 数据目录>/state/source-sync-build/<commit>/` 构建 → 让暂存包自己跑一次 `migrate status` → 停旧托盘，原地替换 `dist/Peach/Peach.exe`（旧文件留成 `Peach.pre-source-sync-<时间>.exe`），起新托盘 → 用项目 CA 严格校验读取生产 HTTPS 口的 `/healthz`。
- 验收看是谁在回话。独立发行版由冻结进程响应，核对 `build_commit` 等于打包提交。源码部署由项目 venv 的进程响应（`src/peach/tray.py` 的 `_peach_executable()` 选择子服务入口），`build_commit` 为 None；这时核对生产入口文件的 sha256 等于暂存包。
- 两种验收都不看版本号：版本号一次发布才加一格，同一个版本号下有很多个构建。
- 结果打印成一份 JSON，`step` 说明停在哪一步。新托盘起不来或接管不了两个服务时，自动换回备份并重开旧托盘。`--staged <路径>` 复用已经打好的包，跳过构建。
- 不要就地构建 `dist/Peach/`：运行中的托盘占用着那个文件，PyInstaller 清目录会遇到 WinError 5。

### 两台机器之间的同步

- 「同步开发进度」走 GitHub，「同步数据库」走 SMB 共享，两者不能合并成一个按钮：任何一边连不上都不该拖住另一边。HTTP/HTTPS 服务只观察角色，不自动复制。
- `replication.enabled` 开启时：建同步观察器、探测并挂载 SMB、托盘出现两个 Ledger 菜单项、追更凭据写入共享副本。关闭时这些都不发生，`/healthz` 的 `ledger_sync` 是 `disabled`，写接口全开。
- 手动同步先用 `sync.resolve()` 只读判断这次会不会真的复制。`offline` 不是结论：macOS 重启后 SMB 共享不会自动挂回来，托盘按 `SHARED_SMB_HOST`／`SHARED_SMB_SHARE`／`SHARED_SMB_USER` 补挂一次再判断，真挂不上才报告。
- 补挂用 `osascript` 的 `mount volume`，而且必须先查钥匙串：没有对应记录时 NetFS 不报错，而是弹出认证框一直等，后台操作既卡到超时，又在用户面前弹出密码框。钥匙串记录按主机名存，同一台机器的 IP 和 mDNS 名是两条不同的记录。
- 补挂后仍是 `offline`，以及 `conflict`／`in-sync`，都直接报告，不停服务；确定要复制时才停掉自己创建的服务，用 SQLite backup API 加原子替换复制，最后恢复服务。
- 「由本机接管数据库写入」只在共享盘不可达时走捷径：macOS smbfs 上的共享源必须以 immutable 的已关闭快照读取，共享目标不能由 SQLite 直接打开事务连接，健康端口不归本托盘时拒绝接管。
- 生成的图片走 Syncthing 单向同步，和数据库、和 Git 是三条互不替代的通道：Windows send-only、Mac receive-only，五个文件夹 `snapshots`／`posters`／`avatars`／`logos`／`covers`。Mac 侧根目录在 `peach-data/artifacts/`（不要把指向它的符号链接设成同步目录），Trash Can 版本保留 30 天。
- `.stignore` 不跨设备同步，两端每个目录各放一份。方向固定，在 Mac 上生成的图片不会回到 Windows。
- 两台机器的访问口令要一致：reader 取 writer 的复核结果时发的是自己的口令。
- 两台机器的本机 CA 各自独立，`secrets` 按设计不共享。
- 双机广播名分别取各机的 `server.mdns_name`，必须不同；读取 `/healthz` 的 `mdns` 核对实际名称。`PEACH_MDNS_NAME` 只作临时覆盖。

### 版本、更新与自我重启

- 版本号只在 `src/peach/__init__.py::__version__` 里定义，采用 pre-1.0 SemVer。Git 提交是构建标识，`vX.Y.Z` 标签是发布点，推到 GitHub 就触发 Release 工作流（见「桌面入口与发布」）。
- 版本号在发布时才动，一次发布加一格，所以每个 `X.Y.Z` 都有一份能下载的制品。
- **定版**：在干净的 master 主检出上运行 `python scripts/release_tag.py --bump auto --apply`。它按上一个版本标签到 HEAD 的区间定档（`bump_part_for()`：主题以 `feat` 开头、带破坏性标记 `!`，或 `migrations/` 有新文件时推 minor，其余推 patch；1.0 要手工 `--bump major` 并另立 ADR，见 ADR-0012），写入 `__version__`，并把 `CHANGELOG.md` 的未发布一节定成这个版本号。区间只改了文档、技能和测试时直接拒绝，因为那样打出的包和上一个标签完全相同。`--bump` 只写文件不提交，并打印定版的那一节供人检查；不带 `--apply` 只打印计划。
- **发布**：确认之后运行 `python scripts/release_tag.py --ship --apply`，一次完成剩下四步：提交 `src/peach/__init__.py` 与 `CHANGELOG.md`（消息 `chore(release): 版本 <新版本>`）、推送 master、每 30 秒查一次这个提交的 Test 工作流、变绿后创建并推送 annotated tag。默认最多等 30 分钟，用 `--timeout` 修改。不带 `--apply` 只打印步骤。
- `--ship` 在动手前检查：不在 master、变更日志缺这一节、标签已被占用、工作区除了那两份定版文件还有别的改动，任何一条成立就拒绝（标签指向发布提交，夹带什么就等于发布什么）。Test 结束不是绿色就立刻停，这时标签还没打。
- 中途停下（断网、CI 还在跑、超时）就再运行一次同一条命令：每一步都先看当前状态，已提交的不重复提交，已推送的不重复推送，Test 没绿绝不打标签。
- 单独补打标签用 `python scripts/release_tag.py`：默认只检查本地与 GitHub master 一致、这个提交最新的 Test 全绿、`CHANGELOG.md` 有这个版本的一节、标签不存在；加 `--apply` 才创建 annotated tag 并只推送这个标签。适用于发布提交已推送、只差标签的情况。变更日志那一节是硬性要求，缺了先用 `--bump` 起草。版本标签不覆盖，下一版先改 `__version__`。推送失败留下本地标签时，先确认归属再人工处理，不强推。Release 工作流会再检查标签提交在 master 历史里、同一提交的 Test 已通过，然后构建、验收制品、创建预发布。打标签代表公开预发布，不等于替换本机生产入口。
- 已确认的发布提交推送后，若 master 继续推进，用 `python scripts/release_tag.py --release-sha <完整提交 SHA>` 核对固定提交的发布计划，确认输出后加 `--apply` 补打标签。这个模式从选定提交读取版本与变更日志，并在创建标签前再次核对主线归属、同一提交最新的绿色 Test 和标签占用；只推送标签，保留当前 master。它不能与 `--bump`、`--ship` 或 `--verify-sha` 合用。
- **什么时候该发**：`scripts/agent_worktree.py integrate` 不改版本号，只报当前值，并输出 `release` 字段（`scripts/changelog.py` 的 `due()`）。`due` 为真时提出发布，理由在 `why`。节奏是每周一次、条目攒够提前、破坏性变化和安全修复不等周期（`DUE_DAYS = 7`、`DUE_ENTRIES = 10`）。只数使用者看得见的变更条目，不数提交。`DUE_ENTRIES` 是没有样本时的保守起点，`due()` 每次都报出实际条目数与分组，积累几次真实发布后再校准。
- **会话收尾提醒**：`scripts/release_due.py --hook-event` 挂在 Claude 的 `.claude/settings.json` 与 Codex 的 `.codex/hooks.json` 的 `Stop` 钩子上（两边约定一致：stdin 收 JSON、stdout 回 `systemMessage`、退出码 0）。只有在主检出、在 master、工作区干净、这个 master sha 还没问过，并且 `due` 为真时才输出一句，其余情况什么都不打印。已问记录在 `<Peach 数据目录>/state/release-due.json`，master 前进后才会再问。手工查看用不带参数的 `python scripts/release_due.py`，它只读，不改记录。
- 钩子用 `uv run --no-project` 找解释器，同一条命令在两个平台都能用。`--no-project` 不能省：`release_due.py` 只用标准库；如果让 uv 识别项目，它会在一个有 `pyproject.toml` 但还没建 venv 的检出里建一个空 venv，`scripts/test.ps1` 随后会优先选中它，测试连 `filelock` 都导入不了。`tests/test_release_due.py` 会把配置里的命令原样执行一遍，并检查工作目录没有多出 `.venv`。需要 `uv` 在 PATH 上，推荐 `winget install --id astral-sh.uv`，不要把远端脚本直接管道进 shell。
- 「检查更新」只 fetch 和比较；「同步开发进度」只做 `merge --ff-only`，不 stash、不 rebase、不 `--force`：并行工作树和主检出共用同一个对象库与 reflog，任何改写历史的操作都会波及其他分支。工作区有改动或两边分叉时，原样报告交给人处理。本地不落后远端或连不上远端时，按构建身份判断打包托盘要不要重建（见「桌面入口与发布」）；这条路径不拦有改动的工作区，因为构建用的就是检出里的代码。
- 快进涉及 `tray.py`／`menubar.py`／`versioning.py`／`certs.py`／`netwatch.py`／`config.py`／`pyproject.toml` 时，只重启子服务不够，托盘要用 `launchctl kickstart -k` 重启自己：先 `stop_owned()` 再 kickstart，前提是 launchd 报的 pid 等于自己的 pid。

### 网络与服务细节

- 对本机服务的 HTTP 探测必须设 `trust_env=False`：代理客户端会设置系统级 HTTP 代理，httpx 默认通过 `urllib.getproxies()` 读取它，探测 `127.0.0.1` 的请求会被送进代理、由代理返回 503，服务活着却被判为「未运行」。实现在 `peach.tray.ServiceManager.healthy`，由 `test_health_check_never_goes_through_a_proxy` 守门，新写的健康检查同样要这样做。
- `peach serve` 按平台发布固定的 mDNS 主机名，源码里不写死任何家庭 IP，并保留 `Zeroconf()` 在所有合格网卡上监听。mDNS 验收要包含单元测试、运行时 health、DNS-SD、主机名解析和真实局域网客户端。
- FastAPI 是唯一的 Web 服务器，不要恢复平行的 `http.server` 或动态 legacy loader。
- 切换服务前检查 80、443、8900 端口和实际的进程归属。
- 长任务只能停止自己拥有、且命令行匹配的 Python/FFmpeg 进程树，禁止结束整台机器上的 FFmpeg。
- FFmpeg 解析不回退到 Stash 的私有目录。转码由 `TranscodeService` 负责，走和普通播放相同的 Range 端点；探测与每次转码都受 2 槽并发闸门、1 小时总时限和 stream session 取消保护。
- 115/PikPak 的 `B:`／`A:` 盘符对不同 Windows token 的可见性不同，最终以 Peach 对已知作品的 `/stream` 实测为准。

### 迁移、备份与运维脚本

- 真实迁移前依次执行：SQLite 备份、asset/tag 计数、`PRAGMA integrity_check`、迁移版本检查、服务冒烟测试。已应用与待应用的迁移只认 `peach migrate status` 的输出。
- 已应用的迁移文件不能修改，任何后续变更都要新增一个版本。已应用的迁移被改写会造成校验和漂移，只能用迁移前备份重放、逐条比对差异为 0 后再校正 `schema_migration`。
- 数据库备份自动清退：`ledger.pre-*.db` 由 `peach.ledger_backups` 管理，「最近 5 份」「24 小时内」「比 `ledger.db` 新」三类全部保留，其余连同 `-wal`／`-shm` 删除。Windows 托盘每次启动自动执行；`scripts/prune_ledger_backups.py` 手动运行时默认只列计划，`--apply` 才删除；数据库 `integrity_check` 不是 ok 时拒绝清退，退出码 2。每份备份一百多 MB，5 天不清就是 7 GB。
- `<Peach 数据目录>/logs` 里的 `*.log` 统一保留半年，不限大小（`peach.log_retention.sweep`，托盘在启动任何子进程之前运行）：半年没写过的文件整份删除；还在写的文件按自然月分段，上次写入在更早月份的改名成 `<名字>.until-<最后写入日期>.log`，再过半年删除。子进程直接追加 stdout，不经过 `logging`，所以按文件处理，不按行处理。
- 更新与打包产物自动清退：托盘每次启动只保留最近 2 份 `dist/Peach/Peach.pre-source-sync-*.exe` 备份，并删掉 `<Peach 数据目录>/state/source-sync-build/` 里不属于待应用记录的暂存构建（`WindowsUpdateInstaller.sweep_artifacts`）；`build_windows.ps1` 成功后删掉 PyInstaller 工作目录 `build/windows/app`；单文件托盘每次启动删掉 `%TEMP%` 里超过一天、不属于自己的 `_MEI*` 解压残留（`sweep_onefile_extractions`，托盘被结束进程时 PyInstaller 不会自己清理，每份 40–80 MB）。自动清理只认这几种命名：`Peach.exe` 本体、手工放进 `dist/` 的目录、`build/release-*`、`attic/` 都不碰，手工构建的残留要自己删。
- 可重建缓存的删除范围由当前数据库路径决定：生产库只能清理同一个 `peach-data` 下的缓存，临时库只能清理它的临时目录，范围之外一律跳过。测试里的清空回收站必须用临时 Peach 数据目录，漏配会删到真实封面。
- 资源同步使用后台作业并逐来源报告进度，避免同步全库扫描阻塞请求；同一目录只枚举一次，目录元数据并发固定为 8，不重跑全库，也不盲信旧结果。
- 运维脚本被导入时不能产生文件、网络或数据库副作用。`scrape_codes.py` 默认写可续跑的复核 CSV；`clean_names.py` 先预览，`--apply` 必须同时给出 `--backup <路径>`，备份写完后当场校验完整性（`peach.scripting.open_for_write`）。
- 追更的合集判据只作用于新抓到的候选。收紧阈值后用 `scripts/prune_follow_compilations.py` 扫描存量：默认只列清单并写一份复核 CSV，`--apply` 先备份成 `ledger.pre-follow-compilations-*.db` 再删除，已保存成 asset 的条目一律不动。没探过详情页的旧行拿不到署名，判据数不到它们。
- 文件名只按数据库里已确认的番号规范化，不从文件名重新猜番号。只改大小写时经同目录临时名中转；去广告后重名的，用 `(2)` 起的后缀两份都保留。
- `generated/cover-fetch-log.csv` 是来源、尺寸和结果的证据，不随图片一起清理。图片丢失时用 `scripts/fetch_jav_covers.py --restore-successes` 按成功记录里的原 URL 恢复并原子替换；失败的仍记为失败，不拿缩略图顶替封面。
- 封面候选必须限定在当前作品的主封面节点：作品页混有数百张关联作品、剧照和演员头像，禁止对整页图片 URL 逐张量尺寸。
- FC2CMADB 的文章存档只作为官方商品页的镜像证据：标题与保守翻译后的标签写进候选 CSV，和最新的 JAV 元数据批次一起进入 `/review`，不自动写入数据库。封面把列表的 `w276` 换成确认存在的 `w1200`，再走 `fetch_jav_covers.py --fc2-only` 的解码、宽度、磁盘和原子写入检查，失败的逐条记日志。
- 批准官方标签时必须用 `(asset_id, tag)` 冲突更新来源与置信度：`asset_tag` 的唯一约束会让 `INSERT OR IGNORE` 保留旧来源，结果值已存在却不显示「官方」。
- 「不是垃圾」写入的是按 asset 的可撤销 `junk_file` 复核决定。垃圾判断里推广名、目录与创作者位置的证据跨类型共用，视频另外参考时长、体积和同番号长版。

### 前端与验证口径

- 数据库里的元数据不能插值进 inline JavaScript 事件属性：真实厂牌名里的撇号会直接造成 Firefox 语法错误。
- 前端 API 包装必须先检查 HTTP 状态再返回 JSON：只读冲突时写接口返回 `409` 和错误 JSON，当成普通成功处理会清空选择并重载，用户只看到条目原样回来。批量处置和详情反馈必须保留当前选择并显示失败原因。
- 验证结果分开报告：静态/单元/API、桌面浏览器、390×844 手机、生产服务是否已重启。
