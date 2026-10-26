import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { playbackKey, playbackPosition, stopPlaybackPosition, trackPlaybackPosition } from '../src/player/progress';
import { disposePlayer } from '../src/player/dispose';
import { stopTelemetry, wireFollowTelemetry } from '../src/player/telemetry';
import type { VjsPlayer } from '../src/player/types';
import { mountDetailPlayer } from '../src/player/detail-player';
import { configurePlayer } from '../src/player/host';
import { setDetailPlayer } from '../src/player/registry';
import * as videojsLoader from '../src/player/videojs';

const item = { id: 12, duration: 300, leave_ratio: .4 };
const key = playbackKey('item', item);
let video: HTMLVideoElement;

beforeEach(() => { localStorage.clear(); video = document.createElement('video') });
afterEach(() => { stopPlaybackPosition(video); stopTelemetry(video); vi.useRealTimers(); vi.unstubAllGlobals() });

describe('视频续播位置', () => {
  it('关注片段的实际时长独立于帖子时长，恢复到该片段的保存位置', async () => {
    configurePlayer({ settings: () => ({ ambientMode: false, theaterMode: false, seekSeconds: 10,
      miniplayer: true, detailAutoplay: false, javImage: 'cover' }), saveSettings: () => {}, toast: () => {},
      loadSourceStatus: async () => ({}), offlineReason: () => '', stage: () => null });
    setDetailPlayer(null);
    const root = document.createElement('div'); root.append(video);
    Object.defineProperty(video, 'duration', { value: 108 });
    let time = 0, duration = 108, disposed = false;
    const listeners = new Map<string, (() => void)[]>();
    const player = {
      el: () => root, isDisposed: () => disposed, ready: () => {}, off: () => {},
      on: (events: string | string[], callback: () => void) => {
        for (const event of [events].flat()) listeners.set(event, [...listeners.get(event) || [], callback]);
      },
      one: (event: string, callback: () => void) => listeners.set(event, [...listeners.get(event) || [], callback]),
      duration: (seconds?: number) => { if (seconds !== undefined) duration = seconds; return duration },
      currentTime: (seconds?: number) => { if (seconds !== undefined) time = seconds; return time },
      src: () => listeners.get('loadedmetadata')?.forEach((callback) => callback()),
    } as unknown as VjsPlayer;
    vi.spyOn(videojsLoader, 'ensureVideojs').mockResolvedValue(() => player);
    try {
      await mountDetailPlayer({ id: 12, duration: 30 }, video, false,
        { source: { src: '/follow-stream?id=12&media=2', type: 'video/mp4' }, resume: { time: 97, autoplay: false } });
      await Promise.resolve();
      expect(time).toBe(97);
    } finally { disposed = true; setDetailPlayer(null) }
  });

  it('暂停、关闭和页面退出保留当前秒数，存储读取独立于条目的旧比例', () => {
    trackPlaybackPosition(key, video);
    video.dispatchEvent(new Event('loadedmetadata'));
    video.currentTime = 53.25;
    video.dispatchEvent(new Event('seeked'));
    expect(playbackPosition(key, item)).toBe(53.25);
    video.currentTime = 17;
    window.dispatchEvent(new Event('pagehide'));
    expect(playbackPosition(key, item)).toBe(17);
    video.currentTime = 8;
    stopPlaybackPosition(video);
    expect(playbackPosition(key, item)).toBe(8);
    video.currentTime = 29;
    window.dispatchEvent(new Event('pagehide'));
    expect(playbackPosition(key, item)).toBe(8);
  });

  it('未加载元数据与换源时的零位置不会覆盖已保存秒数', () => {
    trackPlaybackPosition(key, video);
    video.dispatchEvent(new Event('loadedmetadata'));
    video.currentTime = 91;
    video.dispatchEvent(new Event('pause'));
    video.dispatchEvent(new Event('emptied'));
    video.currentTime = 0;
    stopPlaybackPosition(video);
    expect(playbackPosition(key, item)).toBe(91);
    trackPlaybackPosition(key, video);
    video.dispatchEvent(new Event('timeupdate'));
    stopPlaybackPosition(video);
    expect(playbackPosition(key, item)).toBe(91);
  });

  it('本地旧记录按离开比例恢复，已播完与未知时长从头开始', () => {
    expect(playbackPosition(key, item)).toBe(120);
    expect(playbackPosition(key, { ...item, duration: -1 })).toBe(0);
    expect(playbackPosition(key, { ...item, leave_ratio: 1 })).toBe(0);
    trackPlaybackPosition(key, video);
    video.dispatchEvent(new Event('loadedmetadata'));
    video.currentTime = 300;
    Object.defineProperty(video, 'ended', { value: true });
    video.dispatchEvent(new Event('ended'));
    expect(playbackPosition(key, item)).toBe(0);
  });

  it('关注与本地身份互不覆盖，多视频按媒体序号分别记位置', () => {
    expect(playbackKey('follow', item)).not.toBe(key);
    expect(playbackKey('follow', item, 0)).not.toBe(playbackKey('follow', item, 1));
    expect(playbackKey('item', { ...item, location: 'online', follow_item_id: 42 }))
      .toBe(playbackKey('follow', { id: 42 }));
  });

  it('损坏的存储与存储拒绝不会阻断播放器', () => {
    localStorage.setItem(`peach.playback-position.v1:${key}`, 'broken');
    expect(playbackPosition(key, item)).toBe(120);
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') });
    trackPlaybackPosition(key, video);
    video.dispatchEvent(new Event('loadedmetadata'));
    expect(() => stopPlaybackPosition(video)).not.toThrow();
    vi.restoreAllMocks();
  });
});

