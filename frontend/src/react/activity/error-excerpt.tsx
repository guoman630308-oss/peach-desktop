/* 列表里的一段报错：平时只露一行（`errorHeadline`），「查看详情」展开全文。
 *
 * 后端回的常是整段 traceback 或整页 HTML，铺开会把一张卡撑成几屏高；只露一行又看不到全文，
 * 所以全文收在键后面，展开后保留原有换行、长 URL 就地折断，太长时在自己的框里滚。 */
import { useId, useState } from 'react';

import { Button } from '@/components/base/buttons/button';

import { errorHeadline } from './tasks';

export function ErrorExcerpt({ text, className = '' }: { text: string; className?: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const full = String(text || '').trim();
  const head = errorHeadline(full);
  if (!head) return null;
  const more = head !== full;
  return (
    <div data-error-excerpt="" className={`flex min-w-0 max-w-full flex-col items-start gap-1 ${className}`}>
      {open
        ? <p id={id} className="max-h-80 w-full overflow-y-auto whitespace-pre-line wrap-anywhere">{full}</p>
        : <p className="w-full wrap-anywhere">{head}</p>}
      {more
        ? <Button variant="ghost" size="xs" className="-ml-2" aria-expanded={open} aria-controls={open ? id : undefined}
            onClick={() => setOpen(!open)}>
            {open ? '收起详情' : '查看详情'}
          </Button>
        : null}
    </div>
  );
}
