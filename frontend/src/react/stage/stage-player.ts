/* 舞台与小窗共用的那一个播放器（ADR-0031 第 11a 步）。不含 React：舞台岛的媒体框在 layout effect
 * 里调 `attachStagePlayer`，小窗组件把自己的节点登记进来，其余都是命令式的。
 *
 * 照 YouTube 桌面版的 miniplayer（docs/reference-snapshots/youtube-miniplayer-measured.md）：离开详情
 * 时正在放的视频不销毁，Video.js 的壳整块搬进小窗接着放；小窗开着时点别的卡片就在小窗里换片；
 * 点标题或「展开」回到详情，同一个实例搬回舞台的媒体框，不重建；拖到哪个象限就吸附到哪个角。
 * 上游只把播放列表内的切换留在小窗里，Peach 按用户要求把卡片点击也收进来。
 *
 * 舞台与小窗同一时刻只放一条，所以只有两个位置：`attached` 是舞台媒体框里的那一个，`held`
 * 是小窗里的那一个。一个实例从 `attached` 交到 `held`（离开详情），或从 `held` 认领回
 * `attached`（展开），不会同时在两处。 */
import { api, fmtClock, realDuration } from '@peach/legacy/core';
import { iconSwapHtml, setIconSwap } from '@peach/legacy/ui';
import { prefersExternalPlayer } from '../../local-player';

import {
  attachPlayerChrome, cancelDetailStream, closePlayerMenu, configurePlayer, configurePlayerMenu, detailPlayer,
  disposePlayer, mountPlayer, preparePlayerFrame, resizeSoon, setDetailPlayer,
  type PlayerHost, type PlayerItem, type PlayerMedia, type PlayerResume, type VjsPlayer,
} from '../../player';

export type StageKind = 'item' | 'follow';

/** 舞台此刻画着的那一条：小窗的标题与来源、右键菜单复制网址用的种类都从这里取。 */
export interface StageMeta { kind: StageKind; item: PlayerItem; title: string; sub: string }

/** 舞台与小窗向壳要的东西。播放器宿主里的舞台元素由舞台自己补上。 */
export interface StagePlayerHost {
  player: Omit<PlayerHost, 'stage'>;
  /** 这个来源此刻是不是脱盘。 */
  sourceOffline(location: string): boolean;
  /** 小窗里的「展开」：回到这一条的详情，壳负责地址与来处。 */
  expand(kind: StageKind, id: number, mediaIndex: number | null): void;
  /** 小窗接不了的卡片照常打开详情。 */
  openItem(id: number): void;
}

/** 小窗组件画好之后登记进来的节点；播放态、时间与比例随播放器事件直接写在它们身上。 */
export interface MiniplayerDom {
  root: HTMLElement;
  frame: HTMLElement;
  play: HTMLElement;
  time: HTMLElement;
  back: HTMLElement;
  ahead: HTMLElement;
  title: HTMLElement;
  sub: HTMLElement;
  info: HTMLElement;
}

interface Slot {
  player: VjsPlayer;
  kind: StageKind;
  item: PlayerItem;
  mediaIndex: number | null;
  key: string;
}

interface Held extends Slot { meta: StageMeta; off: (() => void)[]; pending: boolean }

interface Attached {
  player: VjsPlayer | null;
  kind: StageKind;
  item: PlayerItem;
  mediaIndex: number | null;
  key: string;
}

let host: StagePlayerHost | null = null;
let held: Held | null = null;
let attached: Attached | null = null;
let meta: StageMeta | null = null;
let dom: MiniplayerDom | null = null;
/* 右键菜单与 i 键的「迷你播放器」：越过播放态判定，把舞台上这一个送进小窗。关窗有退场动画，
   所以它一直留到舞台真正拆掉的那一刻才消费。 */
let requested = false;
let token = 0;
let requestClose: (() => void) | null = null;
let stageOpen: () => boolean = () => false;
type ExternalMount = (frame: HTMLElement, options: StagePlayerOptions, internal: () => () => void) => () => void;
let externalMount: ExternalMount | null = null;

export const playerKey = (kind: StageKind, id: number, mediaIndex: number | null): string =>
  `${kind}:${id}:${mediaIndex ?? ''}`;

