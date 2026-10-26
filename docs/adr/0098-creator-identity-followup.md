# ADR-0098：刮削后按页名去日本资料站查创作者身份

状态：已接受。最后复核：2026-10-10。

文件名里的创作者常常不是人。`COSH こすっち` 一直被当成个人账号，实际是スコッチ的系列；通用搜索不收 sougouwiki、av_neme 这类站，身份调研只搜通用引擎，查不出来。命令行那一趟（`scripts/research_creator_identity_wikis.py`）补上了这两站，但要人跑、人看、再交给 `apply_entity_identity_research.py` 写，新登记的创作者照样没人去问。

处理任务结束时为创作者派一条「查创作者身份」后继（`creator_identity_followup`，ADR-0040）：新登记的每个一条，存量每轮至多 `STOCK_SHARE` 条，在女优头像的存量之前取。目标是身份面上没有任何可信断言（`trusted_sql`，任何来源、任何值）且名字不是结构目录或转载站的创作者；人复核过的一律不查，不会被另一个来源冲掉。

判据与命令行那一趟逐字相同（`entity_identity_research.wiki_finding`）：页名候选是规范名、去掉番号前缀的名字和文件名里的发行前缀，两站按 EUC-JP 直取；同名页不是人物页、作品表里至少列出 `LABEL_PAGE_CAST`（3）位不同出演者才算命中，这是页面结构判据，不是唯一身份的确证。命中按 ADR-0052 直接落 `identity=release` 的 observed，来源是批次号 `script:creator-identity-wiki@<任务行 id>`，由 `revert_auto_landing.py --source script:creator-identity-wiki` 整批撤回。

ADR-0095 把 observed 收窄到代码判据来源（`CODE_SOURCES`），这个前缀随本决定加进去：取页、判定和写入都在代码里，没有人或模型经手。命令行那一趟产出的清单人可以改过，经 `apply_entity_identity_research.py` 写入时照旧降为 candidate。要收回这一档信任，从 `CODE_SOURCES` 删掉前缀，再用 `repair_identity_claims.py` 把存量降为 candidate。人物页、出演者不足的页和不存在的页都不写，问过什么留在活动页那一行；没取到记「未取得」，24 小时后再派。

后继不改实体类型。改成厂牌或系列要迁移作品关系，是人复核的那一步；observed 已经让它离开博主名册和作品画面头像（ADR-0095）。

取页复用补别名后继的 `WikiSitePages`：页缓存、本条请求上限、站上没有的页记 30 天、403 与 429 按站名进来源冷却，两站共用 seesaawiki.jp 每 2 秒一次的主机间隔。没有新依赖。

两站主要收录日本发行资料；无法按站点编码表示的名称、没有同名页或只有人物页时，不形成发行角色断言。
