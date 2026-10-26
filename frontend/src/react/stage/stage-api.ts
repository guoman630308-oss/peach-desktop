/* 舞台岛对壳的契约：壳只拿命令式入口，舞台、两座详情、播放器与小窗都在 `peach-app.js` 里。 */
import type { PlayerItem, PlayerResume } from '../../player';
import type { FollowDetailActions, FollowDetailProps } from '../follow-detail/follow-detail';
import type { ItemDetailActions, ItemDetailProps } from '../item-detail/item-detail';
import type { StagePlayerHost } from './stage-player';
import type { JavDetailProps } from '../jav-detail/jav-detail';

export type StageHost = StagePlayerHost;

/** 打开作品详情。动作里没有 `mountPlayer`：媒体框交给舞台自己的播放器。 */
export interface StageItemRequest extends Omit<ItemDetailProps, 'actions'> {
  kind: 'item';
  actions: Omit<ItemDetailActions, 'mountPlayer'>;
  /** 深链带 `?t=` 进来：第一次挂上这一条时从这一刻接着放。 */
  resume?: PlayerResume | null;
}

export interface StageFollowRequest extends Omit<FollowDetailProps, 'actions'> {
  kind: 'follow';
  actions: Omit<FollowDetailActions, 'mountPlayer'>;
  resume?: PlayerResume | null;
}

export interface StageJavRequest extends JavDetailProps { kind: 'jav'; id?: never; resume?: never }
export type StageRequest = StageItemRequest | StageFollowRequest | StageJavRequest;

/** 开着的详情上会变的那几样：选择态与版式推给「接着看」那一排。 */
export type StagePatch = Partial<Pick<ItemDetailProps, 'layout' | 'selectMode' | 'selected' | 'seekSeconds' | 'relatedLimit'>>;

export interface StageApi {
  /** 播放墙的媒体宿主。收起后可交给小窗，详情可认领同一实例。 */
  mountWallPlayer(item: PlayerItem, frame: HTMLElement, signal: AbortSignal): Promise<(continuePlaying?: boolean) => void>;
  /** 打开一条详情：先画骨架、`showModal()`，取完数再换成内容。小窗放着的正是这一条时，内容
   *  画出来那一刻把同一个播放器搬回来。浮窗开着同一种详情时不重开，取完数原地换内容。 */
  open(request: StageRequest): Promise<void>;
  update(patch: StagePatch): void;
  /** 退场动画；拆舞台之前等它演完。 */
  exit(): Promise<void>;
  /** 拆掉舞台。`miniplayer` 为假时正在放的视频不进小窗（显式关闭、换详情、删掉当前条目）。 */
  dispose(options?: { miniplayer?: boolean }): void;
  isOpen(): boolean;
  /** 浮窗此刻开着、能原地换条的那一种详情；没开、在退场或已要求关闭时为空。壳据此决定换条前拆不拆舞台。 */
  showing(): StageRequest['kind'] | null;
  /** 关闭键、Escape 与点浮窗外面走的那一条：交给当前详情自己的 `close`，退场期间再要一次不重复。 */
  requestClose(): void;
  /** 该响应播放快捷键的 video：舞台开着取舞台里的，否则取小窗里的。 */
  activeVideo(): HTMLVideoElement | null;
  toggleTheater(): void;
  /** i 键：详情里进小窗，小窗里展开回详情。 */
  toggleMiniplayer(): void;
  closeMiniplayer(): void;
  miniplayerActive(): boolean;
  miniplayerTakesCard(item: PlayerItem | null | undefined): boolean;
  miniplayerPlay(id: number): void;
  /** 默认封面换了：开着的作品详情把海报位跟着换。 */
  repaintPoster(): void;
}
