/* 播放器向外要的几样东西：设置、回执、来源状态与舞台元素。
 *
 * 播放器是命令式模块，不持有这些状态：设置归遗留壳的 `appSettings`（存在 localStorage 的
 * `peach.settings.v1` 里），回执归全站那一份 Toast，舞台的氛围光与剧场模式类挂在 `#stage` 上。
 * 挂播放器之前由持有方调一次 `configurePlayer` 接上。 */

/** 播放器读写的那几项设置。对象由持有方给出，播放器改了就地写回再调 `save`。 */
export interface PlayerSettings {
  ambientMode: boolean;
  theaterMode: boolean;
  seekSeconds: number;
  miniplayer: boolean;
  detailAutoplay: boolean;
  /** 番号作品开播前那层封面取官方封面还是预览图（`card-art` 的 `detailPosterUrl` 按它挑）。 */
  javImage: string;
}

export interface PlayerHost {
  settings(): PlayerSettings;
  saveSettings(): void;
  toast(text: string, options: { timeout?: number; warn?: boolean }): void;
  /** 各来源此刻在不在线（`/api/sources` 的快照，`false` 就是脱盘）。 */
  loadSourceStatus(): Promise<Record<string, boolean>>;
  /** 脱盘时给人看的那一句原因。 */
  offlineReason(location: string): string;
  /** 舞台元素：氛围光的 `--video-glow`、`data-ambient` 与 `data-theater` 挂在它身上。 */
  stage(): HTMLElement | null;
}

let current: PlayerHost | null = null;

export function configurePlayer(host: PlayerHost): void { current = host }

export function playerHost(): PlayerHost {
  if (!current) throw new Error('播放器还没有接上宿主（configurePlayer）');
  return current;
}
