/* 吸顶的那块玻璃是不是已经贴到了顶栏下沿。贴住了就在元素上标 `data-stuck`，样式表按它换成
 * 抬起来那一档影（`../styles.css` 的 `[data-filter-glass][data-stuck]`）。
 *
 * 判据照壳的 `updateStickySurfaces`：页面滚过了、元素上沿到了它自己 `top` 那条线。滚动与改
 * 尺寸各合成一帧再量；属性直接写在元素上，不走 React 状态——每一帧滚动都重画整条浮层没有意义。 */
import { useEffect, type RefObject } from 'react';

export function useStuck(ref: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    let frame = 0;
    const measure = () => {
      frame = 0;
      const top = parseFloat(getComputedStyle(element).top);
      const stuck = window.scrollY > 0 && Number.isFinite(top)
        && element.getBoundingClientRect().top <= top + 1;
      element.toggleAttribute('data-stuck', stuck);
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(measure); };
    measure();
    addEventListener('scroll', schedule, { passive: true });
    addEventListener('resize', schedule);
    return () => {
      cancelAnimationFrame(frame);
      removeEventListener('scroll', schedule);
      removeEventListener('resize', schedule);
    };
  }, [ref]);
}
