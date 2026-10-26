# 测试与依赖

这份文档讲三件事：依赖怎么装、怎么升级；改完代码要跑哪些测试、CI 怎么分；新测试该写成什么样。

Windows 发布制品的覆盖升级在 Actions 的「Windows 覆盖升级验收」手动执行：填写已发布的起始版本、目标版本及目标制品的完整构建提交。工作流分别验证 ZIP 原位解压和安装包原位安装，每种路径覆盖默认数据目录与 `PEACH_DATA_ROOT` 指定目录，并上传日志。它使用已发布制品，不重新构建或发布；在线更新和失败回滚仍由 `scripts/smoke_online_update.py` 单独验证。

Release 工作流还在干净的 Windows runner 上运行 `smoke_update_tasks.py --apply`，使用临时账本验证安装接受正在运行的采集任务、暂停并保留任务标识、覆盖后自动续跑，以及配置保留。脚本默认只预演；本机可以验证 ZIP 与安装助手协议，安装包实装限干净的 Actions runner。

## 环境与锁文件

依赖由 uv 按锁文件精确复现。源码安装用 `uv sync --locked`。开发、测试和构建在隔离工作树里建自己的 `.venv`，工作树不碰正在服务的环境；主检出的依赖同步与迁移由重启流程负责，见 [运行与配置](OPERATIONS.md) 的「刷新源码运行态」一节（ADR-0091）。

安装全部测试依赖（先完成前端依赖安装和构建，再开始正式验证）：

```text
uv sync --locked --all-extras
```

添加直接依赖并同时更新清单和传递锁文件；版本必须精确固定：

```text
uv add "包名==版本"
```

构建环境只选择构建依赖：

```text
uv sync --locked --extra build
```

提交 `pyproject.toml` 与 `uv.lock`。CI 用 `--locked` 拒绝过期锁文件；Dependabot 的 `uv` 生态负责更新。`uv pip install` 只用于临时环境或安装产物，不用来维护项目依赖。普通 pip 安装 wheel 的冒烟另外独立验证打包声明。

### 接手 Dependabot 的升级

Dependabot 的 PR 一律在本地接管，不在网页上合：

依赖升级须同时提交派生产物。只改 npm 清单与锁文件时，vendor 版本记录或构建结果可能与 Git 中的文件不一致，相关门槛会拒绝；这本身不代表依赖存在兼容性问题。Test 工作流先完成所选前端检查，通过后才启动 Python、浏览器与 wheel 回归。缺少产物时先本地接管重算，不能跳过一致性检查后直接合并。

- npm 的两份清单各有一层派生产物：根 `package.json` 对应 `web/vendor/**` 与 `web/index.html` 的版本注释，`frontend/package.json` 对应 `web/dist/` 的主界面与独立页面包。Dependabot 算不出这些，它的 workflow 拿到的 token 又是只读的，推不回 `dependabot/**`，所以 `npm run check:vendor` 或前端产物检查会失败。
- uv 与 github-actions 没有派生产物，但 master 在本机集成、通常领先 origin。在网页上合会让两边分叉，回并要在主检出 master 上 merge，被 `scripts/githooks/` 拒收。

每周的升级在一个隔离工作树里一次接完：

1. 运行 `scripts/adopt_dependency_bump.py --all-open --co-author '<工具> (<模型>) <厂商 noreply>'`，看它列出的 PR 与各自的清单。
2. 加 `--apply`，按编号从小到大一个 PR 一个提交：取回分支，只把它自己对清单的改动三方合并套进来，npm 重算派生产物，uv 跑 `uv lock --check`。
3. 两个 PR 改到相邻行时脚本停下，报出冲突文件并在 `build/adopt-pr-<编号>.txt` 备好提交说明。按它印出的步骤解冲突、重算、提交，再跑一次 `--all-open --apply`；已接管的 PR 按提交说明跳过。
4. 跑 `test.ps1 full`，走 ready / integrate，推送 master 后按它印出的 `gh pr close` 逐个关 PR。PR 显示为 Closed 而不是 Merged，因为提交是在本地重做的。
5. 同一轮跑 `scripts/check_reference_updates.py check --cadence weekly --diff`，查 Dependabot 管不到的上游：按 sha 钉住的 amane 是否前移，Peach 代码直接消费或逐字复制的参考来源是否变化。审阅与 `accept` 按 `peach-reference-evidence` 技能；amane 前移单开分支升级。

