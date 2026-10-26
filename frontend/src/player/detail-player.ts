/* 详情播放器：给舞台播放区的 `<video>` 挂 Video.js 与全部 Peach 控件。
 *
 * - `mountDetailPlayer` 挂一个 Video.js 实例：片源解析、时长校正、出错时从分片退回直读、脱盘判定，
 *   ready 之后挂设置菜单、拖动预览、Media Session、转圈与字幕。
 * - `attachPlayerChrome` 把统计面板、加载速度角标与氛围光接到一块媒体框上。播放器进小窗时摘下，
 *   展开回舞台时接到新的媒体框上，实例本身不重建。
 * - `mountPlayer` 是舞台播放区与沉浸模式每一格的入口：插氛围光画布与统计角标，按作品或关注条目
 *   给片源、海报与上报，返回拆掉这一个播放器的函数。 */
import { detailPosterUrl } from '@peach/card-art';
import { api, esc, fmtClock, fmtSize, icon, realDuration } from '@peach/legacy/core';

import {
  mountPlayerAmbient, mountPlayerMediaSession, mountPlayerQualityControl, mountPlayerSeekPreview,
  mountPlayerSpinner, mountPlayerSubtitles,
} from './controls';
import { disposePlayer } from './dispose';
import { playerHost } from './host';
import { mountImmersePlayer, type ImmersePlayerOptions } from './immerse-player';
import { wirePlayerContextMenu } from './menu';
import { detailPlayer, setDetailPlayer } from './registry';
import { playbackKey, playbackPosition, stopPlaybackPosition, trackPlaybackPosition } from './progress';
import {
  averageBitrate, bufferedAhead, createBufferMeter, fmtLoadRate, fmtSpeed, PLAYER_PANEL_EVENT,
  playerSpeedBits, playerStatsOverlayHtml, playerStatsPlot, pushPlayerStat, streamEntries,
} from './stats';
import { detailStreamSession, detailStreamSource, directDetailSource } from './stream';
import { stopTelemetry, wireFollowTelemetry, wireTelemetry } from './telemetry';
import type { MediaFacts, PlayerItem, PlayerSource, SourceQuality, VjsPlayer } from './types';
import { ensureVideojs } from './videojs';

/** 从小窗展开回来或带 `?t=` 深链进来时接着放的那一刻。 */
export interface PlayerResume { time: number; autoplay: boolean }

export interface DetailPlayerOptions {
  /** 给定片源（关注条目的 `/follow-stream`）；不给就按作品的来源解析。 */
  source?: PlayerSource;
  /** 播放出错时要不要查来源状态判脱盘。关注条目的片源不在本机来源里，不查。 */
  checkSourceStatus?: boolean;
  size?: number | null | undefined;
  poster?: string | undefined;
  qualities?: SourceQuality[] | null;
  /** 清晰度表与字节数，跟默认片源并行解析，回来得比挂载晚。 */
  mediaPromise?: Promise<MediaFacts | null>;
  resume?: PlayerResume | null;
}

/** 媒体框里那三块读数：统计键、统计面板与加载速度角标（`playerStatsOverlayHtml`）。 */
interface PlayerChrome {
  statsButton: HTMLElement | null;
  statsPanel: HTMLElement | null;
  netBadge: HTMLElement | null;
}

/** 一个播放器实例的读数来源。面板画在哪一块框里由 `chrome` 决定，换框时只换它。 */
interface PlayerSession {
  chrome: PlayerChrome | null;
  /** ready 之后统计键才露出来：控件条还没挂上时点它什么也读不到。 */
  ready: boolean;
  updateStats(): void;
}

const sessions = new WeakMap<VjsPlayer, PlayerSession>();

/* 统计面板与加载速度角标的定时器。它们画在舞台的媒体框里，播放器进小窗时跟着舞台一起停。 */
let statsTimer: ReturnType<typeof setInterval> | null = null;
let netTimer: ReturnType<typeof setInterval> | null = null;
let netHideTimer: ReturnType<typeof setTimeout> | null = null;

