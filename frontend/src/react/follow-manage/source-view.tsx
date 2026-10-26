/* 一条关注来源在屏幕上的那几格：站标、头像、状态徽章、来源名。
 *
 * 创作者卡片、表格视图和别名清单读的是同一份，所以这几格摆在这里而不是各画一次：
 * 同一条来源在两种视图里的状态写法一旦分开写，就会出现同一行在卡片里说「未检查」、
 * 在表格里说「正常」。 */
import { useLayoutEffect, useRef, useState } from 'react';
import { VisuallyHidden } from 'react-aria-components';

import { Chip } from '@/components/base/badges/chip';
import { Button } from '@/components/base/buttons/button';

import {
  authorAvatar, authorInitial, errorHeadline, isBroken, sourceIconUrl, statusText,
  type FollowSource,
} from './follow-manage';

/** 站点圆标。
 *
 * 给了 `label` 就是「画得出图标就只画图标」：图标在时名字交给读屏，没登记图标或者那一枚
 * 取不下来时名字显出来——一个站在屏幕上总要认得出是哪个站，不能两种情况都指望同一枚图。 */
export function SourceIcon({ provider, label }: { provider: string; label?: string }) {
  const src = sourceIconUrl(provider);
  const [broken, setBroken] = useState(false);
  if (!src || broken) return label ? <>{label}</> : null;
  return (
    <>
      <img src={src} alt="" width={14} height={14} loading="lazy"
        onError={() => setBroken(true)}
        className="size-3.5 shrink-0 rounded-sm object-contain" />
      {label ? <VisuallyHidden>{label}</VisuallyHidden> : null}
    </>
  );
}

/** 创作者圆标：官方来源的头像优先，归档来源回退，都取不到就用首字母。
 *
 * 首字母取自创作者名而不是某条来源的标签——那会切出「初」「一」这类和创作者无关的字。 */
export function AuthorAvatar({ group, name }: { group: FollowSource[]; name: string }) {
  const { src, fallback } = authorAvatar(group);
  const [at, setAt] = useState(0);
  const chain = [src, fallback].filter(Boolean);
  const current = chain[at];
  if (!current) {
    return (
      <span title="没有可用头像"
        className="inline-grid size-8 shrink-0 place-items-center rounded-full bg-background-tertiary-default text-caption-1-semibold text-text-secondary">
        {authorInitial(name)}
      </span>
    );
  }
  return (
    <img src={current} alt="" width={32} height={32} loading="lazy" referrerPolicy="no-referrer"
      onError={() => setAt(at + 1)}
      className="size-8 shrink-0 rounded-full bg-background-tertiary-default object-cover" />
  );
}

/** 状态徽章。三档颜色对应三件事：要处理的、正常的、还没有结论的。 */
export function StatusBadge({ source }: { source: FollowSource }) {
  const color = isBroken(source) ? 'rose'
    : source.enabled && source.last_status === 'ok' ? 'lime'
    : source.enabled ? 'yellow' : 'neutral';
  return <Chip variant="caption" color={color}>{statusText(source)}</Chip>;
}

/** 来源名连到站上的原页面。新标签页不继承这一页的会话，来源地址也不带走 Peach 自己的地址。 */
export function SourceLink({ source }: { source: FollowSource }) {
  return (
    <a href={source.url} target="_blank" rel="noreferrer noopener" title="打开原来源"
      className="min-w-0 text-body-medium wrap-anywhere text-text-primary underline-offset-2 hover:underline">
      {source.label}
    </a>
  );
}

/** 一条报错：列表里只露一行（`errorHeadline`），放不下就截断；多于这一行或被截了时给「查看详情」原地展开全文。
 *  来源的上次检查、JAV 入库的刮削失败都用它，后端常回整页 HTML 或整段 traceback。 */
export function ErrorLine({ text, prefix = '', tone = 'error' }: { text: string; prefix?: string; tone?: 'error' | 'muted' }) {
  const line = useRef<HTMLElement>(null);
  const [open, setOpen] = useState(false);
  const [cut, setCut] = useState(false);
  const head = errorHeadline(text);
  useLayoutEffect(() => {
    const node = line.current;
    if (!node || open) return undefined;
    const measure = () => setCut(node.scrollWidth > node.clientWidth + 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [head, open]);
  const more = cut || head !== String(text || '').trim();
  return (
    <span className="flex min-w-0 max-w-full flex-col items-start gap-0.5">
      <small ref={line} data-error-line=""
        className={`max-w-full text-caption-1-regular ${tone === 'error' ? 'text-text-error-primary' : 'text-text-secondary'} ${open ? 'whitespace-pre-line wrap-anywhere' : 'block truncate'}`}>
        {prefix}{open ? text : head}
      </small>
      {more ? (
        <Button variant="ghost" size="xs" data-error-line-toggle="" aria-expanded={open} className="-ml-2"
          onClick={() => setOpen(!open)}>
          {open ? '收起详情' : '查看详情'}
        </Button>
      ) : null}
    </span>
  );
}
