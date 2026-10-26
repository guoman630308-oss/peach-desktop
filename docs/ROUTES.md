# 页面路由

列表使用一层路径，详情使用两层路径。查询参数表达筛选、排序与播放上下文；名称只用于展示和检索。

## 详情对象

| 对象 | 规范地址 | 身份 |
| --- | --- | --- |
| 资产 | `/assets/:id` | ledger 中的一条媒体记录；不同文件版本有各自 ID |
| 作品 | `/works/:code` | 规范番号；一个作品可对应多个馆藏资产 |
| 关注条目 | `/posts/:id` | 关注来源的一条帖子，包含视频或图片 |
| 艺人 | `/performers/:id` | `performer` 实体，含女优等艺人 |
| 厂牌、片商 | `/studios/:id` | `studio` 实体；资料按厂牌与上级片商关系展示 |
| 创作者 | `/creators/:id` | `creator` 实体，含 FC2 卖家与动画作者 |
| 事务所 | `/agencies/:id` | `agency` 实体 |
| 系列 | `/series/:id` | `series` 实体 |
| 本地标签 | `/tags/:id` | `tag` 实体；聚合相关资产与图片 |

数字 ID 不包含名字。实体改名沿用同一地址；合并 ID 跳到最终身份。来源中的人物或厂牌没有本地身份时只显示名称。

作品路径的番号使用规范形式，例如 `/works/ABP-968`。资产和帖子使用正整数 ID。不存在或种类不匹配的实体 ID 返回 404。

## 浏览入口

| 地址 | 用途 |
| --- | --- |
| `/` | 全部馆藏，含本地硬盘与已挂载网盘；新作行使用作品详情地址 |
| `/?browse=folders` | 按原目录浏览；`folder` 指定目录，`folder_recursive=0` 只看当前层 |
| `/?jav=1` | JAV 馆藏 |
| `/jav?tab=discover` | JAV 探索；`scope=library` 看馆藏，`scope=external` 看外部发现 |
| `/jav?tab=wants` | 想要的作品 |
| `/follow` | 关注更新；作者、来源、标签和观看状态使用查询参数 |
| `/wall` | 视频播放墙 |
| `/immerse` | 沉浸浏览；`id` 指定起始资产 |
| `/performers` | 艺人名册；动画作者通过 `category=animation` 浏览 |
| `/studios` | 厂牌、片商名册 |
| `/creators` | FC2 卖家名册；资料对象属于创作者 |
| `/agencies` | 事务所名册 |
| `/tags` | 本地或在线标签；在线标签通过关注页筛选 |
| `/playlists` | 播放列表 |
| `/unseen` | 没看过 |
| `/watch-later` | 稍后看 |
| `/flagged` | 已标记 |
| `/junk-files` | 垃圾文件 |
| `/trash` | 回收站 |

探索页将搜索词 `q`、标签 `tag`、排序 `sort`、方向 `dir` 与范围 `scope` 保存在地址中。点开详情和退出沿用原列表、滚动与筛选状态。

系列资料可直达；没有独立系列索引页面。

## 播放上下文

| 地址示例 | 上下文 |
| --- | --- |
| `/assets/30741?playlist=7` | 播放列表 7 |
| `/assets/30741?mix=30740` | 以资产 30740 为起点的 Mix |
| `/assets/30741?parts=30740` | 资产 30740 所在分卷组 |
| `/assets/30741?editions=30740` | 资产 30740 所在版本组 |

路径始终标识当前资产。换条更新 ID，队列键标识来源。复制播放地址时保留当前时间与队列上下文。

## 设置与管理

| 地址 | 用途 |
| --- | --- |
| `/settings`、`/settings/appearance` | 外观与导航 |
| `/settings/playback` | 浏览与播放 |
| `/settings/jav` | JAV 与新作 |
| `/settings/search` | 搜索与推荐 |
| `/settings/content` | 内容显示 |
| `/settings/library` | 媒体库与入库 |
| `/settings/subscriptions` | 来源与订阅参数 |
| `/settings/downloads` | 下载渠道 |
| `/settings/network` | 网络与访问 |
| `/settings/maintenance` | 应用与维护 |
| `/settings/diagnostics` | 系统诊断：系统检查、库健康与来源会话 |
| `/subscriptions` | 关注管理：关注列表、添加关注、JAV 订阅源 |
| `/sources` | 来源和凭证：采集来源、访问方式与凭据状态 |
| `/tasks` | 任务中心与云下载进度 |
| `/stats` | 统计 |
| `/taste` | 口味分析 |
| `/review` | 人工复核 |
| `/data-cleanup` | 数据管理 |
| `/duplicates` | 重复文件 |
| `/quality-goals` | 高清版目标 |
| `/login` | 登录 |

设置分区可直接打开、刷新和用浏览器前进后退。偏好面板从当前页面打开时保留背景；配置分区复用配置页及其权限检查。配置分类导航停在搜索栏下方，切换分类时显示所选分区的开头。

## 兼容地址与服务接口

| 兼容地址 | 规范去处 |
| --- | --- |
| `/item/:id` | `/assets/:id` |
| `/jav/movie/:code` | `/works/:code` |
| `/follow/item/:id` | `/posts/:id` |
| `/mix/:seed/:item`、`/parts/:seed/:item`、`/editions/:seed/:item` | `/assets/:item` 加相应队列参数 |
| `/playlists/:playlist/:item` | `/assets/:item?playlist=:playlist` |
| 实体的姓名、别名路径 | 对应数字 ID 地址 |
| `/activity` | `/tasks` |
| `/scraping` | `/sources` |
| `/follow-manage` | `/subscriptions` |
| `/configuration` | `/settings/library` |
| `/diagnostics` | `/settings/diagnostics` |
| `/resource-sync` | `/data-cleanup#resource-sync` |

兼容跳转保留查询参数；后退前进的背景信息留在历史条目中。索引尾斜杠跳到无尾斜杠路径。

`/api/*`、`/stream`、`/follow-stream`、封面、图片及其他媒体端点使用各自的服务契约。实体只读接口 `/api/entity`、`/api/photos` 支持 `kind` 加 `id`，也接受 `kind` 加 `name`。鉴权与本机写入权限由服务端检查。

启用接口文档时，`/docs`、`/docs/oauth2-redirect` 和 `/openapi.json` 是服务文档入口；首次运行表单由未配置状态下的首页展示。

单文件入库使用本机 `POST /api/library-processing/import-file`，请求体为 `{"path":"视频完整路径"}`，可附 `speed_mode`。文件须在已配置媒体根内；登记后只处理返回任务中的该资产，进度仍由 `GET /api/library-processing` 查询。本机 `POST /api/pick-media-file` 打开系统文件选择窗口，返回 `{"path":null}` 表示取消。两条写请求均检查身份与来源；入库另受账本只读状态约束。
