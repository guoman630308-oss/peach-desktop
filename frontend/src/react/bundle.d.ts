/* 内部常驻组件与页面的类型契约。
 * application-residents.ts 与 entry.tsx 在主界面同一源码图内组合这些接口，不发布独立运行时桥包。
 * 配置数据以 /api/configuration（src/peach/routes_configuration.py）为准。 */
import type { QualityGoal } from './quality-goals/quality-goals';
import type { IndexProps } from './index/index-data';
import type { CatalogGridProps } from './catalog-grid/types';
import type { JunkQueueProps } from './junk-queue/junk-queue';
import type { EntityPageProps } from './entity-page/entity-page';
import type { FeedNewProps } from './feed-new/feed-new';
import type { CatalogFilterProps, CatalogView } from './catalog-filter/catalog-filter';
import type { SearchProps } from './search/search';
import type { FollowDetailProps } from './follow-detail/follow-detail';
import type { ItemDetailProps } from './item-detail/item-detail';
import type { StageApi, StageHost } from './stage/stage-api';
import type { SettingsPanelApi, SettingsPanelHost } from './settings-panel/settings-panel-api';
import type { ImmerseApi, ImmerseHost } from './immerse/immerse-api';
import type { SidebarApi, SidebarHost } from './sidebar/sidebar-api';
import type { ManageHeaderApi, ManageHeaderHost } from './manage-header/manage-header-api';
import type { BatchDockApi, BatchDockHost } from './batch-dock/batch-dock-api';
import type { GlowPickerHost } from './glow-picker/glow-picker-api';

export type { IndexProps };
export type { CatalogGridProps };
export type { JunkQueueProps };
export type { EntityPageProps };
export type { FeedNewProps };
export type { CatalogFilterProps, CatalogView };
export type { SearchProps };
export type { FollowDetailProps };
export type { ItemDetailProps };

export interface AccessState { mode: 'open' | 'password' | 'legacy' | 'locked'; revision: string }

export interface TunnelState {
  enabled: boolean;
  state: 'stopped' | 'starting' | 'running' | 'error';
  url: string;
  error: string;
  available: boolean;
  mode: 'quick' | 'named';
  /** 命名隧道绑定的公开主机名，临时链接模式下为空。 */
  hostname: string;
  /** 隧道令牌存没存过。令牌本身不回传，页面只据此显示已保存。 */
  token_set: boolean;
  /** 这台部署能不能用命名隧道；独立包为 false，那一块整个不渲染。 */
  named_available: boolean;
}

export interface AccessSettingsProps {
  initial: AccessState;
  receipt(message: string): void;
}

export interface StartupState {
  available: boolean; enabled: boolean; silent: boolean; message: string; desktop: boolean; desktop_message: string;
}

export interface UninstallState {
  available: boolean; full_available: boolean; message: string; data_root: string; directories: string[];
}

export interface PeachProxyState { mode: string; proxy_saved: boolean; needs_selection: boolean }

/** 一个能换镜像域名的外部入口站点。`host` 为空串就是没换过，走 `default_host`。 */
export interface EntryLinkSite {
  key: string;
  label: string;
  host: string;
  default_host: string;
}

export interface EntryLinksState { sites: EntryLinkSite[] }

/** 一条「CloudDrive2 里的路径前缀 → 哪个媒体根」的对应。 */
export interface PushDiscoveryPrefix { prefix: string; root: string }

export interface PushDiscoveryQueue {
  pending: number; submitted: number; ingested: number; missing: number;
  dropped: number; timed_out: number; failed: number;
  last_path?: string; last_ingested_at?: number; last_error?: string;
}

export interface PushDiscoveryState {
  enabled: boolean;
  watch_local: boolean;
  cloud: boolean;
  prefixes: PushDiscoveryPrefix[];
  /** 这台机器是数据库写入端才为真；只读端不起任何监听。 */
  available: boolean;
  secret: string;
  secret_set: boolean;
  endpoint: string;
  local_running: boolean;
  local_message: string;
  local_roots: string[];
  queue: PushDiscoveryQueue;
  /** 可选的媒体根，前缀表里的下拉就从这里取。 */
  media_roots: string[];
  /** CloudDrive2 该往哪个地址推。没开 TLS 或这台机器不知道自己的地址时是空串。 */
  origin: string;
  /** 整段抄进 CloudDrive2「配置内容」的 TOML；地址或密钥缺一样就是空串。 */
  config_toml: string;
}

