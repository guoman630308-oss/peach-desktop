/* 沉浸模式（ADR-0031 第 11e 步）：`/immerse` 的全屏连播。片单、每一格的播放器、加载提示、手势、
 * 动作键、作者与标题都在这一面里。
 *
 * 常驻组件 `immerse`（`router/managed-routes.tsx` 的常驻表）：路由树把它画进 body 末尾的常驻宿主
 * `[data-immerse-host]`，宿主就是那个节点本身，第一次打开沉浸模式时由 `application-residents.ts` 的 `loadImmerse` 建好、
 * 在画首帧的同一个任务里挂进文档。首帧就是藏着的外框。壳只拿 `configureImmerse` 给的命令式入口：沉浸
 * 模式盖在所有页面之上，壳的键盘、换批与播放快捷键要随时同步问它开没开、当前是哪一个 video。句柄写本模块
 * 的 store 再 `flushSync` 通知，`open(startId)` 里骨架、列表与播放器那几次绘制都在返回之前画完，紧跟着读
 * DOM 的代码读到的就是刚画好的结构。
 *
 * 外框、动作列、作者标题与进度条由 React 画；每一格（`[data-immerse-slide]` 与里面的 `<video>`）
 * 由控制器在空轨道里命令式建、拆：Video.js 要占着那块 DOM，上下滑动的位移按帧写在格子的
 * `style.transform` 上，进度条的宽度随 timeupdate 写，这三样都不能等一次重画。
 *
 * 方向键、改窗口大小与离开页面这三条监听挂在模块上、不挂在组件上：错误边界卸掉这一面之后，壳照旧能
 * 开关它，离开页面时还开着的格照旧按会话取消读取。
 *
 * 宿主不包 `.peach-react`：这块全屏层的排版一直在 Preflight 之外，按钮、链接与字号继承的是遗留层
 * 的全局规则，样式全在 `immerse.css`，不用工具类。 */
import { useLayoutEffect, useRef, useSyncExternalStore, type MouseEvent, type PointerEvent } from 'react';
import { flushSync } from 'react-dom';
import { useMutation, useQuery } from '@tanstack/react-query';

import { avatarInner, representativeOf } from '@peach/card-art';
import { fmtDur, realDuration } from '@peach/legacy/core';
import { spinnerHtml } from '@peach/legacy/ui';

import { apiSend } from '../../api';
import {
  cancelStreamSession, fmtSpeed, mountPlayer, newStreamSession, seekVideoBy, streamSpeedBits, toggleVideoPlayback,
} from '../../player';
import { replaceCatalogItem } from '../catalog-grid/catalog-grid';
import {
  FEEDBACK_URL, feedbackReceipt, fetchItem, ItemGone, itemKey, type DetailItem,
} from '../item-detail/item-detail';
import { queryClient } from '../query';
import {
  AXIS_LOCK_PX, DISLIKE_ADVANCE_MS, DOUBLE_TAP_MS, READY_TIMEOUT_MS, SLIDE_MS, SWIPE_PX, SYNTHETIC_CLICK_MS,
  TAP_SLOP_PX, WHEEL_GAP_MS, ensureDetail, extendList, fetchImmerseList, fitMode, immerseListKey, immerseQuery,
  isWide, ownerOf, type ImmerseItem,
} from './immerse';
import type { ImmerseApi, ImmerseHost } from './immerse-api';

/* ── 状态 ── */

interface Shown { item: ImmerseItem; index: number; length: number }

interface View {
  open: boolean;
  /** 第一条片子出画前只留加载提示：框、动作列、作者标题和进度条按出画时的形状一起出现，不会先按
   *  竖屏摆一遍再跳。片子在隐藏的舞台里照常缓冲。 */
  idle: boolean;
  /** 舞台形状只在一条片子真正出画时换。新片预加载时旧片还在屏上，形状提前翻过去，旧片就被塞进
   *  另一种比例的框里。 */
  wide: boolean;
  /** 加载提示的整句（带读取速度）；null 是收起。 */
  loading: string | null;
  shown: Shown | null;
}

/** 一格：外层负责上下滑动，里面的 video 交给 Video.js。当前格用变量持有，不按 id 查：Video.js
 *  挂载后会把 video 的 id 挪到外包的 div 上。 */
