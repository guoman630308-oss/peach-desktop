/* 垃圾队列里的一张卡：上面 16:9 的预览格（没有预览时是类型字形），下面名字、类型、理由与
 * 大小，底部三颗等宽的键：打开位置、判断（不是垃圾／重新判断）、移入回收站，再一行状态。
 *
 * 卡片盒复用馆藏作品卡的钩子（`data-media-card`／`data-media-pic`／`data-media-badge`／
 * `data-media-check`）：预览格、来源角标、选中描边与勾是同一套，Shift 连选时壳按
 * `[data-media-grid] > [data-media-card]` 数本页的卡，垃圾卡也在其中。垃圾卡自己的几何
 * （描边面、元信息区、键行）写在 `./junk-queue.css`，钩子是 `data-junk-*`。
 *
 * 标题照遗留层写成转义后的 HTML：`data-middle-truncate` 由中段截断（`src/ui-kit/middle-truncate.ts`）按宽度
 * 从中间截断，它直接改写这一格的文字，交给 React 管的话下一次重画会和它互相覆盖。 */
import { memo, useRef, useState, type CSSProperties, type MouseEvent } from 'react';
import { esc, fmtSize } from '@peach/legacy/core';
import { spinnerHtml } from '@peach/legacy/ui';

import {
  junkDecision, junkKindMeta,
  type JunkItem, type JunkOperation, type JunkQueueActions, type JunkQueueHelpers, type JunkView,
} from './junk-queue';
import { suppressShiftTextSelection } from '../components/row-click';

/** 雪碧图里的一枚字形，写法同遗留层 `icon()`：尺寸与描边由样式表按位置给。 */
function Icon({ name }: { name: string }) {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><use href={`#i-${name}`} /></svg>;
}

export interface JunkCardProps {
  item: JunkItem;
  view: JunkView;
  selected: boolean;
  selectMode: boolean;
  helpers: JunkQueueHelpers;
  actions: JunkQueueActions;
}

type Busy = '' | 'reveal' | JunkOperation;

const picking = (selectMode: boolean, event: MouseEvent) => selectMode || event.shiftKey || event.ctrlKey || event.metaKey;

function JunkCardView({ item, view, selected, selectMode, helpers, actions }: JunkCardProps) {
  const card = useRef<HTMLElement | null>(null);
  const [busy, setBusy] = useState<Busy>('');
  const [status, setStatus] = useState('');
  const kind = item.junk_kind || 'other';
  const [label, glyph] = junkKindMeta(item);
  const name = String(item.name || '');
  const decision = junkDecision(view);
  const preview = kind === 'video' ? `/thumb?id=${item.id}&c=4` : kind === 'image' ? `/photo-thumb?id=${item.id}` : '';
  const openable = kind === 'video' || kind === 'image';

  /* 多选与修饰键优先：点在卡上任何地方（键行除外）都是切换选中。 */
  const clickCard = (event: MouseEvent<HTMLElement>) => {
    if ((event.target as HTMLElement).closest('[data-junk-actions]')) return;
    if (!picking(selectMode, event)) return;
    event.preventDefault();
    actions.toggleSelection(item.id, event.shiftKey);
  };
  const clickTitle = (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    if (picking(selectMode, event)) {
      event.preventDefault();
      actions.toggleSelection(item.id, event.shiftKey);
      return;
    }
    if (card.current) actions.open(item, card.current);
  };
  const reveal = (event: MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    if (busy === 'reveal') return;
    setBusy('reveal');
    setStatus('');
    void actions.reveal(item).then(setStatus, () => {}).finally(() => setBusy(''));
  };
  /* 写 ledger 的三颗键。成功后壳重读队列，这张卡随之离开；失败由壳报，键恢复成可点。 */
  const operate = (operation: JunkOperation) => (event: MouseEvent) => {
    event.preventDefault();
    event.stopPropagation();
    if (busy === operation) return;
    setBusy(operation);
    setStatus('');
    void actions.operate(item, operation).catch(() => {}).finally(() => setBusy(''));
  };
  const busyAttrs = (key: Busy) => (busy === key ? { 'aria-busy': true, 'aria-disabled': true } as const : {});

  const title = openable
    ? <button type="button" data-junk-title="" data-junk-open="" data-middle-truncate="" title={name}
      onClick={clickTitle} dangerouslySetInnerHTML={{ __html: esc(name || '未命名资源') }} />
    : <span data-junk-title="" data-middle-truncate="" title={name}
      dangerouslySetInnerHTML={{ __html: esc(name || '未命名资源') }} />;

  return (
    <article ref={card} data-media-card="" data-variant="junk" data-junk-card="" data-id={item.id} data-junk-kind={kind}
      data-selected={selected ? '' : undefined} onClick={clickCard} onMouseDown={suppressShiftTextSelection}>
      <div data-media-pic="" style={{ '--card-ratio': String(16 / 9) } as CSSProperties}>
        <span data-media-glyph=""><Icon name={glyph} /><b>{label}</b></span>
        {preview
          ? <span data-media-art="thumb" dangerouslySetInnerHTML={{
            __html: `<img class="poster" src="${preview}" width="640" height="360" alt="" loading="lazy" data-drop="self">`,
          }} />
          : null}
        <div data-media-badge="" dangerouslySetInnerHTML={{ __html: helpers.badgeHtml(item.location || '', item.cost || '') }} />
        <span data-media-check=""><Icon name="check" /></span>
      </div>
      <div data-junk-meta="">
        {title}
        <div data-junk-facts="">
          <span data-junk-who="">{label}</span>
          {item.why ? <span data-junk-why="">{item.why}</span> : null}
          <span data-junk-size="">{Number(item.size) > 0 ? fmtSize(Number(item.size)) : '大小未知'}</span>
        </div>
      </div>
      <footer data-junk-actions="">
        <button type="button" data-junk-action="reveal" title="在资源管理器中显示" aria-label="在资源管理器中显示"
          {...busyAttrs('reveal')} onClick={reveal}>
          {busy === 'reveal' ? <span data-junk-spinner="" dangerouslySetInnerHTML={{ __html: spinnerHtml('正在定位') }} /> : <Icon name="folder-open" />}
          <span data-junk-label="">打开位置</span>
        </button>
        <button type="button" data-junk-action={decision.operation} title={decision.label} aria-label={decision.label}
          {...busyAttrs(decision.operation)} onClick={operate(decision.operation)}>
          <Icon name={decision.glyph} /><span data-junk-label="">{decision.label}</span>
        </button>
        <button type="button" data-junk-action="dispose" title="移入回收站" aria-label="移入回收站"
          {...busyAttrs('dispose')} onClick={operate('dispose')}>
          <Icon name="trash" /><span data-junk-label="">移入回收站</span>
        </button>
        <span data-junk-state="" aria-live="polite">{status}</span>
      </footer>
    </article>
  );
}

/** 一屏上百张，选择之外的重画不该连带每一张：比较的都是引用，壳递进来的助手与动作身份不变。 */
export const JunkCard = memo(JunkCardView);
