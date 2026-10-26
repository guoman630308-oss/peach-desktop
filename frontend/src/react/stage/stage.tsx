/* 舞台（ADR-0031 第 11a 步）：详情浮窗 `<dialog id="stage">`、它的进出场、骨架与揭示、两座详情、
 * 播放器与小窗都在这一面里。
 *
 * 常驻组件 `stage`（`router/managed-routes.tsx` 的常驻表）：路由树把它画进 body 末尾的常驻宿主
 * `[data-stage-host]`，宿主就是那个节点本身、不包 `.peach-react`，第一次打开详情时由 `application-residents.ts` 的
 * `loadStage` 建好、在画出小窗节点的同一个任务里挂进文档。舞台盖在所有页面之上，小窗要在换页之后接着放，
 * 所以宿主不跟某一页走。壳只拿 `configureStage` 给的命令式入口：句柄写本模块的 store 再 `flushSync` 通知，
 * `open`（含原地换条）、`update` 与 `dispose` 里每一次绘制都在返回之前画完，紧跟着读 DOM 的代码（骨架量尺寸、
 * `showModal`、标题揭示、焦点交给关闭键）读到的就是刚画好的结构。
 *
 * 每次 `open` 换一枚 `generation`，取数回来时代次不对就作废；重开时 dialog 按它重建，原地换条沿用开着的那个。
 * 两座详情是这一面的子组件，和页面共用路由树的 QueryClient（`providers.tsx`）。两座详情画出来时报给壳的 `present` 排到微任务里：壳收到后
 * 会画侧栏与顶栏，那几座常驻组件的句柄内部 `flushSync`，在路由树的提交阶段里画不出来。微任务在浏览器绘制前
 * 跑完；那一条已经换走（原地换条或重开）或舞台已经收起就不报。 */
import { useLayoutEffect, useRef, useSyncExternalStore, type MouseEvent, type PointerEvent } from 'react';
import { flushSync } from 'react-dom';

import { detailPosterUrl, releaseHoverPreviews } from '@peach/card-art';
import { fitSkeleton, revealTexts } from '@peach/legacy/ui';

import { detailSkeletonBody } from '../../board-skeleton';
import { applyTheaterMode, closePlayerMenu, type PlayerResume } from '../../player';
import { followItemKey, prefetchFollowDetail, type FollowDetailActions, type FollowDetailProps } from '../follow-detail/follow-detail';
import { FollowDetailPage } from '../follow-detail/follow-detail-page';
import { chooseItem, itemKey, prefetchItemDetail, queueKey, type DetailQueue, type ItemDetailActions, type ItemDetailProps } from '../item-detail/item-detail';
import { ItemDetailPage } from '../item-detail/item-detail-page';
import { javDetailKey, prefetchJavDetail, type JavDetailProps } from '../jav-detail/jav-detail';
import { queryClient } from '../query';
import { JavDetailPage } from '../jav-detail/jav-detail-page';
import { Miniplayer } from './miniplayer';
import * as player from './stage-player';
import { mountLocalPlayer } from './local-player';
import type { StageApi, StageHost, StagePatch, StageRequest } from './stage-api';

type Phase = 'skeleton' | 'reveal' | 'content';

type Content =
  | { kind: 'item'; props: ItemDetailProps }
  | { kind: 'jav'; props: JavDetailProps }
  | { kind: 'follow'; props: FollowDetailProps };

interface View {
  generation: number;
  request: StageRequest;
  content: Content;
  /* 骨架 → 淡出（骨架抬成 `[data-stage-fade]` 一层，内容同时清晰起来）→ 内容。取得比显示门槛快时跳过
     中间那一段：从未露面的骨架不该再演一次退场。 */
  phase: Phase;
}

let host: StageHost | null = null;
let view: View | null = null;
const listeners = new Set<() => void>();
let generation = 0;
let controller: AbortController | null = null;
/* 关闭键、Escape、点浮窗外面与原生 `cancel` 可能在同一下里各来一次：退场动画期间再要一次，
   会让第二次关闭跳过动画直接拆。 */
let closeRequested = false;
/* 按下那一刻在不在浮窗外面。详情里进度条、音量条和队列都能拖，从控件上拖出边界再松手同样会在
   dialog 上收到一次 click——那是一次拖动的收尾，不是要关窗。 */
let dismissArmed = false;

