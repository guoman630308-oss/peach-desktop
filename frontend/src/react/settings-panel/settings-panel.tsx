/* 设置面板：顶栏齿轮打开的那一屏浮层，左栏分区、右边一组组设置行。
 *
 * 常驻组件 `settings-panel`（`router/managed-routes.tsx` 的常驻表）：第一次打开时壳的 `loadSettingsPanel` 建一枚
 * `[data-settings-host]`，常驻组件的 `place` 把它放进 body 末尾，路由树在同一个任务里把面板画成它的子节点。
 * 之后开合只换 `hidden`，分区、滚动位置之外的状态跨次打开保留。壳经 `configureSettingsPanel` 给的句柄开合：
 * 句柄写本模块的 store 再 `flushSync` 通知，返回时面板已经画好，锁滚、取数与焦点紧跟在后面。界面偏好读写的是
 * 壳递进来的那一份 `appSettings`（`host.store`），改完一项用 `host.changed(effect)` 告诉壳跟着做什么；跟数据库走的
 * 那几项经 `/api/settings` 写（`settings-data.ts`）。
 *
 * 宿主不在 `.peach-react` 里：行、开关、下拉、拉条与色块都是全站共用的遗留控件，外观由
 * `settings-panel.css` 与遗留样式表给。
 *
 * 每次打开时共用控件重画、数值框回到当前值，并重取共享设置。
 * 焦点先交给关闭钮，Tab 在面板里转圈，关掉后还给打开前那一枚。 */
import {
  useLayoutEffect, useRef, useState, useSyncExternalStore,
  type KeyboardEvent, type ReactNode,
} from 'react';
import { flushSync } from 'react-dom';
import { ButtonLink } from '@/components/base/buttons/button';
import {
  attachOverlayScrollbar, closeAnchoredMenu, iconSwapHtml, moveGlidePane, revealTexts,
  setActionBusy, setIconSwap, wireHorizontalScroller,
} from '@peach/legacy/ui';

import { normalizeJavImage } from '../../jav-artwork';
import { boundedPreference } from '../../number-setting';
import { GlowSettings } from './glow-settings';
import { Icon, RemixIcon } from './icon';
import { IconSwitch, SelectField } from './legacy-controls';
import { NumberSetting } from './number-setting';
import type { Choice, PanelSettings, SettingsPanelApi, SettingsPanelHost } from './settings-panel-api';
import { refreshPanelData, useSaveSettings } from './settings-data';
import { SidebarOrder } from './sidebar-order';
import { SettingsDialogClose } from '../components/settings-dialog-close';

/* 左栏的分区，顺序即右边各组的顺序。字形按条目自己的名字取：整列取自 Remix，一家画的笔画
   才一样粗。 */
const SECTIONS = [
  ['外观与导航', 'ri-palette-line'], ['浏览与播放', 'ri-play-circle-line'],
  ['JAV 与新作', 'jav'], ['搜索与推荐', 'ri-search-line'], ['内容显示', 'ri-shield-check-line'],
] as const;

const SORT_OPTIONS: readonly Choice[] = [
  ['seed', '随机'], ['rating', '评分'], ['o', '高潮计数'], ['plays', '观看次数'],
  ['dur', '时长'], ['size', '体积'], ['new', '入库时间'], ['played', '观看时间'],
];
const DIRECTION_OPTIONS: readonly Choice[] = [['desc', '降序'], ['asc', '升序']];
const JAV_IMAGE_OPTIONS: readonly Choice[] = [['cover', '官方封面', ''], ['thumbnail', '预览图', '']];
/* 新作那一行收不收合集由服务端按数据库里的设置筛，这里的开关只是镜像：真相在 `/api/settings`。 */
const FEED_COMPILATIONS = [
  ['feedHideGroupSetting', 'feedHideGroupCompilations', '大合集', '隐藏大合集'],
  ['feedHideSoloSetting', 'feedHideSoloCompilations', '单人合集', '隐藏单人合集'],
  ['feedHideExcerptSetting', 'feedHideExcerpts', '切片', '隐藏切片'],
] as const;
type CompilationKey = typeof FEED_COMPILATIONS[number][1];

