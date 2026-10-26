/* 管理区那张「一件要在这一页上做的事」的卡：填充底加一条描边、16px 圆角、一层接触阴影，
 * 卡里分成正文和一条放按钮的页脚。
 *
 * 旧样式表里数据管理页的四块（`.cleanupfieldset`、`[data-cleanup-processing]`、
 * `.resourcesyncbox`）本来就是同一副卡面（`web/board.css` 把它们并进同一条规则），只在排法上
 * 分两档：
 * - `split`：左说明、右按钮，两列竖向居中，正文与页脚各 24px 内边距；1000px 以下按钮落到说明
 *   下面，页脚靠左、不再留上内边距。媒体修复、空文件夹与资源同步。
 * - `stack`：正文在上、页脚在下，正文 `20px 20px 16px`、最矮 120px；页脚是一条比卡面暗一点点
 *   的带子（`--board-card-foot`），上边一条线、`12px 20px`、最矮 56px。扫描与采集、整理和链接管理。
 *
 * 它和 `../settings/section.tsx` 的 `Section` 不是同一张：那一张是设置弹层的分组，无描边、
 * 标题在卡外；这一张有描边，标题是卡内的 Title 2。 */
import type { ReactNode } from 'react';

import { cardClass } from './card';

type Layout = 'split' | 'stack';

const FACE = cardClass({ padding: 'none', bordered: 'line', className: 'overflow-hidden' });

const BODY: Record<Layout, string> = {
  split: 'min-w-0 p-6',
  stack: 'flex min-h-30 min-w-0 flex-1 flex-col px-5 pt-5 pb-4 max-cleanup-tight:min-h-0 max-cleanup-tight:p-4.5',
};

const FOOT: Record<Layout, string> = {
  split: 'flex flex-wrap items-center justify-end gap-2 self-stretch p-6'
    + ' max-[1001px]:justify-start max-[1001px]:pt-0',
  stack: 'flex min-h-14 flex-wrap items-center justify-end gap-2 border-t border-border-button-default'
    + ' bg-card-footer px-5 py-3 max-cleanup-tight:px-4.5',
};

/** 结果面板底下那条带子，和 `stack` 页脚同一副几何；颜色按语气另给。 */
const BAND = 'flex min-h-14 flex-wrap items-center justify-end gap-2 border-t px-5 py-3';
const BAND_TONE = {
  neutral: 'border-border-button-default bg-card-footer',
  error: 'border-border-error-default bg-background-tertiary-error',
};

/** 卡下面那块结果面板：同一副填充底，一条描边、14px 圆角、不浮起。
 *  链接检查与资源同步把一趟后台任务的结论摆在这里，一段一段往下叠，段间一条发丝线。 */
export const RESULT_PANEL = cardClass({
  padding: 'none', radius: 'surface', bordered: 'line', className: 'overflow-hidden',
});

/** 结果面板里的一段：16px 内边距，除第一段外顶上一条线。 */
export const RESULT_SECTION = 'border-t border-border-button-default p-4 first:border-t-0';

/** 面板底下那条放按钮的带子。`error` 是「这一步删了就回不来」的那一档：红线红底，左边
 *  那句说明也换成红字。 */
export function PanelFooter(
  { tone = 'neutral', status, children }:
  { tone?: 'neutral' | 'error'; status?: string; children: ReactNode },
) {
  return (
    <div className={`${BAND} ${BAND_TONE[tone]}`}>
      {status
        ? <p className={`mr-auto min-w-0 text-caption-1-regular ${tone === 'error' ? 'text-text-error-primary' : 'text-text-secondary'}`}>
            {status}
          </p>
        : null}
      {children}
    </div>
  );
}

/** 卡外那一行分区标题（链接管理、资源同步），Title 2，与下面那张卡隔 16px。
 *
 *  这一页挂在 `#stats` 里，遗留样式表那条 `.stats h2` 给所有二级标题定了 24px 与 4px 底距。
 *  它不在层里，工具类压不过，所以字形与外边距这两组带 `!`。 */
export function SectionHeading({ id, children }: { id: string; children: ReactNode }) {
  return <h2 id={id} className="text-title-2-medium! m-0! text-text-primary">{children}</h2>;
}

/** 卡上的标题。`gap` 是它和下面那一段之间的距离：整理与空文件夹 12px，链接与资源同步 8px；
 *  扫描与采集那两张的正文本身是一列 8px 间距，标题不另留。 */
export function FieldsetTitle(
  { id, gap = 'none', children }: { id?: string; gap?: 'none' | 'small' | 'medium'; children: ReactNode },
) {
  const margin = gap === 'medium' ? 'mb-3' : gap === 'small' ? 'mb-2' : '';
  return <h3 id={id} className={`text-title-2-medium text-text-primary ${margin}`}>{children}</h3>;
}

export function Fieldset(
  { layout, label, labelledBy, id, footer, children, attributes }:
  {
    layout: Layout;
    /** 没有可见标题可指的时候（扫描与采集那两张的标题在正文那一列里）用它。 */
    label?: string;
    labelledBy?: string;
    id?: string;
    footer?: ReactNode;
    children: ReactNode;
    /** 冒烟与外观用例认卡的 `data-*`。类名给不了：生成出来的名字会和旧样式表撞。 */
    attributes?: Record<`data-${string}`, string>;
  },
) {
  return (
    <section id={id} aria-label={label} aria-labelledby={labelledBy} {...attributes}
      className={`${FACE} ${layout === 'split' ? 'fieldset-split' : 'flex flex-col'}`}>
      <div className={BODY[layout]}>{children}</div>
      {footer ? <footer className={FOOT[layout]}>{footer}</footer> : null}
    </section>
  );
}
