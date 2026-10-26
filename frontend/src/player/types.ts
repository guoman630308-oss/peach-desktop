/* Peach 播放会话接口与条目形状；Video.js 10 通过本地 ESM 模块提供媒体与控件。 */

export type PlayerEvents = string | string[];

/** Video.js 的质量轨道表（HLS/DASH 的自适应档）。 */
export interface QualityLevel { width?: number; height?: number; id?: string; enabled: boolean }
export interface QualityLevelList {
  length: number;
  selectedIndex?: number;
  [index: number]: QualityLevel;
  on?(events: PlayerEvents, handler: () => void): void;
}

export interface PlayerSource { src: string; type: string }

export interface VjsPlayer {
  /** 挂载时记下的条目：右键菜单复制网址按它取 id。 */
  peachItem?: PlayerItem;
  on(events: PlayerEvents, handler: (...args: unknown[]) => void): void;
  off(events: PlayerEvents, handler: (...args: unknown[]) => void): void;
  one(events: PlayerEvents, handler: (...args: unknown[]) => void): void;
  ready(handler: () => void): void;
  el(): HTMLElement;
  isDisposed(): boolean;
  dispose(): void;
  trigger(event: string): void;
  paused(): boolean;
  ended(): boolean;
  error(): unknown;
  error(value: { code: number; message: string }): void;
  play(): Promise<void> | undefined;
  pause(): void;
  currentTime(): number;
  currentTime(seconds: number): void;
  duration(): number;
  duration(seconds: number): void;
  playbackRate(): number;
  playbackRate(rate: number): void;
  loop(): boolean;
  loop(value: boolean): void;
  muted(): boolean;
  volume(): number;
  isFullscreen(): boolean;
  poster(url: string): void;
  src(source: PlayerSource): void;
  currentSrc(): string;
  currentSource(): { type?: string } | undefined;
  qualityLevels?(): QualityLevelList;
  bandwidth?(): number;
  addRemoteTextTrack(track: Record<string, unknown>, manualCleanup: boolean): void;
}

export type VideojsFactory = (video: HTMLVideoElement, options: Record<string, unknown>) => VjsPlayer;

/** 作品与关注条目共有的那几样。作品来自 `/api/item`，关注来自 `/api/follow/item`。 */
export interface PlayerItem {
  id: number;
  name?: string;
  title?: string;
  location?: string;
  cost?: string;
  duration?: number | null;
  width?: number | null;
  height?: number | null;
  size?: number | null;
  follow_item_id?: number | null;
  media_type?: string;
  thumb_url?: string;
  [field: string]: unknown;
}

/** 来源自己给的一档清晰度（从高到低）。 */
export interface SourceQuality { height: number; label?: string }
export interface MediaFacts { qualities?: SourceQuality[] | null; size?: number | null }
