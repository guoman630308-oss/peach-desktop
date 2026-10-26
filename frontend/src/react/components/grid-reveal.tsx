/* 卡片网格的等待与失败：壳铺的骨架怎么退场，取数失败留下什么。馆藏卡片网格与垃圾文件队列共用。 */
import { useEffect, useLayoutEffect, useRef, useState, type AnimationEvent, type MouseEvent } from 'react';
import { fitSkeleton, noteHtml } from '@peach/legacy/ui';

/** 骨架与它的退场。骨架那一层在数据到了之后原地变成淡出层：同一个元素、同一段 HTML，
 *  React 不重写它，`fitSkeleton` 补齐的那几张卡也还在。骨架还没到显示门槛（`fitSkeleton`
 *  给它挂着 `.skeleton-awaiting`）就已取完，就直接撤掉，不为从未被看见的占位播退场。
 *
 *  `layer` 放在 `[data-grid-reveal]` 容器的第一格，`fading` 为真时容器带上这个属性。 */
export function useSkeletonReveal(pending: boolean, skeletonHtml: () => string) {
  const [phase, setPhase] = useState<'skeleton' | 'fade' | 'none'>(pending ? 'skeleton' : 'none');
  const [markup] = useState(() => (pending ? skeletonHtml() : ''));
  const node = useRef<HTMLDivElement | null>(null);
  useLayoutEffect(() => { if (node.current) fitSkeleton(node.current) }, []);
  useLayoutEffect(() => {
    if (pending || phase !== 'skeleton') return;
    const seen = !!node.current && !node.current.querySelector('.skeleton-awaiting');
    setPhase(seen ? 'fade' : 'none');
  }, [pending]);
  /* 动画结束事件丢了（标签页在后台、元素被别的动效接管）也要收掉，同遗留层的 1 秒兜底。 */
  useEffect(() => {
    if (phase !== 'fade') return undefined;
    const timer = setTimeout(() => setPhase('none'), 1000);
    return () => clearTimeout(timer);
  }, [phase]);
  const done = (event: AnimationEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) setPhase('none');
  };
  const layer = phase === 'none' ? null : (
    <div ref={node} data-grid-fade={phase === 'fade' ? '' : undefined} aria-hidden={pending ? undefined : true}
      hidden={!pending && phase === 'skeleton'} onAnimationEnd={done}
      dangerouslySetInnerHTML={{ __html: markup }} />
  );
  return { layer, fading: phase === 'fade' };
}

/** 失败留在原位，给一颗重试。Note 由遗留层 `noteHtml` 拼，和壳里别处的失败提示是同一份。 */
export function RetryNote({ message, onRetry }: { message: string; onRetry: () => void }) {
  const click = (event: MouseEvent<HTMLDivElement>) => {
    if ((event.target as HTMLElement).closest('[data-note-action]')) onRetry();
  };
  return (
    <div data-media-error="" onClick={click}
      dangerouslySetInnerHTML={{ __html: noteHtml(message, { variant: 'error', actionLabel: '重试' }) }} />
  );
}