const stageDialog = (): HTMLDialogElement | null => {
  const el = document.getElementById('stage');
  return el instanceof HTMLDialogElement ? el : null;
};

function stageHost(): StageHost {
  if (!host) throw new Error('舞台还没有接上宿主（configureStage）');
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

function StageTree({ view: current }: { view: View | null }) {
  return (
    <>
      {current ? <StageDialog key={current.generation} view={current} /> : null}
      <Miniplayer />
    </>
  );
}

const DetailSkeleton = () => (
  <div data-skeleton="detail" role="status" aria-label="正在读取作品详情" dangerouslySetInnerHTML={{ __html: detailSkeletonBody() }} />
);

/* 点浮窗外面就退出。原生模态里「外面」还是这个 dialog 自己——遮罩归它，落在遮罩上的事件 target
   就是它本人，所以判据取坐标不取 target：按 target 判，浮窗身上任何一块不属于内容的地方都会被算成
   点了外面。只看坐标也不够：播放器全屏后铺满整个视口，浮窗的矩形仍是详情排版里那块，点进度条右段
   或底部控制栏会落在矩形外。所以两条都要成立：target 是 dialog 本身，坐标在浮窗外。 */
function outside(event: MouseEvent | PointerEvent): boolean {
  const dialog = event.currentTarget as HTMLElement;
  if (event.target !== dialog) return false;
  const box = dialog.getBoundingClientRect();
  return event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom;
}

function StageDialog({ view: current }: { view: View }) {
  const ref = useRef<HTMLDialogElement>(null);
  /* 原生 `cancel`（Escape 之外的关闭请求，例如安卓返回手势）与关闭键走同一条路。Escape 本身在
     keydown 上就处理掉了：那里 `preventDefault` 之后浏览器不再派 `cancel`。 */
  useLayoutEffect(() => {
    const dialog = ref.current!;
    // 文件选择框的 cancel 会冒泡；详情只处理自己的关闭请求。
    const cancel = (event: Event) => { if (event.target === dialog) { event.preventDefault(); requestClose() } };
    dialog.addEventListener('cancel', cancel);
    return () => dialog.removeEventListener('cancel', cancel);
  }, []);
  const { phase, content } = current;
  /* 原地换条时关注详情按条目重建：选中的那份媒体、写操作的忙碌与失败都只属于上一条。作品详情
     自己按条目给 `Detail` 换键，队列取数留在外层不重来。 */
  const body = content.kind === 'item'
    ? <ItemDetailPage {...content.props} />
    : content.kind === 'jav' ? <JavDetailPage key={content.props.code} {...content.props} />
    : <FollowDetailPage key={content.props.id} {...content.props} />;
  return (
    <dialog ref={ref} id="stage" data-stage="" data-stage-kind={content.kind} aria-label="作品详情"
      onKeyDown={(event) => {
        /* 里层弹层（标签搜索框、右键菜单）先处理 Escape 并 `preventDefault`，这一下就不再关浮窗。 */
        if (event.key !== 'Escape' || event.defaultPrevented) return;
        event.preventDefault();
        requestClose();
      }}
      onPointerDown={(event) => { dismissArmed = outside(event) }}
      onClick={(event) => { if (dismissArmed && outside(event)) requestClose() }}>
      {phase === 'skeleton' ? <DetailSkeleton key="skeleton" /> : null}
      {phase === 'reveal' ? <div key="fade" data-stage-fade="" aria-hidden="true"><DetailSkeleton /></div> : null}
      {phase === 'skeleton' ? null : (
        /* 窄屏下滚的是 `[data-stage-scroll]`，全站那条覆盖式滚动条挂在它的兄弟位置（`<dialog>` 在顶层，
           轨道挂到它外面会落进遮罩底下）；「接着看」是 `[data-stage-grid]` 的兄弟，也得一起装进来。里面那层
           `.peach-react` 不占盒子，token 与 Preflight 从它开始作用。 */
        <div key="content" data-stage-scroll=""><div className="peach-react">{body}</div></div>
      )}
    </dialog>
  );
}

function requestClose(): void {
  if (!view || closeRequested) return;
  closeRequested = true;
  view.request.actions.close();
}

function toggleStageModes(ambient: boolean, theater: boolean): void {
  const dialog = stageDialog();
  dialog?.toggleAttribute('data-ambient', ambient);
  dialog?.toggleAttribute('data-theater', theater);
}

/* 两座详情的动作经舞台包一层：关闭走 `requestClose`；画出来那一刻登记小窗元数据、切氛围光与剧场
   模式；媒体框交给舞台的播放器。续播时刻只给第一次挂上的那一条。 */
function contentFor(request: StageRequest): Content {
  if (request.kind === 'jav') return { kind: 'jav', props: { code: request.code, readOnly: request.readOnly,
    actions: { ...request.actions, close: requestClose } } };
  let resume: PlayerResume | null = request.resume ?? null;
  const takeResume = (id: number) => {
    const hit = resume && (request.id == null || request.id === id) ? resume : null;
    resume = null;
    return hit;
  };
  const settings = () => stageHost().player.settings();
  /* 原地换条沿用浮窗开着时的代次（dialog 不重建），所以按这一次打开的 request 认：换条与重开都换一份新的。 */
  const live = () => view?.request === request;
  if (request.kind === 'item') {
    const { kind: _kind, resume: _resume, externalAutoplay, actions: base, ...props } = request;
    const actions: ItemDetailActions = {
      ...base,
      close: requestClose,
      present: (item, queue) => {
        const performers = item.performers as string[] | undefined;
        player.setStageMeta({ kind: 'item', item, title: String(item.title || item.name || ''),
          sub: String(performers?.[0] || item.creator || '未归属') });
        toggleStageModes(settings().ambientMode, settings().theaterMode);
        queueMicrotask(() => { if (live()) base.present(item, queue) });
      },
      mountPlayer: (frame, item, _media, options) => player.attachStagePlayer(frame, {
        kind: 'item', item, autoplay: options?.autoplay, externalAutoplay, resume: takeResume(item.id), id: 'vid',
      }),
    };
    return { kind: 'item', props: { ...props, actions } };
  }
  const { kind: _kind, resume: _resume, actions: base, ...props } = request;
  const actions: FollowDetailActions = {
    ...base,
    close: requestClose,
    present: (item, kind) => {
      player.setStageMeta({ kind: 'follow', item, title: item.title || '', sub: item.author || item.source_label || '' });
      toggleStageModes(kind === 'video' && settings().ambientMode, kind === 'video' && settings().theaterMode);
      queueMicrotask(() => { if (live()) base.present(item, kind) });
    },
    mountPlayer: (frame, item, media, options) => player.attachStagePlayer(frame, {
      kind: 'follow', item, media, poster: item.thumb_url || undefined, onError: options.onError, resume: takeResume(item.id),
    }),
  };
  return { kind: 'follow', props: { ...props, actions } };
}

/* 骨架抬成一层淡出，内容同时从模糊里清晰起来；走完（或 1s 兜底）摘掉那一层。 */
function startReveal(dialog: HTMLDialogElement, mine: number): void {
  const fade = dialog.querySelector<HTMLElement>(':scope > [data-stage-fade]');
  dialog.setAttribute('data-stage-reveal', '');
  dialog.getBoundingClientRect();
  dialog.setAttribute('data-stage-revealing', '');
  let timer = 0;
  const drop = () => {
    clearTimeout(timer);
    fade?.removeEventListener('transitionend', done);
    dialog.removeAttribute('data-stage-reveal');
    dialog.removeAttribute('data-stage-revealing');
    if (view?.generation === mine && view.phase === 'reveal') { view = { ...view, phase: 'content' }; paint() }
  };
  const done = (event: Event) => { if (event.target === fade) drop() };
  fade?.addEventListener('transitionend', done);
  timer = window.setTimeout(drop, 1000);
}

/** 浮窗开着同一种详情、不在退场：换条就原地换内容，不拆浮窗。 */
function swappable(kind: StageRequest['kind']): HTMLDialogElement | null {
  const dialog = stageDialog();
  if (!view || view.content.kind !== kind || closeRequested || !dialog?.open || dialog.hasAttribute('data-closing')) return null;
  return dialog;
}

async function open(request: StageRequest): Promise<void> {
  const current = swappable(request.kind);
  if (current) { await swap(request, current); return }
  if (view) dispose({ miniplayer: false });
  const mine = ++generation;
  const abort = new AbortController();
  controller = abort;
  closeRequested = false;
  dismissArmed = false;
  if (request.kind === 'jav') player.closeMiniplayer();
  else player.prepareStageFor(request.kind, request.id ?? Number.NaN);
  const content = contentFor(request);
  view = { generation: mine, request, content, phase: 'skeleton' };
  const waiting = content.kind === 'item' ? prefetchItemDetail(content.props, abort.signal)
    : content.kind === 'jav' ? prefetchJavDetail(content.props, abort.signal)
    : prefetchFollowDetail(content.props, abort.signal);
  if (mine !== generation || view?.request !== request) { await waiting.catch(() => {}); return }
  const cached = cachedContent(content);
  view = { ...view, phase: cached ? 'content' : 'skeleton' };
  paint();
  const dialog = stageDialog();
  if (!dialog) { await waiting.catch(() => {}); return }
  if (!cached) fitSkeleton(dialog);
  document.body.setAttribute('data-detail-open', '');
  if (!dialog.open) dialog.showModal();
  try {
    await waiting;
  } catch {
    // 中止就是用户已经走开。其余失败照画：原因和重试的节律都在详情自己手里。
    if (abort.signal.aborted) return;
  }
  if (mine !== generation || !view) return;
  if (cached) return;
  /* 还没到显示门槛就已经取完：直接落内容，隐藏中的占位从未被人看见。 */
  const direct = !!dialog.querySelector('.skeleton-awaiting');
  view = { ...view, phase: direct ? 'content' : 'reveal' };
  paint();
  if (!direct) startReveal(dialog, mine);
  /* 浮窗里那两行标题跟着这一次重画揭示一遍。绕开正在淡出的那一层：骨架照着最终结构画，里面也有
     一个 `[data-stage-side-content]`，按文档顺序找的话拿到的是它。 */
  revealTexts(dialog, ':scope>:not([data-stage-fade]) [data-reveal-line]');
  /* 骨架里没有可聚焦的元素，`showModal()` 只能把焦点给 dialog 本身；内容到了交给关闭键。 */
  const active = document.activeElement;
  if (active === dialog || !dialog.contains(active)) dialog.querySelector<HTMLElement>('#closeStage')?.focus();
}

/** 可立即完整呈现的详情沿用查询缓存，刷新只更新内容。 */
function cachedContent(content: Content): boolean {
  if (content.kind === 'jav') return !!queryClient.getQueryData(javDetailKey(content.props.code));
  if (content.kind === 'follow') return !!queryClient.getQueryData(followItemKey(content.props.id));
  const { queue: ref, id } = content.props;
  const queue = ref ? queryClient.getQueryData<DetailQueue>(queueKey(ref)) : null;
  if (ref && !queue) return false;
  const current = queue ? chooseItem(queue, id) : id;
  return current != null && !!queryClient.getQueryData(itemKey(current));
}

/* 详情开着时换到同一种的另一条（队列换卷、合集换版本、相关作品、后退前进）：浮窗、遮罩与进场都不
   重演，也不回骨架。先把新的一条取齐，取的这段时间上一条照旧在放；取到再换内容，媒体框按条目卸下，
   上一条的播放器随之拆掉（不进小窗）。 */
async function swap(request: StageRequest, dialog: HTMLDialogElement): Promise<void> {
  const mine = ++generation;
  controller?.abort();
  const abort = new AbortController();
  controller = abort;
  dismissArmed = false;
  const content = contentFor(request);
  try {
    if (content.kind === 'item') await prefetchItemDetail(content.props, abort.signal);
    else if (content.kind === 'jav') await prefetchJavDetail(content.props, abort.signal);
    else await prefetchFollowDetail(content.props, abort.signal);
  } catch {
    if (abort.signal.aborted) return;
  }
  if (mine !== generation || !view || swappable(request.kind) !== dialog) return;
  if (request.kind === 'jav') player.closeMiniplayer();
  else player.prepareStageFor(request.kind, request.id ?? Number.NaN);
  releaseHoverPreviews(dialog);
  closePlayerMenu();
  view = { ...view, request, content, phase: 'content' };
  paint();
  /* 首次揭示还没走完就换了条：淡出那一层已经卸下，等不到它的 transitionend，模糊要当场摘掉。 */
  dialog.removeAttribute('data-stage-reveal');
  dialog.removeAttribute('data-stage-revealing');
  dialog.querySelector<HTMLElement>(':scope > [data-stage-scroll]')?.scrollTo({ top: 0 });
  revealTexts(dialog, ':scope>:not([data-stage-fade]) [data-reveal-line]');
  /* 点下去的那一行随详情重建卸掉了，焦点落回 body：交给队列里新的当前行，没有队列交给关闭键。 */
  const active = document.activeElement;
  if (active === dialog || !dialog.contains(active)) {
    (dialog.querySelector<HTMLElement>('[data-mix-item][aria-current="true"]')
      || dialog.querySelector<HTMLElement>('#closeStage'))?.focus({ preventScroll: true });
  }
}

/* 详情浮窗的退场跟设置弹层同一条：`data-closing` 让 `board-dialog-out` 和遮罩淡出演完，再拆。等待有
   上限：`animation` 被别的规则关掉时 animationend 不会来。 */
function exit(): Promise<void> {
  const dialog = stageDialog();
  if (!dialog?.open || dialog.hasAttribute('data-closing') || matchMedia('(prefers-reduced-motion: reduce)').matches) {
    return Promise.resolve();
  }
  dialog.setAttribute('data-closing', '');
  return new Promise((resolve) => {
    let timer = 0;
    const done = () => { clearTimeout(timer); dialog.removeEventListener('animationend', onEnd); resolve() };
    // 浮窗里的控件也会冒泡出 animationend，只认目标就是舞台本身的那一条。
    const onEnd = (event: Event) => { if (event.target === dialog) done() };
    dialog.addEventListener('animationend', onEnd);
    timer = window.setTimeout(done, 380);
  });
}

function dispose({ miniplayer = true }: { miniplayer?: boolean } = {}): void {
  generation++;
  controller?.abort();
  controller = null;
  /* 离开详情时正在放的视频不销毁：整个播放器搬进小窗接着放。要在卸掉媒体框之前搬。 */
  player.handOffStage(miniplayer);
  if (!view) return;
  const dialog = stageDialog();
  // 舞台拆掉之前收起挂在舞台里的悬停预览（「接着看」那一排的卡）。
  if (dialog) releaseHoverPreviews(dialog);
  // 右键菜单开着时挪进了舞台：先放回 body，别跟着浮窗一起被卸掉。
  closePlayerMenu();
  if (dialog?.open) dialog.close();
  view = null;
  closeRequested = false;
  dismissArmed = false;
  paint();
  player.setStageMeta(null);
  document.body.removeAttribute('data-detail-open');
}

function update(patch: StagePatch): void {
  if (!view) return;
  view = { ...view, content: view.content.kind === 'item'
    ? { kind: 'item', props: { ...view.content.props, ...patch } }
    : view.content.kind === 'jav' ? view.content
    : { kind: 'follow', props: { ...view.content.props,
      ...(patch.relatedLimit === undefined ? {} : { relatedLimit: patch.relatedLimit }) } } };
  paint();
}

function repaintPoster(): void {
  const meta = player.stageMeta();
  const item = meta?.kind === 'item' ? meta.item : null;
  const poster = item ? detailPosterUrl(item, stageHost().player.settings().javImage) : '';
  if (!poster) return;
  const current = player.stagePlayer();
  if (current) current.poster(poster);
  else document.getElementById('vid')?.setAttribute('poster', poster);
}

const api: StageApi = {
  open, update, exit, dispose, requestClose, repaintPoster,
  isOpen: () => !!view,
  showing: () => (view && swappable(view.content.kind) ? view.content.kind : null),
  activeVideo: () => player.activeStageVideo(),
  mountWallPlayer: player.mountWallPlayer,
  toggleTheater: () => { if (view) applyTheaterMode(!stageHost().player.settings().theaterMode) },
  toggleMiniplayer: () => player.toggleMiniplayer(),
  closeMiniplayer: () => player.closeMiniplayer(),
  miniplayerActive: () => player.miniplayerActive(),
  miniplayerTakesCard: (item) => player.miniplayerTakesCard(item),
  miniplayerPlay: (id) => { void player.miniplayerPlay(id) },
};

/** 接上壳给的宿主，拿回舞台的命令式入口。先接播放器；小窗的节点在路由树打开这一面时画出来，第一次进小窗
 *  之前就在。 */
export function configureStage(next: StageHost): StageApi {
  host = next;
  player.configureStagePlayer(next, { isOpen: () => !!view, requestClose }, mountLocalPlayer);
  return api;
}

/** 常驻组件 `stage` 的组件：订阅本模块的 store。小窗节点无条件画着，播放器搬进搬出都命令式地落在它里面。 */
export function StageSurface() {
  const at = useSyncExternalStore(subscribe, () => view);
  return host ? <StageTree view={at} /> : null;
}
