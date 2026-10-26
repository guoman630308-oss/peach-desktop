/* 垃圾文件队列（`/junk-files`，由路由树画进 `#grid`）：计数行加一屏逐项处置的卡片。
 *
 * 页面画在壳的 `#grid` 里，计数行经 portal 画进 `#count` 里自己建的一个 `.peach-react` 容器：
 * 那一行本身归壳（目录页也用它，换页时由壳清空），里面的摘要与分类条归这里。
 *
 * 首屏不在 `prefetch` 里等。分类条由地址决定、此刻就画得出最终样子，等的只有读数，所以一打开
 * 就画：计数行是等待态（`#count[aria-busy]`、摘要里一条占位），网格是壳那份骨架，数据到了
 * 骨架淡出、卡片从模糊里清晰起来（同目录网格）。在 `prefetch` 里等的话，请求挂着的这段时间
 * 屏上只有壳的静态骨架，分类点不动。
 *
 * 换分类、换视图是换查询键，网格按新键重挂、重铺骨架。壳处置完推进 `revision` 时查询也换键，
 * 但旧的一份留在屏上直到新的一份到货（`placeholderData`），被处置的那张卡原地消失，
 * 已经往下露出来的那几段也不收回去。 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useQuery } from '@tanstack/react-query';
import { requestErrorMessage } from '@peach/legacy/core';
import { loadingDotsHtml, popBadges } from '@peach/legacy/ui';

import { EmptyState } from '../components/empty-state';
import { RetryNote, useSkeletonReveal } from '../components/grid-reveal';
import { spriteGlyph } from '../components/sprite-glyph';
import { JunkCard } from './junk-card';
import { JunkCount } from './junk-count';
import { fetchJunkPage, junkQueryKey, type JunkPage, type JunkQueueProps, type JunkRoute } from './junk-queue';

const CheckGlyph = spriteGlyph('check');

const sameRoute = (key: readonly unknown[], route: JunkRoute) => key[1] === route.view && key[2] === route.kind;

export function JunkQueuePage(props: JunkQueueProps) {
  const route: JunkRoute = { kind: props.kind, view: props.view };
  const query = useQuery<JunkPage, Error>({
    queryKey: junkQueryKey(route, props.revision),
    queryFn: ({ signal }) => fetchJunkPage(route, signal),
    placeholderData: (previous, previousQuery) =>
      (previousQuery && sameRoute(previousQuery.queryKey, route) ? previous : undefined),
  });

  /* 一次取数落定就告诉壳一次，成功、为空、失败都算：壳里 `await` 这次重读的调用方据此放行。 */
  useEffect(() => {
    if (!query.isFetching) props.settled?.(props.revision);
  }, [query.isFetching, props.revision]);

  const host = useCountHost(props.countRow);
  const waiting = query.isPending;
  useLayoutEffect(() => {
    const row = props.countRow;
    if (!row) return;
    if (waiting) row.setAttribute('aria-busy', 'true');
    else row.removeAttribute('aria-busy');
  }, [props.countRow, waiting]);
  /* 判过一批之后各类的计数变了，弹的是变了的那几枚，没动的原地不动（遗留层 `popBadges`）。 */
  useLayoutEffect(() => {
    if (host && query.data) popBadges(host, 'junk');
  }, [host, query.data]);

  const { navigate } = props.actions;
  const count = host
    ? createPortal(
      <JunkCount route={route} page={query.data} failed={query.isError && !query.data} onNavigate={navigate} />, host)
    : null;
  return (
    <>
      {count}
      <JunkBody key={`${route.view}:${route.kind}`} props={props} data={query.data} pending={waiting}
        error={query.data ? null : query.error} retry={() => void query.refetch()} />
    </>
  );
}

/** 计数行里归这一页的那一格。挂上时换掉壳铺的骨架，卸载时撤掉，行本身还给壳。 */
function useCountHost(row: HTMLElement | null): HTMLElement | null {
  const [host, setHost] = useState<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (!row) return undefined;
    const el = row.ownerDocument.createElement('div');
    el.className = 'peach-react';
    row.replaceChildren(el);
    setHost(el);
    return () => { el.remove(); setHost(null) };
  }, [row]);
  return host;
}

/** 一个分类、一个视图的网格：骨架、卡片、往下露的下一段与空态。 */
function JunkBody({ props, data, pending, error, retry }: {
  props: JunkQueueProps; data: JunkPage | undefined; pending: boolean; error: Error | null; retry(): void;
}) {
  const reveal = useSkeletonReveal(pending, props.skeletonHtml);
  const batch = Math.max(1, props.batchSize || 60);
  const [segments, setSegments] = useState(1);
  const items = data?.items || [];
  const shown = items.slice(0, segments * batch);
  const { canLoadMore } = props;
  const more = useCallback(() => {
    if (canLoadMore?.() === false) return;
    setSegments((current) => current + 1);
  }, [canLoadMore]);

  const content = () => {
    if (pending) return null;
    if (!data) return <RetryNote message={requestErrorMessage(error)} onRetry={retry} />;
    if (!items.length) {
      const dismissed = props.view === 'dismissed';
      return (
        <EmptyState icon={CheckGlyph} title={dismissed ? '没有已排除的文件' : '没有待判断的垃圾文件'}>
          {dismissed ? '点“不是垃圾”的资源会保留在这里，可随时重新判断。' : '当前分类没有待检查文件。'}
        </EmptyState>
      );
    }
    return (
      <>
        <div data-media-grid="" data-junk-grid="" data-select-mode={props.selectMode ? '' : undefined}>
          {shown.map((item) => (
            <JunkCard key={item.id} item={item} view={props.view} selected={props.selected.has(item.id)}
              selectMode={props.selectMode} helpers={props.helpers} actions={props.actions} />
          ))}
        </div>
        {shown.length < items.length ? <RevealMore key={segments} onMore={more} /> : null}
        {/* 队列一次只取前 200 条：露到底了还有没取的，说清楚是截断而不是全部。 */}
        {shown.length >= items.length && Number(data.total || 0) > items.length
          ? <p data-junk-truncated="" className="py-6 text-center text-caption-1-regular text-text-secondary">
              {`显示前 ${items.length.toLocaleString()} 条，共 ${Number(data.total).toLocaleString()} 条；处理掉这些后，其余的会补上来。`}
            </p>
          : null}
      </>
    );
  };
  return (
    <div data-junk-body="" data-grid-reveal={reveal.fading ? '' : undefined}>
      {reveal.layer}
      {content()}
    </div>
  );
}

/** 往下露下一段。数据已经整批在手上，不发请求：哨兵进入视口 320px 内就露，点它也露，
 *  判据同目录网格的续页。每露一段按新键重挂，还在视口里就接着露。 */
function RevealMore({ onMore }: { onMore(): void }) {
  const node = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = node.current;
    if (!el || typeof IntersectionObserver === 'undefined') return undefined;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) onMore();
    }, { rootMargin: '320px' });
    observer.observe(el);
    return () => observer.disconnect();
  }, [onMore]);
  return <div ref={node} data-load-more="" onClick={onMore} dangerouslySetInnerHTML={{ __html: loadingDotsHtml('继续载入中…') }} />;
}