/** 停掉统计面板与角标的刷新。播放器本身不动。 */
export function stopPlayerPanels(): void {
  if (statsTimer) { clearInterval(statsTimer); statsTimer = null }
  if (netTimer) { clearInterval(netTimer); netTimer = null }
  if (netHideTimer) { clearTimeout(netHideTimer); netHideTimer = null }
}

export async function mountDetailPlayer(
  item: PlayerItem, video: HTMLVideoElement, autoplay: boolean, options: DetailPlayerOptions = {},
): Promise<VjsPlayer | null> {
  const existing = detailPlayer();
  if (existing && !existing.isDisposed()) return existing;
  const host = playerHost();
  const resume = options.resume ?? null;
  if (resume?.autoplay) autoplay = true;
  const source = () => (options.source ? Promise.resolve(options.source) : detailStreamSource(item));
  /* 拉不到就退回原生 video，和「页面里没有 videojs」是同一个兜底出口。 */
  let videojs;
  try { videojs = await ensureVideojs() } catch {
    video.controls = true;
    source().then((next) => {
      video.addEventListener('loadedmetadata', () => {
        if (resume && resume.time > 0) video.currentTime = Math.min(resume.time, realDuration(video.duration) || resume.time);
      }, { once: true });
      video.src = next.src; if (autoplay) video.play().catch(() => {});
    }).catch(() => {});
    return null;
  }
  const player = videojs(video, {
    controls: true, preload: 'metadata', language: 'zh-CN', responsive: true,
    /* 海报与作品当前封面偏好一致，交给原生视频显示。 */
    poster: options.poster || detailPosterUrl(item, host.settings().javImage),
    controlBar: {
      pictureInPictureToggle: true, currentTimeDisplay: false, timeDivider: false,
      durationDisplay: false, remainingTimeDisplay: false,
      // 时间与悬停预览归 Peach 控件，进度条只保留播放与拖动所需的组件。
      progressControl: { seekBar: { mouseTimeDisplay: false, playProgressBar: { timeTooltip: false } } },
    },
  });
  setDetailPlayer(player);
  player.peachItem = item;
  wirePlayerContextMenu(player);
  /* 手机上的画面格按视频自己的比例排（`--peach-video-ratio`）。 */
  player.on('loadedmetadata', () => {
    const tech = player.el()?.querySelector('video');
    if (tech?.videoWidth && tech.videoHeight) player.el().style.setProperty('--peach-video-ratio', `${tech.videoWidth}/${tech.videoHeight}`);
  });
  // 非正时长一律当未知。
  const expected = realDuration(item.duration);
  const statsHistory = { speed: [] as number[], activity: [] as number[], buffer: [] as number[] };
  /* 关注条目的字节数要回源 HEAD 才知道，跟清晰度表同一趟回来，比挂载晚。码率是速度读数的
     换算系数，所以留一个可以后填的口子，别把它固定在挂载那一刻。 */
  let mediaSize = Number(options.size ?? item.size) || 0;
  const meter = createBufferMeter(averageBitrate(mediaSize, item.duration));
  let statsLoaded = 0;
  let correcting = false;
  const live = () => detailPlayer() === player && !player.isDisposed();
  const enforceDuration = () => {
    if (!expected || correcting || !live()) return;
    const reported = Number(player.duration());
    if (!Number.isFinite(reported) || Math.abs(reported - expected) > Math.max(2, expected * .001)) {
      correcting = true; player.duration(expected); queueMicrotask(() => { correcting = false });
    }
  };
  const segmentedNow = () => String(player.currentSource()?.type || '').includes('mpegurl');
  const session: PlayerSession = { chrome: null, ready: false, updateStats: () => {} };
  sessions.set(player, session);
  session.updateStats = () => {
    const statsPanel = session.chrome?.statsPanel;
    if (!statsPanel || statsPanel.hidden || !live()) return;
    const quality = video.getVideoPlaybackQuality ? video.getVideoPlaybackQuality() : null;
    const rect = video.getBoundingClientRect(), current = `${video.videoWidth || item.width || '?'}×${video.videoHeight || item.height || '?'}`;
    const segmented = segmentedNow();
    const stream = detailStreamSession();
    const resources = segmented ? streamEntries(item.id, stream) : [];
    const bytes = resources.reduce((n, entry) => n + (entry.transferSize || entry.encodedBodySize || 0), 0);
    const seconds = resources.reduce((n, entry) => n + (entry.duration || 0), 0) / 1000;
    meter.sample(video);
    const speed = playerSpeedBits(player, item.id, stream, segmented ? null : meter) || (seconds > 0 ? bytes * 8 / seconds : 0);
    /* 分片流按已完成请求的字节累计；渐进源没有这种请求，按前沿推进折算，码率未知时退到秒。 */
    const loaded = segmented ? bytes : (meter.bitrate > 0 ? meter.bytes() : meter.seconds);
    const activity = Math.max(0, loaded - statsLoaded); statsLoaded = loaded;
    const buffer = bufferedAhead(video);
    pushPlayerStat(statsHistory.speed, speed);
    pushPlayerStat(statsHistory.activity, activity);
    pushPlayerStat(statsHistory.buffer, buffer);
    /* 关注条目没有落盘文件名，容器格式只能从片源 MIME 反推；HLS 已经写在传输一侧，不重复。 */
    const named = String(item.name || '');
    const container = (named.includes('.') ? named.split('.').pop() || ''
      : segmented ? '' : String(player.currentSource()?.type || '').split('/').pop() || '').toUpperCase() || '—';
    const speedText = speed ? `${(speed / 1e6).toFixed(1)} Mbps` : '—';
    const byteScale = segmented || meter.bitrate > 0;
    const loadedRow: [string, string, string] = segmented
      ? ['网络活动', `${bytes ? fmtSize(bytes) : '—'} · ${resources.length} 请求`,
        `最近一秒网络活动 ${activity ? fmtSize(activity) : '0 B'}`]
      : ['已下载', byteScale ? `${fmtSize(loaded)}${mediaSize > 0 ? ` / ${fmtSize(mediaSize)}` : ''}` : `${loaded.toFixed(0)} 秒`,
        byteScale ? `最近一秒下载 ${activity ? fmtSize(activity) : '0 B'}` : `最近一秒下载 ${activity.toFixed(1)} 秒`];
    const rows: [string, string, string?][] = [
      ['视频 ID / 会话', stream && !options.source ? `${item.id} / ${stream.slice(0, 8)}` : `${item.id}`],
      ['视口 / 帧', `${Math.round(rect.width)}×${Math.round(rect.height)} / ${quality ? `${quality.totalVideoFrames - quality.droppedVideoFrames} of ${quality.totalVideoFrames}` : '—'}`],
      ['当前 / 最佳分辨率', `${current} / ${item.width || video.videoWidth || '?'}×${item.height || video.videoHeight || '?'}`],
      ['编码 / 传输', `${container} / ${segmented ? 'HLS' : 'HTTP Range'}`],
      ['连接速度', speedText,
        playerStatsPlot(statsHistory.speed, 'speed', Math.max(10e6, ...statsHistory.speed), `连接速度 ${speedText === '—' ? '暂无数据' : speedText}`)],
      [loadedRow[0], loadedRow[1],
        playerStatsPlot(statsHistory.activity, 'activity', Math.max(1, ...statsHistory.activity), loadedRow[2])],
      ['缓冲健康', `${buffer.toFixed(1)} 秒`,
        playerStatsPlot(statsHistory.buffer, 'buffer', 30, `当前可连续播放 ${buffer.toFixed(1)} 秒`)],
      ['播放时间', `${fmtClock(video.currentTime)} / ${fmtClock(expected || player.duration())}`],
      ['日期', new Date().toLocaleString()],
    ];
    statsPanel.innerHTML = `<dl>${rows.map(([key, value, plot]) =>
      `<dt>${esc(key)}</dt><dd${plot ? ' data-player-stats-metric=""' : ''}>${plot || ''}<span>${esc(value)}</span></dd>`).join('')}</dl>`;
  };
  player.on(['loadstart', 'loadedmetadata', 'durationchange', 'error'], enforceDuration);
  let segmentedSource = false, fallbackUsed = false;
  const updateNet = () => {
    const netBadge = session.chrome?.netBadge;
    if (!netBadge || player.isDisposed()) return;
    const segmented = segmentedNow();
    if (!segmented) meter.sample(video);
    const bits = playerSpeedBits(player, item.id, detailStreamSession(), segmented ? null : meter);
    const rate = segmented ? fmtSpeed(bits) : fmtLoadRate(bits, bufferedAhead(video));
    netBadge.innerHTML = `${icon('gauge')}<span class="sr-only">加载速度</span><span>${esc(rate)}</span>`;
  };
  const showNet = () => {
    const netBadge = session.chrome?.netBadge;
    if (!netBadge || !netBadge.isConnected) return;
    netBadge.hidden = false; updateNet();
    if (netTimer) clearInterval(netTimer);
    netTimer = setInterval(updateNet, 500);
  };
  const hideNet = () => {
    const netBadge = session.chrome?.netBadge;
    if (!netBadge) return;
    if (netHideTimer) clearTimeout(netHideTimer);
    netHideTimer = setTimeout(() => { netBadge.hidden = true; if (netTimer) { clearInterval(netTimer); netTimer = null } }, 1400);
  };
  player.on(['loadstart', 'progress', 'waiting', 'stalled'], showNet);
  player.on(['canplay', 'playing'], () => { updateNet(); hideNet() });
  player.on('error', () => {
    if (segmentedSource && !fallbackUsed && !player.isDisposed()) {
      fallbackUsed = true; segmentedSource = false;
      player.src(directDetailSource(item));
      if (autoplay) player.play()?.catch(() => {});
      return;
    }
    // 播到一半掉盘时 video 元素只报通用错误；来源状态才分得清脱盘和文件损坏。
    if (options.checkSourceStatus === false) return;
    void host.loadSourceStatus().then((status) => {
      if (status[String(item.location)] !== false || player.isDisposed()) return;
      player.error({ code: 2, message: `媒体目录无法访问 · ${host.offlineReason(String(item.location))}` });
    });
  });
  player.ready(() => {
    enforceDuration();
    const updateQualities = mountPlayerQualityControl(player, video, item.height, options.qualities ?? null);
    options.mediaPromise?.then((next) => {
      if (!live()) return;
      updateQualities?.(next?.qualities?.length ? next.qualities : null);
      const size = Number(next?.size) || 0;
      if (size > 0 && !mediaSize) { mediaSize = size; meter.bitrate = averageBitrate(size, item.duration) }
    }).catch(() => {});
    mountPlayerSeekPreview(player, item, { thumbnail: !options.source });
    mountPlayerMediaSession(player, item);
    mountPlayerSpinner(player);
    if (!options.source) mountPlayerSubtitles(player, item.id);
    session.ready = true;
    if (session.chrome?.statsButton) session.chrome.statsButton.hidden = false;
  });
  source().then((next) => {
    if (!live()) return;
    segmentedSource = String(next.type || '').includes('mpegurl');
    if (resume && resume.time > 0) player.one('loadedmetadata', () => {
      if (player.isDisposed()) return;
      const duration = realDuration(player.el().querySelector('video')?.duration) || realDuration(player.duration());
      player.currentTime(Math.min(resume.time, duration || resume.time));
    });
    player.src(next);
    enforceDuration(); setTimeout(enforceDuration, 0); setTimeout(enforceDuration, 250);
    if (autoplay) player.play()?.catch(() => {});
  }).catch(() => {});
  return player;
}