export interface AutomaticUpdateState {
  mode: string; interval_hours: number; available: boolean; download_available: boolean; error?: string;
}

/** 云下载的两条渠道：115 经 CloudDrive2，PikPak 直连。 */
export type DownloadProviderKey = '115' | 'pikpak';

export interface DownloadConfig {
  clouddrive_address: string;
  targets: Partial<Record<DownloadProviderKey, string>>;
  /** PikPak 根目录对应的数据库路径前缀，空串表示还没选。 */
  pikpak_root: string;
  wait_hours: number;
}

export type PikPakBrowserState = 'idle' | 'waiting' | 'done' | 'cancelled' | 'timeout' | 'failed';

/** 设置页的云下载块。凭据只报存没存过，不回值。 */
export interface DownloadSettingsState {
  available: boolean;
  config: DownloadConfig;
  token_set: boolean;
  /** `method`：`browser` 是浏览器登录取来的网页端会话，`password` 是账号密码换的令牌。 */
  pikpak: { logged_in: boolean; username: string; remember: boolean; method: 'browser' | 'password' };
  /** 浏览器登录（ADR-0093）：这台电脑有没有 Chrome 或 Edge，登录窗口此刻在哪一步。 */
  pikpak_browser: { available: boolean; state: PikPakBrowserState; message: string };
  pikpak_roots: string[];
  providers: { key: DownloadProviderKey; label: string }[];
  max_wait_hours: number;
}

/** 「保存配置」的响应：设置块加一份按已保存配置做的检查报告，没有令牌时为 null。 */
export interface DownloadSaveResult extends DownloadSettingsState {
  report?: DownloadCheckReport | null;
}

/** 检查的结果：每一项各自报，一项失败不挡住其余几项。 */
export interface DownloadCheckReport {
  ok: boolean;
  /** 实际查的 CloudDrive2 地址。表单留空时是探测到的本机地址，探测不到时为空串。 */
  address: string;
  permissions: { name: string; label: string; granted: boolean }[];
  missing: string[];
  root: string;
  folder: { path: string; can_offline: boolean; cloud: string } | null;
  quota: { total: number; used: number; left: number } | null;
  /** 115 目标目录留空时按推送发现推的目录；`exists` 为假时 CloudDrive2 里还没有它。 */
  suggested_target: { path: string; exists: boolean } | null;
  /** 未选择 PikPak 对应目录时，按推送发现的配置建议数据库路径前缀；无法确定时为空串。 */
  suggested_pikpak_root: string;
  problems: string[];
}

export interface PikPakLoginResult {
  ok: boolean;
  /** 要人机验证时给验证页地址，用户在浏览器里完成后再登录一次。 */
  captcha_url?: string;
  message?: string;
  settings: DownloadSettingsState;
}

export interface DownloadTask {
  id: number;
  info_hash: string | null;
  provider: DownloadProviderKey;
  provider_label: string;
  source_uri: string;
  display_name: string;
  target: string;
  code: string | null;
  title: string | null;
  origin: string | null;
  state: string;
  state_label: string;
  failure: string | null;
  failure_label: string | null;
  failure_detail: string | null;
  progress: number | null;
  remote_name: string | null;
  ledger_path: string | null;
  asset_id: number | null;
  submitted_at: string | null;
  updated_at: string;
  finished_at: string | null;
  blocked: boolean;
  cancellable: boolean;
  resubmittable: boolean;
  postprocess_state: string;
  postprocess_label: string;
  postprocess_detail: string | null;
  postprocess_run_id: number | null;
  postprocessed_at: string | null;
  postprocess_active: boolean;
  postprocess_retryable: boolean;
}

export interface DownloadsSnapshot {
  available: boolean;
  providers: { key: DownloadProviderKey; label: string; configured: boolean; target: string }[];
  tasks: DownloadTask[];
}

export interface DownloadSubmitResult {
  ok: boolean;
  outcome: 'submitted' | 'adopted' | 'adopted_remote' | 'refused' | 'failed';
  task: DownloadTask;
}

