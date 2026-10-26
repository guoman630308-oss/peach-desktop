/* 播放器模块（`src/player/`）里不依赖真 Video.js 的几块：片源判据、流会话取消、沉浸模式的每一格、观看上报、
 * 加载读数、画中画的 Media Session 与外挂字幕。挂上真 Video.js 的那一面在 `e2e/stage.test.ts`。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { mountPlayerMediaSession, mountPlayerQualityControl, mountPlayerSubtitles } from '../src/player/controls';
import { configurePlayer, type PlayerHost } from '../src/player/host';
import { averageBitrate, bufferedAhead, fmtLoadRate, pushPlayerStat } from '../src/player/stats';
import {
  cancelDetailStream, detailStreamSource, detailStreamSession, directStreamSource, playableStreamSource,
} from '../src/player/stream';
import { mountPlayer } from '../src/player/detail-player';
import { wireTelemetry } from '../src/player/telemetry';
import type { QualityLevel, QualityLevelList, VjsPlayer } from '../src/player/types';
import * as videojsLoader from '../src/player/videojs';

type FetchCall = { url: string; init: RequestInit | undefined };

function stubFetch(reply: (url: string) => unknown): FetchCall[] {
  const calls: FetchCall[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(reply(url) ?? null), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }));
  return calls;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

const host = (seekSeconds = 10): PlayerHost => ({
  settings: () => ({ ambientMode: false, theaterMode: false, seekSeconds, miniplayer: true, detailAutoplay: false, javImage: 'cover' }),
  saveSettings: () => {},
  toast: () => {},
  loadSourceStatus: async () => ({}),
  offlineReason: () => '',
  stage: () => null,
});

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('片源判据', () => {
  it('保存过的在线作品走关注条目的代理，不问 stream-plan', async () => {
    const calls = stubFetch(() => null);
    const source = await playableStreamSource({ id: 7, location: 'online', follow_item_id: 42 }, 's1');
    expect(source).toEqual({ src: '/follow-stream?id=42', type: 'video/mp4' });
    expect(calls).toEqual([]);
  });

  it('服务端要转码分片就给分片；计划取不到就带会话直读，webm 按扩展名给类型', async () => {
    stubFetch(() => ({ protocol: 'hls', src: '/stream/hls/3/index.m3u8' }));
    expect(await playableStreamSource({ id: 3 }, 's2'))
      .toEqual({ src: '/stream/hls/3/index.m3u8', type: 'application/vnd.apple.mpegurl' });
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }));
    expect(await playableStreamSource({ id: 3, name: 'a.WEBM' }, 's 2'))
      .toEqual({ src: '/stream?id=3&session=s%202', type: 'video/webm' });
    expect(directStreamSource({ id: 4 }, 'x').type).toBe('video/mp4');
  });

  it('详情这一路共用一个会话，取消时按会话通知服务端并清空', async () => {
    const calls = stubFetch(() => ({ cancelled: 1 }));
    await detailStreamSource({ id: 5 });
    const session = detailStreamSession();
    expect(session).not.toBe('');
    expect(calls[0]!.url).toBe(`/api/stream-plan?id=5&session=${encodeURIComponent(session)}`);
    cancelDetailStream();
    expect(detailStreamSession()).toBe('');
    const cancel = calls.at(-1)!;
    expect(cancel.url).toBe(`/api/stream-cancel?session=${encodeURIComponent(session)}`);
    expect(cancel.init).toMatchObject({ method: 'POST', keepalive: true });
    await flush(); await flush();
    expect(JSON.parse(document.documentElement.dataset.peachStreamCancel || 'null')).toEqual({ cancelled: 1 });
  });

  it('STRM 的文件类型使用服务端播放计划', async () => {
    stubFetch(() => ({ protocol: 'range', src: '/stream?id=6', mime_type: 'video/webm' }));
    expect(await playableStreamSource({ id: 6, name: 'ABC-123.strm' }, 'strm'))
      .toEqual({ src: '/stream?id=6', type: 'video/webm' });
  });
});

describe('观看上报', () => {
  beforeEach(() => { vi.useFakeTimers(); });

  it('离开详情时既不 pause 也不 ended：emptied 收尾停表', async () => {
    const calls = stubFetch(() => ({ real_ratio: .5 }));
    const video = document.createElement('video');
    wireTelemetry({ id: 9, duration: -1 }, video);
    video.onplay?.(new Event('play'));
    video.dispatchEvent(new Event('seeking'));
    vi.advanceTimersByTime(10_000);
    expect(calls.filter((call) => call.url === '/api/activity')).toHaveLength(1);
    video.dispatchEvent(new Event('emptied'));
    const settled = calls.length;
    vi.advanceTimersByTime(60_000);
    expect(calls).toHaveLength(settled);
  });

  it('放完冲一次 ended，再交给调用方接着放下一条；-1 时长不画负比例', () => {
    const calls = stubFetch(() => null);
    const video = document.createElement('video');
    const ratio = document.createElement('span');
    ratio.id = 'ratioProbe';
    document.body.append(ratio);
    const onEnded = vi.fn();
    wireTelemetry({ id: 11, duration: -1 }, video, { ratio: '#ratioProbe', onEnded });
    expect(ratio.textContent).toBe('');
    video.onended?.(new Event('ended'));
    const body = JSON.parse(String(calls[0]!.init?.body));
    expect(body).toMatchObject({ id: 11, ended: true, duration: 0 });
    expect(onEnded).toHaveBeenCalledOnce();
    ratio.remove();
  });
});

describe('沉浸模式的每一格', () => {
  /** 一个记账的 Video.js：记下挂了什么片源、`error` 一次性监听由用例触发。 */
  function fakeVideojs() {
    const made: { sources: unknown[]; disposed: boolean; error?: () => void }[] = [];
    const factory = vi.fn((video: HTMLVideoElement) => {
      const record: (typeof made)[number] = { sources: [], disposed: false };
      made.push(record);
      const shell = document.createElement('div');
      shell.append(video);
      return {
        src: (source: unknown) => { record.sources.push(source) },
        one: (_event: string, handler: () => void) => { record.error = handler },
        el: () => shell,
        isDisposed: () => record.disposed,
        pause: () => {},
        dispose: () => { record.disposed = true },
      } as unknown as VjsPlayer;
    });
    vi.spyOn(videojsLoader, 'ensureVideojs').mockResolvedValue(factory);
    return { factory, made };
  }

  it('片源按 stream-plan 交给裸播放器，分片出错退回直读；拆这一格时按会话取消并拆播放器', async () => {
    const calls = stubFetch((url) => (url.startsWith('/api/stream-plan')
      ? { protocol: 'hls', src: '/stream/hls/8/index.m3u8' } : { cancelled: 1 }));
    const { factory, made } = fakeVideojs();
    const video = document.createElement('video');
    const onLoaded = vi.fn();
    const dispose = mountPlayer(video, { kind: 'immerse', item: { id: 8 }, session: 'imm-1', onLoaded });
    await flush(); await flush();
    expect(factory).toHaveBeenCalledWith(video, expect.objectContaining({ controls: false, controlBar: false }));
    expect(made[0]!.sources).toEqual([{ src: '/stream/hls/8/index.m3u8', type: 'application/vnd.apple.mpegurl' }]);
    expect(onLoaded).toHaveBeenCalledOnce();
    made[0]!.error!();
    expect(made[0]!.sources.at(-1)).toEqual({ src: '/stream?id=8&session=imm-1', type: 'video/mp4' });
    dispose();
    dispose();
    expect(made[0]!.disposed).toBe(true);
    const cancels = calls.filter((call) => call.url.startsWith('/api/stream-cancel'));
    expect(cancels.map((call) => call.url)).toEqual(['/api/stream-cancel?session=imm-1']);
  });

  it('片源还没解析出来就拆掉：不再挂播放器，也不回调', async () => {
    stubFetch(() => ({ protocol: 'direct', src: '/stream?id=9' }));
    const { factory } = fakeVideojs();
    const onLoaded = vi.fn();
    const dispose = mountPlayer(document.createElement('video'), { kind: 'immerse', item: { id: 9 }, session: 'imm-2', onLoaded });
    dispose();
    await flush(); await flush();
    expect(factory).not.toHaveBeenCalled();
    expect(onLoaded).not.toHaveBeenCalled();
  });
});

