/* 播放统计：缓冲读数、加载速度与统计面板的小图。
 *
 * 渐进下载（HTTP Range）量不到网络：浏览器用一条长连接边下边播，请求在播放期间不结束，
 * resource timing 里就一直不出现新条目。实测本地 MP4 播到 37 秒时仍只有挂载那两条、字节数停在
 * 862 KB，面板于是显示「— · 0 请求」。HLS 是另一回事，每个分片都是一次独立完成的请求，VHS
 * 自己也报 bandwidth，resource timing 那套口径只对它成立。
 * 渐进源改看缓冲前沿的推进：每秒新推进的秒数 × 平均码率就是字节速率。码率要有文件大小才算得
 * 出来，关注条目的大小由 `/follow-qualities` 回源 HEAD 带回；连上游都不给 content-length 时没有
 * 任何办法把秒换成字节，角标改报还能往前放多久。缓冲吃满后浏览器停拉，增量归零，此时保留上一次
 * 读数而不是跳回 0——那不是速度掉了，是没有在下载。 */
import { esc, icon, realDuration } from '@peach/legacy/core';

import type { VjsPlayer } from './types';

export function bufferedAhead(video: HTMLVideoElement): number {
  const at = video.currentTime || 0;
  for (let i = 0; i < video.buffered.length; i++) {
    if (video.buffered.start(i) <= at && video.buffered.end(i) >= at) return Math.max(0, video.buffered.end(i) - at);
  }
  return 0;
}

/* 缓冲前沿：当前播放位置所在那段缓冲的末端。看的是前沿而不是缓冲区总长——播放时浏览器会驱逐
   播过的部分，总长几乎恒定，拿它当下载量会得出「一直是 0」。 */
function bufferedFrontier(video: HTMLVideoElement): number {
  const at = video.currentTime || 0;
  for (let i = 0; i < video.buffered.length; i++) {
    if (video.buffered.start(i) <= at + .25 && video.buffered.end(i) >= at) return video.buffered.end(i);
  }
  return video.buffered.length ? video.buffered.end(video.buffered.length - 1) : 0;
}

const BUFFER_METER_WINDOW_MS = 3000;

export function averageBitrate(size: unknown, duration: unknown): number {
  const bytes = Number(size) || 0, seconds = realDuration(duration) || 0;
  return bytes > 0 && seconds > 0 ? bytes * 8 / seconds : 0;
}

export interface BufferMeter {
  bitrate: number;
  readonly bits: number;
  readonly seconds: number;
  bytes(): number;
  sample(video: HTMLVideoElement | null): number;
}

export function createBufferMeter(bitrate: number): BufferMeter {
  const samples: { at: number; advanced: number }[] = [];
  let last: { at: number; frontier: number; ct: number } | null = null, advanced = 0, bits = 0;
  return {
    bitrate: Number(bitrate) || 0,
    get bits() { return bits },
    get seconds() { return advanced },
    bytes() { return this.bitrate > 0 ? advanced * this.bitrate / 8 : 0 },
    sample(video) {
      if (!video) return bits;
      const at = performance.now(), frontier = bufferedFrontier(video), ct = video.currentTime || 0;
      if (last) {
        const gap = (at - last.at) / 1000;
        /* seek 会把前沿整段挪走，那不是这一秒下载了几十分钟；判据是播放位置自己跳了。 */
        const seeked = Math.abs(ct - last.ct) > gap * 4 + 1;
        const step = frontier - last.frontier;
        if (!seeked && step > 0) advanced += step;
        /* 面板和角标都关着时没人采样，再打开时两点隔了几分钟，窗口要重新起算。 */
        if (gap * 1000 > BUFFER_METER_WINDOW_MS * 2) samples.length = 0;
      }
      last = { at, frontier, ct };
      samples.push({ at, advanced });
      while (samples.length > 2 && at - samples[0]!.at > BUFFER_METER_WINDOW_MS) samples.shift();
      const first = samples[0]!;
      const span = (at - first.at) / 1000, gained = advanced - first.advanced;
      if (span >= .5 && gained > 0 && this.bitrate > 0) bits = gained * this.bitrate / span;
      return bits;
    },
  };
}

const PLAYER_STATS_HISTORY = 24;