export interface ReleaseState {
  current_version: string;
  latest_version: string | null;
  channel: string;
  installation: string;
  state: string;
  message: string;
  release_url: string;
  checked_at?: number;
  package_kind?: 'update' | 'full';
  asset_size?: number;
}

export interface UpdateJob {
  state: string; progress: number; message?: string; downloaded?: number; total?: number; version?: string;
}

export interface MediaSource {
  location: string; root: string; path: string; online?: boolean; library?: string; library_icon?: string; cost?: 'free' | 'metered';
}

export interface ConfigurationFact {
  term: string;
  value: string;
  download_url?: string;
  download_label?: string;
}

export interface ConfigurationData {
  remote_reporting?: import('./settings/remote-reporting-settings').RemoteReportingState;
  library_sharing?: import('./settings/library-sharing-settings').LibrarySharingState;
  ledger_read_only?: boolean;
  startup?: StartupState;
  uninstall?: UninstallState;
  peach_proxy?: PeachProxyState;
  entry_links?: EntryLinksState;
  push_discovery?: PushDiscoveryState;
  downloads?: DownloadSettingsState;
  updates?: ReleaseState;
  update_job?: UpdateJob;
  automatic_updates?: AutomaticUpdateState;
  access?: AccessState;
  tunnel?: TunnelState;
  editable: boolean;
  /** 不能编辑时给用户看的原因，可编辑时为空。 */
  notice: string;
  /** 设置文件的指纹，保存时带回去，服务端据此拒绝盖掉别处的改动。 */
  revision: string;
  media_dirs: string[];
  media_sources?: MediaSource[];
  /** 媒体库个数，服务端按侧栏媒体库切换器那一份分组数好（同名的文件夹算一个）。 */
  library_count: number;
  windows?: boolean;
  port: number;
  port_editable?: boolean;
  mount_dependencies?: { name: string; available: boolean; download_url: string }[];
  facts: ConfigurationFact[];
}

/** 配置页只要遗留层的 Toast：保存是写操作，回执归全站那一份。 */
export interface ConfigurationProps {
  /** 保存成功后的过去时回执（遗留层的 Toast）。 */
  receipt(message: string): void;
  /** 让安装教程重新出现：跳过与折叠归位、撤回数据库标记。 */
  reopenTutorial(): Promise<void>;
  /** 首帧选中的那一组页签名（别处点「管理媒体库」「添加媒体目录」等进来）；认不出或没给就选第一组。 */
  section?: string;
}

/** 配置页按页签切分组时，一组的根节点作为第 `index` 格页签面板要带的属性；`active` 是此刻选中的那一组。 */
export interface ConfigurationPanel {
  index: number;
  id: string;
  labelledBy: string;
  active: boolean;
}

/** Peach 配置的分组：媒体库与入库、来源与订阅、下载渠道、网络与访问、应用与维护。
 *  `panel` 只在配置页里给，单独挂一组时没有。 */
export interface ConfigurationGroupProps extends Pick<ConfigurationProps, 'receipt'> {
  data: ConfigurationData;
  panel?: ConfigurationPanel;
}

/** 高清版目标页仍由遗留层提供的能力。全是纯函数或导航，页面不持有它们的状态。 */
export interface QualityGoalsProps {
  /** 写操作在服务端落地之后的过去时回执。 */
  toast(message: string): void;
  /** 打开作品详情（遗留层的整页视图，含播放器与队列）。 */
  openItem(id: number): void;
  /** 番号 + 版次徽章 + 标题的 HTML。非 JAV 条目退化成转义后的文件名。 */
  javTitleHtml(item: QualityGoal): string;
  /** 同一条目的纯文本形态，用于无障碍名称。 */
  javDisplayName(item: QualityGoal): string;
  /** 来源徽标（含计费标记）的 HTML。 */
  srcBadge(location: string, cost: string): string;
}

/** 统计页仍由遗留层提供的能力。都是纯函数或导航，页面不持有它们的状态。 */
export interface StatsProps {
  /** 标签键到界面上的名称（`core/tags.ts` 的 `tagLabel`）。 */
  tagLabel(key: string): string;
  /** 点一个内容标签：回目录并按它筛选。整页换成目录由遗留壳做。 */
  onTag(key: string): void;
  /** 打开配置页的「媒体」页签。没有视频或没有存储来源时空态里的那个去处。 */
  openMediaSettings(): void;
  /** 这台机器能不能改配置。不能改时空态不给「添加媒体目录」。 */
  configurable: boolean;
}