interface Slide {
  el: HTMLDivElement;
  video: HTMLVideoElement;
  session: string;
  release: () => void;
  disposed: boolean;
  /** 拆掉时兑现：还在等片源或等出画的那一步不再干等。 */
  gone: Promise<void>;
  leave: () => void;
}

interface Dom { root: HTMLElement; track: HTMLElement; bar: HTMLElement; progress: HTMLElement }

const CLOSED: View = { open: false, idle: false, wide: false, loading: null, shown: null };

let host: ImmerseHost | null = null;
let dom: Dom | null = null;
let view: View = CLOSED;
const listeners = new Set<() => void>();
/* 每次打开、关闭各换一代：取数与等待回来时代次不对就作废。 */
let generation = 0;
let seed = 0;
let draw = 0;
let query = '';
let list: ImmerseItem[] = [];
let index = 0;
let switching = false;
let extending = false;
let current: Slide | null = null;
const slides = new Set<Slide>();
let loadingLabel = '';
let loadingItem: ImmerseItem | null = null;
let ticker = 0;
/** 进度条拖动作用在哪一格上；第一条出画前与关掉之后没有。 */
let scrubTarget: { video: HTMLVideoElement; duration: () => number } | null = null;
let scrubbing = false;

function immerseHost(): ImmerseHost {
  if (!host) throw new Error('沉浸模式还没有接上宿主（configureImmerse）');
  return host;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener) };
}

function notify(): void {
  for (const listener of [...listeners]) listener();
}

/* 返回时已经画完。这一面抛错、被错误边界卸掉之后没人订阅，`view` 照旧写进 store，不画、不抛。 */
function paint(): void {
  flushSync(notify);
}

/* ── 加载提示 ── */

function loadingText(): string {
  const speed = loadingItem ? streamSpeedBits(loadingItem.id) : 0;
  return speed ? `${loadingLabel} · ${fmtSpeed(speed)}` : loadingLabel;
}

/** 打开、收起加载提示。开着时每 500ms 按当前那一条刷新读取速度。 */
function setLoading(label: string | null, item: ImmerseItem | null = null): void {
  clearInterval(ticker);
  ticker = 0;
  if (label === null) { view = { ...view, loading: null }; paint(); return }
  loadingLabel = label;
  loadingItem = item;
  view = { ...view, loading: loadingText() };
  paint();
  ticker = window.setInterval(() => {
    loadingItem = list[index] ?? null;
    view = { ...view, loading: loadingText() };
    paint();
  }, 500);
}

/* ── 每一格 ── */

function createSlide(offset: number): Slide {
  const el = document.createElement('div');
  el.dataset.immerseSlide = '';
  if (offset) el.style.transform = `translateY(${offset}%)`;
  // `video-js` 类要写在挂载前的 video 上，Video.js 才会把它带到外包 div 上。
  const video = document.createElement('video');
  video.className = 'video-js';
  video.playsInline = true;
  video.preload = 'auto';
  el.append(video);
  dom!.track.append(el);
  const session = newStreamSession();
  let leave = () => {};
  const gone = new Promise<void>((resolve) => { leave = resolve });
  const slide: Slide = { el, video, session, release: () => cancelStreamSession(session), disposed: false, gone, leave };
  slides.add(slide);
  return slide;
}

/** 挂上这一格的播放器；片源交给 video 之后兑现（拆得更早就随拆兑现）。 */
function loadSlide(slide: Slide, item: ImmerseItem): Promise<void> {
  return new Promise((resolve) => {
    slide.release = mountPlayer(slide.video, {
      kind: 'immerse', item, session: slide.session, onLoaded: resolve,
      onEnded: () => { if (view.open) void step(1) },
    });
    void slide.gone.then(resolve);
  });
}

/** 拆一格：按会话取消读取、拆播放器、摘节点。 */
function disposeSlide(slide: Slide | null): void {
  if (!slide || slide.disposed) return;
  slide.disposed = true;
  slides.delete(slide);
  slide.leave();
  slide.release();
  slide.el.remove();
  if (current === slide) current = null;
}

const READY_EVENTS = ['loadeddata', 'canplay', 'error'] as const;