export function configureStagePlayer(next: StagePlayerHost, stage: {
  isOpen(): boolean; requestClose(): void;
}, external?: ExternalMount): void {
  host = next;
  externalMount = external ?? null;
  stageOpen = () => stage.isOpen();
  requestClose = () => stage.requestClose();
  configurePlayer({ ...next.player, stage: () => document.getElementById('stage') });
  configurePlayerMenu({
    inMiniplayer: (player) => held?.player === player,
    kind: (player) => (held?.player === player ? held.kind : attached?.kind ?? meta?.kind ?? 'item'),
    expand: () => expandMiniplayer(),
    toMiniplayer: () => toggleMiniplayer(),
  });
  // 离开页面时收掉详情这一路的清晰度探测与转码会话，服务端不留孤儿进程。
  if (!pagehideWired) { pagehideWired = true; addEventListener('pagehide', () => cancelDetailStream()) }
}
let pagehideWired = false;

function stageHost(): StagePlayerHost {
  if (!host) throw new Error('舞台播放器还没有接上宿主（configureStagePlayer）');
  return host;
}

/** 舞台画出一条时登记它的标题与来源；舞台拆掉时清掉。 */
export function setStageMeta(next: StageMeta | null): void { meta = next }
export const stageMeta = (): StageMeta | null => meta;

/* ── 舞台媒体框 ── */

export interface StagePlayerOptions {
  kind: StageKind;
  item: PlayerItem;
  media?: PlayerMedia | null | undefined;
  autoplay?: boolean | undefined;
  resume?: PlayerResume | null | undefined;
  /** `<video>` 的 id：作品详情那一枚是 `vid`，键盘与 e2e 按它找播放区。 */
  id?: string | undefined;
  poster?: string | undefined;
  /** 原生 `<video>` 报错（关注条目据此说明片源失败）。 */
  onError?: (() => void) | undefined;
}

/** 给舞台里一块媒体框挂播放器，返回卸下它的函数。
 *
 *  小窗正放着同一条（同种类、同 id、同一份媒体）就把那一个实例认领回来：Video.js 的壳整块搬进
 *  这块框，读数重新接上，播放不断。否则新建一个 `<video>` 交给 `mountPlayer`。卸下时这个实例
 *  已经被小窗接走就只摘读数，没被接走就连流会话一起拆掉。 */
export function attachStagePlayer(frame: HTMLElement, options: StagePlayerOptions): () => void {
  if (prefersExternalPlayer() && externalMount) {
    closeMiniplayer();
    return externalMount(frame, options, () => attachInternalStagePlayer(frame, { ...options, autoplay: true }));
  }
  return attachInternalStagePlayer(frame, options);
}

function attachInternalStagePlayer(frame: HTMLElement, options: StagePlayerOptions): () => void {
  const { kind, item, media = null } = options;
  const key = playerKey(kind, item.id, media?.index ?? null);
  if (held && held.key === key) return claimHeld(frame, held);
  if (held) closeMiniplayer();
  const slot: Attached = { player: null, kind, item, mediaIndex: media?.index ?? null, key };
  attached = slot;
  const video = document.createElement('video');
  if (options.id) video.id = options.id;
  video.className = 'video-js vjs-big-play-centered';
  video.controls = false;
  video.setAttribute('playsinline', '');
  video.preload = 'metadata';
  if (options.poster) video.poster = options.poster;
  if (options.onError) video.addEventListener('error', options.onError);
  frame.append(video);
  const release = mountPlayer(video, {
    kind, item, media, autoplay: options.autoplay, resume: options.resume ?? null,
    onPlayer: (player) => { if (attached === slot) slot.player = player },
    handedOff: (player) => held?.player === player,
  });
  return () => {
    const handed = !!slot.player && held?.player === slot.player;
    if (attached === slot) attached = null;
    release();
    if (handed) return;
    /* Video.js 没拉到时是裸 `<video>` 在放：节点离开文档照样出声，先停下再清掉片源。 */
    if (!slot.player) { video.pause(); video.removeAttribute('src'); video.load() }
    cancelDetailStream();
  };
}

