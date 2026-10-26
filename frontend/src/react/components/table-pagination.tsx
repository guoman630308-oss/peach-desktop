import { Button } from '@/components/base/buttons/button';
import { DOTS, paginationRange } from '../../pagination';

export function Pagination({ page, pages, onPage, label = '关注列表分页' }: {
  page: number; pages: number; onPage(page: number): void; label?: string;
}) {
  if (pages <= 1) return null;
  return (
    <nav aria-label={label} className="flex flex-wrap items-center justify-center gap-2">
      <Button variant="secondary" size="small" disabled={page <= 1} onClick={() => onPage(page - 1)}>上一页</Button>
      <ul className="flex flex-wrap items-center gap-1">
        {paginationRange(page, pages).map((item, at) => (
          <li key={item === DOTS ? `dots-${at}` : item}>
            {item === DOTS ? <span aria-hidden className="px-1 text-body-2-regular text-text-tertiary">{DOTS}</span>
              : <Button variant={item === page ? 'secondary' : 'ghost'} size="small"
                  aria-label={`第 ${item} 页`} aria-current={item === page ? 'page' : undefined}
                  onClick={() => onPage(Number(item))}>{item}</Button>}
          </li>
        ))}
      </ul>
      <Button variant="secondary" size="small" disabled={page >= pages} onClick={() => onPage(page + 1)}>下一页</Button>
    </nav>
  );
}