function waitReady(slide: Slide): Promise<void> {
  const { video } = slide;
  if (video.readyState >= 3) return Promise.resolve();
  return new Promise((resolve) => {
    let finished = false;
    const done = () => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      READY_EVENTS.forEach((event) => video.removeEventListener(event, done));
      resolve();
    };
    READY_EVENTS.forEach((event) => video.addEventListener(event, done, { once: true }));
    const timer = setTimeout(done, READY_TIMEOUT_MS);
    void slide.gone.then(done);
  });
}

/* ── 铺满还是完整显示 ── */

function fitVideo(video: HTMLVideoElement): void {
  const track = video.closest<HTMLElement>('[data-immerse-track]');
  const box = track && track.clientWidth && track.clientHeight
    ? track.clientWidth / track.clientHeight
    : window.innerWidth / window.innerHeight;
  const mode = fitMode(video.videoWidth, video.videoHeight, box);
  if (mode === 'contain') video.dataset.fit = 'contain';
  else if (mode) delete video.dataset.fit;
}

const fitAll = () => dom?.track.querySelectorAll('video').forEach(fitVideo);

function applyFit(video: HTMLVideoElement): void {
  delete video.dataset.fit;
  if (video.readyState >= 1) fitVideo(video);
  else video.addEventListener('loadedmetadata', () => fitVideo(video), { once: true });
}

/** 换舞台形状，框里每条片子的铺满判定跟着重算。 */
function setStage(wide: boolean): void {
  view = { ...view, wide };
  paint();
  fitAll();
}

/* ── 片单与换条 ── */

const fetchDraw = (): Promise<ImmerseItem[]> => {
  const at = query;
  return queryClient.fetchQuery({
    queryKey: immerseListKey(at, seed, draw),
    queryFn: () => fetchImmerseList(at, immerseHost().sourceOffline),
    staleTime: Infinity,
  });
};

function wireProgress(video: HTMLVideoElement, item: ImmerseItem): void {
  const duration = () => realDuration(video.duration) || realDuration(item.duration);
  video.addEventListener('timeupdate', () => {
    const total = duration();
    if (total && dom) dom.progress.style.width = `${(video.currentTime / total * 100).toFixed(2)}%`;
  });
  scrubTarget = { video, duration };
}

async function show(direction = 0): Promise<void> {
  const item = list[index];
  if (!item || switching) return;
  const mine = generation;
  /* 当前这一条交给壳写进地址栏：竖划十条之后刷新页面，落回来的该是同一条片子，而不是重新抽一批。 */
  immerseHost().route(item.id);
  switching = true;
  setLoading(direction ? '切换中…' : '加载中…', item);
  let incoming: Slide | null = null;
  try {
    // 这一条刚被删掉（详情回 `{error}`）照样放：作者与动作键只是没有可画的。
    await ensureDetail(item.id).catch((error: unknown) => { if (!(error instanceof ItemGone)) throw error });
    if (mine !== generation) return;
    const old = current, sliding = !!(direction && old);
    if (!sliding) disposeSlide(old);
    const next = createSlide(sliding ? (direction > 0 ? 100 : -100) : 0);
    incoming = next;
    applyFit(next.video);
    await loadSlide(next, item);
    await waitReady(next);
    if (next.disposed) return;
    setStage(isWide(next.video, item));
    if (sliding && old) {
      requestAnimationFrame(() => requestAnimationFrame(() => {
        old.el.style.transform = `translateY(${direction > 0 ? -100 : 100}%)`;
        next.el.style.transform = '';
      }));
      await new Promise((resolve) => setTimeout(resolve, SLIDE_MS));
      // 动画帧没跑到（页面在后台）也要落位，否则新片停在屏幕外。
      disposeSlide(old);
      next.el.style.transform = '';
    }
    if (next.disposed) return;
    current = next;
    incoming = null;
    next.video.play()?.catch(() => {});
    view = { ...view, idle: false, shown: { item, index, length: list.length } };
    wireProgress(next.video, item);
    void apiSend('/api/play', { id: item.id }).catch(() => {});
    setLoading(null);
  } catch {
    disposeSlide(incoming);
    if (mine === generation) setLoading(null);
  } finally {
    if (mine === generation) switching = false;
  }
}

