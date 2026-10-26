/* 拆一个 Video.js 实例。详情、小窗与沉浸模式的每一格都走这一条。 */
import type { VjsPlayer } from './types';
import { stopPlaybackPosition } from './progress';
import { stopTelemetry } from './telemetry';

/** 拆掉一个播放器：暂停后同步保存位置、冲掉观看末段并清理定时器与监听，再销毁实例。 */
export function disposePlayer(player: VjsPlayer | null): void {
  if (!player || player.isDisposed()) return;
  const video = player.el()?.querySelector('video');
  try { player.pause() } catch { /* 已拆 */ }
  if (video) {
    stopPlaybackPosition(video);
    stopTelemetry(video);
  }
  try { player.dispose() } catch { /* 已拆 */ }
}