describe('加载读数', () => {
  const buffered = (ranges: [number, number][]) => ({
    length: ranges.length, start: (i: number) => ranges[i]![0], end: (i: number) => ranges[i]![1],
  });

  it('缓冲健康只数播放位置所在那一段；码率要有大小和真时长才算得出', () => {
    const video = { currentTime: 12, buffered: buffered([[0, 5], [10, 30]]) } as unknown as HTMLVideoElement;
    expect(bufferedAhead(video)).toBe(18);
    expect(averageBitrate(1_000_000, 8)).toBe(1_000_000);
    expect(averageBitrate(1_000_000, -1)).toBe(0);
  });

  it('码率未知时报还能往前放多久，不报「× 实时」这类要换算的口径', () => {
    expect(fmtLoadRate(0, 12.4)).toBe('已缓冲 12 秒');
    expect(fmtLoadRate(8 * 2048, 0)).toBe('2 KB/s');
    expect(fmtLoadRate(0, 0)).toBe('加载中…');
  });

  it('三条指标各留 24 个采样，坏值记成 0', () => {
    const samples: number[] = [];
    for (let i = 1; i <= 30; i++) pushPlayerStat(samples, i);
    pushPlayerStat(samples, Number.NaN);
    expect(samples).toHaveLength(24);
    expect(samples[0]).toBe(8);
    expect(samples.at(-1)).toBe(0);
  });
});

