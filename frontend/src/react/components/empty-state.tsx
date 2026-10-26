/* 没有数据或结果时的空态：图标、标题、说明同处一个组件里（`peach-web-ui`）。
 *
 * 注册表里没有空态条目。标题是真的标题元素，冒烟用例按它认页面主体：一片白和「画出来了、
 * 只是没有内容」在别的断言下长得一模一样。根上的 `data-empty-state` 是给冒烟与外观用例认的
 * 钩子，不参与样式。
 *
 * 外壳按它落在哪里选，不按「哪个好看」选：卡里的空态自己不再画一层框。 */
import type { ComponentType, ReactNode } from 'react';

type Glyph = ComponentType<{ className?: string; 'aria-hidden'?: boolean | 'true' | 'false' }>;

/* 卡片里的空态不描边：外面那张卡已经是一个框，再套一层就是框中框。
 * - `page`：整屏就它一个，自己收一条边，同遗留层 `.ui-emptystate`（`frontend/src/ui-kit/markup.css`）：
 *   沉一档底色、浮层圆角、至少 320px 高，内容竖向居中；图标装进 54px 的描边方框。
 * - `plain`：落在一张卡或一块面板里，只留内边距，同旧 `.insightempty{padding:16px}`。
 * - `inset`：占住卡里图区那块地方，沉一档底色，读起来仍是「这里本该有东西」。 */
const SHELL = {
  page: 'min-h-80 justify-center rounded-floating border border-separator-border bg-background-secondary-default px-6 py-12',
  plain: 'px-6 py-12',
  inset: 'min-h-40 justify-center rounded-xl bg-background-secondary-default px-4 py-4',
} as const;

export function EmptyState(
  { icon: Icon, title, actions, children, shell = 'page' }:
  { icon: Glyph; title: string; actions?: ReactNode; children: ReactNode;
    shell?: keyof typeof SHELL },
) {
  const page = shell === 'page';
  return (
    <div data-empty-state="" className={`flex flex-col items-center gap-2 text-center ${SHELL[shell]}`}>
      {/* 图标盒子只在整页那一档：描边取主文字色 15%，同旧 `--border-15`，BoardUI 没有这一档透明描边。 */}
      {page
        ? (
          <span aria-hidden className="mb-2 flex size-empty-glyph items-center justify-center rounded-floating border border-text-primary/15 p-2.5 text-text-secondary">
            <Icon aria-hidden className="size-8" />
          </span>
        )
        : <Icon aria-hidden className="size-6 text-text-tertiary" />}
      <h3 className={`${page ? 'text-body-semibold' : 'text-headline-medium'} text-text-primary`}>{title}</h3>
      <p className={`${page ? 'max-w-empty-copy leading-empty-copy' : 'max-w-prose'} text-body-2-regular text-text-secondary`}>{children}</p>
      {/* 去处属于这一块，不摆到框外面：「还没有内容」和一个不知道属于谁的按钮分开放时，
          读者要先把两样东西连起来才知道按下去做什么。 */}
      {actions ? <div className="mt-2 flex flex-wrap justify-center gap-2">{actions}</div> : null}
    </div>
  );
}
