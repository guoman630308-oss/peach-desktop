/* 挂在 Video.js 控制条上的 Peach 控件：设置菜单（氛围模式、播放速度、清晰度）、影院模式键、
 * 控制条排版与形变图标、拖动预览、Media Session、转圈与外挂字幕，外加舞台的氛围光采样。
 *
 * 控件样式取自 YouTube delhi-modern（player 9470c977），每一块的出处写在它自己的函数上。 */
import { api, esc, fmtClock, icon, realDuration } from '@peach/legacy/core';

import { playerHost } from './host';
import { detailPlayer, resizeSoon } from './registry';
import { PLAYER_PANEL_EVENT } from './stats';
import type { PlayerItem, SourceQuality, VjsPlayer } from './types';
import { SubtitleControls, subtitleIcon } from './subtitles';

type TooltipSync = (label?: string, aria?: string) => void;

/** 控制条上每个按钮的悬停提示都从这里出：文案 + 快捷键徽标，样式取自 YouTube delhi-modern。
 *  原生 title 与状态提示共用同一个同步入口。 */
export function playerControlTooltip(button: Element | null | undefined, label: string, shortcut = ''): TooltipSync {
  if (!button) return () => {};
  let tip = button.querySelector<HTMLElement>(':scope>.vjs-peach-tooltip');
  if (!tip) {
    tip = document.createElement('span'); tip.className = 'vjs-peach-tooltip'; tip.setAttribute('role', 'tooltip');
    tip.innerHTML = '<span class="vjs-peach-tooltip-text"></span><kbd hidden></kbd>';
    button.append(tip);
  }
  const text = tip.querySelector('.vjs-peach-tooltip-text')!, key = tip.querySelector('kbd')!;
  if (shortcut) button.setAttribute('aria-keyshortcuts', shortcut);
  const sync: TooltipSync = (nextLabel = label, aria = '') => {
    text.textContent = nextLabel; key.textContent = shortcut; key.hidden = !shortcut;
    button.setAttribute('aria-label', aria || nextLabel); button.removeAttribute('title');
  };
  sync(); return sync;
}

/** 快捷键复用按钮自己的点击路径：全屏、画中画、静音各有兜底逻辑挂在按钮上，在键盘分支里
 *  再实现一遍就会和按钮走岔。小窗里的播放器不在媒体框里，按 Video.js 自己的壳找控件条。 */
export function clickPlayerControl(video: Element | null, selector: string): void {
  video?.closest('.video-js')?.querySelector<HTMLElement>(`.vjs-control-bar ${selector}`)?.click();
}

export function applyAmbientMode(enabled: boolean, save = true): void {
  const host = playerHost(), settings = host.settings();
  settings.ambientMode = !!enabled; if (save) host.saveSettings();
  host.stage()?.toggleAttribute('data-ambient', settings.ambientMode);
  document.dispatchEvent(new CustomEvent('peachambientchange', { detail: { enabled: settings.ambientMode } }));
}

const theaterSyncs = new WeakMap<Element, TooltipSync>();

function syncPlayerTheaterButton(button: Element | null | undefined): void {
  if (!button) return;
  const theater = playerHost().settings().theaterMode;
  button.setAttribute('aria-pressed', String(theater));
  button.querySelector('use')?.setAttribute('href', theater ? '#i-theater-exit' : '#i-theater-enter');
  theaterSyncs.get(button)?.(theater ? '默认视图' : '影院模式');
}

export function applyTheaterMode(enabled: boolean, save = true): void {
  const host = playerHost(), settings = host.settings();
  settings.theaterMode = !!enabled; if (save) host.saveSettings();
  const stage = host.stage(); stage?.toggleAttribute('data-theater', settings.theaterMode);
  syncPlayerTheaterButton(stage?.querySelector('[data-player-theater]'));
  resizeSoon(detailPlayer());
}

/** 氛围光：按画面平均色给舞台写 `--video-glow`。
 *
 *  取样有两条入口：播放中跟着帧回调走，`start` 则立刻取一帧。暂停的画面同样是一帧可画的图，
 *  只跟着帧回调走的话，暂停时关掉氛围模式就再也开不回来——关掉抹掉了光，而帧回调只在有新
 *  画面时才来。每条采样链带一个 run 号：暂停或页面隐藏时排队的那个回调可能永远不来，用一个
 *  「已排队」布尔判重会被它永久锁死；换成 run 号后旧回调醒来直接退出，链上永远只有一条在跑。 */
export function mountPlayerAmbient(video: HTMLVideoElement): () => void {
  const stage = playerHost().stage(), canvas = stage?.querySelector<HTMLCanvasElement>('[data-ambient-canvas]');
  if (!stage || !canvas) return () => {};
  const ctx = canvas.getContext('2d', { alpha: false });
  if (!ctx) return () => {};
  let stopped = false, last = 0, run = 0;
  let glow: number[] | null = null;
  const clear = () => { glow = null; ctx.clearRect(0, 0, canvas.width, canvas.height); stage.style.removeProperty('--video-glow') };
  const sample = () => {
    if (video.readyState < 2) return;
    try {
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      const px = ctx.getImageData(0, 0, canvas.width, canvas.height).data; let r = 0, g = 0, b = 0, n = 0;
      for (let i = 0; i < px.length; i += 16) { r += px[i]!; g += px[i + 1]!; b += px[i + 2]!; n++ }
      if (n) {
        const next = [Math.round(r / n), Math.round(g / n), Math.round(b / n)];
        // 平均色的微小抖动保持稳定，继承色只在可见变化时刷新整座详情。
        if (!glow || next.some((value, index) => Math.abs(value - glow![index]!) >= 6)) {
          glow = next; stage.style.setProperty('--video-glow', `rgb(${next.join(' ')})`);
        }
      }
    } catch { /* 跨源画面取不到像素 */ }
  };
  const queue = (id: number) => {
    if (video.requestVideoFrameCallback) video.requestVideoFrameCallback((now) => paint(id, now));
    else requestAnimationFrame((now) => paint(id, now));
  };
  const paint = (id: number, now: number) => {
    if (stopped || id !== run) return;
    if (!playerHost().settings().ambientMode) { clear(); return }
    if (video.paused) return;
    if (!document.hidden && now - last > 480) { last = now; sample() }
    queue(id);
  };
  const start = () => { if (stopped || !playerHost().settings().ambientMode) return; sample(); if (!video.paused) queue(++run) };
  const onChange = (event: Event) => {
    const enabled = !!(event as CustomEvent<{ enabled: boolean }>).detail.enabled;
    stage.toggleAttribute('data-ambient', enabled);
    if (enabled) start(); else { run++; clear() }
  };
  document.addEventListener('peachambientchange', onChange);
  video.addEventListener('play', start); video.addEventListener('loadeddata', start); start();
  return () => {
    stopped = true; run++; document.removeEventListener('peachambientchange', onChange);
    video.removeEventListener('play', start); video.removeEventListener('loadeddata', start); clear();
  };
}

