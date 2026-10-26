/* 独立页面的外框：页面底色上居中一张描边卡，卡头是站标、标题和可选的一句引言。
 *
 * 注册表里没有 Auth Card，用 BoardUI token 组合，分层同配置页的设置区：整页 `background-full`
 * （由 `pages.css` 落在 body 上），卡面 `background-secondary-default` 加一条 `separator-border`，
 * 卡里的输入框与分段是 tertiary、文件夹行与次级按钮是 primary，深浅两色下都比卡面亮一档。
 * 24px 圆角，窄屏收内边距。
 * `busy` 时卡上写 `aria-busy`，题目还没取回来。 */
import type { ReactNode } from 'react';

export function AuthCard({ title, lede, busy = false, children }: {
  title: string;
  lede?: string;
  busy?: boolean;
  children?: ReactNode;
}) {
  return (
    <main className="flex justify-center px-6 py-12 max-sm:px-4 max-sm:py-6">
      <section aria-labelledby="auth-card-title" aria-busy={busy || undefined}
        className="flex w-full max-w-140 flex-col gap-6 rounded-3xl border border-separator-border bg-background-secondary-default p-8 max-sm:p-5">
        <header className="flex flex-col gap-1">
          <img src="/peach-logo.png" alt="" width={40} height={40} className="mb-4 size-10" />
          <h1 id="auth-card-title" className="text-title-2-medium text-text-primary">{title}</h1>
          {lede ? <p className="whitespace-pre-line text-body-2-regular text-text-secondary wrap-anywhere">{lede}</p> : null}
        </header>
        {children}
      </section>
    </main>
  );
}

/** 卡里的一组：上面一条分隔线，24px 留白。第一组紧跟卡头，不画线。 */
export function CardGroup({ first = false, labelledBy, children }: {
  first?: boolean;
  labelledBy?: string;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={labelledBy}
      className={first ? 'flex flex-col gap-4' : 'flex flex-col gap-4 border-t border-separator-border pt-6'}>
      {children}
    </section>
  );
}

/** 一级分组的标题。 */
export function GroupTitle({ id, children }: { id?: string; children: ReactNode }) {
  return <h2 id={id} className="text-body-semibold text-text-primary">{children}</h2>;
}