/** 墙面接收已选定的本地视频条目；计费与在线作品使用详情的来源门槛。 */
export async function mountWallPlayer(summary: PlayerItem, frame: HTMLElement, signal: AbortSignal): Promise<(continuePlaying?: boolean) => void> {
  const item = await api(`/api/item?id=${summary.id}`, { signal }) as PlayerItem & { error?: string };
  signal.throwIfAborted();
  if (!item || item.error) throw new Error(item?.error || '作品未取得');
  if (item.medium && item.medium !== 'video') throw new Error('这条作品没有可播放的视频');
  if (item.location === 'online') throw new Error('请打开详情播放在线作品');
  if (item.cost === 'metered') throw new Error('请打开详情确认计费来源');
  const sources = await stageHost().player.loadSourceStatus();
  signal.throwIfAborted();
  if (sources[String(item.location || '')] === false) throw new Error(stageHost().player.offlineReason(String(item.location || '')));
  const metadata: StageMeta = { kind: 'item', item, title: String(item.title || item.name || ''),
    sub: String((item.performers as string[] | undefined)?.[0] || item.creator || '未归属') };
  setStageMeta(metadata);
  const release = attachStagePlayer(frame, { kind: 'item', item, autoplay: true });
  let released = false;
  return (continuePlaying = false) => {
    if (released) return;
    released = true;
    if (continuePlaying && meta === metadata) handOffStage(true);
    release();
    if (meta === metadata) setStageMeta(null);
  };
}

function claimHeld(frame: HTMLElement, from: Held): () => void {
  const { player } = from;
  unbindHeld(from);
  held = null;
  hideMiniplayer();
  player.el().classList.remove('vjs-peach-mini');
  preparePlayerFrame(frame);
  frame.append(player.el());
  setDetailPlayer(player);
  const slot: Attached = { player, kind: from.kind, item: from.item, mediaIndex: from.mediaIndex, key: from.key };
  attached = slot;
  const detachChrome = attachPlayerChrome(player, frame);
  resizeSoon(player);
  return () => {
    if (attached === slot) attached = null;
    detachChrome();
    if (held?.player === player) return;
    if (detailPlayer() === player) setDetailPlayer(null);
    disposePlayer(player);
    cancelDetailStream();
  };
}

/** 舞台此刻挂着的那个播放器（右键菜单、i 键与剧场模式要）。 */
export const stagePlayer = (): VjsPlayer | null => {
  const player = attached?.player;
  return player && !player.isDisposed() ? player : null;
};

/** 舞台要拆了：舞台上正在放的这一个该不该进小窗。`allow` 是这次离开允不允许进小窗——显式
 *  关闭（叉、Escape）、换成别的详情与删掉当前条目都不允许；右键菜单与 i 键请求过的例外。
 *  展开之后还没来得及认领的那一个（取数改道、盘没挂上）在这里一起收掉。 */
export function handOffStage(allow: boolean): void {
  const wanted = requested;
  requested = false;
  if (held?.pending) closeMiniplayer();
  const slot = attached;
  const player = slot?.player;
  const current = meta;
  if (!slot || !player || player.isDisposed() || !current || !dom) return;
  const settings = stageHost().player.settings();
  const eligible = wanted || (allow && settings.miniplayer && !player.paused() && !player.ended() && !player.error());
  if (!eligible) return;
  enterMiniplayer({ player, kind: slot.kind, item: slot.item, mediaIndex: slot.mediaIndex, key: slot.key }, current);
}

/** 舞台打开这一条之前：小窗放着的不是它就先关掉，两个播放器不同时出声；是它就先藏起来，
 *  等媒体框画出来再认领。 */
export function prepareStageFor(kind: StageKind, id: number): void {
  requested = false;
  if (!held) return;
  if (held.kind === kind && held.item.id === id) {
    held.pending = true;
    hideMiniplayer();
    return;
  }
  closeMiniplayer();
}

/* ── 小窗 ── */

export function registerMiniplayer(next: MiniplayerDom | null): void {
  dom = next;
  if (dom) syncSeekLabels();
}

export const miniplayerActive = (): boolean => !!held && !held.pending && !held.player.isDisposed();

export function miniplayerVideo(): HTMLVideoElement | null {
  return miniplayerActive() ? dom?.frame.querySelector('video') ?? null : null;
}

/** 此刻该响应播放快捷键的 video：舞台开着取舞台里的，否则取小窗里的。直接操作原生元素而不是
 *  Video.js 实例：挂载后 `#vid` 是 Video.js 的 div，真正的媒体元素是 `#vid_html5_api`。 */
export function activeStageVideo(): HTMLVideoElement | null {
  if (stageOpen()) return document.getElementById('stage')?.querySelector('video') ?? null;
  return stagePlayer()?.el().querySelector('video') ?? miniplayerVideo();
}