/** 设置菜单：氛围模式、播放速度、清晰度。返回的函数换掉来源清晰度表。
 *
 *  `sourceQualities` 是来源自己给的清晰度表（[{height,label}]，从高到低）。rule34video 把每档
 *  写成独立字段，videojs 的 qualityLevels 看不到它们——那套只认 HLS/DASH 的自适应轨道，而这里
 *  是四个各自独立的 mp4。所以由调用方查好再传进来。 */
export function mountPlayerQualityControl(
  player: VjsPlayer, video: HTMLVideoElement, fallbackHeight: unknown = 0, initialSourceQualities: SourceQuality[] | null = null,
): ((next: SourceQuality[] | null) => void) | undefined {
  const controlBar = player.el().querySelector<HTMLElement>('.vjs-control-bar');
  if (!controlBar || controlBar.querySelector('[data-player-quality]')) return undefined;
  const root = document.createElement('div');
  root.className = 'vjs-peach-settings vjs-control'; root.dataset.playerQuality = '';
  root.innerHTML = `<button type="button" class="vjs-peach-settings-toggle" aria-label="播放器设置" aria-expanded="false">
    ${icon('settings')}<span data-player-quality-badge hidden></span></button>
    <div class="vjs-peach-settings-menu" role="menu" aria-label="播放器设置" aria-hidden="true"></div>`;
  const fullscreen = controlBar.querySelector('.vjs-fullscreen-control');
  controlBar.insertBefore(root, fullscreen || null);
  const toggle = root.querySelector('button')!, badge = root.querySelector<HTMLElement>('[data-player-quality-badge]')!;
  playerControlTooltip(toggle, '设置');
  const menu = root.querySelector<HTMLElement>('.vjs-peach-settings-menu')!;
  let subtitleControls: SubtitleControls | undefined;
  const levels = typeof player.qualityLevels === 'function' ? player.qualityLevels() : null;
  let sourceQualities = initialSourceQualities;
  let selectedQuality = 'auto';
  const resolution = (width: unknown, height: unknown) => {
    const values = [Number(width), Number(height)].filter((value) => value > 0);
    return values.length ? Math.min(...values) : 0;
  };
  interface Row { key: string; label: string; pixels: number }
  const rows = (): Row[] => {
    const result: Row[] = [];
    if (levels?.length) {
      if (levels.length > 1) result.push({ key: 'auto', label: '自动', pixels: 0 });
      for (let index = 0; index < levels.length; index++) {
        const level = levels[index]!;
        const pixels = resolution(level.width, level.height)
          || (levels.length === 1 ? resolution(video.videoWidth, video.videoHeight) : 0);
        result.push({ key: String(index), label: pixels ? `${pixels}p` : (levels.length === 1 ? '当前画质' : `线路 ${index + 1}`), pixels });
      }
      return result;
    }
    if (sourceQualities?.length) {
      return sourceQualities.map((quality) => ({ key: `h${quality.height}`, label: quality.label || `${quality.height}p`, pixels: quality.height }));
    }
    const pixels = resolution(video.videoWidth, video.videoHeight) || Number(fallbackHeight) || 0;
    return [{ key: 'original', label: pixels ? `${pixels}p` : '原画', pixels }];
  };
  const qualityRows = () => {
    const options = rows();
    const active = options.find((option) => option.key === selectedQuality) || options[0]!;
    const playing = levels?.[levels.selectedIndex ?? -1];
    const activePixels = active.key === 'auto'
      ? resolution(playing?.width, playing?.height) || resolution(video.videoWidth, video.videoHeight)
      : active.pixels;
    badge.textContent = activePixels >= 2160 ? '4K' : activePixels >= 720 ? 'HD' : ''; badge.hidden = !badge.textContent;
    return { options, active };
  };
  const isOpen = () => menu.getAttribute('aria-hidden') !== 'true';
  const setOpen = (open: boolean) => {
    menu.setAttribute('aria-hidden', String(!open)); toggle.setAttribute('aria-expanded', String(open));
    if (!open) subtitleControls?.hide();
    if (open) document.dispatchEvent(new CustomEvent(PLAYER_PANEL_EVENT, { detail: 'settings' }));
  };
  const close = () => setOpen(false);
  const closeSettingsForOtherPanel = (event: Event) => { if ((event as CustomEvent<string>).detail !== 'settings') close() };
  document.addEventListener(PLAYER_PANEL_EVENT, closeSettingsForOtherPanel);
  /* 面板之间的切换照 YouTube 播放器 9470c977 的 www-player.css：popup 自己 .25s
     cubic-bezier(.4,0,.2,1) 改高度，旧面板往来的方向滑出、新面板从去的方向滑入。
     旧面板必须先脱离布局再滑，否则两块内容会在动画期间把菜单撑成两倍高；高度也得
     先钉在旧值、下一帧再写新值，同一帧写两次只会直接跳到新值，看不到过渡。 */
  const PANEL_MS = 250;
  let panelTimer: ReturnType<typeof setTimeout> | null = null;
  let panelRun = 0;
  const renderPanel = (html: string, direction: number): HTMLElement => {
    const run = ++panelRun;
    if (panelTimer) {
      clearTimeout(panelTimer); panelTimer = null;
      menu.classList.remove('vjs-peach-popup-animating'); menu.style.height = '';
    }
    menu.querySelectorAll('.vjs-peach-panel-leaving').forEach(panel => panel.remove());
    menu.scrollTop = 0;
    const current = menu.querySelector<HTMLElement>('.vjs-peach-panel');
    const next = document.createElement('div'); next.className = 'vjs-peach-panel'; next.innerHTML = html;
    if (!current || !direction || !isOpen() || matchMedia('(prefers-reduced-motion:reduce)').matches) { menu.replaceChildren(next); menu.style.height = ''; return next }
    const from = menu.getBoundingClientRect().height;
    current.classList.add('vjs-peach-panel-leaving'); current.inert = true; current.setAttribute('aria-hidden', 'true');
    next.classList.add(direction > 0 ? 'vjs-peach-panel-animate-forward' : 'vjs-peach-panel-animate-back');
    menu.append(next); menu.style.height = `${from}px`;
    const to = next.scrollHeight;
    requestAnimationFrame(() => {
      if (run !== panelRun) return;
      menu.classList.add('vjs-peach-popup-animating'); menu.style.height = `${to}px`;
      next.classList.remove('vjs-peach-panel-animate-forward', 'vjs-peach-panel-animate-back');
      current.classList.add(direction > 0 ? 'vjs-peach-panel-animate-back' : 'vjs-peach-panel-animate-forward');
      panelTimer = setTimeout(() => {
        panelTimer = null; current.remove();
        menu.classList.remove('vjs-peach-popup-animating'); menu.style.height = '';
      }, PANEL_MS);
    });
    return next;
  };
  const button = (panel: HTMLElement, selector: string) => panel.querySelector<HTMLElement>(selector)!;
  /** 菜单此刻摆的是哪一面；轨道与尺寸变化只就地改这一面（`refreshPanel`）。 */
  let view: 'main' | 'speed' | 'quality' | 'subtitles' = 'main';
  const showMain = (direction = 0) => {
    view = 'main';
    const { active } = qualityRows(), speed = Number(player.playbackRate()) || 1;
    const panel = renderPanel(`<div class="vjs-peach-panel-menu"><button type="button" class="vjs-peach-menu-row" role="menuitemcheckbox" data-player-ambient aria-checked="${playerHost().settings().ambientMode}">
      ${icon('player-ambient')}<span>氛围模式</span><i class="vjs-peach-switch" aria-hidden="true"></i></button>
      <button type="button" class="vjs-peach-menu-row" role="menuitem" data-player-speed>${icon('player-speed')}<span>播放速度</span><b>${speed === 1 ? '正常' : `${speed}×`}</b>${icon('player-menu-next')}</button>
      <button type="button" class="vjs-peach-menu-row" role="menuitem" data-player-subtitles><span class="vjs-peach-menu-cc" aria-hidden="true">${subtitleIcon()}</span><span>字幕</span><b>${esc(subtitleControls?.label() || '关闭')}</b>${icon('player-menu-next')}</button>
      <button type="button" class="vjs-peach-menu-row" role="menuitem" data-player-quality-view>${icon('player-quality')}<span>清晰度</span><b>${esc(active.label)}</b>${icon('player-menu-next')}</button></div>`, direction);
    button(panel, '[data-player-ambient]').onclick = () => { applyAmbientMode(!playerHost().settings().ambientMode); showMain() };
    button(panel, '[data-player-speed]').onclick = () => showSpeed();
    button(panel, '[data-player-subtitles]').onclick = () => subtitleControls?.show();
    button(panel, '[data-player-quality-view]').onclick = () => showQuality();
  };
  /* 播放速度面板照 YouTube delhi-modern（player 9470c977 的 base.js）：滑条两端取播放器
     支持的最低与最高倍速，步进 0.05，加减键各动 0.05 并按两位小数收敛，读数写成 1.00x。
     预设胶囊点了就地生效，面板不退回上一级。第五格 3.0 在上游是 Premium 专属，本机装的
     Peach 没有会员分级这回事，那一格照上游留着，只是不画角标；滑条上限跟着抬到 3，
     不然点 3.0 会被收敛回 2。 */
  const SPEED_MIN = .25, SPEED_MAX = 3, SPEED_STEP = .05, SPEED_PRESETS = [1, 1.25, 1.5, 2, 3];
  const speedLabel = (speed: number) => Number.isInteger(speed) ? speed.toFixed(1) : String(speed);
  const showSpeed = (direction = 1) => {
    view = 'speed';
    const min = SPEED_MIN, max = SPEED_MAX;
    const clampSpeed = (value: number) => Math.min(max, Math.max(min, Number(value.toFixed(2))));
    const panel = renderPanel(`<div class="vjs-peach-panel-header"><button type="button" class="vjs-peach-menu-back" data-player-menu-back aria-label="返回上一个菜单">${icon('player-menu-back')}</button><strong>播放速度</strong></div>
      <div class="vjs-peach-speed-panel"><div class="vjs-peach-speed-display"><output data-player-speed-display></output></div>
      <div class="vjs-peach-speed-slider"><button type="button" class="vjs-peach-speed-button" data-player-speed-step="-1" aria-label="播放速度减 0.05">${icon('minus')}</button>
      <input type="range" class="vjs-peach-speed-range" data-player-speed-range min="${min}" max="${max}" step="${SPEED_STEP}" aria-label="播放速度">
      <button type="button" class="vjs-peach-speed-button" data-player-speed-step="1" aria-label="播放速度加 0.05">${icon('plus')}</button></div>
      <div class="vjs-peach-speed-chips">${SPEED_PRESETS.map((speed) =>
        `<span class="vjs-peach-speed-preset"><button type="button" class="vjs-peach-speed-button" data-player-speed-option="${speed}" aria-pressed="false">${speedLabel(speed)}</button>${speed === 1 ? '<span class="vjs-peach-speed-preset-label">正常</span>' : ''}</span>`).join('')}</div></div>`, direction);
    const display = panel.querySelector<HTMLOutputElement>('[data-player-speed-display]')!;
    const range = panel.querySelector<HTMLInputElement>('[data-player-speed-range]')!;
    // player.playbackRate() 读的是 ratechange 之后才写的缓存，面板自己记住这一次的倍速。
    let rate = clampSpeed(Number(player.playbackRate()) || 1);
    const syncSpeed = () => {
      display.textContent = `${rate.toFixed(2)}x`; range.value = String(rate);
      range.style.setProperty('--peach-speed-percent', `${(rate - min) / (max - min) * 100}%`);
      panel.querySelectorAll<HTMLElement>('[data-player-speed-option]').forEach((option) =>
        option.setAttribute('aria-pressed', String(Number(option.dataset.playerSpeedOption) === rate)));
    };
    const setSpeed = (value: number) => { rate = clampSpeed(value); player.playbackRate(rate); syncSpeed() };
    button(panel, '[data-player-menu-back]').onclick = () => showMain(-1);
    range.oninput = () => setSpeed(Number(range.value));
    panel.querySelectorAll<HTMLElement>('[data-player-speed-step]').forEach((step) => {
      step.onclick = () => setSpeed(rate + Number(step.dataset.playerSpeedStep) * SPEED_STEP);
    });
    panel.querySelectorAll<HTMLElement>('[data-player-speed-option]').forEach((option) => {
      option.onclick = () => setSpeed(Number(option.dataset.playerSpeedOption));
    });
    syncSpeed();
  };
  /** 清晰度那一列：打开面板时画一次，轨道增减或自动档换轨时原地重画，焦点留在同一档上。 */
  const fillQualityOptions = (list: HTMLElement) => {
    const { options, active } = qualityRows();
    const focused = list.contains(document.activeElement) ? (document.activeElement as HTMLElement).dataset.playerQualityOption : undefined;
    list.innerHTML = options.map((option) =>
      `<button type="button" class="vjs-peach-menu-option" role="menuitemradio" data-player-quality-option="${esc(option.key)}" aria-checked="${option.key === active.key}"><span class="vjs-peach-option-check">${option.key === active.key ? icon('player-option-check') : ''}</span><span class="vjs-peach-option-label">${esc(option.label)}</span></button>`).join('');
    const buttons = [...list.querySelectorAll<HTMLElement>('[data-player-quality-option]')];
    buttons.find((option) => focused !== undefined && option.dataset.playerQualityOption === focused)?.focus();
    buttons.forEach((option) => {
      option.onclick = () => {
        selectedQuality = option.dataset.playerQualityOption || 'auto';
        if (levels?.length) {
          for (let index = 0; index < levels.length; index++) levels[index]!.enabled = selectedQuality === 'auto' || selectedQuality === String(index);
        }
        /* 来源档位是四个各自独立的 mp4，不是同一条流的多个轨道，所以只能换 src。
           记住当前进度和播放状态再换：换源会重新加载，不接回去就等于从头开始。 */
        if (sourceQualities?.length && selectedQuality.startsWith('h')) {
          const height = selectedQuality.slice(1);
          const at = player.currentTime() || 0, wasPlaying = !player.paused();
          const next = new URL(player.currentSrc() || video.src, location.origin);
          next.searchParams.set('quality', height);
          player.src({ src: next.pathname + next.search, type: 'video/mp4' });
          player.one('loadedmetadata', () => {
            if (at > 0) player.currentTime(at);
            if (wasPlaying) player.play()?.catch(() => {});
          });
        }
        showMain(-1);
      };
    });
  };
  const showQuality = (direction = 1) => {
    view = 'quality';
    const panel = renderPanel(`<div class="vjs-peach-panel-header"><button type="button" class="vjs-peach-menu-back" data-player-menu-back aria-label="返回上一个菜单">${icon('player-menu-back')}</button><strong>清晰度</strong></div><div class="vjs-peach-panel-menu"></div>`, direction);
    button(panel, '[data-player-menu-back]').onclick = () => showMain(-1);
    fillQualityOptions(button(panel, '.vjs-peach-panel-menu'));
  };
  /* HLS 自动码率起播时轨道陆续登记、码率随网速换档，视频尺寸也跟着变：这些事件只更新角标和
     正在看的那一面。退回主菜单的话，正在清晰度面板里挑档的人会被一次换轨踢出去。 */
  const refreshPanel = () => {
    if (!isOpen()) { qualityRows(); return }
    const panel = menu.lastElementChild as HTMLElement | null;
    if (view === 'quality' && panel) { fillQualityOptions(button(panel, '.vjs-peach-panel-menu')); return }
    const { active } = qualityRows();
    const label = view === 'main' ? panel?.querySelector('[data-player-quality-view] b') : null;
    if (label) label.textContent = active.label;
  };
  toggle.onclick = (event) => { event.stopPropagation(); const open = !isOpen(); if (open) showMain(); setOpen(open) };
  const outside = (event: Event) => { if (!root.contains(event.target as Node)) close() };
  document.addEventListener('pointerdown', outside);
  root.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && isOpen()) { event.preventDefault(); event.stopPropagation(); close(); toggle.focus() }
    if (['ArrowDown', 'ArrowUp'].includes(event.key) && isOpen() && !(event.target instanceof HTMLInputElement)) {
      event.preventDefault();
      const buttons = [...menu.querySelectorAll<HTMLElement>('button:not([disabled]),input,a[href]')].filter(node => node.offsetParent !== null && !node.closest('[inert]'));
      const current = buttons.indexOf(document.activeElement as HTMLElement);
      buttons[(current + (event.key === 'ArrowDown' ? 1 : buttons.length - 1)) % buttons.length]?.focus();
    }
  });
  video.addEventListener('loadedmetadata', refreshPanel);
  video.addEventListener('resize', refreshPanel);
  levels?.on?.(['addqualitylevel', 'removequalitylevel', 'change'], refreshPanel);
  player.on('dispose', () => {
    panelRun++;
    document.removeEventListener('pointerdown', outside);
    document.removeEventListener(PLAYER_PANEL_EVENT, closeSettingsForOtherPanel);
    if (panelTimer) clearTimeout(panelTimer);
  });
  subtitleControls = new SubtitleControls(player, video, {
    render: renderPanel, open: () => { view = 'subtitles'; setOpen(true) }, close,
    back: () => { subtitleControls?.hide(); showMain(-1) },
  });
  qualityRows();
  mountPlayerTheaterControl(player, root);
  mountPlayerChromeLayout(player);
  return (next) => { sourceQualities = next?.length ? next : null; refreshPanel() };
}