const AMBIENT_CANVAS = '<canvas data-ambient-canvas="" width="32" height="18"></canvas>';

/** 媒体框里氛围光画布与三块读数的占位：画布是框的第一个子节点，读数插在 `before` 前面（不给就
 *  放到最后）。已经有了就不再插。 */
export function preparePlayerFrame(frame: HTMLElement, before: Element | null = null): void {
  if (!frame.querySelector(':scope > [data-ambient-canvas]')) frame.insertAdjacentHTML('afterbegin', AMBIENT_CANVAS);
  if (frame.querySelector(':scope > #playerStatsBtn')) return;
  const overlay = document.createElement('template');
  overlay.innerHTML = playerStatsOverlayHtml();
  frame.insertBefore(overlay.content, before);
}

/** 把统计面板、加载速度角标与氛围光接到 `frame` 上（它先经过 `preparePlayerFrame`）。返回摘下的
 *  函数：进小窗时摘，播放器销毁时自己摘。 */
export function attachPlayerChrome(player: VjsPlayer, frame: HTMLElement): () => void {
  const session = sessions.get(player);
  if (!session || player.isDisposed()) return () => {};
  const find = (id: string) => frame.querySelector<HTMLElement>(`:scope > #${id}`);
  const chrome: PlayerChrome = { statsButton: find('playerStatsBtn'), statsPanel: find('playerStats'), netBadge: find('playerNet') };
  session.chrome = chrome;
  const { statsButton, statsPanel } = chrome;
  let offPanels = () => {};
  if (statsButton && statsPanel) {
    statsButton.hidden = !session.ready;
    const closeStats = () => {
      if (statsPanel.hidden) return;
      statsPanel.hidden = true; statsButton.setAttribute('aria-pressed', 'false');
      if (statsTimer) { clearInterval(statsTimer); statsTimer = null }
    };
    statsButton.onclick = () => {
      if (!statsPanel.hidden) { closeStats(); return }
      document.dispatchEvent(new CustomEvent(PLAYER_PANEL_EVENT, { detail: 'stats' }));
      statsPanel.hidden = false; statsButton.setAttribute('aria-pressed', 'true');
      session.updateStats();
      if (statsTimer) clearInterval(statsTimer);
      statsTimer = setInterval(session.updateStats, 1000);
    };
    const closeStatsForOtherPanel = (event: Event) => { if ((event as CustomEvent<string>).detail !== 'stats') closeStats() };
    document.addEventListener(PLAYER_PANEL_EVENT, closeStatsForOtherPanel);
    offPanels = () => document.removeEventListener(PLAYER_PANEL_EVENT, closeStatsForOtherPanel);
  }
  const tech = player.el().querySelector('video');
  const stopAmbient = tech ? mountPlayerAmbient(tech) : () => {};
  let attached = true;
  const detach = () => {
    if (!attached) return;
    attached = false;
    offPanels(); stopAmbient();
    if (session.chrome === chrome) { session.chrome = null; stopPlayerPanels() }
  };
  player.one('dispose', detach);
  return detach;
}

