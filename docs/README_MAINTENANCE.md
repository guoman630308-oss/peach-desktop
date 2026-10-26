# README 维护

1. 按下表核对本次涉及的能力、安装方式与限制。
2. 同批核对中英文 README，修改需要调整的内容，保留使用入口和安全说明。
3. 按 [文档与界面文案](WRITING.md#检查与复核) 检查，再运行 [正式测试入口](TESTING.md#正式入口)。

README 面向初次访问项目的人，依次讲它是什么、介绍视频与截图、能做什么、怎么装、常见问题、
问题反馈和文档入口。用使用者的话写：说能做什么、会不会动文件，不写账本、候选、实体这类内部
概念，内部机制与开发流程只留链接。中英文同批维护，平台支持、安装方式和能力限制保持一致。主线能力、已安装运行态和发行制品分别
以实现、本机健康检查和 Releases 为准，README 不填写本机 IP、端口归属、真实馆藏数字或动态版本号。

| 内容 | 核验入口 |
| --- | --- |
| Python 与桌面命令 | `pyproject.toml`、`src/peach/cli.py`、`src/peach/tray.py` |
| 前端工具与产物 | `frontend/package.json`、[docs/FRONTEND.md](FRONTEND.md) |
| 桌面包与平台范围 | [docs/TESTING_DESKTOP.md](TESTING_DESKTOP.md)、`.github/workflows/release.yml` |
| 当前行为与已部署范围 | 实现与测试、[运行与配置](OPERATIONS.md)、[公开路线图](https://github.com/orgs/peach-mitao/projects/7) |
| 来源、配置与数据边界 | [docs/SOURCING.md](SOURCING.md)、[docs/OPERATIONS.md](OPERATIONS.md)、[docs/CLOUDDRIVE.md](CLOUDDRIVE.md) |

README 迭代绑定 Codex 与 Claude 共用的工作树交付流程。每次提交前核对暂存差异，
涉及用户可见能力、安装、平台或构建要求时，同批更新中英文。只改变内部实现时保留 README，
在交付分支最后一个提交的 Git trailer 写明原因：

| 判断 | 提交消息末尾的独立字段 |
| --- | --- |
| 已更新两份 README | `README-Impact: updated; 同步了哪些用户说明` |
| 只需修改中文 README | `README-Impact: updated; 中文修改原因`，另加 `README-Reviewed: README.en.md; 英文无需修改的原因` |
| 只需修改英文 README | `README-Impact: updated; 英文修改原因`，另加 `README-Reviewed: README.md; 中文无需修改的原因` |
| 不影响 README | `README-Impact: none; 具体原因` |

声明与 `Co-Authored-By` 等其它 trailer 连续写在提交消息末尾的同一块里，彼此之间不留空行。
同一块里的署名形态见 `.claude/skills/peach-worktree/SKILL.md`「暂存与提交」。
`git interpret-trailers --parse` 只解析末尾那一块，被空行隔开的 `README-Impact` 属于正文段落，
`commit-msg` 会指出不在末尾同一块的字段，`ready` 也会拒绝未取得唯一声明的交付。

`scripts/check_readme_impact.py` 读取目标分支到交付 HEAD 的实际差异，并使用
`git interpret-trailers --parse` 读取最后提交的声明。涉及运行时代码、前端、迁移、资源、
依赖或安装说明时必须声明；纯测试和其他内部文档不触发。触发清单由脚本统一维护。
README 改动必须核对两种语言；`updated` 至少对应一份实际差异，`none` 不得伴随 README 改动。
仅一份需要调整时，用唯一 `README-Reviewed` 写明未改文件及原因，不能为了满足检查改动正确的译文。
提交后的暂存内容不能满足要求。原因的真实性仍由代码审阅负责，脚本不代替语义判断。

`agent_worktree.py ready/integrate` 在接受测试记录前执行这一门槛。检查覆盖本次分支交付，
允许在最后交付提交统一核对整批差异。仓库 `scripts/githooks/commit-msg` 在 `agent/*`
分支提交时检查署名，以及消息中已有 README 声明的格式与分段；中间提交可以不写 README 声明。
合并消息免除署名检查，amend 使用同一套字段判据；钩子只读消息。README 声明与整个分支差异
的一致性由交付门槛检查，未通过的分支不能经项目入口集成。
无需定时任务、账号专属 hook 或额外模型调用。Python 下限、文案和快照校验继续复用现有测试。
结构或品牌调整时重读已登记的参考快照，上游变化只作为参考，计划功能不得写成已实现。

## 演示数据集

README、教程与网站的截图默认取自演示库。用分类模型从真实馆藏里筛「看起来无害」的帧不算
替代方案：筛出来的多是黑场与标题卡，卡片上的番号、姓名与厂牌照样泄露来源。

逐段确认过的真实馆藏画面可以用，条件是画面里不出现访问凭据、局域网地址、隧道地址
和文件系统完整路径；这四样一旦入镜就是把本机坐标发到公网，撤不回来。README 的介绍视频挂在 `intro-video` Release 的附件上；截图由官网静态资源提供 WebP 展示图与无损 PNG 原图，README 的图片链接指向 PNG，避免 Release 附件的下载响应。WebP 展示图须小于 4 MB，并核验 GitHub 图片代理返回图片且浏览器完整解码；高密度播放墙使用单独的压缩展示图，原图保持 3840×2160。截图不进仓库：`resources/` 会被整个打进安装包。仓库里确需
留存的少量演示媒体放 `docs/assets/`，单个文件压到 8 MB 以内（`tests/test_repo_hygiene.py`）。

生成器是 `scripts/demo_dataset.py`，只写目标目录，不碰任何账本：

```powershell
& .\.venv\Scripts\python.exe -X utf8 scripts\demo_dataset.py --output <目录> --count 24
```

封面由 `--art` 决定。默认 `portrait`：人像取自 Gfriends 图库，封面与图集由这张人像排版
而成，出演者用清单里的规范艺名，界面因此有真实馆藏的样子。`synthetic` 是 Pillow 几何
海报，不联网，规模基准与测试走它。两种模式的短片都是 FFmpeg lavfi 的渐变、测试图与分形，
只占位，不拿去当演示视频。
两种模式下作品、厂牌、系列与标签一律虚构：真实艺名配虚构作品，演示素材因此不会断言
「某人演过某片」这种它没有资格断言的事。

`tests/test_demo_dataset.py` 把守作品词表与 `catalog_rules` 成人词表不相交、目录布局
符合 `library_nfo` 的边车规则、`process_library` 跑完没有发过外部请求，以及人像清单的
形状与「卡片上的名字就是封面上那位」。`--seed` 固定内容，`--video stub` 用占位字节代替
编码，给性能基准的规模档用。

### 人像清单

清单是 `scripts/demo-portraits.json`：去哪取、取到的该是什么哈希。图片字节不进仓库
（ADR-0026），生成器运行时取一次，缓存在输出目录的 `.portrait-cache/`；字节与清单的
SHA-256 对不上是硬错误，不降级放行。

**清单只能人工逐张看过后更新，不许按图库顺序自动取。** 图库把目录前缀当来源优先级排序，
排第一的答的是「先试哪一张」，不是「哪一张能放进公开文档」：实测排在前面的大图多是片商
宣传素材，内衣、泳装乃至露点都在里面，`8-GRAPHIS` 这种看着像写真机构的档位同样混着
半裸。清单须人工复核，公开图片不得露点。2026-09-16 的复核覆盖 42 位；实际名单见 [人像清单](../scripts/demo-portraits.json)。

换图或加人时：取回候选、拼联系表一次看完、剔掉出界的，再按名字对应的**文件名**（不是
索引里的键名，那可能是别名或简称）与字节哈希写进清单，最后按内容哈希去重，因为一个人在
图库里常有好几个键指向同一个文件。

三种形状对应三条产品路径：番号型（`<番号>/<番号>.mp4` 配 NFO 与 `-poster.jpg`，部分带
`P/` 图集）走本地资料入库；创作者型（`<创作者>/<标题>.mp4` 配 PNG 与无番号 NFO）走
`posters/<id>_4.jpg` 本地海报；裸文件演示没有番号也没有边车的条目，只登记、不报问题。让 Peach 看见它：`peach init`
建演示 Peach 数据目录，在设置文件 `[media.locations]` 声明这个目录，`peach scan local`、
`peach process local`，候选在复核页全选通过或用 `scripts/apply_metadata_tags.py --source local_nfo`
按字段落地；要九宫格再跑 `scripts/probe.py` 与 `scripts/sheets.py`。生成器结束时会把这几步连同
当前平台的设置行一起打印出来。

新库的实体只来自复核落地，扫描本身不派生创作者，也不把图片挂到实体上，因此演示库的
创作者页与实体图集为空；这是产品现状，不是数据集缺项。

## 参考依据

- **README 结构（2026-09-06）**：取 Immich、Bruno、Hoppscotch 当时的 README 原文作参照，
  URL、日期与 SHA-256 见 `docs/reference-sources.json` 的 `readme-*` 条目。
  采用短定位、小尺寸具象 Logo、集中下载导航和精简能力分组；仅沿用 Peach 自有图标，
  不复制外部商标、截图、功能承诺或宣传内容。上游快照仅为取证，保留原文及上游许可入口。
- **同类项目的 README（2026-09-26）**：映库、OpenAver、amane、Stash、MetaTube、MDCx，
  登记同在 `docs/reference-sources.json`。借的是信息顺序：页头链接行、
  视频与截图靠前、功能一行一条、下载三步走、常见问题、反馈要附什么。不借文案、截图和附加
  使用条款，Peach 的边界以 AGPL 与 `SECURITY.md` 为准。
- **演示素材**：README 里任何截图或视频都按上面「演示数据集」一节的边界取图，换素材时重新核对
  那几条边界。
- **篇幅取舍（2026-10-17）**：复读已登记的 amane、Stash 与 OpenAver 原文，并核对线上 README。采用 amane 的短功能列表与安装入口，保留 Peach 的演示、全部五张截图、六条特色和必要安全说明；源码安装步骤集中在 [运行与配置](OPERATIONS.md#从源码运行windowsmacos)。路由、字段判据、缓存周期、更新包机制与开发流程由专题文档承载。
- **截图与 Star 历史（2026-10-17）**：截图沿用 `a5f979d6^` 的双列表格，播放墙独占一行，五张 WebP 均链接 PNG 原图。Star History 依据官方 [`EmbedMarkdownSection.tsx`](https://github.com/star-history/star-history/blob/608d0e39be441665c01cd6ac1fd01ca5ec1eaf39/frontend/components/EmbedMarkdownSection.tsx) 使用 `/svg`，保留深浅主题并链接用户指定的交互页。实测 `/chart` 与 `/svg` 均返回 HTTP 200、SVG 类型及 `Cache-Control: public, s-maxage=86400, max-age=86400`；同版本 [`cache.ts`](https://github.com/star-history/star-history/blob/608d0e39be441665c01cd6ac1fd01ca5ec1eaf39/backend/cache.ts) 将数据与 SVG 缓存各设为 24 小时。交互页 URL 仅改变点击去向，不能保证嵌入图片即时刷新；README 不显示实时性承诺。
- **交互图与嵌入图（2026-10-17）**：用户截图显示纵轴为 0、10、100；嵌入接口使用 `logscale` 得到相同刻度。官方同版本 [`StarChartViewer.tsx`](https://github.com/star-history/star-history/blob/608d0e39be441665c01cd6ac1fd01ca5ec1eaf39/frontend/components/StarChartViewer.tsx) 在浏览器调用 GitHub，并在页面内存中复用结果；24 小时服务端缓存针对嵌入图片接口，不能据此判断官网交互图的数据新鲜度。官网 `https://peach.video` 位于中英文 README 顶部导航。
