/* 管理区正文居中，宽度使用 `max-w-board` 对应的 `--board-content`；页面标题由页头独立布局。 */
import type { ReactNode } from 'react';

export function Page({ children }: { children: ReactNode }) {
  return <div className="mx-auto flex w-full max-w-board flex-col gap-8">{children}</div>;
}