/** 关注条目里的一份媒体（组里的第几条视频）。 */
export interface PlayerMedia { index: number; media_type?: string; size?: number | null }

export interface MountPlayerOptions {
  kind: 'item' | 'follow';
  item: PlayerItem;
  media?: PlayerMedia | null;
  /** 不给就按设置（`detailAutoplay`）。 */
  autoplay?: boolean | undefined;
  resume?: PlayerResume | null;
  /** 挂载之前在媒体框里插氛围光画布与统计角标。默认插；小窗里换片不要这些。 */
  chrome?: boolean;
  /** 挂上之后把实例交出去：小窗与展开要认得这一个。挂不上（Video.js 拉不到、退回原生 video）
   *  给 null。拆得比挂载快时不回调。 */
  onPlayer?: (player: VjsPlayer | null) => void;
  /** 这个播放器已经被小窗接走：拆播放区时不销毁它。 */
  handedOff?: (player: VjsPlayer) => boolean;
}

/** 舞台播放区的入口：给播放区里的 `<video>` 挂播放器，返回拆掉它的函数。
 *
 *  氛围光画布与统计角标在挂载之前插进媒体框：Video.js 一包，`video` 的父级就换成它自己的那层了。
 *  - 作品（`item`）：片源由 `mountDetailPlayer` 按来源解析，海报是本地图，这一次挂载第一次开播时
 *    记一次播放，离开位置与真实观看由 `wireTelemetry` 随播放写回侧栏。
 *  - 关注（`follow`）：片源是 `/follow-stream`，清晰度与字节数（`/follow-qualities`）跟默认片源并行
 *    解析——它要回源抓详情、再 HEAD 一次正片，不能挡住播放器挂载。
 *  - 沉浸模式的一格（`immerse`）：裸 Video.js、这一格自己的流会话，见 `immerse-player.ts`。
 *  返回的清理在舞台卸下这块媒体区时调：换一份媒体只拆这一个播放器；被小窗接走的只摘读数。 */