function mountPlayerTheaterControl(player: VjsPlayer, settingsRoot: Element): void {
  const controlBar = player.el().querySelector<HTMLElement>('.vjs-control-bar');
  if (!controlBar || controlBar.querySelector('[data-player-theater]')) return;
  const theater = playerHost().settings().theaterMode;
  const root = document.createElement('div'); root.className = 'vjs-peach-theater vjs-control';
  root.innerHTML = `<button type="button" data-player-theater aria-pressed="${theater}">${icon(theater ? 'theater-exit' : 'theater-enter')}</button>`;
  controlBar.insertBefore(root, settingsRoot.nextSibling);
  const theaterButton = root.querySelector('button')!;
  theaterSyncs.set(theaterButton, playerControlTooltip(theaterButton, '影院模式', 'T'));
  syncPlayerTheaterButton(theaterButton);
  theaterButton.onclick = (event) => { event.stopPropagation(); applyTheaterMode(!playerHost().settings().theaterMode) };
}

function mountPlayerChromeLayout(player: VjsPlayer): void {
  const controlBar = player.el().querySelector<HTMLElement>('.vjs-control-bar');
  if (!controlBar || controlBar.querySelector('.vjs-peach-right-controls')) return;
  const play = controlBar.querySelector<HTMLElement>(':scope>.vjs-play-control');
  if (play && !play.querySelector(':scope>.vjs-peach-hover')) play.insertAdjacentHTML('beforeend', '<span class="vjs-peach-hover" aria-hidden="true"></span>');
  const explicitIcon = (button: HTMLElement | null, name: string) => {
    if (!button) return null;
    button.dataset.peachExplicitIcon = '';
    button.insertAdjacentHTML('beforeend', icon(name, 'vjs-peach-control-icon'));
    return button.querySelector(':scope>.vjs-peach-control-icon use');
  };
  /* 播放键和静音键的图标要自己形变，不能整块换掉：`<use>` 克隆出来的是影子树，里面的
     `d` 改不动，也挂不上过渡。所以这两个键把 sprite 里的 <path> 搬进自己的 svg，图标怎么变
     由 CSS 说。照 YouTube delhi-modern（player 9470c977 的 base.js）：播放↔暂停是同一条
     路径逐个数字插值 200ms（上游 `eST` 把 `d` 拆成数字与分隔符再逐位插值），音量的两道弧
     各自缩放 250ms（上游 `jjc`：内弧绕 (18,12)、外弧绕 (22,12)），两处曲线都是 `qn3`
     也就是 cubic-bezier(.4,0,.2,1)。 */
  const morphIcon = (button: HTMLElement | null | undefined, name: string) => {
    if (!button) return null;
    const symbol = document.getElementById(`i-${name}`); if (!symbol) return null;
    button.dataset.peachExplicitIcon = '';
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', symbol.getAttribute('viewBox') || '');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('class', 'vjs-peach-control-icon vjs-peach-morph-icon');
    svg.innerHTML = symbol.innerHTML; button.append(svg); return svg;
  };
  const spritePaths = (name: string) => [...(document.getElementById(`i-${name}`)?.querySelectorAll('path') || [])];
  const playIcon = morphIcon(play, 'player-play'), playPath = playIcon?.querySelector('path');
  const playD = spritePaths('player-play')[0]?.getAttribute('d') || '', pauseD = spritePaths('player-pause')[0]?.getAttribute('d') || '';
  const syncPlayTooltip = playerControlTooltip(play, '播放', 'K');
  /* WebKit（Safari 与 iOS 上的所有浏览器）不认 CSS 的 `d` 属性，写进 style 等于没写，图标
     停在播放那一枚。那边直接改路径属性：没有插值，瞬间切换。 */
  const cssPathD = CSS.supports('d', 'path("M0 0")');
  const syncPlayIcon = () => {
    const paused = player.paused() || player.ended(), d = paused ? playD : pauseD;
    if (playPath) { if (cssPathD) playPath.style.setProperty('d', `path("${d}")`); else playPath.setAttribute('d', d) }
    syncPlayTooltip(paused ? '播放' : '暂停');
  };
  player.on(['play', 'pause', 'ended'], syncPlayIcon); syncPlayIcon();
  const volume = controlBar.querySelector<HTMLElement>(':scope>.vjs-volume-panel');
  const mute = volume?.querySelector<HTMLElement>(':scope>.vjs-mute-control'), muteIcon = morphIcon(mute, 'player-volume');
  /* 静音那张图标搬进同一个 svg：挖空的喇叭和叉号跟实心喇叭、两道弧共处一处，弧缩完了
     它们才一起顶上，靠 opacity 换而不是换整块 svg。 */
  if (muteIcon) spritePaths('player-volume-muted').forEach((path) => muteIcon.append(path.cloneNode(true)));
  const syncMuteTooltip = playerControlTooltip(mute, '静音', 'M');
  /* 外弧跟音量走：上游 `setVolume` 里超过 50 才给 1，否则 0，静音时两道弧一起收掉。 */
  const syncVolumeIcon = () => {
    const silent = player.muted() || player.volume() === 0;
    if (muteIcon) { muteIcon.dataset.silent = String(silent); muteIcon.dataset.loud = String(!silent && player.volume() > .5) }
    syncMuteTooltip(silent ? '取消静音' : '静音');
  };
  player.on('volumechange', syncVolumeIcon); syncVolumeIcon();
  /* 中心提示照 YouTube delhi-modern（player 9470c977 的 www-player.css 与 base.js）：一块
     78px 的毛玻璃圆闪一下当前动作的图标，1s 走完 0→1.33→1 的缩放淡出。捕获阶段读的是
     切换之前的状态，闪出来的正好是这一次做的事：暂停中点播放键闪播放。键盘快捷键走的
     也是同一个按钮的点击路径，所以键盘不用另挂一处。
     画面中心只允许有这一块提示圆：整个播放器里凡是能切换播放的入口——控制条的播放键、
     静音键、点画面本身——都汇到这个 flashBezel 上。同一块画面上再挂第二个 78px 圆，
     两边各按自己的时机取状态，点一下就会一个闪播放一个闪暂停，叠在一起看不清哪个才是
     刚做的事。 */
  const bezel = document.createElement('div');
  bezel.className = 'vjs-peach-bezel'; bezel.setAttribute('role', 'status'); bezel.hidden = true;
  bezel.innerHTML = `<span class="vjs-peach-bezel-icon">${icon('player-play')}</span>`;
  const bezelUse = bezel.querySelector('use'); let bezelTimer: ReturnType<typeof setTimeout> | null = null;
  const flashBezel = (name: string, label: string) => {
    bezelUse?.setAttribute('href', `#i-${name}`); bezel.setAttribute('aria-label', label);
    bezel.hidden = false; bezel.classList.remove('vjs-peach-bezel-run');
    void bezel.offsetWidth; bezel.classList.add('vjs-peach-bezel-run');
    if (bezelTimer) clearTimeout(bezelTimer);
    bezelTimer = setTimeout(() => { bezel.hidden = true; bezel.classList.remove('vjs-peach-bezel-run') }, 1000);
  };
  controlBar.parentElement!.insertBefore(bezel, controlBar);
  player.el().addEventListener('click', (event) => {
    const target = event.target as Element;
    if (target.closest('.vjs-mute-control')) {
      const silent = player.muted() || player.volume() === 0;
      flashBezel(silent ? 'player-volume' : 'player-volume-muted', silent ? '取消静音' : '静音');
    } else if (target.closest('.vjs-play-control,.vjs-tech,.vjs-poster')) {
      const paused = player.paused() || player.ended();
      flashBezel(paused ? 'player-play' : 'player-pause', paused ? '播放' : '暂停');
    }
  }, true);
  player.on('dispose', () => { if (bezelTimer) clearTimeout(bezelTimer) });
  const time = document.createElement('button'); let remaining = false;
  time.type = 'button'; time.className = 'vjs-peach-time vjs-control'; time.dataset.playerTime = '';
  time.innerHTML = '<span class="vjs-peach-time-text"></span>';
  const timeText = time.querySelector('.vjs-peach-time-text')!;
  const syncTimeTooltip = playerControlTooltip(time, '显示剩余时间');
  const syncTime = () => {
    const current = Math.max(0, Number(player.currentTime()) || 0), duration = Math.max(0, Number(player.duration()) || 0);
    const left = fmtClock(Math.max(0, duration - current));
    timeText.textContent = `${remaining ? `-${left}` : fmtClock(current)} / ${fmtClock(duration)}`;
    time.dataset.remaining = String(remaining);
    syncTimeTooltip(remaining ? '显示已播放时间' : '显示剩余时间', remaining
      ? `剩余 ${left}，总时长 ${fmtClock(duration)}；点击显示已播放时间`
      : `已播放 ${fmtClock(current)}，总时长 ${fmtClock(duration)}；点击显示剩余时间`);
  };
  time.onclick = (event) => { event.stopPropagation(); remaining = !remaining; syncTime() };
  player.on(['timeupdate', 'durationchange', 'loadedmetadata'], syncTime); syncTime();
  if (volume) volume.insertAdjacentElement('afterend', time); else controlBar.append(time);
  const pip = controlBar.querySelector<HTMLElement>(':scope>.vjs-picture-in-picture-control');
  playerControlTooltip(controlBar.querySelector('.vjs-subtitles-control>button'), '字幕');
  explicitIcon(pip, 'player-pip');
  // i 键归迷你播放器（YouTube 的 aria-keyshortcuts="i"），画中画只留按钮。
  const syncPipTooltip = playerControlTooltip(pip, '画中画');
  player.on(['enterpictureinpicture', 'leavepictureinpicture'], () => syncPipTooltip(document.pictureInPictureElement ? '退出画中画' : '画中画'));
  const fullscreen = controlBar.querySelector<HTMLElement>(':scope>.vjs-fullscreen-control');
  const fullscreenUse = explicitIcon(fullscreen, 'player-fullscreen-enter');
  const syncFullscreenTooltip = playerControlTooltip(fullscreen, '全屏', 'F');
  /* CSS 的 `.vjs-fullscreen` 只能覆盖 Video.js 已经同步状态类的路径。实际浏览器还可能
     走 full-window 回退，或者先触发 fullscreenchange、下一帧才完成 class 更新。把播放器
     自己的 `isFullscreen()` 结果登记到 DOM，画面填充不再依赖某一个实现细节类名。 */
  const syncFullscreenState = () => {
    const active = !!player.isFullscreen();
    player.el().toggleAttribute('data-peach-fullscreen', active);
    fullscreenUse?.setAttribute('href', active ? '#i-player-fullscreen-exit' : '#i-player-fullscreen-enter');
    syncFullscreenTooltip(active ? '退出全屏' : '全屏');
    resizeSoon(player);
  };
  player.on(['fullscreenchange', 'enterFullWindow', 'exitFullWindow'], syncFullscreenState);
  syncFullscreenState();
  const controls = [
    pip,
    controlBar.querySelector<HTMLElement>(':scope>.vjs-subtitles-control'),
    controlBar.querySelector<HTMLElement>(':scope>.vjs-peach-settings'),
    controlBar.querySelector<HTMLElement>(':scope>.vjs-peach-theater'),
    fullscreen,
  ].filter((control): control is HTMLElement => !!control);
  if (!controls.length) return;
  const group = document.createElement('div'); group.className = 'vjs-peach-right-controls'; group.setAttribute('aria-label', '播放器视图控制');
  controlBar.insertBefore(group, controls[0]!);
  controls.forEach((control) => {
    control.insertAdjacentHTML('beforeend', '<span class="vjs-peach-hover" aria-hidden="true"></span>');
    group.append(control);
  });
  /* 窄屏折叠照 YouTube 的判据来：base.js 9470c977 里播放器宽度 `v.width<528` 打开
     ytp-xsmall-width-mode，右侧收成「设置 + 展开」，点开才铺开其余按钮。判据必须是播放器
     自己的宽度，不是视口——同一个视口下影院模式和普通视图的播放器宽度差一大截。 */
  const expand = document.createElement('div'); expand.className = 'vjs-peach-expand vjs-control';
  /* 高亮层要挂在 `.vjs-control` 这一层：亮起来的规则是 `>.vjs-peach-hover`，塞进
     button 里就差一层，展开键成了这排唯一没有 hover 的按钮。位置在这排左端——
     这排整体右对齐，展开时新按钮从它右边长出来，箭头指左才对得上要发生的事。箭头用
     `i-player-expand`：菜单行那个 `>` 是 24 视框、一个单位粗的细线，铺到 32px 只有 1.3px 粗；
     上游展开键自带一个 32 视框、两个单位粗的箭头，同样 32px 渲染就是 2px。 */
  expand.innerHTML = `<button type="button" data-player-expand aria-expanded="false">${icon('player-expand')}</button><span class="vjs-peach-hover" aria-hidden="true"></span>`;
  group.prepend(expand);
  const expandButton = expand.querySelector('button')!;
  const syncExpandTooltip = playerControlTooltip(expandButton, '展开控件');
  const setExpanded = (open: boolean) => {
    player.el().classList.toggle('vjs-peach-right-expanded', open);
    expandButton.setAttribute('aria-expanded', String(open)); syncExpandTooltip(open ? '收起控件' : '展开控件');
  };
  expandButton.onclick = (event) => { event.stopPropagation(); setExpanded(!player.el().classList.contains('vjs-peach-right-expanded')) };
  const syncWidthMode = () => {
    const box = player.el(), narrow = box.clientWidth < 528;
    /* 设置面板要按播放器高度收顶，而它的定位祖先只有 36px 高，百分比取不到播放器。 */
    box.style.setProperty('--peach-player-h', `${box.clientHeight}px`);
    box.classList.toggle('vjs-peach-xsmall', narrow);
    if (!narrow) setExpanded(false);
  };
  const widthObserver = new ResizeObserver(syncWidthMode); widthObserver.observe(player.el());
  player.on('dispose', () => widthObserver.disconnect());
  setExpanded(false); syncWidthMode();
}