/** 口味页仍由遗留层提供的能力。都是导航、查表或回执，页面不持有它们的状态。 */
export interface TasteProps {
  /** 点一条名次：回目录并按它筛选。`kind` 是 `tag`／`creator`／`performer`。 */
  onSignal(kind: string, name: string): void;
  /** 打开口味总结指向的页面。 */
  navigate(route: string): void;
  /** 写操作在服务端落地之后的过去时回执。 */
  toast(message: string): void;
  /** 从引导流程进来（`?onboarding=1`）：浏览记录导入指南直接展开。 */
  onboarding: boolean;
}

/** 来源和凭证页只要遗留层的 Toast：保存与撤销是写操作，回执归全站那一份。 */
export interface ScrapingProps {
  /** 写操作在服务端落地之后的过去时回执。 */
  toast(message: string): void;
}

/** 关注管理页。
 *
 *  地址栏与个人偏好在这里分成两组：`tab`／`page`／`sort`／`dir` 外面链接得过来，跟着地址
 *  走；`pageSize`／`layout` 是这台浏览器的习惯，跟着 `appSettings` 走。两组都由壳持有存储、
 *  React 持有实时状态，各只有一份。 */
export interface FollowManageProps {
  /** 地址栏此刻带着的页签（`list`／`add`／`feeds`）。 */
  tab: string;
  page: number;
  sort: string;
  dir: string;
  /** 把这四项写回地址栏。空串表示这一项此刻就是默认值，壳不写进去。 */
  route(params: { tab: string; page: number; sort: string; dir: string }): void;
  /** 这台浏览器的偏好当前值。 */
  pageSize: number;
  layout: string;
  /** 偏好改了：存储归壳（`appSettings`），页面不自己写一份。 */
  savePreference(patch: { pageSize?: number; layout?: string }): void;
  /** 写操作在服务端落地之后的过去时回执。 */
  toast(message: string): void;
  /** 去「看更新」那一页（`/follow`）。整页换成哪一屏仍归遗留壳。 */
  openFollow(): void;
  openServiceConfiguration(): void;
  /** 数据库只读：这台机器只能浏览，写操作全部不给点。 */
  readOnly: boolean;
  readOnlyMessage: string;
  /** 写入端上这一页的地址。取不到时门禁里不给去处。 */
  writerUrl: string;
}

/** 人工复核页。
 *
 *  `category` 是唯一进地址栏的那一项：十个分类是固定的一组身份，「在看哪一条队列」链接
 *  得过来，刷新也要还原。分组、筛选与页码是这一刻的看法，队列又是消耗性的（判一条就少
 *  一条），写进地址栏分享出去只会指向另一批东西。 */
export interface ReviewProps {
  /** 地址栏此刻带着的分类。不认识的值退回默认那一档。 */
  category: string;
  /** 把分类写回地址栏。空串表示这一项此刻就是默认值，壳不写进去。 */
  route(params: { category: string }): void;
  /** 打开作品详情（遗留层的整页视图，含播放器与队列）。 */
  openItem(id: number): void;
  /** 打开实体资料页。`kind` 是 `creator`／`performer`。 */
  openEntity(kind: string, name: string): void;
  /** 在资源管理器里显示这个文件。成功由遗留层自己发回执，失败回一句原因。 */
  revealSource(id: number): Promise<string>;
  /** 写操作在服务端落地之后的过去时回执。 */
  toast(message: string): void;
  /** 数据库只读：这台机器只能浏览，判定与收录全部不给点，进页面也不发那一次自动落库。 */
  readOnly: boolean;
  readOnlyMessage: string;
  /** 写入端上这一页的地址。取不到时门禁里不给去处。 */
  writerUrl: string;
}

/** 扫描与采集。同一份数据两个读者，所以同一个名字挂两种形态：数据管理页那张卡片，
 *  和目录页顶上那条横幅（`mode: 'notice'`）。 */