export function pushPlayerStat(samples: number[], value: number): void {
  samples.push(Number.isFinite(value) && value > 0 ? value : 0);
  if (samples.length > PLAYER_STATS_HISTORY) samples.splice(0, samples.length - PLAYER_STATS_HISTORY);
}

/** 设置面板和播放统计都盖在画面上，同时开就互相遮挡。开哪个都往 document 广播一次，另一个
 *  自己收起：两块面板挂在不同的作用域里，共享一个事件名比互相持有引用干净。 */
export const PLAYER_PANEL_EVENT = 'peach-player-panel';

export function playerStatsPlot(samples: number[], kind: 'speed' | 'activity' | 'buffer', ceiling: number, label: string): string {
  const values: (number | null)[] = [...Array<null>(Math.max(0, PLAYER_STATS_HISTORY - samples.length)).fill(null), ...samples];
  const top = Math.max(1, ceiling || 0);
  const bars = values.map((value) => {
    if (value === null) return '<i aria-hidden="true"></i>';
    const level = value <= 0 ? 0 : Math.max(.08, Math.min(1, value / top));
    const state = kind === 'buffer' ? (value < 5 ? 'low' : value < 15 ? 'mid' : 'good') : 'on';
    return `<i data-bar="${state}" style="height:${(level * 100).toFixed(1)}%" aria-hidden="true"></i>`;
  }).join('');
  return `<span data-player-stats-plot="${kind}" role="img" aria-label="${esc(label)}">${bars}</span>`;
}

/** 统计键、加载速度角标与统计面板。作品详情与关注详情共用这一组；挂载在 Video.js 包住
 *  `<video>` 之前插到它前面，Video.js 一包，`video` 的父级就换成它自己的那层了。 */
export function playerStatsOverlayHtml(): string {
  return `<button data-player-stats-button="" id="playerStatsBtn" aria-label="播放统计" title="播放统计" aria-pressed="false" hidden>${icon('chart')}</button>
       <div data-player-net="" id="playerNet" role="status" aria-live="polite" hidden></div>
       <div data-player-stats="" id="playerStats" role="status" hidden></div>`;
}

export function streamEntries(id: number, session = ''): PerformanceResourceTiming[] {
  const encoded = session ? encodeURIComponent(session) : '';
  return (performance.getEntriesByType('resource') as PerformanceResourceTiming[]).filter((entry) => entry.name.includes('/stream')
    && (entry.name.includes(`/stream?id=${id}`) || entry.name.includes(`/stream/hls/${id}/`))
    && (!encoded || entry.name.includes(`session=${encoded}`)));
}

export function streamSpeedBits(id: number, session = ''): number {
  const entries = streamEntries(id, session);
  const bytes = entries.reduce((n, entry) => n + (entry.transferSize || entry.encodedBodySize || 0), 0);
  const seconds = entries.reduce((n, entry) => n + (entry.duration || 0), 0) / 1000;
  return bytes > 0 && seconds > 0 ? bytes * 8 / seconds : 0;
}

export function playerSpeedBits(player: VjsPlayer | null, id: number, session = '', meter: BufferMeter | null = null): number {
  let bandwidth = 0;
  try { bandwidth = Number(player?.bandwidth?.()) || 0 } catch { /* 播放引擎没有带宽读数 */ }
  if (bandwidth > 0) return bandwidth;
  /* 传了 meter 就是渐进源：它的 resource timing 本来就量不到，不能拿别的条目顶上。 */
  return meter ? Number(meter.bits) || 0 : streamSpeedBits(id, session);
}

export function fmtSpeed(bits: number): string {
  if (!Number.isFinite(bits) || bits <= 0) return '加载中…';
  const bytes = bits / 8;
  return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB/s` : `${Math.max(1, Math.round(bytes / 1024))} KB/s`;
}

/** 码率未知时字节速率无从换算，退回已经缓冲的秒数：那是这种情况下唯一还能直接用的读数
 *  ——现在断网还能往前放多久。 */
export function fmtLoadRate(bits: number, ahead: number): string {
  if (bits > 0) return fmtSpeed(bits);
  return ahead > 0 ? `已缓冲 ${Math.round(ahead)} 秒` : fmtSpeed(0);
}