function fakePlayer(state: { time: number; duration: number }) {
  const handlers = new Map<string, (() => void)[]>();
  const player = {
    on(events: string | string[], fn: () => void) {
      for (const event of [events].flat()) handlers.set(event, [...handlers.get(event) || [], fn]);
    },
    currentTime(value?: number) { if (value !== undefined) state.time = value; return state.time },
    duration: () => state.duration,
    playbackRate: () => 1,
    play: () => Promise.resolve(),
    pause: () => {},
    isDisposed: () => false,
    addRemoteTextTrack: vi.fn(),
  };
  const fire = (event: string) => handlers.get(event)?.forEach((fn) => fn());
  return { player: player as unknown as VjsPlayer, raw: player, fire };
}

describe('播放器清晰度', () => {
  function menu(entries: QualityLevel[], selectedIndex = 0) {
    configurePlayer(host());
    const root = document.createElement('div'), bar = document.createElement('div'), video = document.createElement('video');
    bar.className = 'vjs-control-bar';
    bar.innerHTML = '<div class="vjs-subtitles-control vjs-control"><button type="button">CC</button></div>';
    const container = document.createElement('media-container'); container.append(video, bar);
    root.append(container); document.body.append(root);
    const { player, fire } = fakePlayer({ time: 0, duration: 100 });
    const events = new Map<string, () => void>();
    const levels = Object.assign(entries, { selectedIndex, on: (names: string | string[], fn: () => void) => {
      for (const name of [names].flat()) events.set(name, fn);
    } }) as QualityLevelList;
    Object.assign(player, { el: () => root, qualityLevels: () => levels,
      paused: () => true, ended: () => false, isFullscreen: () => false, muted: () => false, volume: () => 1, trigger: () => {} });
    mountPlayerQualityControl(player, video, 2160);
    const open = () => {
      bar.querySelector<HTMLButtonElement>('.vjs-peach-settings-toggle')!.click();
      bar.querySelector<HTMLButtonElement>('[data-player-quality-view]')!.click();
    };
    const labels = () => [...bar.querySelectorAll('[data-player-quality-option]')].map((node) => node.textContent);
    return { bar, video, levels, open, labels, change: () => events.get('change')?.(), notify: (name: string) => events.get(name)?.(),
      close: () => { fire('dispose'); root.remove() } };
  }

  it('单档 HLS 用实际视频尺寸命名，不展示轨道地址或虚构自动档', () => {
    const view = menu([{ id: '0-https://example.test/index.m3u8?session=private', enabled: true }]);
    try {
      Object.defineProperties(view.video, { videoWidth: { value: 1920 }, videoHeight: { value: 1080 } });
      view.video.dispatchEvent(new Event('loadedmetadata'));
      view.open();
      expect(view.labels()).toEqual(['1080p']);
      expect(view.bar.querySelector('[data-player-quality-option]')?.getAttribute('aria-checked')).toBe('true');
      expect(view.bar.querySelector('[data-player-quality-badge]')?.textContent).toBe('HD');
      view.bar.querySelector<HTMLButtonElement>('[data-player-quality-option]')!.click();
      expect(view.levels[0]!.enabled).toBe(true);
    } finally { view.close() }
  });

  it('尺寸未取得的单档不拿源文件高度猜清晰度', () => {
    const view = menu([{ id: 'private-stream-url', enabled: true }]);
    try {
      view.open();
      expect(view.labels()).toEqual(['当前画质']);
      expect(view.bar.querySelector<HTMLElement>('[data-player-quality-badge]')!.hidden).toBe(true);
    } finally { view.close() }
  });

  it('轨道逐个加入时保留自动选择', () => {
    const entries = [{ width: 640, height: 360, enabled: true }];
    const view = menu(entries);
    try {
      view.open();
      entries.push({ width: 1920, height: 1080, enabled: true });
      view.notify('addqualitylevel');
      expect(view.labels()).toEqual(['自动', '360p', '1080p']);
      expect(view.bar.querySelector('[data-player-quality-option="auto"]')?.getAttribute('aria-checked')).toBe('true');
    } finally { view.close() }
  });

  it('自动档的角标跟随正在播放的轨道，未知档位使用线路名', () => {
    const view = menu([{ width: 640, height: 360, enabled: true }, { width: 3840, height: 2160, enabled: true },
      { id: 'private-url', enabled: true }]);
    try {
      view.open();
      expect(view.labels()).toEqual(['自动', '360p', '2160p', '线路 3']);
      const badge = view.bar.querySelector<HTMLElement>('[data-player-quality-badge]')!;
      expect(badge.hidden).toBe(true);
      view.levels.selectedIndex = 1; view.change();
      expect(badge.textContent).toBe('4K');
      expect(badge.hidden).toBe(false);
    } finally { view.close() }
  });

  it('菜单开着时换轨与尺寸变化只就地更新清晰度面板，焦点留在原档', () => {
    const entries = [{ width: 640, height: 360, enabled: true }, { width: 1920, height: 1080, enabled: true }];
    const view = menu(entries);
    try {
      view.open();
      view.bar.querySelector<HTMLElement>('[data-player-quality-option="1"]')!.focus();
      view.levels.selectedIndex = 1; view.change();
      view.video.dispatchEvent(new Event('resize'));
      entries.push({ width: 3840, height: 2160, enabled: true });
      view.notify('addqualitylevel');
      const panel = view.bar.querySelector('.vjs-peach-settings-menu')!.lastElementChild!;
      expect(panel.querySelector('strong')?.textContent).toBe('清晰度');
      expect([...panel.querySelectorAll('[data-player-quality-option]')].map((node) => node.textContent))
        .toEqual(['自动', '360p', '1080p', '2160p']);
      expect((document.activeElement as HTMLElement).dataset.playerQualityOption).toBe('1');
    } finally { view.close() }
  });

  it('主菜单开着时尺寸变化只改清晰度那一行的读数', () => {
    const view = menu([{ id: 'private-stream-url', enabled: true }]);
    try {
      view.bar.querySelector<HTMLButtonElement>('.vjs-peach-settings-toggle')!.click();
      const row = view.bar.querySelector('[data-player-quality-view]')!;
      expect(row.querySelector('b')?.textContent).toBe('当前画质');
      Object.defineProperties(view.video, { videoWidth: { value: 1920 }, videoHeight: { value: 1080 } });
      view.video.dispatchEvent(new Event('resize'));
      expect(view.bar.querySelector('[data-player-quality-view]')).toBe(row);
      expect(row.querySelector('b')?.textContent).toBe('1080p');
    } finally { view.close() }
  });
});