export interface LibraryProcessingProps {
  /** 任务跑完时的一次通知。同一趟按 `job_id` 只报一次，谁先看到结束谁报。 */
  toast(message: string): void;
  /** 这一趟从运行走到终态：让遗留层重画数据管理页那几个计数。 */
  onComplete?(): void;
    /** 紧凑任务面持续观察后台状态。 */
  mode?: 'notice';
  /** 卡片闲着时也接着问：任务由后台自己起，页面要认出「现在跑起来了」。 */
  monitor?: boolean;
}

/** 换头像：资料页圆框角上那个加号，连同它点开的那一屏候选。资料卡（`entity-hero`）在自己
 *  的头像框上直接渲染它，不单独挂载。 */
export interface AvatarPickerProps {
  /** 实体类型（`performer`／`creator`）。 */
  kind: string;
  entityId: number;
  /** 页面上显示的这个人的名字，用在无障碍名称和那一句说明里。 */
  name: string;
  /** 换成功后让宿主重画头像。遗留层传的是「重新进这一页」。 */
  onPicked(): void;
}

/** 裁剪封面：作品详情页标题旁那枚键，连同它点开的那一屏取景框。 */
export interface CoverCropProps {
  /** 归一前的番号，原样回递给写端点。 */
  code: string;
  /** 封面原图的地址（`/cover?code=`）。框量的是这张图的像素。 */
  coverUrl: string;
  /** 当前生效的取景框，没有就是 null。形状同接口的 `poster_box`。 */
  box: { x0: number; y0: number; x1: number; y1: number; px: number[] } | null;
  /** 存好之后让宿主重画封面，`box` 是写端点回的新框（形状同 `box`）：作品详情重取这一条，
   *  背后网格里同一番号的卡就地换上它。 */
  onSaved(box: CoverCropProps['box']): void;
}

/** 数据管理页顶上那排读数卡各通往哪一页。`ads` 是垃圾文件。 */
export type DataCleanupSection = 'review' | 'quality' | 'duplicates' | 'ads' | 'trash';

/** 数据管理页仍由遗留层提供的能力：回执和换页。 */
export interface DataCleanupProps {
  /** 操作回执（遗留层 `actionReceipt`）。`warning` 是做完了但有一部分要留意，走警告音效。 */
  toast(message: string, options?: { warning?: boolean }): void;
  /** 失败回执（遗留层 `actionFailure`）：`action` 是没做成的那件事。 */
  failure(action: string, error: unknown): void;
  /** 点一张读数卡：换到那一页。 */
  open(section: DataCleanupSection): void;
}

/** 重复文件页仍由遗留层提供的能力。 */
export interface DuplicatesProps {
  /** 打开作品详情（遗留层的整页视图）。 */
  openItem(id: number): void;
  /** 操作回执；给了 `undo` 就带一颗撤销键。 */
  toast(message: string, options?: { undo?: () => Promise<void> }): void;
  failure(action: string, error: unknown): void;
}

/** 播放列表页仍由遗留层提供的能力。 */
export interface PlaylistsProps {
  /** 打开一份列表的播放队列，从 `resumeAssetId` 那一个接着播。 */
  openPlaylist(id: number, resumeAssetId: number): void;
  /** 点署名行的头像：去这个人的资料页。`kind` 是 `performer`／`creator`。 */
  openEntity(kind: string, name: string): void;
  /** 此刻能不能悬停翻页：壳的多选、遮挡、减少动效与滚动中都回 false。 */
  canFlip(): boolean;
  /** 操作回执（遗留层 `actionReceipt`）；给了 `undo` 就带一颗撤销键。撤销抛错时回执自己
   *  换成「撤销失败」，页面不另报一次。 */
  toast(message: string, options?: { undo?: () => Promise<void> }): void;
  /** 刷新代次。壳在这一页上要求重读（顶栏「换一批」、从别处写完回来）时加一，页面据此重取，不重挂。 */
  revision?: number;
}

/** 全站 Toast 的两枚字形，遗留层 `icon()` 画好的 SVG 片段。 */
export interface ToastIcons { success: string; error: string }