const FOCUSABLE = 'button:not([disabled]),input:not([disabled]),textarea:not([disabled]),a[href]';

interface View { open: boolean; closing: boolean; epoch: number; tab: number; configurable: boolean | null }

let host: SettingsPanelHost | null = null;
let view: View = { open: false, closing: false, epoch: 0, tab: 0, configurable: null };
let transition = 0;
let returnFocus: Element | null = null;
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener) };
}

function notify(): void {
  for (const listener of [...listeners]) listener();
}

/* 写进 store 当场画完：句柄返回时面板已经是新的样子，紧跟着的锁滚、取数与焦点读的都是画好的 DOM。
   这一面抛错、被错误边界卸掉之后没人订阅，写进来的只进 store，不画、不抛。 */
function update(patch: Partial<View>): void {
  view = { ...view, ...patch };
  flushSync(notify);
}

/* Peach 配置入口按当前设备的配置权限放行。 */
async function checkMachine(current: SettingsPanelHost): Promise<void> {
  const epoch = view.epoch;
  const configurable = await current.configurable();
  if (!view.open || view.epoch !== epoch) return;
  update({ configurable });
}

function open(section = ''): void {
  const current = host;
  if (!current) return;
  const requested = SECTIONS.findIndex(([title]) => title === section);
  if (view.open && !view.closing) {
    if (requested >= 0 && requested !== view.tab) update({ tab: requested });
    return;
  }
  /* 设置这一屏盖住整页，任何还开着的锚定弹层都得先收掉：设置有侧栏的设置钮、配色弹层的
     「详细设置」和快捷键几处入口，收在这里就不会漏掉哪一处。 */
  closeAnchoredMenu();
  transition += 1;
  returnFocus ||= document.activeElement;
  current.sound('whoosh');
  document.documentElement.style.overflow = 'hidden';
  document.body.classList.add('settings-open');
  /* 先发取数再画：第一次打开时查询还不存在，这一趟由 `fetchQuery` 建起来，组件画出来读到
     的就是在途的这一份，不会再发第二趟。 */
  const remote = refreshPanelData();
  update({ open: true, closing: false, configurable: null, epoch: view.epoch + 1, tab: requested >= 0 ? requested : view.tab });
  void remote.then((settings) => { if (settings) current.syncRemote(settings) });
  void checkMachine(current);
  queueMicrotask(() => document.getElementById('settingsClose')?.focus());
}

function close(notify = true): void {
  if (!view.open || view.closing) return;
  const ticket = ++transition;
  if (notify) host?.closed?.();
  update({ closing: true });
  const finish = () => {
    if (ticket !== transition || !view.closing) return;
    update({ open: false, closing: false });
    document.documentElement.style.overflow = '';
    document.body.classList.remove('settings-open');
    if (returnFocus instanceof HTMLElement && document.contains(returnFocus)) returnFocus.focus();
    returnFocus = null;
  };
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) { queueMicrotask(finish); return }
  document.querySelector('#settingsPanel > [data-settings-card]')?.addEventListener('animationend', finish, { once: true });
  setTimeout(finish, 380);
}

const api: SettingsPanelApi = {
  open,
  close,
  isOpen: () => view.open,
  reveal: (selector) => queueMicrotask(() => document.querySelector(selector)?.scrollIntoView({ block: 'nearest' })),
};

/** 接上壳给的宿主，拿回设置面板的命令式入口。只调一次；壳的 `loadSettingsPanel` 接着在路由树里打开这一面，
 *  画上（收着的）面板之后才交出句柄。 */
export function configureSettingsPanel(next: SettingsPanelHost): SettingsPanelApi {
  host = next;
  return api;
}