function paintMeta(next: { title: string; sub: string }): void {
  if (!dom) return;
  dom.title.textContent = next.title || '';
  dom.sub.textContent = next.sub || '';
  dom.info.setAttribute('aria-label', next.title ? `展开到详情：${next.title}` : '展开到详情');
}

/* 画面区按视频比例给高：上游 4:3 的片子小窗就是 400×300。竖片压到 1:1 以内，400 宽的 9:16
   会高过视口。 */
function syncAspect(item: PlayerItem | undefined = held?.item): void {
  if (!dom) return;
  const video = dom.frame.querySelector('video');
  const width = video?.videoWidth || Number(item?.width) || 16;
  const height = video?.videoHeight || Number(item?.height) || 9;
  dom.frame.style.setProperty('--miniplayer-aspect', `${Math.max(width, height)}/${height}`);
}

function syncPlayState(): void {
  const player = held?.player;
  if (!player || player.isDisposed() || !dom) return;
  const paused = player.paused();
  dom.play.setAttribute('aria-label', paused ? '播放' : '暂停');
  /* 主播放器那一枚走的是 path 形变，迷你条上这一枚只有 20px，形变看不出来，走两枚字形叠着换。
     换的只是容器状态，不改 `use` 的 href——改 href 是硬切。 */
  if (!dom.play.querySelector('[data-icon-swap]')) dom.play.innerHTML = iconSwapHtml('player-pause', 'player-play', paused ? 'b' : 'a');
  setIconSwap(dom.play, paused ? 'b' : 'a');
}

function syncTime(): void {
  const player = held?.player;
  if (!player || player.isDisposed() || !dom) return;
  const total = realDuration(held?.item.duration) || realDuration(player.duration());
  dom.time.textContent = `${fmtClock(player.currentTime())} / ${total ? fmtClock(total) : '0:00'}`;
}

const seekStep = (): number => Math.max(1, Number(stageHost().player.settings().seekSeconds) || 10);

/* 步长跟设置走，标签里带着这个数：读屏用户按之前听得到自己会跳多远。每次接手播放器时重写
   一遍，设置改完开的下一个小窗就是新的秒数。 */
function syncSeekLabels(): void {
  if (!dom || !host) return;
  const step = seekStep();
  for (const [button, text] of [[dom.back, `后退 ${step} 秒`], [dom.ahead, `前进 ${step} 秒`]] as const) {
    button.setAttribute('aria-label', text);
    button.title = text;
  }
}

function bindHeld(slot: Held): void {
  const on = (events: string | string[], handler: () => void) => {
    slot.player.on(events, handler);
    slot.off.push(() => { try { slot.player.off(events, handler) } catch { /* 已拆 */ } });
  };
  on(['play', 'pause', 'ended'], syncPlayState);
  on(['timeupdate', 'durationchange', 'loadedmetadata'], syncTime);
  on('loadedmetadata', () => syncAspect());
  syncPlayState(); syncTime(); syncAspect(); syncSeekLabels();
}

function unbindHeld(slot: Held): void { slot.off.forEach((off) => off()); slot.off = [] }

function showMiniplayer(): void {
  const root = dom?.root;
  if (!root) return;
  const entering = root.hidden;
  root.hidden = false;
  if (!entering) return;
  root.setAttribute('data-miniplayer-enter', '');
  const settle = () => root.removeAttribute('data-miniplayer-enter');
  root.addEventListener('animationend', settle, { once: true });
  setTimeout(settle, 500);
}

function hideMiniplayer(): void {
  const root = dom?.root;
  if (!root) return;
  root.hidden = true;
  for (const state of ['data-miniplayer-drag', 'data-miniplayer-snap', 'data-miniplayer-enter']) root.removeAttribute(state);
  root.style.transform = '';
}

function enterMiniplayer(slot: Slot, next: StageMeta): void {
  if (!dom) return;
  const entry: Held = { ...slot, meta: next, off: [], pending: false };
  held = entry;
  token++;
  slot.player.el().classList.add('vjs-peach-mini');
  dom.frame.prepend(slot.player.el());
  paintMeta(next);
  bindHeld(entry);
  showMiniplayer();
  resizeSoon(slot.player);
}

export function closeMiniplayer(): void {
  const slot = held;
  if (slot) unbindHeld(slot);
  held = null;
  token++;
  const player = slot?.player ?? null;
  if (player && detailPlayer() === player) setDetailPlayer(null);
  disposePlayer(player);
  if (player) cancelDetailStream();
  dom?.frame.querySelectorAll('.video-js,video').forEach((el) => el.remove());
  closePlayerMenu();
  hideMiniplayer();
}