/** 一条回执。`html` 已由遗留层的 `toastBody` 按调用点的声明转义或原样放行。 */
export interface ToastRequest {
  html: string;
  alert: boolean;
  /** 毫秒；0 与负数按默认的 6 秒，`Infinity` 表示不自己消失。 */
  timeout: number;
  action: { label: string; run(button: HTMLButtonElement): void } | null;
}

export type { StageApi, StageHost, StagePatch, StageRequest } from './stage/stage-api';

/** 接上壳给的宿主，拿回舞台的命令式入口（`stage/stage.tsx`）。只调一次；这一面由 `application-residents.ts` 的
 *  `loadStage` 接着经常驻表的 `openResidentSurface` 在路由树里打开。 */
export declare function configureStage(host: StageHost): StageApi;

export type {
  SettingsEffect, SettingsPanelApi, SettingsPanelHost,
} from './settings-panel/settings-panel-api';

/** 接上壳给的宿主，拿回设置面板的命令式入口（`settings-panel/settings-panel.tsx`）。只调一次；这一面由
 *  `application-residents.ts` 的 `loadSettingsPanel` 接着经常驻表的 `openResidentSurface` 在路由树里打开。 */
export declare function configureSettingsPanel(host: SettingsPanelHost): SettingsPanelApi;

export type { ImmerseApi, ImmerseHost } from './immerse/immerse-api';

/** 接上壳给的宿主，拿回沉浸模式的命令式入口（`immerse/immerse-island.tsx`）。只调一次；这一面由
 *  `application-residents.ts` 的 `loadImmerse` 接着经常驻表的 `openResidentSurface` 在路由树里打开。 */
export declare function configureImmerse(host: ImmerseHost): ImmerseApi;

export type {
  SidebarApi, SidebarChip, SidebarContent, SidebarDot, SidebarFacets, SidebarHost, SidebarProps,
} from './sidebar/sidebar-api';

/** 接上壳给的宿主，拿回侧栏的命令式入口（`sidebar/sidebar-island.tsx`）。只调一次；这一面由 `application-residents.ts` 的
 *  `loadSidebar` 接着经常驻表的 `openResidentSurface` 在路由树里打开。 */
export declare function configureSidebar(host: SidebarHost): SidebarApi;

export type {
  ManageEntry, ManageHeaderApi, ManageHeaderHost, ManageHeaderProps, TrashCount,
} from './manage-header/manage-header-api';

/** 接上壳给的宿主，拿回管理区页头的命令式入口（`manage-header/manage-header-island.tsx`）。只调一次；这一面由
 *  `application-residents.ts` 的 `loadManageHeader` 接着经常驻表的 `openResidentSurface` 在路由树里打开。 */
export declare function configureManageHeader(host: ManageHeaderHost): ManageHeaderApi;

export type {
  BatchAction, BatchContext, BatchDockApi, BatchDockHost, BatchDockProps, BatchGroup,
} from './batch-dock/batch-dock-api';

/** 接上壳给的宿主，拿回批量条的命令式入口（`batch-dock/batch-dock-island.tsx`）。只调一次；这一面由
 *  `application-residents.ts` 的 `loadBatchDock` 接着经常驻表的 `openResidentSurface` 在路由树里打开。 */
export declare function configureBatchDock(host: BatchDockHost): BatchDockApi;

export type { GlowPickerHost } from './glow-picker/glow-picker-api';

/** 接上壳给的宿主（`glow-picker/glow-picker-island.tsx`）。只调一次；卡的内容由 `application-residents.ts` 的
 *  `loadGlowPicker` 接着经常驻表的 `openResidentSurface` 在路由树里画。 */
export declare function configureGlowPicker(host: GlowPickerHost): void;

export type { ManagedOpenProps, ManagedPath, ShellActions } from './router/shell-actions';

/** 路由树那几页的首屏取数（`@peach/history` 的 `openManagedRoute` 经 `connectManagedRoutes` 调它）。 */
export declare function prefetchManagedRoute(path: string, open: object, signal: AbortSignal): Promise<void>;

/** 在 `host` 上挂全站唯一的 Toaster；重复调用是空操作。 */
export declare function mountToaster(host: Element, icons: ToastIcons): void;
/** 发出一条回执；同一个 `id` 再调一次就是改写那一条。 */
export declare function showToast(id: string, request: ToastRequest): void;