interface TimelineSheets { frames: number; interval: number; columns?: number; rows?: number }

/** 进度条上的拖动预览：时间与那一秒的画面。
 *
 *  采集任务铺好了时间轴接触印相就按它走：每 10 或 30 秒一帧，指到哪一秒看到的就是那一秒。
 *  取不到退回九宫格那九格——那是全片九等分，两小时的片子格与格之间隔着十几分钟，指的位置和
 *  看到的画面对不上，但比没有画面强。关注条目没有本地图，只报时间。 */
export function mountPlayerSeekPreview(player: VjsPlayer, item: PlayerItem | null, options: { thumbnail?: boolean } = {}): void {
  const progress = player.el().querySelector<HTMLElement>('.vjs-control-bar .vjs-progress-control');
  if (!progress || progress.querySelector('[data-player-seek-preview]')) return;
  const hasThumbnail = !!item && options.thumbnail !== false;
  const preview = document.createElement('div');
  preview.className = 'vjs-peach-seek-preview'; preview.dataset.playerSeekPreview = ''; preview.hidden = true;
  preview.innerHTML = `${hasThumbnail ? '<i class="vjs-peach-seek-frame" hidden><img alt=""></i>' : ''}<span class="mono">0:00</span>`;
  progress.append(preview);
  const frame = preview.querySelector<HTMLElement>('.vjs-peach-seek-frame');
  const image = preview.querySelector('img'), label = preview.querySelector('span')!;
  let sheets: TimelineSheets | null = null, shown = '';
  if (frame && item?.id) {
    (api(`/api/timeline?id=${encodeURIComponent(item.id)}`) as Promise<TimelineSheets | null>)
      .then((meta) => { if (meta && meta.frames > 0 && meta.interval > 0) sheets = meta }).catch(() => {});
  }
  const showSheetFrame = (seconds: number, sheet: TimelineSheets, box: HTMLElement, img: HTMLImageElement) => {
    if (!item) return;
    const columns = sheet.columns || 10, per = columns * (sheet.rows || 10);
    const index = Math.min(sheet.frames - 1, Math.max(0, Math.floor(seconds / sheet.interval)));
    const page = Math.floor(index / per), slot = index % per;
    // 末张通常不满 10 行，行数按它自己那几帧反算：按满行去铺，格子会落到图外面的空白上。
    const rows = Math.ceil(Math.min(per, sheet.frames - page * per) / columns);
    const source = `/timeline?id=${encodeURIComponent(item.id)}&s=${page}`;
    if (source !== shown) { shown = source; img.src = source }
    box.hidden = false;
    // 取景框按片源比例，图按格子铺满：两边比例一致，`fill` 才既不裁也不拉。
    if (item.width && item.height) box.style.aspectRatio = `${item.width} / ${item.height}`;
    img.style.objectFit = 'fill';
    img.style.width = `${columns * 100}%`; img.style.height = `${rows * 100}%`;
    img.style.left = `${-(slot % columns) * 100}%`; img.style.top = `${-Math.floor(slot / columns) * 100}%`;
  };
  let cell = -1;
  const move = (event: PointerEvent) => {
    if (event.pointerType === 'touch') return;
    const duration = realDuration(player.duration()) || realDuration(item?.duration);
    if (!duration) return;
    const rect = progress.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    const width = frame ? Math.min(240, Math.max(160, rect.width * .28)) : 76;
    const x = Math.min(rect.width - width / 2, Math.max(width / 2, event.clientX - rect.left));
    preview.style.left = `${x}px`; preview.hidden = false; label.textContent = fmtClock(duration * ratio);
    if (!frame || !image || !item) return;
    if (sheets) { showSheetFrame(duration * ratio, sheets, frame, image); return }
    const nextCell = Math.min(8, Math.floor(ratio * 9));
    if (nextCell === cell) return;
    cell = nextCell; frame.hidden = false; image.removeAttribute('style');
    image.src = `/poster?id=${encodeURIComponent(item.id)}&c=${nextCell}`;
  };
  const hide = () => { preview.hidden = true };
  if (image && frame) image.onerror = () => { frame.hidden = true };
  progress.addEventListener('pointermove', move); progress.addEventListener('pointerleave', hide);
  player.on('dispose', () => { progress.removeEventListener('pointermove', move); progress.removeEventListener('pointerleave', hide) });
}

