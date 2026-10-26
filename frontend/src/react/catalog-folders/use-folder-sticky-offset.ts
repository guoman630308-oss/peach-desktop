import { useLayoutEffect } from 'react';

/** 目录栏的吸顶边界跟随首页筛选浮层的实际高度。 */
export function useFolderStickyOffset(active: boolean) {
  useLayoutEffect(() => {
    if (!active) return;
    const host = document.getElementById('catalogFilter');
    let observed: Element | null = null;
    let frame = 0;
    const resize = new ResizeObserver(() => schedule());
    const measure = () => {
      frame = 0;
      const filter = host?.querySelector<HTMLElement>('[data-catalog-frame]') ?? null;
      if (filter !== observed) {
        resize.disconnect();
        observed = filter;
        if (filter) resize.observe(filter);
      }
      const top = filter ? parseFloat(getComputedStyle(filter).top) :
        parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--topH'));
      const offset = (Number.isFinite(top) ? top : 0) + (filter?.getBoundingClientRect().height ?? 0) + 16;
      document.body.style.setProperty('--folder-sticky-top', `${offset}px`);
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(measure) };
    const mutation = new MutationObserver(schedule);
    if (host) mutation.observe(host, { childList: true, subtree: true });
    addEventListener('resize', schedule);
    measure();
    return () => {
      resize.disconnect();
      mutation.disconnect();
      cancelAnimationFrame(frame);
      removeEventListener('resize', schedule);
      document.body.style.removeProperty('--folder-sticky-top');
    };
  }, [active]);
}
