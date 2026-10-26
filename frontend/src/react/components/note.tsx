/* 字段、分区、页面旁的持久提示。配置页与活动页共用这一份。
 *
 * 注册表里没有行内 Note：`notification` 条目是带关闭键和计时的浮动通知。这里按语气取
 * `status-*` 与 `notification-*` token 组合，差异登记在 `../boardui/ORIGIN.md`。 */
import type { ReactNode } from 'react';
import { cx } from '@/utils/cx';

type Tone = 'neutral' | 'info' | 'success' | 'warning' | 'error';

/** 没有密码、保存失败、数据库缺表这类是当前状态，不和字段说明共用灰色小字。
 *
 *  `action` 是这条提示要人当场做的那件事，宽容器内与结论并排；窄容器或 below 布局独占下一行。
 *  `extra` 收结论以外的东西（折叠起来的问题清单）：它们讲的是
 *  同一件事，摆到 Note 外面就成了一句话加一块不知道属于谁的内容。
 *  正文是段落，塞不进这些块级内容。 */
export function Note(
  { tone, title, action, extra, children, actionLayout = 'inline', className }:
  { tone: Tone; title?: string; action?: ReactNode; extra?: ReactNode; children: ReactNode; actionLayout?: 'inline' | 'below'; className?: string },
) {
  const content = (
    <>
      {/* 窄屏放不下时按钮换到下一行：挤成两个字一行的按钮比换行更难认。 */}
      <div data-note-layout="" className={cx('flex flex-col items-start gap-3',
        actionLayout === 'inline' && '@xl:flex-row @xl:items-center @xl:justify-between')}>
        <div className="flex min-w-0 flex-1 flex-col justify-center gap-0.5 self-stretch">
          {/* 正文常是服务端原话：长 URL、堆栈和多行原因照原样换行，不撑宽所在的卡。 */}
          {title ? <p className="text-body-medium wrap-anywhere">{title}</p> : null}
          <p className="whitespace-pre-line text-body-2-regular wrap-anywhere">{children}</p>
        </div>
        {action ? <div data-note-action="" className="max-w-full">{action}</div> : null}
      </div>
      {extra}
    </>
  );
  switch (tone) {
    case 'info':
      return <div role="status" className={cx('@container flex flex-col gap-2 rounded-2lg bg-notification-information-background px-3 py-2 text-notification-information-foreground', className)}>{content}</div>;
    case 'success':
      return <div role="status" className={cx('@container flex flex-col gap-2 rounded-2lg bg-notification-success-background px-3 py-2 text-notification-success-foreground', className)}>{content}</div>;
    case 'warning':
      return <div role="note" className={cx('@container flex flex-col gap-2 rounded-2lg bg-status-yellow-background px-3 py-2 text-status-yellow-text', className)}>{content}</div>;
    case 'error':
      return <div role="alert" className={cx('@container flex flex-col gap-2 rounded-2lg bg-background-tertiary-error px-3 py-2 text-text-error-primary', className)}>{content}</div>;
    default:
      return <div role="note" className={cx('@container flex flex-col gap-2 rounded-2lg bg-background-tertiary-default px-3 py-2 text-text-secondary', className)}>{content}</div>;
  }
}
