/* 卡片悬停预览：起不起、什么时候进入长悬停、怎么收掉。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { configureHoverPreview, releaseHover, releaseHoverPreviews, wireHover } from '../../src/card-art/hover';
import { rememberLocalPlayerPolicy, resetLocalPlayerPolicy } from '../../src/local-player';

const settings = { hoverDelaySeconds: 0, selecting: false, censored: false };

function card(): HTMLElement {
  const el = document.createElement('div');
  el.dataset.mediaCard = '';
  el.innerHTML = '<div data-media-pic=""></div>';
  document.body.append(el);
  return el;
}

const enter = (el: HTMLElement) => el.dispatchEvent(new MouseEvent('mouseenter'));
const leave = (el: HTMLElement) => el.dispatchEvent(new MouseEvent('mouseleave'));

beforeEach(() => {
  rememberLocalPlayerPolicy({ available: true, mode: 'internal', executable: '', label: 'PotPlayer' });
  Object.assign(settings, { hoverDelaySeconds: 0, selecting: false, censored: false });
  configureHoverPreview({
    selecting: () => settings.selecting,
    censored: () => settings.censored,
    delaySeconds: () => settings.hoverDelaySeconds,
  });
  vi.useFakeTimers();
});

afterEach(() => {
  releaseHoverPreviews();
  vi.unstubAllGlobals();
  resetLocalPlayerPolicy();
  vi.useRealTimers();
  document.body.replaceChildren();
});

describe('悬停放大设置', () => {
  it('关闭时不启动计时，开启后按延迟放大', () => {
    const el = card();
    wireHover(el, { id: 7, location: 'pikpak', has_thumb: true });
    enter(el);
    vi.advanceTimersByTime(10000);
    expect(el.hasAttribute('data-previewing')).toBe(false);
    leave(el);
    settings.hoverDelaySeconds = 3;
    enter(el);
    expect(el.hasAttribute('data-previewing')).toBe(true);
    vi.advanceTimersByTime(2999);
    expect(el.hasAttribute('data-longhover')).toBe(false);
    vi.advanceTimersByTime(1);
    expect(el.hasAttribute('data-longhover')).toBe(true);
    leave(el);
    expect(el.hasAttribute('data-previewing')).toBe(false);
    expect(el.hasAttribute('data-longhover')).toBe(false);
  });

  it('计时中关闭后不进入放大状态：延时到点时再读一次设置', () => {
    settings.hoverDelaySeconds = 3;
    const el = card();
    wireHover(el, { id: 7, location: 'pikpak', has_thumb: true });
    enter(el);
    settings.hoverDelaySeconds = 0;
    vi.advanceTimersByTime(3000);
    expect(el.hasAttribute('data-longhover')).toBe(false);
  });
});

describe('远端源只扫接触印相', () => {
  it('叠一层扫视图，不碰封面那张图；移开就撤掉', () => {
    const el = card();
    el.querySelector('[data-media-pic]')!.innerHTML = '<img class="poster cover" src="/cover?code=A">';
    wireHover(el, { id: 7, location: 'pikpak', has_thumb: true });
    expect(el.dataset.hoverMode).toBe('frames');
    enter(el);
    const layer = el.querySelector<HTMLImageElement>('img.ui-hvframes');
    expect(layer?.getAttribute('src')).toBe('/poster?id=7&c=4');
    expect(el.querySelector('img.cover')?.getAttribute('src')).toBe('/cover?code=A');
    leave(el);
    expect(el.querySelector('img.ui-hvframes')).toBeNull();
  });

  it('下一格先拉到手再换上去；没到手就停在当前这格，取图失败也放开闸', () => {
    const preloads: HTMLImageElement[] = [];
    const RealImage = globalThis.Image;
    vi.stubGlobal('Image', class { constructor() { const img = new RealImage(); preloads.push(img); return img; } });
    const el = card();
    wireHover(el, { id: 7, location: 'pikpak', has_thumb: true });
    enter(el);
    const layer = el.querySelector<HTMLImageElement>('img.ui-hvframes')!;
    vi.advanceTimersByTime(430);
    expect(preloads.map(img => img.getAttribute('src'))).toEqual(['/poster?id=7&c=5']);
    vi.advanceTimersByTime(430);
    expect(preloads).toHaveLength(1);
    expect(layer.getAttribute('src')).toBe('/poster?id=7&c=4');
    preloads[0]!.onload!(new Event('load'));
    expect(new URL(layer.src).search).toBe('?id=7&c=5');
    vi.advanceTimersByTime(430);
    expect(preloads[1]!.getAttribute('src')).toBe('/poster?id=7&c=6');
    preloads[1]!.onerror!(new Event('error'));
    vi.advanceTimersByTime(430);
    expect(preloads[2]!.getAttribute('src')).toBe('/poster?id=7&c=6');
    vi.unstubAllGlobals();
  });

  it('没有接触印相就不挂预览', () => {
    const el = card();
    wireHover(el, { id: 7, location: 'pikpak', has_thumb: false });
    enter(el);
    expect(el.querySelector('img.ui-hvframes')).toBeNull();
  });

  it('多选或遮挡时不起', () => {
    const el = card();
    wireHover(el, { id: 7, location: 'pikpak', has_thumb: true });
    settings.selecting = true;
    enter(el);
    expect(el.querySelector('img.ui-hvframes')).toBeNull();
    settings.selecting = false;
    settings.censored = true;
    enter(el);
    expect(el.querySelector('img.ui-hvframes')).toBeNull();
  });
});

describe('本地文件拉真视频', () => {
  it('默认外部播放时悬停不创建内置视频流', () => {
    rememberLocalPlayerPolicy({ available: true, mode: 'external', executable: '', label: 'PotPlayer' });
    const el = card();
    wireHover(el, { id: 1, location: 'local' });
    enter(el);
    vi.advanceTimersByTime(1000);
    expect(el.querySelector('video')).toBeNull();
  });

  it('首次悬停等外部播放器设置返回后也不创建视频', async () => {
    resetLocalPlayerPolicy();
    let respond!: (value: Response) => void;
    const fetcher = vi.fn((_url: string) => new Promise<Response>(resolve => { respond = resolve }));
    vi.stubGlobal('fetch', fetcher);
    const el = card();
    wireHover(el, { id: 1, location: 'local' });
    enter(el);
    await vi.advanceTimersByTimeAsync(340);
    expect(el.querySelector('video')).toBeNull();
    expect(fetcher.mock.calls[0]?.[0]).toBe('/api/configuration/local-player');
    respond(Response.json({ available: true, mode: 'external', executable: '', label: 'PotPlayer' }));
    await vi.advanceTimersByTimeAsync(0);
    expect(el.querySelector('video')).toBeNull();
  });

  it('等播放器设置时离开卡片，迟到的响应不启动预览', async () => {
    resetLocalPlayerPolicy();
    let respond!: (value: Response) => void;
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { respond = resolve })));
    const el = card();
    wireHover(el, { id: 1, location: 'local' });
    enter(el);
    await vi.advanceTimersByTimeAsync(340);
    leave(el);
    respond(Response.json({ available: true, mode: 'internal', executable: '', label: 'PotPlayer' }));
    await vi.advanceTimersByTimeAsync(0);
    expect(el.querySelector('video')).toBeNull();
  });

  it('读不到播放器设置时不发起视频流', async () => {
    resetLocalPlayerPolicy();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')));
    const el = card();
    wireHover(el, { id: 1, location: 'local' });
    enter(el);
    await vi.advanceTimersByTimeAsync(340);
    expect(el.querySelector('video')).toBeNull();
  });

  it('停 340 ms 才起一段静音视频，同一时间只留一个', async () => {
    const first = card();
    const second = card();
    wireHover(first, { id: 1, location: 'local' });
    wireHover(second, { id: 2, location: 'local' });
    expect(first.dataset.hoverMode).toBe('video');
    enter(first);
    await vi.advanceTimersByTimeAsync(339);
    expect(first.querySelector('video.hv')).toBeNull();
    await vi.advanceTimersByTimeAsync(1);
    const video = first.querySelector<HTMLVideoElement>('video.hv');
    expect(video?.getAttribute('src')).toBe('/stream?id=1');
    expect(video?.muted).toBe(true);
    enter(second);
    await vi.advanceTimersByTimeAsync(340);
    expect(first.querySelector('video.hv')).toBeNull();
    expect(second.querySelector('video.hv')).not.toBeNull();
  });

  it('滚动中划过的卡不起；到点时正在滚动也不放', () => {
    const scrolling = window as unknown as { __scrolling?: boolean };
    const el = card();
    wireHover(el, { id: 1, location: 'local' });
    scrolling.__scrolling = true;
    enter(el);
    vi.advanceTimersByTime(340);
    expect(el.querySelector('video.hv')).toBeNull();
    scrolling.__scrolling = false;
    enter(el);
    scrolling.__scrolling = true;
    vi.advanceTimersByTime(340);
    expect(el.querySelector('video.hv')).toBeNull();
    delete scrolling.__scrolling;
  });

  it('遮挡在防抖期间打开，到点也不放', () => {
    const el = card();
    wireHover(el, { id: 1, location: 'local' });
    enter(el);
    settings.censored = true;
    vi.advanceTimersByTime(340);
    expect(el.querySelector('video.hv')).toBeNull();
  });
});

describe('收掉预览', () => {
  it('releaseHover 收掉这张卡自己的预览；releaseHoverPreviews 留下 except 那张', () => {
    const kept = card();
    const dropped = card();
    wireHover(kept, { id: 1, location: 'pikpak', has_thumb: true });
    wireHover(dropped, { id: 2, location: 'pikpak', has_thumb: true });
    enter(kept);
    enter(dropped);
    releaseHoverPreviews(document, kept);
    expect(kept.querySelector('img.ui-hvframes')).not.toBeNull();
    expect(dropped.querySelector('img.ui-hvframes')).toBeNull();
    releaseHover(kept);
    expect(kept.querySelector('img.ui-hvframes')).toBeNull();
  });

  it('卡片重画过、回调跟着旧元素走了，留在画面上的扫视图照样收掉', () => {
    const el = card();
    el.querySelector('[data-media-pic]')!.innerHTML = '<img class="ui-hvframes" src="/poster?id=1&c=4">';
    releaseHoverPreviews();
    expect(el.querySelector('img.ui-hvframes')).toBeNull();
  });
});