export function mountPlayer(video: HTMLVideoElement, options: MountPlayerOptions | ImmersePlayerOptions): () => void {
  if (options.kind === 'immerse') return mountImmersePlayer(video, options);
  const { kind, item, media = null, handedOff, onPlayer } = options;
  const host = playerHost();
  const frame = options.chrome === false ? null : video.parentElement;
  if (frame) preparePlayerFrame(frame, video);
  const key = playbackKey(kind, item, media?.index ?? null);
  let detail: DetailPlayerOptions = { resume: options.resume ?? { time: playbackPosition(key, item), autoplay: false } };
  if (kind === 'follow') {
    detail = {
      ...detail,
      source: { src: `/follow-stream?id=${item.id}${media ? `&media=${media.index}` : ''}`, type: media?.media_type || item.media_type || 'video/mp4' },
      checkSourceStatus: false, size: media?.size, poster: item.thumb_url,
      mediaPromise: (api(`/follow-qualities?id=${encodeURIComponent(item.id)}`) as Promise<MediaFacts | null>).catch(() => null),
    };
  } else {
    const poster = detailPosterUrl(item, host.settings().javImage);
    if (poster) video.poster = poster;
  }
  let player: VjsPlayer | null = null, released = false, detachChrome = () => {}, trackedVideo = video;
  const release = () => {
    detachChrome();
    if (player && handedOff?.(player)) return;
    stopPlaybackPosition(trackedVideo);
    if (!player) { stopTelemetry(trackedVideo); return }
    if (detailPlayer() === player) { setDetailPlayer(null); stopPlayerPanels() }
    disposePlayer(player);
  };
  void mountDetailPlayer(item, video, options.autoplay ?? host.settings().detailAutoplay, detail).then((mounted) => {
    player = mounted;
    // 挂载还没回来媒体区就换了：这一个一出来就拆掉。
    if (released) { release(); return }
    trackedVideo = mounted?.el().querySelector('video') ?? video;
    trackPlaybackPosition(key, trackedVideo);
    if (kind === 'follow') wireFollowTelemetry(item, trackedVideo);
    else {
      trackedVideo.addEventListener('play', () => {
        void (api('/api/play', { method: 'POST', body: JSON.stringify({ id: item.id }) }) as Promise<unknown>).catch(() => {});
      }, { once: true });
      wireTelemetry(item, trackedVideo, { watched: '#watched', mark: '#mark', ratio: '#ratioTxt' });
    }
    if (!mounted) { onPlayer?.(null); return }
    if (frame) detachChrome = attachPlayerChrome(mounted, frame);
    onPlayer?.(mounted);
  });
  return () => { released = true; release() };
}
