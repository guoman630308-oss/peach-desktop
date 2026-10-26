/* 详情这一路的播放器实例。舞台与小窗同一时刻只放一条：从详情进小窗时同一个实例整块搬过去，
 * 所以这里只有一个槽位。 */
import type { VjsPlayer } from './types';

let detail: VjsPlayer | null = null;

export const detailPlayer = (): VjsPlayer | null => detail;

export function setDetailPlayer(player: VjsPlayer | null): void { detail = player }

/** 排到下一帧让 Video.js 重新量一次尺寸。下一帧之前播放器可能已经被换片或关小窗销毁，
 *  对着空壳 trigger 会抛「Invalid target」。 */
export function resizeSoon(player: VjsPlayer | null): void {
  if (!player || player.isDisposed()) return;
  requestAnimationFrame(() => { if (!player.isDisposed()) player.trigger('resize') });
}