/** 常驻表里的那一面：读本模块的 store，宿主还没接上时不画。 */
export function SettingsPanelSurface() {
  const shown = useSyncExternalStore(subscribe, () => view);
  return host ? <SettingsPanel host={host} view={shown} /> : null;
}

function trapTab(event: KeyboardEvent<HTMLElement>): void {
  if (event.key !== 'Tab') return;
  /* 没选中的分区整块 hidden，里面的控件拿不到焦点；圈的两头只在看得见的控件里找。 */
  const focusable = [...event.currentTarget.querySelectorAll<HTMLElement>(FOCUSABLE)]
    .filter((node) => node.getClientRects().length > 0);
  if (!focusable.length) return;
  const first = focusable[0], last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}

const tabId = (index: number) => `settings-tab-${index}`;
const groupId = (index: number) => `settings-group-${index}`;

/* 左栏当前项由一块滑过去的玻璃标出来，和左侧抽屉是同一件事的两种形态。坐标取 `offsetTop`
   不取屏幕坐标：面板开合自带一段缩放动画，量屏幕坐标会把正在走的那一下吃进来。玻璃跟着指针走，
   指针离开这一列就滑回当前那格；触点没有悬停，碰一下就滑过去等于替用户点了一次，所以挡在外面。 */
function SettingsNav({ tab, epoch, configurable, openConfiguration, onSelect }: {
  tab: number; epoch: number; configurable: boolean | null; openConfiguration(): void; onSelect(index: number): void;
}) {
  const nav = useRef<HTMLDivElement | null>(null);
  const glide = useRef<HTMLSpanElement | null>(null);
  const box = useRef<{ x: number; y: number; w: number; h: number } | null>(null);
  const shown = useRef(tab);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);

  const place = (active: HTMLElement | null | undefined, animate: boolean) => {
    const pane = glide.current;
    if (!pane) return;
    if (!active || !active.offsetHeight) { pane.hidden = true; return }
    pane.hidden = false;
    const next = { x: active.offsetLeft, y: active.offsetTop, w: active.offsetWidth, h: active.offsetHeight };
    const from = box.current;
    box.current = next;
    moveGlidePane(pane, animate ? from : null, next, 'y');
  };
  const placeRef = useRef(place);
  placeRef.current = place;

  useLayoutEffect(() => {
    const node = nav.current;
    if (!node) return undefined;
    wireHorizontalScroller(node);
    /* 面板收着时这一栏是零尺寸，量不到落点；露出来那一帧、窄屏断点换宽度时都由这一条对齐。 */
    const observer = new ResizeObserver(() => placeRef.current(buttons.current[shown.current], false));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  useLayoutEffect(() => {
    const moved = shown.current !== tab;
    shown.current = tab;
    placeRef.current(buttons.current[tab], moved);
  }, [tab, epoch]);

  const keys = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const count = SECTIONS.length;
    let next = index;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (index + 1) % count;
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (index + count - 1) % count;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = count - 1;
    else return;
    event.preventDefault();
    onSelect(next);
    buttons.current[next]?.focus();
  };

  /* 整块是一个 tablist：小标题写成 presentation，不占 tab 的位置，方向键从头走到尾。 */
  return (
    <div data-settings-nav="" data-glass-pane="" role="tablist" aria-label="设置分区" ref={nav}
      onPointerLeave={(event) => { if (event.pointerType !== 'touch') place(buttons.current[shown.current], true) }}>
      <span data-settings-glide="" aria-hidden="true" hidden ref={glide} />
      <p data-settings-nav-caption="" role="presentation">设置</p>
      {SECTIONS.map(([title, icon], index) => (
        <button type="button" role="tab" key={title} id={tabId(index)} aria-controls={groupId(index)}
          aria-selected={index === tab} tabIndex={index === tab ? 0 : -1}
          ref={(node) => { buttons.current[index] = node }}
          onClick={() => onSelect(index)} onKeyDown={(event) => keys(event, index)}
          onPointerEnter={(event) => { if (event.pointerType !== 'touch') place(event.currentTarget, true) }}>
          {icon === 'jav' ? <Icon name={icon} /> : <RemixIcon name={icon} />}{title}
        </button>
      ))}
      <div data-settings-service="" role="presentation">
        <button type="button" disabled={configurable !== true} onClick={openConfiguration}
          title={configurable === false ? '请在运行 Peach 的设备上打开 Peach 配置' : undefined}>
          <Icon name="folder-cog" />Peach 配置<Icon name="chevron-right" />
        </button>
      </div>
    </div>
  );
}

