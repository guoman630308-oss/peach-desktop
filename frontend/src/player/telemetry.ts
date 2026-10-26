/* 观看上报：作品走 `/api/activity`，关注条目走 `/api/follow/activity`，都是十秒一次加暂停、结束
 * 与换源时各冲一次。 */
import { api, realDuration } from '@peach/legacy/core';

import type { PlayerItem } from './types';

const cleanup = new WeakMap<HTMLVideoElement, () => void>();

export function stopTelemetry(video: HTMLVideoElement): void {
  cleanup.get(video)?.();
  cleanup.delete(video);
}

function lifecycle(video: HTMLVideoElement, flush: (ended: boolean) => void, stop: () => void, seeking: () => void): void {
  const leave = () => flush(false);
  const empty = () => { stop(); flush(false) };
  video.addEventListener('seeking', seeking);
  video.addEventListener('emptied', empty);
  window.addEventListener('pagehide', leave);
  cleanup.set(video, () => {
    stop(); flush(false);
    video.onplay = null; video.ontimeupdate = null; video.onpause = null; video.onended = null;
    video.removeEventListener('seeking', seeking);
    video.removeEventListener('emptied', empty);
    window.removeEventListener('pagehide', leave);
  });
}

export interface TelemetryOptions {
  /** 侧栏「离开位置」那条的三个节点（进度条、刻度与百分比）；不给就不画。 */
  watched?: string;
  mark?: string;
  ratio?: string;
  /** 放完之后要做的事（沉浸模式接着放下一条）。 */
  onEnded?: () => void;
}

/** 作品的观看上报。
 *
 *  同一个 -1 哨兵：`it.duration||v.duration||0` 对 -1 求值仍是 -1，通过了真值判断，于是
 *  currentTime/-1 得到负比例，面板上就是「离开位置 -3320%」。后端 w_activity 有 `dur > 0` 守卫，
 *  脏比例不会进数据库；坏的只是显示。
 *
 *  十秒一次的上报只有一个定时器。onplay 每次新起一个而只有 onpause 清的话，「播放→拖动→播放」
 *  这类不经过 pause 的序列会把定时器叠起来；更要紧的是离开详情时既不 pause 也不 ended，
 *  setInterval 连着已被销毁的 video 一直跑，每十秒往 /api/activity 打一发。所以 `emptied` 收尾，
 *  拆播放器时 `disposePlayer` 同步冲掉最后一段再整组摘掉。处理器挂在 `on*`
 *  属性上，播放器从舞台搬进小窗时表不停。 */
export function wireTelemetry(item: PlayerItem, video: HTMLVideoElement | null, options: TelemetryOptions = {}): void {
  if (!video) return;
  let last = 0, acc = 0, seeks = 0, timer: ReturnType<typeof setInterval> | null = null;
  const node = (selector?: string) => (selector ? document.querySelector<HTMLElement>(selector) : null);
  const paint = () => {
    const duration = realDuration(item.duration) || realDuration(video.duration); if (!duration) return;
    const ratio = Math.min(video.currentTime / duration, 1);
    const watched = node(options.watched), mark = node(options.mark), text = node(options.ratio);
    if (watched) watched.style.width = `${(ratio * 100).toFixed(1)}%`;
    if (mark) mark.style.left = `${(ratio * 100).toFixed(1)}%`;
    if (text) text.textContent = `${(ratio * 100).toFixed(0)}%`;
  };
  const flush = (ended: boolean) => {
    const duration = realDuration(item.duration) || realDuration(video.duration);
    if (!acc && !ended && !seeks) return;
    (api('/api/activity', { method: 'POST', keepalive: true, body: JSON.stringify(
      { id: item.id, position: video.currentTime, duration, delta: acc, ended: !!ended, seeks }) }) as Promise<{ real_ratio?: number | null } | null>)
      .then((result) => { // 回填面板的真实观看率
        const real = document.getElementById('realTxt');
        if (real && result && result.real_ratio != null) {
          const percent = Math.min(result.real_ratio, 1) * 100;
          real.textContent = `${percent.toFixed(0)}%`;
          const bar = document.getElementById('realBar'); if (bar) bar.style.width = `${percent.toFixed(1)}%`;
        }
      }).catch(() => {});
    acc = 0; seeks = 0;
  };
  const stop = () => { if (timer) { clearInterval(timer); timer = null } };
  video.onplay = () => { last = video.currentTime; stop(); timer = setInterval(() => flush(false), 10000) };
  video.ontimeupdate = () => { const delta = video.currentTime - last; if (!video.paused && !video.seeking && delta > 0 && delta < 2) acc += delta; last = video.currentTime; paint() };
  video.onpause = () => { stop(); flush(false) };
  video.onended = () => { stop(); flush(true); options.onEnded?.() };
  lifecycle(video, flush, stop, () => { seeks++ });
  paint();
}

/** 关注条目的观看上报；第一次开播另记一次播放，状态随回执改写在条目上。 */
export function wireFollowTelemetry(item: PlayerItem & { status?: string | undefined }, video: HTMLVideoElement): void {
  let last = 0, acc = 0, started = false, timer: ReturnType<typeof setInterval> | null = null;
  const flush = (ended: boolean) => {
    const duration = realDuration(item.duration) || realDuration(video.duration);
    if (!acc && !ended) return;
    (api('/api/follow/activity', { method: 'POST', keepalive: true, body: JSON.stringify({
      item: item.id, position: video.currentTime, duration, delta: acc, ended: !!ended,
    }) }) as Promise<unknown>).catch(() => {});
    acc = 0;
  };
  const stop = () => { if (timer) clearInterval(timer); timer = null };
  video.onplay = () => {
    if (!started) {
      started = true;
      (api('/api/follow/play', { method: 'POST', body: JSON.stringify({ item: item.id }) }) as Promise<{ status?: string } | null>)
        .then((result) => { item.status = result?.status || item.status }).catch(() => {});
    }
    last = video.currentTime;
    stop(); timer = setInterval(() => flush(false), 10000);
  };
  video.ontimeupdate = () => {
    const delta = video.currentTime - last; if (!video.paused && !video.seeking && delta > 0 && delta < 2) acc += delta; last = video.currentTime;
  };
  video.onpause = () => { stop(); flush(false) };
  video.onended = () => { stop(); flush(true) };
  lifecycle(video, flush, stop, () => {});
}