async function step(direction: number): Promise<void> {
  if (switching || !view.open) return;
  const mine = generation;
  index += direction;
  setLoading('切换中…', list[index] ?? null);
  // 滚到尾部就续取下一批：无限流。每次都是新的随机抽样，按 id 去重接在后面。
  if (index >= list.length - 3 && !extending) {
    extending = true;
    try {
      draw += 1;
      const more = await fetchDraw();
      if (mine === generation) list = extendList(list, more);
    } catch {
      // 续取失败就在已有的那一批里绕回。
    } finally {
      extending = false;
    }
  }
  if (mine !== generation) return;
  if (index >= list.length) index = 0;
  if (index < 0) index = list.length - 1;
  await show(direction);
}

async function open(startId: number | null): Promise<void> {
  const mine = ++generation;
  teardown();
  seed += 1;
  draw = 0;
  query = immerseQuery(immerseHost().filters());
  queryClient.removeQueries({ queryKey: ['immerse-list'] });
  view = { ...view, open: true, idle: true, shown: null };
  document.body.setAttribute('data-immerse-open', '');
  setLoading('加载内容…');
  try {
    let next = await fetchDraw();
    if (startId && !next.some((item) => item.id === startId)) {
      const selected = await ensureDetail(startId).catch((error: unknown) => {
        if (error instanceof ItemGone) return null;
        throw error;
      });
      if (selected?.id) next = [selected as ImmerseItem, ...next.filter((item) => item.id !== startId)];
    }
    if (mine !== generation) return;
    list = next;
    if (!list.length) {
      close();
      immerseHost().warn('当前筛选下没有可直接播放的内容');
      return;
    }
    index = Math.max(0, list.findIndex((item) => item.id === startId));
    await show();
  } catch {
    if (mine !== generation) return;
    setLoading(null);
    close();
  }
}

/** 拆掉所有格、收起提示与手势的中间态。 */
function teardown(): void {
  clearInterval(ticker);
  ticker = 0;
  clearTap();
  touch = null;
  scrubTarget = null;
  scrubbing = false;
  [...slides].forEach(disposeSlide);
  switching = false;
}

function close(): void {
  if (!view.open) return;
  generation++;
  teardown();
  view = CLOSED;
  document.body.removeAttribute('data-immerse-open');
  paint();
  immerseHost().closed();
}

/* ── 单击、双击与触屏手势 ──
   手机上竖划切片、横划拖进度。横划在哪儿起手都行——屏幕最下沿那条进度条在手机上几乎摸不到。
   位移按屏宽换算成时长的相对量，所以从任何位置起手都是「往右 = 往后」。 */

let tapTimer = 0;
let lastTap: { side: number; at: number } | null = null;
let ignoreClickUntil = 0;
let touch: { x: number; y: number; axis: '' | 'x' | 'y'; from: number; to?: number } | null = null;
let lastWheel = 0;

function clearTap(): void {
  clearTimeout(tapTimer);
  tapTimer = 0;
  lastTap = null;
}

/** 左右半区：同侧两下快退快进，一下切播放（等双击窗口过去才兑现）。 */
function handleTap(clientX: number): void {
  const video = current?.video;
  if (!video) return;
  const side = clientX < window.innerWidth / 2 ? -1 : 1;
  const now = Date.now();
  if (lastTap && lastTap.side === side && now - lastTap.at <= DOUBLE_TAP_MS) {
    clearTap();
    seekVideoBy(video, immerseHost().seekSeconds() * side);
    return;
  }
  // 两次点在不同半区时，第一下仍是一次完整的单击；立即兑现后再等当前点击。
  if (tapTimer) { clearTimeout(tapTimer); tapTimer = 0; toggleVideoPlayback(video) }
  lastTap = { side, at: now };
  tapTimer = window.setTimeout(() => {
    tapTimer = 0;
    lastTap = null;
    if (view.open) toggleVideoPlayback(current?.video);
  }, DOUBLE_TAP_MS);
}

function clickTrack(): void {
  // 触屏的合成 click 会紧跟 touchend；那一下已经由单击／双击判定接管，不能再切一次。
  if (Date.now() < ignoreClickUntil) return;
  toggleVideoPlayback(current?.video);
}

const setBarScrubbing = (on: boolean) => dom?.bar.toggleAttribute('data-scrubbing', on);

function paintProgress(ratio: number): void {
  if (dom) dom.progress.style.width = `${(ratio * 100).toFixed(2)}%`;
}

function onTouchStart(event: TouchEvent): void {
  const target = event.target as Element | null;
  if (event.touches.length !== 1 || !target?.closest('[data-immerse-track]')) { touch = null; return }
  const video = current?.video;
  touch = { x: event.touches[0]!.clientX, y: event.touches[0]!.clientY, axis: '', from: video ? video.currentTime || 0 : 0 };
}

