# ADR-0023：开源为通用自托管单人媒体应用的路线

- 状态：Accepted（路线；各阶段实施仍需按 `peach-ledger-write` 单独授权）
- 日期：2026-09-02
- 关系：延续 ADR-0017（双主机本地运行）与 ADR-0004（网络边界）；把它们从「用户的两台机器」推广为「任意用户的一到两台机器」。

## 背景

Peach 要发布到 GitHub 供所有人维护与使用。使用形态不变：每个部署仍是单人、本地优先、ledger 是唯一真相源。但代码和文档里嵌着只对当前用户成立的事实：

- `src/peach/config.py` 用 `Path.home()/Desktop/peach`、`Desktop/<用户目录>/peach`、写者主机的 mDNS 名、SMB 账号名等字面量给默认值；代理与局域网 IP 散在 HANDOFF、STATUS 与技能文件里。
- ledger 路径固定写成 Windows 盘符形状（`R:\Media\...`、`A:\`、`B:\`），由 `peach.platform` 在读取时翻译成本机挂载点；盘符含义只在文档里说明。
- Windows 写者 / macOS 读者的单写者复制（SMB 共享、mDNS 名、钥匙串账号）默认开启、无法绕开，而多数用户只有一台机器。
- [运行与配置](../OPERATIONS.md)、[开发约定](../../AGENTS.md) 与多份技能文件记录了个人运行态、真实 IP、备份文件名与磁盘布局。

不属于本 ADR 的事：多用户与账号系统、云托管、第三方登录。它们改变产品形态，另立 ADR。

## 决策

分四个阶段推进，每个阶段独立可验收；未完成前一阶段不进入下一阶段的真实写入。

1. **配置层与首次运行**
   - 新增 `peach-data/config.toml`（或同等设置文件）承载数据根、媒体根、监听地址、代理、复制开关；环境变量继续作为覆盖，`config.py` 只保留读取与校验逻辑，不再含个人路径与主机名。
   - 新增 `peach init` 子命令：创建数据目录、跑迁移、生成设置文件与自签 CA，并输出下一步。
   - 不带参数的 `peach init` 在终端里问答（数据根、一个本地媒体目录、监听范围、端口、局域网名字），写出只声明 `local` 的设置文件并可选地首次扫描；问答逻辑在 `peach.onboarding`，托盘设置页复用（2026-09-04 落地）。
   - 「未配置」是明确状态：服务能启动并在页面提示去设置，而不是因缺目录崩掉。

2. **来源挂载点取代盘符**
   - ledger 中的路径形状不动（迁移成本与风险最高，且现有翻译层可用），但每个盘符前缀在设置文件里声明为「挂载点 ID → 本机路径」的映射，`peach.platform` 按映射翻译。
   - 新导入的资产用挂载点 ID 前缀写入；现有行的重写是一次真实迁移，需备份与前后计数，单独授权。

3. **复制功能可关闭**
   - 单写者复制（ADR-0017/0020）改为设置项 `replication.enabled`，默认关闭；关闭时不启动同步线程、不探测 SMB、不发布 mDNS。
   - 托盘与 macOS 端的挂载逻辑只在开启时装配。

4. **发布准备**
   - 清扫文档与技能中的个人信息：IP、主机名、账号名、磁盘序列、备份文件名、个人目录。`tests/test_repo_hygiene.py` 增加对已知个人字面量的拒绝门槛。
   - 增加 `LICENSE`（AGPL-3.0-or-later，2026-09-04 定稿）、`CONTRIBUTING.md`（指向 AGENTS.md 与测试入口）、`SECURITY.md`（凭据存储与网络暴露说明）。
   - README 改为面向陌生用户：是什么、怎么装、怎么跑、怎么贡献；个人运行态从 STATUS 移出仓库。
   - 第三方依赖许可证核对：FFmpeg 构建、Video.js、前端依赖（ADR-0022）。

## 勘误（2026-09-03，实施第 2、3 阶段时核对）

原文第 2 阶段写的「每个盘符前缀声明为挂载点 ID」和「现有行的重写是一次真实迁移」都不成立，
因为它把两件事看混了。核对账本后的事实与实际做法：

- `asset.location` **本来就是**挂载点 ID：`local`、`115`、`pikpak`、`online` 四个值，
  不是盘符。盘符只出现在 `asset.path` 里，而那是 AGENTS.md 的不变量（账本一律 Windows 形态），
  本来就不该动。**第 2 阶段因此不重写任何账本行，也没有对应迁移**；ADR-0025 不存在。
- 第 2 阶段真正做的是把「本机挂载点」这一层从盘符改成 location ID：
  设置文件用 `[media.mounts] <location-id> = <本机路径>` 取代第 1 阶段的
  `[media] <盘符> = <本机路径>`，`[media.locations]` 继续声明每个 ID 的账本口径根
  （`local = 'R:\media'`、`115 = 'B:/'`、`pikpak = 'A:/'`）。`peach.platform` 按
  「声明根前缀 → 本机挂载点」翻译，不再按盘符字母。挂载点的语义是**声明根在本机的落点**，
  所以 `R:\media\x` 在 macOS 上等于 `<mounts.local>/x`。
- Windows 上盘符本身就是挂载点，`translate_ledger_path` 原样返回，`[media.mounts]` 整表可空。
- 第 2 阶段同时补上了写入侧的一致性门槛：`peach scan <location> <root>` 现在拒绝
  与 `[media.locations]` 声明根不一致的组合，新导入的行不可能再落到别的来源名下。
- 诊断用的 `PEACH_DRIVE_MAP` 键空间是盘符，与新方案不兼容，已删除；替代品是同样语法、
  按 ID 键的 `PEACH_MEDIA_MOUNTS`（`local=/mnt/res,115=/mnt/115`）。

第 3 阶段有一处与原文不同，是刻意的：

- **mDNS 发布不随 `replication.enabled` 一起关**。原文把「不发布 mDNS」列进了关闭清单，
  但 mDNS 是这台机器自己的局域网入口（ADR-0009），和单写者复制没有关系：单机用户正是
  靠 `peach.local` 从手机访问自己的库，跟着复制开关一起关掉等于把通用化最想服务的那类
  用户的访问入口砍了。mDNS 继续由它自己的判据决定：`--no-mdns` 与「只绑回环就不发布」。
  关闭复制时不装配的是 ledger 同步观察器、SMB 探测与挂载、托盘的两个 Ledger 菜单项，
  以及追更凭据往共享副本写的那一份。

## 边界与不变量

- ledger 仍是唯一真相源；AI 与刮削结果仍是候选，不因通用化放宽。
- 单进程 FastAPI 模块化单体不变；不引入 PostgreSQL、微服务或多账号。
- Windows 与 macOS 都是一等运行平台；Linux 不在支持范围，未测试（2026-09-04 决定）。
- 任何阶段都不允许把当前用户的数据形状写死为默认值；默认值必须对一个全新用户成立。

## 后果

- 短期新增一层设置文件读取与校验代码，换来删除 `config.py` 中所有个人字面量。
- 现有部署的配置导入使用 `peach init --from-existing`；通用运行方式见 OPERATIONS。
- 阶段 2 的真实迁移与阶段 3 的默认关闭会改变当前用户的运行方式，实施前必须在同一轮拿到明确授权并保留回退路径。

## 第 4 阶段实施记录（2026-09-04）

本节是交接面：任何智能体接手开源发布，只读本节加 [公开路线图](https://github.com/orgs/peach-mitao/projects/7) 即可，不需要对话记录。

### 用户已定的决定

- 许可证 **AGPL-3.0-or-later**。`LICENSE` 全文、`pyproject.toml` 的 `license`、README 双语「许可证」节与本 ADR 四处一致，`tests/test_repo_hygiene.py::ReleaseFilesTests` 钉住前两处。
- **Git 历史不重写**。历史提交里保留局域网 IP、本机用户名、主机名与共享账号名，用户接受这一代价；当前树由 `MachineCoordinateTests` 全树门槛保证不含个人坐标，例子一律用 RFC 5737 地址与中性主机名。
- 接受 issue 与 PR，大改动先开 issue 讨论；模板在 `.github/`，规则在 `CONTRIBUTING.md`。
- `AGENTS.md`、`CLAUDE.md` 与 `.claude/skills/` 随仓库公开，术语表的「我 / 用户」指任何部署者。
- 平台与解释器范围以 `pyproject.toml`、测试工作流及后续平台 ADR 为准。
- 仓库定位直说成人内容个人馆藏；README、文档与网站截图一律用 SFW 演示数据。
- 制品与安装验收以发布工作流及 `docs/TESTING_DESKTOP.md` 为准。