export function expandMiniplayer(): void {
  if (!miniplayerActive() || !held) return;
  const { kind, item, mediaIndex } = held;
  stageHost().expand(kind, item.id, mediaIndex);
}

/** i 键与 YouTube 同义：详情里进小窗，小窗里展开回详情。 */
export function toggleMiniplayer(): void {
  if (miniplayerActive() && !stageOpen()) { expandMiniplayer(); return }
  if (stageOpen() && stagePlayer() && requestClose) { requested = true; requestClose() }
}

export function playMiniplayer(): void {
  const player = held?.player;
  if (!player || player.isDisposed()) return;
  if (player.paused()) player.play()?.catch(() => {}); else player.pause();
}

/* 时长取不到时不封顶：直播和还没读到元数据的片子 `duration()` 是 NaN，拿它去 `Math.min` 会把进度
   直接扔成 NaN，视频停在原地不动。 */
export function seekMiniplayer(side: 1 | -1): void {
  const player = held?.player;
  if (!player || player.isDisposed()) return;
  const total = realDuration(player.duration()) || realDuration(held?.item.duration) || 0;
  const at = Math.max(0, (Number(player.currentTime()) || 0) + seekStep() * side);
  player.currentTime(total ? Math.min(total, at) : at);
}

/** 小窗里能直接换的只有普通视频卡：分卷／版次组要先选卷，计费、脱盘和反查不到关注条目的在线
 *  资产都要先过详情里那道门。 */
export function miniplayerTakesCard(item: PlayerItem | null | undefined): boolean {
  if (!miniplayerActive() || !item) return false;
  if (item.part_group || item.edition_group) return false;
  if (item.medium && item.medium !== 'video') return false;
  if (item.cost === 'metered' && item.location !== 'online') return false;
  if (item.location === 'online' && !item.follow_item_id) return false;
  if (stageHost().sourceOffline(String(item.location ?? ''))) return false;
  return true;
}

/** 在小窗里换一条作品。换片就是一条新视频，重新挂一个播放器最干净：上一条的错误兜底、观看
 *  上报和清晰度表都绑在旧实例的闭包里，复用它只会把新片的行为记到旧片头上。 */
export async function miniplayerPlay(id: number): Promise<void> {
  if (prefersExternalPlayer()) { closeMiniplayer(); stageHost().openItem(id); return }
  if (!miniplayerActive() || !held || !dom) return;
  const mine = ++token;
  const item = await (api(`/api/item?id=${id}`) as Promise<(PlayerItem & { error?: unknown }) | null>).catch(() => null);
  if (mine !== token || !miniplayerActive() || !held || !dom) return;
  if (!item || item.error) return;
  if (!miniplayerTakesCard(item)) { stageHost().openItem(id); return }
  const previous = held;
  unbindHeld(previous);
  if (detailPlayer() === previous.player) setDetailPlayer(null);
  disposePlayer(previous.player);
  cancelDetailStream();
  dom.frame.querySelectorAll('.video-js,video').forEach((el) => el.remove());
  const video = document.createElement('video');
  video.className = 'video-js';
  video.setAttribute('playsinline', '');
  video.preload = 'metadata';
  dom.frame.prepend(video);
  const nextMeta: StageMeta = { kind: 'item', item, title: String(item.title || item.name || ''),
    sub: String((item.performers as string[] | undefined)?.[0] || item.creator || '未归属') };
  /* 换片期间 `held` 仍指着旧的那一格（标成已拆），小窗不因为挂载要等一拍而收起。 */
  paintMeta(nextMeta);
  mountPlayer(video, {
    kind: 'item', item, autoplay: true, chrome: false,
    onPlayer: (player) => {
      if (mine !== token) {
        if (player && detailPlayer() === player) setDetailPlayer(null);
        disposePlayer(player);
        return;
      }
      if (!player) { closeMiniplayer(); stageHost().openItem(id); return }
      player.el().classList.add('vjs-peach-mini');
      const slot: Held = { player, kind: 'item', item, mediaIndex: null, key: playerKey('item', item.id, null),
        meta: nextMeta, off: [], pending: false };
      held = slot;
      bindHeld(slot);
        },
  });
  syncAspect(item);
}