单个 PR 用 `--pr <编号>`，流程相同。`.github/dependabot.yml` 把每个生态的 minor 与 patch 合成一个 PR，semver-major 照常一个包一个 PR，接管前先读变更说明。需要改代码的大版本在隔离工作树完成迁移和验证；TanStack Table 的大版本也由 Dependabot 检查。

版本来自 `src/peach/__init__.py`，已纳入 uv 缓存键；源码版本更新后再次同步会刷新安装元数据。缓存规则采用 [uv 官方动态元数据机制](https://docs.astral.sh/uv/concepts/cache/#dynamic-metadata)。

### 每次发版检查

`release_tag.py --bump auto --apply` 在写版本前调用 `release_review.py`，检查两份 Python 锁、两份 npm 清单的可用更新、Dependabot 待处理 PR、sha 固定的运行依赖及其最新稳定发布，以及登记表中全部非 manual 参考来源。独立检查入口为 `scripts/release_review.py`；报告存于 `build/release-review.json`，含提交、时间、差异与逐项错误。Docker 与 GitHub Actions 的更新以 Dependabot PR 为线索，不据空 PR 列表断言全部最新。

网络失败记为「未取得」，其余检查继续；检查不全时版本文件保持原样。发现更新只报告，由智能体对照真实消费者、字段语义与许可证判断采用范围；升级依赖沿用隔离工作树与完整验证。原文证据按 `peach-reference-evidence` 审阅后接受，人工笔记不被网页覆盖。

## 验证频率

测试直接使用项目入口，不设置机器级资源槽位、CPU、内存或进程数限额。每轮并发由 `-Jobs` / `--jobs` 选择，默认按本机核数取最多四路，显式正整数不设机器级上限。取消时只按已创建的 PID 清理本轮子进程树；全量测试与集成仍按代码一致性要求互斥。

Python 静态检查使用开发依赖 Ruff，随每个正式测试域扫描全仓自有 Python 文件，覆盖应用、脚本、测试和打包入口。规则为 Pyflakes（`F`）、Ruff 支持的 Pylint 错误规则（`PLE`），以及可变默认参数、`finally` 跳转、无效表达式和循环闭包规则（`B006`、`B012`、`B018`、`B023`）。生成的 `downloads_clouddrive_pb2.py` 排除；公开导出的导入用显式同名别名声明。命名、行数、空行和复杂度偏好不属于这套 lint 门槛。

文档与文案检查包含 seiso 稳定规则和最终状态检查，随正式入口执行。分类、范围、独立检查与预览规则用法见 [文档与界面文案](WRITING.md#检查与复核)。seiso 由开发依赖与锁文件固定，运行 Peach 不需要它。

不是每次都跑全量：本机按改动涉及的域选测，CI 按提交类型决定覆盖多宽。
调试先跑相关用例，冻结代码后运行一次 `auto`；不要手写逐域循环重复执行公共用例。测试失败先定向复现，修复后由 `auto` 判断哪些域需要补测。

| 场景 | 验证 |
| --- | --- |
| 本机修改 | 正式入口默认 `auto`，按影响域取并集；同代码、环境与范围的有效记录可复用 |
| 普通 PR | Python 3.14：macOS 与 Windows 均按影响域验证 |
| 普通主线提交 | Python 3.14：macOS、Windows、前端、浏览器与 wheel 完整回归 |
| 实际依赖、迁移、共享测试设施、未知影响面 | Windows 与 macOS 的 Python 3.14 全量 |
| 手动 CI | 完整系统矩阵 |
| wheel | Windows 与 macOS 各一次 Python 3.14 独立最小安装 |
| Release | 复用同 SHA 主线成功 CI；Windows 独立 EXE 与 Apple Silicon、Intel DMG 分别构建和验收 |

Python 全量按文件稳定分片，Windows 四片、macOS 两片，每片使用独立 runner；汇总任务要求所有分片和计划选择的任务成功。分片子进程不签发记录。

Test 工作流的 `web-bundle` 在 Ubuntu 上统一执行 tsc、lint、Vitest、构建及产物差异检查；lint 同时检查无诊断且扫描文件数大于零。CI 的 Python 任务把这三个工具用例交给前端任务，把两个真实浏览器用例交给浏览器矩阵；同模块的契约测试仍执行。任务身份由固定工作流与 job 名识别，未知任务和本机保留完整测试。承担部分 CI 职责的运行不签发本机测试记录。

浏览器用例由 `web-e2e` 的六个 Windows runner 分担，每片有自己的服务、演示库与日志。`ci_plan.py` 按 `ci-browser-timings.json` 的实测秒数分配文件，新文件使用默认权重；每个文件恰好执行一次。每片经项目入口的 `-Focus` 运行分配的浏览器文件，首启文件始终归首次设置服务。它装 Node 24、前端依赖、ffmpeg，并经 `PEACH_E2E_CHROME` 指定 Chrome。本机浏览器入口与并发语义保持完整。

PR 计划按改动选择前端、vendor、wheel 与浏览器任务，文档 PR 只执行适用检查；主线、依赖、未知影响与手动验证包含全部任务。`frontend-ready` 汇总所选前端检查，失败时不启动后续完整回归；未选择前端任务的计划可正常执行。产物差异以文件统计显示。`verified` 根据计划核对必需任务，缺失、失败、取消及必需任务跳过均判失败。Actions 工作流本身由固定版 actionlint 检查语法、表达式、依赖与脚本。PR 更新取消同 PR 的过期运行，主线 Test 按提交各自保留结果。

Python 分片上传完整测试输出；浏览器分片上传 TAP、服务日志及已脱敏网络日志，保留 14 天。npm 审计遇到 high 或 critical 漏洞直接失败；只有临时网络错误最多请求三次，在前两次失败后分别等待 15 与 30 秒，未知响应不当成通过。

Release 在媒体工具编译前核对同 SHA 主线 CI，缓存键绑定媒体工具源码清单与构建脚本，缓存内容同时含二进制和对应源码包；制品与独立开箱验收仍是发布前置。Docker 只在镜像相关主线改动、匹配 PR、版本标签或手动请求时运行，验收后推送同一个本地镜像。版本发布与覆盖升级验收不取消进行中的同目标运行；覆盖升级需明确选择目标版本及完整构建提交。

浏览器用例覆盖 `frontend/e2e` 下全部 `*.test.ts`，包括子目录。`setup.test.ts` 由 `SetupE2ETests` 在首次设置服务上执行，其余文件在已配置的演示库服务上按文件并发、文件内串行。并发数取 `PEACH_E2E_CONCURRENCY`；未设时本机取逻辑核数的四分之一、上限 4，CI（`GITHUB_ACTIONS=true`）为 1。并发批最多 12 个文件，串行批每批一个文件，各自取得完整 TAP 结果。测量动画、密度切换与等待态替换的文件（`tests/test_web_e2e.py` 的 `TIMING_SENSITIVE_SUITES`）在并发批之后串行；并发为 1 时按设计决定、交互回归和路由冒烟排序。每批限时 600 秒，任一批失败，整轮验证失败。并发的文件共用一个服务和演示库：用例触发的写请求只能落在别的用例不断言的状态上，否则用 `page.route` 拦下或归进串行批。控制台逐批输出文件名；完整 TAP 日志与服务访问日志 `serve.log` 保存在 `build/agent-verification/browser/`，CI 在测试结束后上传其中的 TAP 日志。

需要外部前置条件（Node、ffmpeg、Chrome 等）的用例，本机缺条件时跳过，在 CI（`GITHUB_ACTIONS=true`）里判失败，判定集中在 `tests/support/conditions.py` 的 `missing_prerequisite`。所以 `python` 矩阵里 `core` 以外的行也装 Node，Windows 行另装 ffmpeg 与 Chrome。

本机默认并行：入口传 `--jobs auto`（Windows `-Jobs`），运行器按同一套稳定分片切成并发数四倍的片，每个子进程各领一片、先完成的接着领下一片，父进程汇总成败、用例数与逐用例耗时后按原口径签发一份记录，一片红整轮红。`-Jobs 1` 退回串行。测试之间没有共享的端口或全局目录，账本与仓库夹具都在各自的临时目录里，并行才是安全的；新增测试保持这一点。

Windows 的路径、挂载、托盘、证书、进程编码、更新、认证及迁移属于 `core`。构建和工作流修改选择 `packaging` 与 `tooling`；测试调度自身仍全量。仅 uv 工具版本或项目展示字段变化不算依赖图变化；无法解析时全量。

人名对照等业务测试保留：修改相关域时执行，主线全量也执行。测试数据库从真实迁移生成模板，各用例复制独立临时库；迁移测试直接执行迁移。重试测试注入 sleeper 并断言退避序列。重复继承的测试只保留一份。

## 写什么测试

纯 HTTP 请求契约可使用 `tests/support/web.py` 的 `route_client`：请求经过真实路由、中间件和依赖，不启动后台服务；调用方仍须准备完整临时账本。服务启动、恢复与关闭语义使用正常 lifespan，由集成测试覆盖。

浏览器入口通过 `PEACH_E2E_NET_LOG_DIR` 保存 Chromium 网络事件日志；模式为 `HeavilyRedacted`，文件位于 `build/agent-verification/browser/network/`，CI 与 TAP 一起上传。网络请求失败仍使测试失败；排查 `ERR_NO_BUFFER_SPACE` 时同时核对 TAP 与该次浏览器的网络事件，不根据错误字样推断机器资源状态，也不添加放行重试。

测试应在用户可感知的行为错误时失败。页面的请求、缓存、选择、错误反馈和路由由组件测试与浏览器测试验证；CSS 层叠、主题、尺寸和交互后的样式由浏览器读取计算值。

源码检查集中在通用规范、隐私边界、产物、依赖和跨语言常量一致性。组件名称、变量名、某个函数是否被调用、端点字面量出现几次和文档的具体措辞，不单独设门槛。一个失败模式优先在最便宜且能真实触发它的层验证；组件已验证的业务分支，不在 Python 中再逐段比对其源码。浏览器保留真实导航、布局、焦点与层叠等需要浏览器才能成立的检查。

按下面的分工写：

- 行为（点了发出什么请求、状态怎么变、算出什么值）写 vitest（`frontend/test/`）或 `tests/test_web_js.py`；后端调用真函数或真接口。
- 用户定过的设计决定写进对应区域的 `frontend/e2e/design-*.test.ts`（共用 `design-fixture.ts`），读 `getComputedStyle`；布局与运行期不变量进 `smoke.test.ts`。
- 源码文本断言只减不增，由 `tests/test_source_assertion_ratchet.py` 按文件计数拦截。已有行为或浏览器验证的实现文本断言直接清退；确有行为缺口时才补用例，并同步降低基线。
- 先列失败模式再写用例。异步请求的中间态用手动放行的 Promise 验证；假 fetch 在同一个 `act` 里就回话，会掩盖请求未完成时的时序问题。
- 修缺陷先写一条会失败的用例，看它红了再修；修完按同一写法搜别处，有同类就收成共用实现。没有行为缺口的改动（文案、纯样式）不补回归用例。
- 使用应用 lifespan 的测试应显式准备账本状态：正常后台服务使用完整迁移的临时账本，缺失或迁移未就绪的场景验证后台服务保持停止。用 `assertNoLogs("peach", level="ERROR")` 覆盖服务启动、请求和正常退出；故障注入用 `assertLogs` 核对预期原因，不能仅凭接口返回成功放过非预期后台错误，也不能全局屏蔽错误日志。
- `support.gitrepo.seed_repository` 的临时仓库在命令返回前完成 Git 自动维护；仓库局部设置 `maintenance.autoDetach=false` 与 `gc.autoDetach=false`，避免后台写入与模板复制、临时目录回收竞争。保留正常维护和清理错误，不修改机器的 Git 配置。
- 不写同义反复的用例：断言常量等于它自己、mock 掉被测对象再断言 mock 被调用，都证明不了什么。

## 正式入口

测试只从下面两个入口跑，入口负责选测、签发测试记录和环境预检，自己拼的命令拿不到有效记录。

Windows 按影响域验证：

```powershell
& .\scripts\test.ps1
```

macOS 按影响域验证：

```sh
./scripts/test.sh
```

显式全量用 Windows `-Scope full` 或 macOS 首参数 `full`。入口通过 `scripts/test_environment.py` 准备 Python 依赖，再核对源码位置、选测并记录慢测试。隔离工作树按 `uv sync --locked --all-extras` 同步自己的 `.venv`；缺少本地环境时用主检出的 Python 启动准备，测试仍在新建的本地环境里运行。同步保留启动解释器的 Python 版本，忽略外部 `VIRTUAL_ENV` 和 `UV_PROJECT_ENVIRONMENT`，拒绝工作树环境指向主检出。

主检出只按 ADR-0091 只读检查依赖；缺包或检查失败时以 `3` 退出，依赖变更由重启流程执行。找不到 uv、锁文件过期或同步失败也会停止测试。前端依赖与构建仍按上文安装。完成安装和编辑后再启动最终验证；运行中发生改动时，完整批次按下述规则保留未受影响的文件结果。

本机全量验证与 `integrate` 共用 `full-suite.lock`，从取得代码和环境快照一直持有到记录写入完成。
锁忙时打印持有者并等待，最长 30 分钟；超时退出且不执行测试或合并。集成事务本身仍由
`integration.lock` 串行保护。部分域验证与 CI 分片不签发独立的本机全量记录。

锁旁的持有者信息用独立文件锁保护读写和清理；主锁仍负责验证与集成互斥。等锁线程关闭记录文件后，持有者才删除记录，避免 Windows 文件共享冲突。

测试入口退出码：`0` 验证有效，`1` 用例失败，`2` 参数或锁等待失败，`3` 环境预检失败，
`4` 验证期间代码或依赖变化，尚未取得当前状态的完整记录。Windows 托盘遇到 `4` 最多执行三轮完整验证，
只有取得有效记录才继续打包；用例失败直接停止。

正式入口会在创建测试分片前探测 PATH 中的外部工具。工具路径存在但当前权限不能启动时，
入口以一条环境结论退出，不把同一个 `CreateProcess` 权限错误散落到多个用例。整条
`scripts/test.ps1` 要在正常 PowerShell 权限下运行；权限不足时补权限再原样重跑，
不拆成单测也不跳过用例。

判失败的只有本次所选域真的会用到的工具，其余启动受限的工具只打印一行提示：受限环境里
ffmpeg 起不来是事实，但它不该挡住一次只改文档的 `checks`。域由选中的测试源码推出来。
确认本次无关时用 `PEACH_SKIP_PREFLIGHT=1` 整体跳过预检；分片子进程不重复预检。

## 定向调试与局部记录

Windows 的 `-Focus` 接受 Python 模块、类、方法或仓库相对路径，可传数组；macOS/Linux 对每个选择器重复传 `--focus`。入口准备隔离依赖并核对源码。选择器不存在、浏览器路径超出 `frontend/e2e` 或与分片混用时拒绝执行。

定向调试 Python 回归：

```powershell
& .\scripts\test.ps1 -Focus test_test_evidence
```

定向调试一个浏览器文件，包括它的桌面和手机用例：

```powershell
& .\scripts\test.ps1 -Focus frontend/e2e/jav-detail.test.ts
```

macOS/Linux 的相同入口：

```sh
./scripts/test.sh auto --focus frontend/e2e/jav-detail.test.ts
```

定向调试不签发记录，不能作为 `ready` / `integrate` 的验收证明。浏览器文件选择只在调试环境生效；正式入口拒绝残留的 `PEACH_TEST_FOCUS` / `PEACH_E2E_FILES`，防止局部运行被签成整域。某个浏览器批次失败或超时后立即结束该轮浏览器检查，保留完整 TAP 与服务日志。

浏览器沿用 Playwright 的默认进程模型与静音设置，不限制渲染进程数，也不把 GPU 或音频服务强制并入浏览器进程。原生浏览器启动限时 60 秒，页面主体等待限时 15 秒，每批限时 600 秒；进程启动与页面行为分别验收。需要任务状态桩的页面通过 `visit` 的准备回调在首次导航前安装响应，等对应的 React 主体出现后再检查任务状态；进行中的任务不作为主体等待态。页面主体等待失败时保留 URL、关闭状态、已收集的运行期问题与无障碍树，夹具关闭本次浏览器上下文。

Windows Release 工作流使用 `uv sync --locked --all-extras` 准备完整依赖。安装目录回归与 PyInstaller 构建共用这份 venv，测试入口按完整锁文件核验依赖。

macOS Release 使用 `macos-15`（Apple Silicon）和 `macos-15-intel`（Intel）分别构建，解释器、依赖与目标架构必须一致。独立验收任务只下载候选制品与冒烟脚本，挂载只读 DMG，核对 Applications 链接、许可证、签名与原生架构，再执行 `scripts/smoke_macos.py`。该脚本将应用移到源码树外，在移除开发工具的 PATH 下验证首次设置、迁移、单机账本、登录、前端资源与配置保存，数据全部使用临时目录。所有平台验收成功才发布；普通 push 不构建安装包，手动运行 Release 不上传公开资产。

`auto` 从同环境、24 小时内的记录计算差异：有效全量基线保留原时间戳；有效局部基线只复用未受差异影响且本次需要的域。相同代码状态补跑缺少的域。新增、删除与修改文件均参与比较；共享设施、未知影响面和环境差异不能复用局部记录。局部记录不会变成全量，继承时间戳不延长有效期。单域失败撤销该域与全量证明，保留其他域的原成功时间；失败域必须补测，不能从旧基线恢复。全量失败撤销所有证明。`-Fresh` 与显式 `full` 始终实际执行。

选测比较文件内容：Python 只有注释或格式变化、npm 清单只有 description/keywords 变化、TOML 只有注释或格式变化时选 `checks`。Python docstring、运行时字串和类型注释属于有效内容。Python 构建元数据变化选 `packaging`，实际依赖变化、文件模式变化、解析失败和未知影响面保留完整验证。

新增、删除文件按所属功能域选测；已有文件的执行权限变化和符号链接保持 `full`。

完整批次通过但运行中发生局部改动时，保留未受影响测试文件的结果和原时间；退出 `4`，随后 `auto` 只补缺少的域。只有同一完整批次覆盖所有测试文件才恢复 `full`，局部批次不能拼成全量证明。环境、共享逻辑或未知范围变化清空保留结果；失败和过期结果不能复用。

测试记录格式升级时，主检出的入口可能无法识别新格式。先在工作树通过 `ready`，再由主检出调用该工作树中已验收的 `scripts/agent_worktree.py`，用 `--repo` 指定主检出并执行 `integrate`。该方式仍检查测试记录、README 声明、目标分支和集成锁；不要手工改写记录或跳过门槛。集成后主检出使用同一版本的入口。

PR 的浏览器 CI 根据界面、Web API、认证与浏览器用例选择；普通后端单测不单独触发浏览器任务。工作流按职责选测，发布工作流触发打包验证，看板等管理工作流只触发工具检查。主线推送与手动完整验证继续覆盖全部 CI 任务。

## 记录的环境身份

记录只在「代码、环境、范围」三项都匹配时可复用，环境那一项由 `scripts/test_evidence.py`
的 `environment()` 算成一个摘要：解释器与已安装包、平台、`PEACH_*` 等环境变量、前端
`node_modules` 锁，以及 node／npm／git／ffmpeg／ffprobe／openssl 六个外部工具。

uv 只探针、不进指纹：它负责建环境，装出来的解释器与包已经逐个记在摘要里，而它自己由
winget 自动升级。把它的版本也算进去的话，升级当天全部记录一起失效，被验证的那套环境
却一个字节都没变。

工具身份取它**自报的版本**，不取 PATH 解析到的路径与文件字节。同一套 Git 安装在
PowerShell 里解析到 `Git\cmd\git.exe`、在 Git Bash 里解析到 `Git\mingw64\bin\git.exe`，
两个前端字节不同而版本和行为相同；按路径记身份会把记录绑在 shell 上：在一个 shell 里
跑出记录，换另一个 shell 跑 `integrate` 就报「缺少有效测试记录」，回到工作树跑 `auto`
又说「复用记录」，两句查的是两个键，代价是白跑一遍全量。

改 `environment()` 的算法本身时，主检出那份代码算不出新键，无法验证带着新算法的分支。
那一次的 `integrate` 用分支自己的脚本跑，并把仓库指到主检出。`--repo` 是顶层参数，
放在子命令前面，放后面会被 argparse 拒收：

```powershell
& .\.venv\Scripts\python.exe -X utf8 <工作树>\scripts\agent_worktree.py `
  --repo <主检出> integrate --branch <分支>
```