function Group({ index, tab, children }: { index: number; tab: number; children: ReactNode }) {
  return (
    <section data-setting-group="" id={groupId(index)} role="tabpanel" aria-labelledby={tabId(index)} hidden={index !== tab}>
      <h3>{SECTIONS[index][0]}</h3>
      {children}
    </section>
  );
}

/** 一行开关：整行是 label，点哪儿都切。说明那一句的 id 默认取开关 id 去掉 `Setting` 再接 `Description`。 */
function ToggleRow({ id, title, description, describedBy = `${id.replace(/Setting$/, '')}Description`, checked, onToggle }: {
  id: string; title: string; description?: string; describedBy?: string; checked: boolean; onToggle(on: boolean): void;
}) {
  return (
    <label data-setting-row="">
      <span><b>{title}</b>{description ? <small id={describedBy}>{description}</small> : null}</span>
      <input data-toggle="" type="checkbox" id={id} role="switch" aria-describedby={description ? describedBy : undefined}
        checked={checked} onChange={(event) => onToggle(event.target.checked)} />
    </label>
  );
}

function Row({ title, description, kind, children }: {
  title: string; description?: ReactNode; kind?: 'sort' | 'related'; children: ReactNode;
}) {
  return (
    <div data-setting-row="" data-setting-sort={kind === 'sort' ? '' : undefined}
      data-setting-related={kind === 'related' ? '' : undefined}>
      <span><b>{title}</b>{typeof description === 'string' ? <small>{description}</small> : description}</span>
      {children}
    </div>
  );
}