describe('画中画的快进快退', () => {
  it('逐个登记动作，一个不认不带走其余；步长取设置；换片时整组摘掉', () => {
    configurePlayer(host(15));
    const handlers = new Map<string, MediaSessionActionHandler | null>();
    const positions: unknown[] = [];
    vi.stubGlobal('navigator', { mediaSession: {
      setActionHandler(action: string, handler: MediaSessionActionHandler | null) {
        if (action === 'seekto' && handler) throw new TypeError('unsupported');
        handlers.set(action, handler);
      },
      setPositionState(state?: unknown) { positions.push(state) },
    } });
    const state = { time: 100, duration: 600 };
    const { player, fire } = fakePlayer(state);
    mountPlayerMediaSession(player, { id: 1, duration: 600 });
    expect([...handlers.keys()]).toEqual(['play', 'pause', 'seekbackward', 'seekforward']);
    handlers.get('seekforward')!({ action: 'seekforward' });
    expect(state.time).toBe(115);
    handlers.get('seekbackward')!({ action: 'seekbackward', seekOffset: 200 });
    expect(state.time).toBe(0);
    expect(positions[0]).toEqual({ duration: 600, position: 100, playbackRate: 1 });
    state.time = 700; fire('timeupdate');
    expect(positions).toHaveLength(1);
    fire('dispose');
    expect([...handlers.values()].every((handler) => handler === null)).toBe(true);
  });
});

describe('外挂字幕', () => {
  it('能播的轨全挂上，一条都不预先打开', async () => {
    stubFetch(() => ({ subtitles: [
      { playable: true, src: '/sub/1.vtt', language: 'zh', label: '简体' },
      { playable: false, src: '/sub/2.ass', language: 'ja', label: '日语' },
      { playable: true, src: '/sub/3.vtt', label: '繁体' },
    ] }));
    const { player, raw } = fakePlayer({ time: 0, duration: 0 });
    mountPlayerSubtitles(player, 21);
    await flush(); await flush();
    expect(raw.addRemoteTextTrack.mock.calls).toEqual([
      [{ kind: 'subtitles', src: '/sub/1.vtt', srclang: 'zh', label: '简体', default: false }, true],
      [{ kind: 'subtitles', src: '/sub/3.vtt', srclang: '', label: '繁体', default: false }, true],
    ]);
  });
});