describe('关注视频关闭上报', () => {
  it('暂停状态的恢复定位不累计观看时间', () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    wireFollowTelemetry(item, video);
    video.currentTime = .7;
    video.ontimeupdate?.(new Event('timeupdate'));
    video.onpause?.(new Event('pause'));
    expect(fetch).not.toHaveBeenCalled();
  });

  it('换源停止定时器，继续播放仍能累计与上报', () => {
    vi.useFakeTimers();
    const calls: RequestInit[] = [];
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
      calls.push(init);
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    }));
    Object.defineProperty(video, 'paused', { value: false });
    wireFollowTelemetry(item, video);
    video.onplay?.(new Event('play'));
    video.currentTime = 1;
    video.ontimeupdate?.(new Event('timeupdate'));
    video.dispatchEvent(new Event('emptied'));
    const count = calls.length;
    vi.advanceTimersByTime(20_000);
    expect(calls).toHaveLength(count);
    video.currentTime = 42;
    video.onplay?.(new Event('play'));
    video.currentTime = 43;
    video.ontimeupdate?.(new Event('timeupdate'));
    video.onpause?.(new Event('pause'));
    expect(JSON.parse(String(calls.at(-1)?.body))).toMatchObject({ item: 12, position: 43, delta: 1 });
    expect(calls).toHaveLength(count + 1);
  });

  it('同步销毁保留最后一段并停止定时器与页面退出监听', () => {
    vi.useFakeTimers();
    const calls: RequestInit[] = [];
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
      calls.push(init);
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    }));
    const root = document.createElement('div'); root.append(video);
    Object.defineProperty(video, 'paused', { value: false });
    wireFollowTelemetry(item, video);
    video.onplay?.(new Event('play'));
    video.currentTime = 1;
    video.ontimeupdate?.(new Event('timeupdate'));
    video.currentTime = 42;
    video.dispatchEvent(new Event('seeking'));
    disposePlayer({ el: () => root, pause: () => {}, isDisposed: () => false, dispose: () => {} } as unknown as VjsPlayer);
    expect(JSON.parse(String(calls.at(-1)?.body))).toMatchObject({ item: 12, position: 42, delta: 1 });
    expect(calls.at(-1)?.keepalive).toBe(true);
    const count = calls.length;
    vi.advanceTimersByTime(60_000);
    window.dispatchEvent(new Event('pagehide'));
    expect(calls).toHaveLength(count);
  });
});