function SettingsPanel({ host: current, view: shown }: { host: SettingsPanelHost; view: View }) {
  /* 订阅只为重画：读的是活对象 `store.value`，版本号本身不用。 */
  useSyncExternalStore(current.store.subscribe, current.store.version);
  const settings = current.store.value;
  const panel = useRef<HTMLElement | null>(null);
  const card = useRef<HTMLDivElement | null>(null);
  const head = useRef<HTMLDivElement | null>(null);
  const scroll = useRef<HTMLDivElement | null>(null);
  const resync = useRef<(() => void) | null>(null);
  const lastTab = useRef(shown.tab);

  /* 标题下那道影子的门槛是那段留白自己：它长在这一栏的上内边距上，滚掉它就等于两块重合，
     差 4px 时点亮。留白的值只写在 `--settings-gap` 一处，这里读它。 */
  const fade = () => {
    const [box, body] = [card.current, scroll.current];
    if (!box || !body) return;
    const gap = parseFloat(getComputedStyle(box).getPropertyValue('--settings-gap')) || 0;
    box.toggleAttribute('data-scrolled', body.scrollTop > Math.max(gap - 4, 0));
  };
  const fadeRef = useRef(fade);
  fadeRef.current = fade;

  useLayoutEffect(() => {
    const body = scroll.current;
    if (!body || !panel.current) return undefined;
    resync.current = attachOverlayScrollbar(body) ?? resync.current;
    current.attached(panel.current);
    const listener = () => fadeRef.current();
    body.addEventListener('scroll', listener, { passive: true });
    return () => body.removeEventListener('scroll', listener);
  }, [current]);
  /* 换分区、每次打开都回到顶上；换了分区时标题逐字揭示，只开合不放。 */
  useLayoutEffect(() => {
    const moved = lastTab.current !== shown.tab;
    lastTab.current = shown.tab;
    if (scroll.current) scroll.current.scrollTop = 0;
    if (moved && head.current) revealTexts(head.current, 'h2');
    fadeRef.current();
  }, [shown.tab, shown.epoch]);
  /* 换分区、展开收起一组都只切 `hidden`，滚动条的观察器只看子树增删，内容长短变了它不知道。
     每次画完按新的内容长度重量一遍滑块和两端渐隐。 */
  useLayoutEffect(() => { resync.current?.() });

  const epoch = shown.epoch;
  const select = (index: number) => { if (index !== view.tab) { update({ tab: index }); current.selectSection?.(SECTIONS[index][0]) } };
  const save = () => current.store.save();

  return (
    <section data-settings-panel="" id="settingsPanel" hidden={!shown.open} aria-modal="true" role="dialog"
      aria-labelledby="settingsTitle" data-closing={shown.closing ? '' : undefined} ref={panel} onKeyDown={trapTab}
      onClick={(event) => { if (event.target === event.currentTarget) close() }}>
      <div data-settings-card="" ref={card}>
        <div data-settings-head="" ref={head}>
          <h2 id="settingsTitle">{SECTIONS[shown.tab][0]}</h2>
            <SettingsDialogClose id="settingsClose" label="关闭设置" onPress={() => close()} />
        </div>
        <SettingsNav tab={shown.tab} epoch={epoch} configurable={shown.configurable} onSelect={select}
          openConfiguration={() => { close(false); current.openConfiguration() }} />
        <div data-settings-scroll="" ref={scroll}>
          <Group index={0} tab={shown.tab}>
            <Row title="主题">
              <IconSwitch id="themeSetting" name="theme" legend="主题" options={current.themeOptions} value={settings.theme}
                attr="data-theme-choice" variant="themeswitch" epoch={epoch}
                onChoose={(choice) => { settings.theme = choice; save(); current.changed('theme') }} />
            </Row>
            <div data-setting-row="">
              <label htmlFor="glassContrastSetting"><b>增加对比度</b></label>
              <input type="checkbox" id="glassContrastSetting" data-toggle="" role="switch" checked={current.highContrast()}
                onChange={(event) => current.setHighContrast(event.target.checked)} />
            </div>
            <ToggleRow id="uiSoundsSetting" title="界面音效" checked={settings.uiSounds}
              onToggle={(on) => { settings.uiSounds = on; save(); current.changed('uiSounds') }} />
            <ToggleRow id="homeGlowSetting" title="侧栏光晕" checked={settings.homeGlow.on}
              onToggle={(on) => { settings.homeGlow.on = on; save(); current.changed('glow') }} />
            <GlowSettings host={current} />
            <section data-sidebar-setting="" aria-labelledby="sidebarOrderTitle">
              <span><b id="sidebarOrderTitle">导航入口与顺序</b></span>
              <SidebarOrder host={current} />
            </section>
          </Group>
          <BrowseGroup host={current} tab={shown.tab} epoch={epoch} />
          <JavGroup host={current} tab={shown.tab} epoch={epoch} />
          <Group index={3} tab={shown.tab}>
            <SearchHistoryRow host={current} epoch={epoch} />
            <Row title="相关推荐数量">
              <div id="relatedLimitSetting">
                <NumberSetting key={epoch} id="relatedLimitSetting" label="相关推荐数量" value={settings.relatedLimit}
                  onApply={(value) => { settings.relatedLimit = value; save() }} />
              </div>
            </Row>
            <Row title="口味与记录"><div className="peach-react"><ButtonLink variant="secondary" href="/taste" onClick={() => close(false)}>打开口味与记录</ButtonLink></div></Row>
          </Group>
          <Group index={4} tab={shown.tab}>
            <ToggleRow id="censorSetting" title="SFW 模式" describedBy="sfwDescription" checked={current.censored()}
              description="隐藏敏感画面，停止悬停预览。"
              onToggle={(on) => current.setCensored(on)} />
          </Group>
        </div>
      </div>
    </section>
  );
}