function onTouchMove(event: TouchEvent): void {
  if (!touch || event.touches.length !== 1) return;
  const dx = event.touches[0]!.clientX - touch.x, dy = event.touches[0]!.clientY - touch.y;
  if (!touch.axis) {
    if (Math.abs(dx) < AXIS_LOCK_PX && Math.abs(dy) < AXIS_LOCK_PX) return;
    touch.axis = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y';
    if (touch.axis === 'x') setBarScrubbing(true);
  }
  if (touch.axis !== 'x') return;
  const video = current?.video, total = video ? video.duration || 0 : 0;
  if (!total) return;
  event.preventDefault(); // 横划归进度，不交给页面滚动
  touch.to = Math.min(total, Math.max(0, touch.from + dx / window.innerWidth * total));
  paintProgress(touch.to / total);
}

function onTouchEnd(event: TouchEvent): void {
  if (!touch) return;
  const gesture = touch;
  touch = null;
  ignoreClickUntil = Date.now() + SYNTHETIC_CLICK_MS;
  setBarScrubbing(false);
  if (gesture.axis === 'x') {
    const video = current?.video;
    if (video && gesture.to != null) video.currentTime = gesture.to;
    return;
  }
  const end = event.changedTouches[0]!, dx = end.clientX - gesture.x, dy = gesture.y - end.clientY;
  if (Math.abs(dy) > SWIPE_PX) { clearTap(); void step(dy > 0 ? 1 : -1); return }
  if (Math.abs(dx) <= TAP_SLOP_PX && Math.abs(dy) <= TAP_SLOP_PX) {
    event.preventDefault();
    handleTap(end.clientX);
  }
}

function onTouchCancel(): void {
  touch = null;
  setBarScrubbing(false);
}

function onWheel(event: WheelEvent): void {
  const now = Date.now();
  if (now - lastWheel < WHEEL_GAP_MS) return;
  lastWheel = now;
  void step(event.deltaY > 0 ? 1 : -1);
}

/* 进度条拖动。pointer 一套同时盖鼠标和触控，捕获指针后手滑出进度条也不会断。拖动中只画进度，
   松手才 seek——每帧都 seek 会让远程源一直重新缓冲。没拖动的单击走同一条路：pointerdown 已经
   画了进度，pointerup 落地。 */
function barRatio(event: PointerEvent): number {
  const box = dom!.bar.getBoundingClientRect();
  return Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
}

function scrubStart(event: PointerEvent<HTMLDivElement>): void {
  if (!scrubTarget?.duration()) return;
  scrubbing = true;
  setBarScrubbing(true);
  event.currentTarget.setPointerCapture(event.pointerId);
  paintProgress(barRatio(event));
  event.preventDefault();
}

function scrubMove(event: PointerEvent): void {
  if (scrubbing) paintProgress(barRatio(event));
}

function scrubEnd(event: PointerEvent): void {
  if (!scrubbing) return;
  scrubbing = false;
  setBarScrubbing(false);
  const total = scrubTarget?.duration();
  if (scrubTarget && total) scrubTarget.video.currentTime = total * barRatio(event);
}

function scrubCancel(): void {
  scrubbing = false;
  setBarScrubbing(false);
}

/* ── 动作键 ── */

type FeedbackKind = 'dislike' | 'seen' | 'o';
interface FeedbackResult { feedback?: string | null; disposal?: string | null; o_count?: number }

/** 写一次反馈，回执换进 `['item', id]`，目录里同一张卡跟着换。 */
export async function sendFeedback(id: number, kind: string): Promise<FeedbackResult> {
  const result = await apiSend<FeedbackResult>(FEEDBACK_URL, { id, kind });
  const before = queryClient.getQueryData<DetailItem>(itemKey(id));
  if (!before) {
    replaceCatalogItem(id, { feedback: result.feedback ?? undefined, o_count: result.o_count });
    return result;
  }
  const next: DetailItem = { ...before, feedback: result.feedback ?? undefined, o_count: result.o_count };
  queryClient.setQueryData(itemKey(id), next);
  replaceCatalogItem(id, {
    feedback: next.feedback, disposal: next.disposal, watch_later: next.watch_later, rating: next.rating, o_count: next.o_count,
  });
  return result;
}

