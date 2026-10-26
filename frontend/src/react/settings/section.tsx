/* 配置页各分区共用的组合件。
 *
 * 外框取 BoardUI 设置弹层的 `SettingsSectionLabel` + `SettingsCard`，开关行直接用 `SettingsRow`。
 * 注册表里没有折叠，它用 BoardUI token 组合，差异登记在 `boardui/ORIGIN.md`。
 * 提示与进度条另有页面共用的一份，在 `../components/`。 */
import { useId, useRef, useState } from 'react';
import type { ComponentType, FormEvent, MouseEvent, ReactNode } from 'react';
import { RiArrowRightSLine, RiExternalLinkLine } from '@remixicon/react';
import { setCollapseOpen } from '@peach/legacy/ui';

import { SettingsCard, SettingsSectionLabel } from '@/components/application/settings/settings-rows';
import { LinkButton } from '@/components/base/buttons/link-button';
import { cx } from '@/utils/cx';

interface SectionProps {
  title: string;
  icon?: ReactNode;
  id?: string;
  /** 跟着标题走的次要内容（来源站点的外链）。标题占剩下的宽度，它靠右。 */
  aside?: ReactNode;
  /** 给了就画成表单：回车提交，提交键放在 `Footer` 里。 */
  onSubmit?: (event: FormEvent<HTMLFormElement>) => void;
  children: ReactNode;
}

/* 分区里的说明、报错与读数常带长 URL、堆栈和无空格路径：整个分区按 `overflow-wrap:anywhere`
   就地折断（属性会继承，行内说明、Help 与读数都跟着走），不把卡片撑出横向滚动。 */
export function Section({ title, icon, id, aside, onSubmit, children }: SectionProps) {
  const label = icon ? <span className="inline-flex items-center gap-1.5">{icon}{title}</span> : title;
  const body = (
    <>
      {aside
        /* 标题至少留 10rem：放不下时外链换到下一行，不把标题挤成一字一行。 */
        ? <div className="flex w-full flex-wrap items-center justify-between gap-2">
            <div className="min-w-40 flex-1"><SettingsSectionLabel>{label}</SettingsSectionLabel></div>
            <div className="flex min-w-0 max-w-full">{aside}</div>
          </div>
        : <SettingsSectionLabel>{label}</SettingsSectionLabel>}
      <SettingsCard>{children}</SettingsCard>
    </>
  );
  return onSubmit
    ? <form id={id} aria-label={title} noValidate onSubmit={onSubmit} className="flex w-full flex-col gap-2 wrap-anywhere">{body}</form>
    : <section id={id} aria-label={title} className="flex w-full flex-col gap-2 wrap-anywhere">{body}</section>;
}

/** 一组 `SettingsRow`。行自己画下边线、最后一行不画，所以要有一层只装行的父元素。 */
export function Rows({ children }: { children: ReactNode }) {
  return <div className="flex flex-col">{children}</div>;
}

/** 卡片只管左内边距和底色；不是 `SettingsRow` 的内容由这一层给行距和上下右内边距。
 *  排在 `Rows` 或 `FactList` 后面时 `divided`，和上面那组行之间补一道分隔线。 */
export function Stack({ divided = false, children }: { divided?: boolean; children: ReactNode }) {
  return divided
    ? <div className="flex flex-col gap-4 border-t border-separator-border py-4 pr-3">{children}</div>
    : <div className="flex flex-col gap-4 py-4 pr-3">{children}</div>;
}

/** 底栏：左边一句说这颗按钮此刻意味着什么，右边是按钮。
 *
 *  底色比卡面暗一点点，通到卡片左右两沿（旧 `.geist-fieldset-footer` 的 `--board-card-foot`）：
 *  卡片的左内边距在卡自己身上，所以这一条要把它抵掉再自己补回来。 */
export function Footer({ status, children }: { status?: ReactNode; children?: ReactNode }) {
  return (
    <div className="-ml-3 flex flex-wrap items-center justify-end gap-3 rounded-b-2xl border-t border-separator-border bg-card-footer px-3 py-3">
      {status ? <div className="mr-auto min-w-0 text-body-2-regular text-text-secondary">{status}</div> : null}
      {children}
    </div>
  );
}

export function Help({ role, children }: { role?: 'status'; children: ReactNode }) {
  return <p role={role} className="text-body-2-regular text-text-secondary">{children}</p>;
}

export function ErrorText({ children }: { children: ReactNode }) {
  return <p role="alert" className="text-body-2-regular text-text-error-primary">{children}</p>;
}

/** 字段上方的名字，外观同 BoardUI `Label`；控件自己的无障碍名称由 `aria-label` 给。 */
export function FieldLabel({ children }: { children: ReactNode }) {
  return <p className="text-body-medium text-text-primary">{children}</p>;
}