interface GroupProps { host: SettingsPanelHost; tab: number; epoch: number }

/* 排序方向那一格叠着两枚箭头，换的只是方向这一件事：整格重写会把旧字形连同它的动画一起丢掉，
   读出来是一次硬切，所以只在这一格还没建起来时写一次。 */
function decorateDirection(field: HTMLElement, ascending: boolean): void {
  const mark = field.querySelector('[data-select-label]');
  if (!mark) return;
  if (!mark.querySelector('[data-icon-swap]')) {
    mark.innerHTML = iconSwapHtml('arrow-up', 'arrow-down', 'a', { className: 'gselectmark', iconClass: 'gselectmark' })
      + '<span data-sort-direction-label></span>';
  }
  setIconSwap(mark, ascending ? 'a' : 'b');
  const label = mark.querySelector('[data-sort-direction-label]');
  if (label) label.textContent = ascending ? '升序' : '降序';
}

function BrowseGroup({ host: current, tab, epoch }: GroupProps) {
  const settings = current.store.value;
  const save = () => current.store.save();
  const random = settings.defaultSort === 'seed';
  const ascending = settings.defaultSortDirection === 'asc';
  return (
    <Group index={1} tab={tab}>
      <Row title="每批加载作品">
        <div id="batchSizeSetting">
          <NumberSetting key={epoch} id="batchSizeSetting" label="每批作品" value={settings.batchSize}
            onApply={(value) => { settings.batchSize = value || 60; save(); current.changed('batchSize') }} />
        </div>
      </Row>
      <Row title="默认排序" kind="sort">
        <div data-setting-sort-controls="">
          <SelectField id="defaultSortSetting" label="默认排序" options={SORT_OPTIONS} value={settings.defaultSort} epoch={epoch}
            onPick={(value) => { settings.defaultSort = value; save(); current.changed('defaultSort') }} />
          <SelectField id="defaultSortDirectionSetting" label="默认排序方向" options={DIRECTION_OPTIONS}
            value={ascending ? 'asc' : 'desc'} hidden={random} disabled={random} epoch={epoch}
            onPick={(value) => { settings.defaultSortDirection = value; save(); current.changed('sortDirection') }}
            decorate={(field) => decorateDirection(field, ascending)} />
        </div>
      </Row>
      <Row title="视频卡片大小">
        <IconSwitch id="javSizeSetting" name="video-size" legend="视频卡片大小" options={current.videoLayouts}
          value={current.videoLayout()} attr="data-video-layout" variant="javimageswitch" text epoch={epoch}
          onChoose={(layout) => current.setVideoLayout(layout)} />
      </Row>
      <PlaybackRows host={current} epoch={epoch} />
    </Group>
  );
}

function JavGroup({ host: current, tab, epoch }: GroupProps) {
  const settings = current.store.value;
  const save = () => current.store.save();
  return (
    <Group index={2} tab={tab}>
      <h4 data-settings-subheading="">作品显示</h4>
      <Row title="默认图片">
        <IconSwitch id="javImageSetting" name="jav-image" legend="默认图片" options={JAV_IMAGE_OPTIONS}
          value={settings.javImage} attr="data-jav-image-choice" variant="javimageswitch" text epoch={epoch}
          onChoose={(choice) => { settings.javImage = normalizeJavImage(choice); save(); current.changed('javImage') }} />
      </Row>
      <label data-setting-row=""><span><b>合并分卷与版本</b></span>
        <input type="checkbox" id="groupCollapseSetting" data-toggle="" role="switch" checked={settings.groupCollapse}
          onChange={(event) => { settings.groupCollapse = event.target.checked; save(); current.changed('groupCollapse') }} />
      </label>
      <h4 data-settings-subheading="">新作显示</h4>
      <ToggleRow id="feedAutoScrollSetting" title="新作自动滚动" checked={settings.feedAutoScroll}
        onToggle={(on) => { settings.feedAutoScroll = on; save(); current.changed('feedAutoScroll') }} />
      {FEED_COMPILATIONS.map(([id, key, label, title]) => (
        <CompilationToggle key={id} host={current} id={id} settingKey={key} label={label} title={title} />
      ))}
    </Group>
  );
}
/* 合集开关由服务端判，列表、未读数和补封面排队都跟着它，所以先存到服务端再重画；往返期间开关
   停在新位置、挂忙态，失败退回原位。 */