const ACTIONS: { kind: FeedbackKind; glyph: string; label: string }[] = [
  { kind: 'dislike', glyph: 'thumbs-down', label: '标为不喜欢' },
  { kind: 'seen', glyph: 'eye', label: '标为看过' },
  { kind: 'o', glyph: 'heart', label: '记一次高潮' },
];

/** 当前出画那一条的详情：出画前已经取过（`ensureDetail`），这里只读缓存、跟着写回重画。 */
function useDetail(id: number | null) {
  return useQuery({
    queryKey: itemKey(id ?? 0),
    queryFn: ({ signal }) => fetchItem(id!, signal),
    enabled: id != null,
    staleTime: Infinity,
  }).data;
}

function ImmerseAction({ kind, glyph, label, id }: { kind: FeedbackKind; glyph: string; label: string; id: number | null }) {
  const full = useDetail(id);
  const mutation = useMutation({
    mutationFn: async (target: number) => {
      const before = queryClient.getQueryData<DetailItem>(itemKey(target))?.feedback || null;
      return { result: await sendFeedback(target, kind), before };
    },
    onSuccess: ({ result, before }, target) => {
      immerseHost().toast(feedbackReceipt(kind, result), {
        undo: async () => {
          if (kind === 'o') await sendFeedback(target, 'o-undo');
          else {
            if (result.feedback) await sendFeedback(target, result.feedback);
            if (before) await sendFeedback(target, before);
          }
        },
      });
      if (kind === 'dislike' && result.feedback === 'dislike') setTimeout(() => { void step(1) }, DISLIKE_ADVANCE_MS);
    },
    onError: (error) => immerseHost().failure('更新反馈', error),
  });
  const busy = mutation.isPending;
  const pressed = kind !== 'o' && full ? (full.feedback === kind ? 'true' : 'false') : undefined;
  return (
    <div data-immerse-action="">
      <button type="button" data-immerse-circle="" title={label} aria-label={label} aria-pressed={pressed}
        {...(busy ? { 'aria-busy': true, 'aria-disabled': true } as const : {})}
        onClick={() => { if (id != null && !mutation.isPending) mutation.mutate(id) }}>
        <svg viewBox="0 0 24 24"><use href={`#i-${glyph}`} /></svg>
      </button>
      <span data-immerse-label="">
        {kind === 'dislike' ? '不喜欢' : null}
        {kind === 'seen' ? (full?.feedback === 'seen' ? '已看' : '看过') : null}
        {kind === 'o' ? <>高潮 <b>{full?.o_count || 0}</b></> : null}
      </span>
    </div>
  );
}

/* ── 作者与标题 ── */

function Caption({ shown }: { shown: Shown | null }) {
  const item = shown?.item ?? null;
  const full = useDetail(item?.id ?? null);
  const owner = full ? ownerOf(full) : null;
  const helpers = immerseHost();
  /* 点作者或头像：先关掉，再进第一位出镜者（没有就是创作者）的资料页。 */
  const openOwner = (event: MouseEvent) => {
    event.preventDefault();
    if (!full) return;
    close();
    if (full.performers?.[0]) helpers.openEntity('performer', full.performers[0]);
    else if (full.creator) helpers.openEntity('creator', full.creator);
    else helpers.openUnowned();
  };
  /* 标题进详情页。沉浸模式里只看得到文件名，想看标签、相关推荐或改东西都得先退出再去列表里
     把它找回来。路径和旁边的作者链接一致：先关，再开。 */
  const openTitle = () => {
    if (!item) return;
    close();
    helpers.openItem(item.id);
  };
  const avatar = owner ? avatarInner(owner.name, owner.ref, representativeOf(owner.name), owner.kind || 'performer') : '';
  return (
    <div data-immerse-ui="">
      <div data-immerse-author="">
        <button type="button" data-immerse-avatar="" aria-label="打开资料" onClick={openOwner}
          dangerouslySetInnerHTML={{ __html: avatar }} />
        <a href="#" onClick={openOwner}>{owner?.who ?? ''}</a>
        <span>{shown && item ? `· ${fmtDur(item.duration)} · ${item.ctx_orient || ''} · ${shown.index + 1}/${shown.length}` : ''}</span>
      </div>
      <button type="button" data-immerse-title="" onClick={openTitle}>{item ? helpers.displayName(item).trim() || '未命名视频' : ''}</button>
    </div>
  );
}