/** 新窗口打开的外部链接，尾部带外链字形。`rel` 两项都写：新标签页不继承这一页的会话，
 *  来源地址也不带走 Peach 自己的地址。 */
export function ExternalLink({ href, children, leadingIcon }: {
  href: string; children: ReactNode;
  leadingIcon?: ComponentType<{ className?: string; 'aria-hidden'?: boolean | 'true' | 'false' }>;
}) {
  return (
    <LinkButton href={href} target="_blank" rel="noopener noreferrer" size="small"
      leadingIcon={leadingIcon} trailingIcon={RiExternalLinkLine} className="min-w-0 max-w-full">
      <span className="min-w-0 whitespace-normal break-all">{children}</span>
    </LinkButton>
  );
}

/** 只读的名目与读数，一行一对，行距与分隔线同 `SettingsRow`。 */
export function FactList({ children }: { children: ReactNode }) {
  return <dl className="flex flex-col">{children}</dl>;
}

/* 名目按内容宽，最多占一半，再长就在自己那一半里折行；读数靠右，折成多行时行内文字左对齐，
   读起来是一段话而不是一面右对齐的字墙。 */
export function Fact({ term, children }: { term: ReactNode; children: ReactNode }) {
  return (
    <div className="flex min-h-11 items-center justify-between gap-4 border-b border-separator-border py-2.5 pr-3 last:border-b-0">
      <dt className="flex max-w-1/2 min-w-0 shrink-0 items-center gap-2 text-body-regular text-text-secondary">{term}</dt>
      <dd className="flex min-w-0 flex-wrap items-center justify-end gap-2 text-left text-body-regular break-all text-text-primary">{children}</dd>
    </div>
  );
}

/** 展开正文。原生 `details` 给键盘与无障碍语义；开合交给共用 Collapse 的 `setCollapseOpen`，
 *  它给正文外层挂上 `ui-kit/collapse.css` 的 `.ui-fcollapse` 让高度过渡。内边距放在里层，高度才能收到 0。 */
export function Disclosure(
  { summary, defaultOpen = false, children, trailing = false, onOpenChange, contentClassName }:
  { summary: ReactNode; defaultOpen?: boolean; children: ReactNode; trailing?: boolean; onOpenChange?(open: boolean): void; contentClassName?: string },
) {
  const id = useId();
  const details = useRef<HTMLDetailsElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const primed = useRef(false);
  const [open, setOpen] = useState(defaultOpen);
  const toggle = (event: MouseEvent) => {
    event.preventDefault();
    if (!details.current || !body.current) return;
    setCollapseOpen(details.current, body.current, !open);
    setOpen(!open);
    onOpenChange?.(!open);
  };
  /* 展开一次就交回给 `setCollapseOpen`：`open` 写成 React 的受控属性的话，收起的那段
     过渡里它会被重新画成展开。这里只在第一次挂上时把初始状态落到 DOM 上。 */
  const attach = (node: HTMLDetailsElement | null) => {
    details.current = node;
    if (node && defaultOpen && !primed.current) {
      primed.current = true;
      node.open = true;
    }
  };
  return (
    <details ref={attach}>
      <summary aria-expanded={open} aria-controls={id} onClick={toggle}
        className={`${trailing ? 'w-full' : 'w-fit'} flex cursor-pointer list-none items-center gap-1 rounded-sm text-body-2-medium text-text-secondary outline-none select-none hover:text-text-primary focus-visible:ring-2 focus-visible:ring-border-focus-ring`}>
        {!trailing ? <RiArrowRightSLine aria-hidden className={open ? 'size-4 shrink-0 rotate-90 transition-transform' : 'size-4 shrink-0 transition-transform'} /> : null}
        {summary}
        {trailing ? <RiArrowRightSLine aria-hidden className={open ? 'ml-auto size-4 shrink-0 -rotate-90 transition-transform' : 'ml-auto size-4 shrink-0 rotate-90 transition-transform'} /> : null}
      </summary>
      <div ref={body} id={id} inert={!open}>
        <div className={cx('flex flex-col gap-2 pt-3', contentClassName)}>{children}</div>
      </div>
    </details>
  );
}

/** 遗留雪碧图（`web/index.html` 的 `#i-*` symbol）里的字形。媒体库图标的取值就是这些名字。 */
export function SpriteIcon({ name }: { name: string }) {
  return (
    <svg aria-hidden viewBox="0 0 24 24" fill="none" className="size-4 shrink-0 stroke-current stroke-2">
      <use href={`#i-${name}`} />
    </svg>
  );
}

/** 来源标识：网盘站标是内嵌 PNG，其余是雪碧图字形（`src/ui-kit/media-source-icons.ts`）。 */
export function SourceMark({ mark }: { mark: string }) {
  return mark.startsWith('data:image/png;base64,')
    ? <img src={mark} alt="" width={16} height={16} className="size-4 shrink-0" />
    : <SpriteIcon name={mark} />;
}