function CompilationToggle({ host: current, id, settingKey, label, title }: {
  host: SettingsPanelHost; id: string; settingKey: CompilationKey; label: string; title: string;
}) {
  const save = useSaveSettings();
  const [pending, setPending] = useState<boolean | null>(null);
  const box = useRef<HTMLInputElement | null>(null);
  const settings: PanelSettings = current.store.value;
  const toggle = (on: boolean) => {
    setPending(on);
    setActionBusy(box.current);
    save.mutate({ [settingKey]: on }, {
      onSuccess: (saved) => {
        settings[settingKey] = saved[settingKey] === true;
        current.store.save();
        current.changed('feedCompilations');
        current.receipt(on ? `新作已收起${label}` : `新作已列出${label}`);
      },
      onError: (error) => current.failure(`保存${label}开关`, error),
      onSettled: () => { setPending(null); setActionBusy(box.current, false) },
    });
  };
  return (
    <label data-setting-row="">
      <span><b>{title}</b></span>
      <input data-toggle="" type="checkbox" id={id} role="switch" ref={box}
        checked={pending ?? settings[settingKey]} onChange={(event) => toggle(event.target.checked)} />
    </label>
  );
}

function PlaybackRows({ host: current, epoch }: Omit<GroupProps, 'tab'>) {
  const settings = current.store.value;
  const save = () => current.store.save();
  return (
    <>
      <Row title="悬停预览延迟">
        <div id="hoverDelaySetting">
          <NumberSetting key={epoch} id="hoverDelaySetting" label="悬停预览延迟" value={settings.hoverDelaySeconds}
            onApply={(value) => {
              settings.hoverDelaySeconds = boundedPreference(value, 0, 60, 5); save(); current.changed('hoverDelay');
            }} />
        </div>
      </Row>
      <ToggleRow id="detailAutoplaySetting" title="打开详情自动播放" checked={settings.detailAutoplay}
        onToggle={(on) => { settings.detailAutoplay = on; save() }} />
      <ToggleRow id="miniplayerSetting" title="离开详情后小窗播放" checked={settings.miniplayer}
        onToggle={(on) => { settings.miniplayer = on; save(); current.changed('miniplayer') }} />
      <Row title="快进 / 快退步长">
        <div id="seekSecondsSetting">
          <NumberSetting key={epoch} id="seekSecondsSetting" label="快进 / 快退" value={settings.seekSeconds}
            onApply={(value) => { settings.seekSeconds = value || 10; save(); current.changed('seekSeconds') }} />
        </div>
      </Row>
    </>
  );
}

function SearchHistoryRow({ host: current, epoch }: { host: SettingsPanelHost; epoch: number }) {
  const settings = current.store.value;
  const save = useSaveSettings();
  /* 条数跟着数据库走（`/api/settings`），所有访问端看到同一个数；本地那份只是镜像。 */
  return (
    <Row title="搜索记录数量">
      <div id="searchHistoryLimitSetting">
        <NumberSetting key={epoch} id="searchHistoryLimitSetting" label="搜索记录" value={settings.searchHistoryLimit}
          onApply={(value) => {
            settings.searchHistoryLimit = boundedPreference(value, 0, 50, 10);
            current.store.save();
            current.changed('searchHistoryLimit');
            save.mutate({ searchHistoryLimit: settings.searchHistoryLimit }, { onError: () => {} });
          }} />
      </div>
    </Row>
  );
}