/** 画中画那个窗口由浏览器画，站内控制条一颗键都递不进去。Media Session 的动作处理器是唯一的
 *  入口：登记 `seekbackward` 和 `seekforward` 之后，Chrome 才在那个窗口里画出快退和快进两颗，
 *  步长用设置里那个秒数；`setPositionState` 让它自己那条进度条知道现在放到哪，`seekto` 让拖它
 *  真的生效。不登记时那里只有播放、暂停和关闭三颗。站内的小窗是另一件事，那三颗键在小窗自己身上。 */
export function mountPlayerMediaSession(player: VjsPlayer, item: PlayerItem): void {
  const session = navigator.mediaSession;
  if (!session || typeof session.setActionHandler !== 'function') return;
  const total = () => realDuration(player.duration()) || realDuration(item.duration) || 0;
  const seekTo = (seconds: number) => {
    const duration = total();
    player.currentTime(Math.max(0, duration ? Math.min(duration, seconds) : seconds));
  };
  const step = () => Math.max(1, Number(playerHost().settings().seekSeconds) || 10);
  const handlers: [MediaSessionAction, MediaSessionActionHandler][] = [
    ['play', () => { void player.play() }],
    ['pause', () => player.pause()],
    ['seekbackward', (details) => seekTo(player.currentTime() - (details?.seekOffset || step()))],
    ['seekforward', (details) => seekTo(player.currentTime() + (details?.seekOffset || step()))],
    ['seekto', (details) => { if (typeof details?.seekTime === 'number') seekTo(details.seekTime) }],
  ];
  const registered: MediaSessionAction[] = [];
  for (const [action, handler] of handlers) {
    /* 浏览器不认的动作会抛，认得的照常登记：整块 try 会让一个不认识的动作带走后面
       全部处理器，小窗于是又回到只有播放暂停。 */
    try { session.setActionHandler(action, handler); registered.push(action) } catch { /* 不认的动作 */ }
  }
  const syncPosition = () => {
    const duration = total(), position = Math.max(0, Number(player.currentTime()) || 0);
    if (typeof session.setPositionState !== 'function') return;
    /* 时长不可用或者进度跑在时长前面时不报：`setPositionState` 对这两种入参直接抛，
       而 `timeupdate` 每秒都来，抛一次就是每秒一条错误。 */
    if (!duration || position > duration) return;
    try {
      session.setPositionState({ duration, position, playbackRate: Math.max(.001, Number(player.playbackRate()) || 1) });
    } catch { /* 浏览器拒收 */ }
  };
  player.on(['timeupdate', 'durationchange', 'ratechange', 'seeked', 'loadedmetadata'], syncPosition);
  syncPosition();
  player.on('dispose', () => {
    registered.forEach((action) => { try { session.setActionHandler(action, null) } catch { /* 已撤 */ } });
    try { session.setPositionState?.() } catch { /* 已撤 */ }
  });
}

