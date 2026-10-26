/* 详情浮窗右侧那一列队列（Mix、分卷、版本、播放列表、关注的合集与多媒体）。
 *
 * 队列、行与条目各挂一枚 `data-mix-*`，样式在
 * `stage/stage.css`，作品详情与关注详情共用。一列十几条要能拖着横滚（窄屏下队列是横排），
 * 拖动借壳的 `wireDrag`，由调用方经 `listRef` 接上。 */
import { useCallback, useLayoutEffect, useRef, type ButtonHTMLAttributes, type ReactNode, type Ref } from 'react';
import { icon } from '@peach/legacy/core';

export function MixQueue({ kind, title, summary, onClose, actions, listRef, children, ...data }: {
  kind: string;
  title: string;
  summary: string;
  onClose(): void;
  /** 关闭键前面的动作键（保存为播放列表之类）。 */
  actions?: ReactNode;
  listRef?: Ref<HTMLDivElement>;
  children: ReactNode;
} & Record<`data-${string}`, string>) {
  const list = useRef<HTMLDivElement | null>(null);
  const attach = useCallback((node: HTMLDivElement | null) => {
    list.current = node;
    if (typeof listRef === 'function') listRef(node);
    else if (listRef) listRef.current = node;
  }, [listRef]);
  /* 换条时详情按条目重建，队列跟着从头挂：把正在看的那一行滚进列表可见处。只动列表自己的滚动，
     `scrollIntoView` 会连浮窗和页面一起滚。 */
  useLayoutEffect(() => {
    const box = list.current;
    const row = box?.querySelector<HTMLElement>('[data-mix-item][aria-current="true"]');
    if (!box || !row) return;
    const outer = box.getBoundingClientRect();
    const inner = row.getBoundingClientRect();
    if (inner.top < outer.top || inner.bottom > outer.bottom) box.scrollTop += inner.top - outer.top - (outer.height - inner.height) / 2;
    if (inner.left < outer.left || inner.right > outer.right) box.scrollLeft += inner.left - outer.left - (outer.width - inner.width) / 2;
  }, []);
  return (
    <aside data-mix-queue="" data-queue-kind={kind} {...data}>
      <div data-mix-queue-head="">
        {/* 作用域 Preflight 把标题字重清成 inherit，这里按字重三档取 semibold。 */}
        <div><h2 className="font-semibold">{title}</h2><span>{summary}</span></div>
        <div data-mix-queue-actions="">
          {actions}
          {/* 详情底部的队列头不放关闭键，关舞台用媒体框上那一枚（`stage.css`）。 */}
          <button type="button" data-queue-close="" title="关闭" aria-label="关闭" onClick={onClose}
            dangerouslySetInnerHTML={{ __html: icon('x') }} />
        </div>
      </div>
      <div data-mix-list="" ref={attach}>{children}</div>
    </aside>
  );
}

/** 队列里的一行：左边缩略图（时长角标由调用方放进 `pic`），右边两行字。作品队列的字前面还有
 *  一枚署名头像（`lead`，和卡片的署名层同一套）；播放列表的行尾多一组抓手与移出（`after`），
 *  拖动排序认的是行上的 `row` 属性。 */
export function MixQueueRow({ current, pic, lead, after, row, children, ...button }: {
  current: boolean;
  pic: ReactNode;
  lead?: ReactNode;
  after?: ReactNode;
  row?: Record<`data-${string}`, string | number>;
  children: ReactNode;
} & ButtonHTMLAttributes<HTMLButtonElement> & Record<`data-${string}`, string | number>) {
  const text = <span data-mix-item-text="">{children}</span>;
  return (
    <div data-mix-row="" {...row}>
      <button type="button" data-mix-item="" aria-current={current ? 'true' : 'false'} {...button}>
        <span data-mix-item-pic="">{pic}</span>
        {lead === undefined ? text : <span data-mix-item-meta="">{lead}{text}</span>}
      </button>
      {after}
    </div>
  );
}

/** 网盘分组的小标题：组名加条数。 */
export const MixGroupLabel = ({ label, count }: { label: string; count: number }) => (
  <h3 data-mix-group-label="">{label} <span>{count}</span></h3>
);