/* ── 外框 ── */

const SPINNER = { __html: spinnerHtml('媒体加载中') };

function ImmerseView({ view: at }: { view: View }) {
  const frame = useRef<HTMLDivElement>(null);
  const track = useRef<HTMLDivElement>(null);
  const bar = useRef<HTMLDivElement>(null);
  const progress = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    const el = frame.current!;
    dom = { root: el, track: track.current!, bar: bar.current!, progress: progress.current! };
    /* 横划要 `preventDefault`，React 的触摸与滚轮事件挂在根上是被动的，拦不住页面滚动：这几条直接挂
       在节点上。 */
    el.addEventListener('touchstart', onTouchStart, { passive: true });
    el.addEventListener('touchmove', onTouchMove, { passive: false });
    el.addEventListener('touchend', onTouchEnd, { passive: false });
    el.addEventListener('touchcancel', onTouchCancel, { passive: true });
    el.addEventListener('wheel', onWheel, { passive: true });
    return () => {
      el.removeEventListener('touchstart', onTouchStart);
      el.removeEventListener('touchmove', onTouchMove);
      el.removeEventListener('touchend', onTouchEnd);
      el.removeEventListener('touchcancel', onTouchCancel);
      el.removeEventListener('wheel', onWheel);
      dom = null;
    };
  }, []);
  const id = at.shown?.item.id ?? null;
  const flag = (on: boolean) => (on ? '' : undefined);
  return (
    <div ref={frame} data-immerse="" data-idle={flag(at.idle)} data-wide={flag(at.wide)} hidden={!at.open}>
      <div data-immerse-stage="" data-wide={flag(at.wide)}>
        <div ref={track} data-immerse-track="" onClick={clickTrack} />
        <div data-immerse-actions="">
          {ACTIONS.map((action) => <ImmerseAction key={action.kind} {...action} id={id} />)}
        </div>
      </div>
      <div data-immerse-loader="" role="status" aria-live="polite" hidden={at.loading === null}>
        <span data-immerse-spinner="" dangerouslySetInnerHTML={SPINNER} />
        <span>{at.loading ?? ''}</span>
      </div>
      <button type="button" data-immerse-close="" title="关闭" aria-label="关闭" onClick={close}>
        <svg viewBox="0 0 24 24"><use href="#i-x" /></svg>
      </button>
      <div ref={bar} data-immerse-bar="" onPointerDown={scrubStart} onPointerMove={scrubMove}
        onPointerUp={scrubEnd} onPointerCancel={scrubCancel}>
        <i ref={progress} />
      </div>
      <Caption shown={at.shown} />
    </div>
  );
}

/* ── 对壳的入口 ── */

const api: ImmerseApi = {
  open,
  close,
  isOpen: () => view.open,
  activeVideo: () => (view.open ? current?.video ?? null : null),
};

function onKeyDown(event: KeyboardEvent): void {
  if (!view.open) return;
  // 输入态不抢键：搜索框、标签弹窗和任何可编辑区域里的按键归它们自己处理。
  const target = event.target as HTMLElement | null;
  if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return;
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  // 纵向切片，和竖屏短视频的手势方向保持一致。
  if (event.key === 'ArrowDown') void step(1);
  if (event.key === 'ArrowUp') void step(-1);
}

/** 接上壳给的宿主，拿回沉浸模式的命令式入口。只调一次；壳的 `loadImmerse` 接着在路由树里打开这一面，
 *  藏着的外框画上之后才交出句柄，第一次打开之前它就在。 */
export function configureImmerse(next: ImmerseHost): ImmerseApi {
  const first = !host;
  host = next;
  if (first) {
    document.addEventListener('keydown', onKeyDown);
    // 旋转手机或改窗口大小后，同一条视频的铺满／完整显示判定可能翻转。
    addEventListener('resize', () => { if (view.open) fitAll() });
    addEventListener('pagehide', () => { slides.forEach((slide) => cancelStreamSession(slide.session)) });
  }
  return api;
}

/** 常驻表里的那一面：读本模块的 store，宿主还没接上时不画。 */
export function ImmerseSurface() {
  const at = useSyncExternalStore(subscribe, () => view);
  return host ? <ImmerseView view={at} /> : null;
}