/** 加载提示复用 YouTube e937390a 的 `FsY`：四段嵌套元素与四段动画。 */
export function mountPlayerSpinner(player: VjsPlayer): void {
  const spinner = player.el().querySelector('.vjs-loading-spinner');
  if (!spinner || spinner.querySelector('.vjs-peach-spinner-container')) return;
  spinner.innerHTML = '<span class="vjs-peach-spinner-container"><span class="vjs-peach-spinner-rotator"><span class="vjs-peach-spinner-left"><span class="vjs-peach-spinner-circle"></span></span><span class="vjs-peach-spinner-right"><span class="vjs-peach-spinner-circle"></span></span></span></span>';
}

/** 本机资产的外挂字幕。服务端已经把 srt/ass/ssa 换成 WebVTT，这里只把它挂成 text track。
 *  一律不设 default：自动打开某一条等于替用户选了语言，而同一部片常有简体、繁体、日语三条。
 *  Peach 字幕菜单只在有轨的时候出现；轨道挂在原生 video 上，随同一枚元素保留。 */
export function mountPlayerSubtitles(player: VjsPlayer, assetId: number): void {
  (api(`/api/assets/${assetId}/subtitles`) as Promise<{ subtitles?: { playable?: boolean; src: string; language?: string; label: string }[] } | null>)
    .then((payload) => {
      if (!player || player.isDisposed()) return;
      (payload?.subtitles || []).filter((track) => track.playable).forEach((track) => {
        player.addRemoteTextTrack({ kind: 'subtitles', src: track.src, srclang: track.language || '', label: track.label, default: false }, true);
      });
    }).catch(() => {});
}
