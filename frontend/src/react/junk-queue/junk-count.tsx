/* 垃圾文件页的计数行：一块摘要面（这一视图、这一类有多少个）和一条分类条。
 *
 * 分类有哪几项、选中哪一项、看的是待判断还是已排除，全由地址决定，等数据时就是最终样子；
 * 随请求变的只有摘要里那个数和各类的计数徽标，数据没到时它们让位给占位。壳在 React 包加载
 * 之前铺的那一版（`src/junk-queue.ts` 的 `junkCountSkeletonHtml`）和这里等数据时逐项相同。
 *
 * 分类条是首页筛选条那一副：一块玻璃浮层，当前那一类底下垫一块滑动玻璃，悬停时滑到指针下
 * 那一枚（`components/use-view-glide.ts`）。分隔线右边的「已排除／返回待判断」是换视图，
 * 不归这块玻璃：已排除视图下它和「全部」同时是当前项，它自己静止垫一块同料的玻璃。
 *
 * 分类仍是 `<a href>`：要落到地址上，可深链、可刷新恢复，中键与修饰键照浏览器默认开新标签。
 * 普通左键交给壳，由它收起多选、改地址并重读。计数为 0 的那一类整枚徽标不画。 */
import { useRef, type MouseEvent } from 'react';

import { JUNK_KIND_OPTIONS, junkPath, junkSummaryLabel, junkViewLink } from '../../junk-queue';
import { CollectionSummary } from '../components/collection-summary';
import { useOverlayScrollbar } from '../components/overlay-scrollbar';
import { useViewGlide } from '../components/use-view-glide';
import { junkCount, type JunkPage, type JunkRoute } from './junk-queue';

function Icon({ name }: { name: string }) {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><use href={`#i-${name}`} /></svg>;
}

function Badge({ scope, count }: { scope: string; count: number }) {
  if (!count) return null;
  return <>{' '}<span data-count-badge={`junk:${scope}`}>{count.toLocaleString()}</span></>;
}

export function JunkCount({ route, page, failed, onNavigate }: {
  route: JunkRoute; page: JunkPage | undefined; failed: boolean; onNavigate(path: string): void;
}) {
  const nav = useOverlayScrollbar<HTMLElement>();
  const pane = useRef<HTMLSpanElement>(null);
  const { kind, view } = route;
  /* 计数徽标到货、判过一批后计数变了，各枚的宽度跟着变：这些数也算进键里，玻璃重新落位。 */
  const counts = page ? JUNK_KIND_OPTIONS.map(([key]) => junkCount(page, key)).join(',') : '';
  const glide = useViewGlide(pane, nav, '[data-junk-kind-link][aria-current="page"]', `${view}:${kind}:${counts}`);
  const other = junkViewLink(view);
  const click = (event: MouseEvent<HTMLElement>) => {
    const link = (event.target as HTMLElement).closest('a');
    if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    onNavigate(link.getAttribute('href') || junkPath());
  };
  /* 读不到就不再转圈：失败的原因写在网格那一格里，这里只说「没有数」。 */
  const figure = page ? `${Number(page.total || 0).toLocaleString()} 个` : failed ? '—' : '';
  return (
    <div data-junk-count="">
      <div data-junk-summary="" aria-live="polite">
        <CollectionSummary label={junkSummaryLabel(view)} figure={figure} pending={!page && !failed} flush />
      </div>
      <div data-junk-filters-frame="" data-filter-glass="" data-glass-pane="">
        <span ref={pane} data-view-glide="" aria-hidden="true" />
        <nav ref={nav} data-junk-filters="" aria-label="垃圾文件分类" onClick={click} onPointerLeave={glide.leave}>
          {JUNK_KIND_OPTIONS.map(([key, label, glyph]) => (
            <a key={key || 'all'} href={junkPath(key, view)} data-junk-kind-link={key}
              aria-current={key === kind ? 'page' : undefined} onPointerEnter={glide.hover}>
              <Icon name={glyph} />{label}<Badge scope={key} count={page ? junkCount(page, key) : 0} />
            </a>
          ))}
          <i data-junk-divider="" aria-hidden="true" />
          <a href={other.href} data-junk-view-link={other.view} aria-current={view === 'dismissed' ? 'page' : undefined}
            onPointerEnter={view === 'dismissed' ? undefined : glide.hover}>
            <Icon name={other.glyph} />{other.label}<Badge scope="dismissed" count={Number(page?.dismissed_total || 0)} />
          </a>
        </nav>
      </div>
    </div>
  );
}
