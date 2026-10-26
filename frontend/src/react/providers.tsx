/* 每一棵 React 根外面的那三层：共享 QueryClient、Motion 的减弱动效与弹出层容器。
 *
 * 路由树的根（`router/router.tsx`）包这一份，页面、页面内组件与常驻组件（舞台在内）都画在这棵树里：舞台里的两座
 * 详情和目录网格读同一个缓存，从列表点进详情时卡片已经在缓存里。 */
import type { ReactNode } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MotionConfig } from 'motion/react';
import { UNSAFE_PortalProvider } from 'react-aria';

import { queryClient } from './query';

/* Popover 这类弹出层由 React Aria 渲染到挂载容器外面。落在 `body` 上就出了 `.peach-react`
 * 的作用域：token 读到的是 `board.css` 的值，Preflight 也管不到。所有 React 根的弹出层都进
 * `body` 末尾这一个同样带 `.peach-react` 的容器。 */
let overlays: HTMLElement | null = null;
function overlayContainer(): HTMLElement {
  if (!overlays?.isConnected) {
    overlays = document.createElement('div');
    overlays.className = 'peach-react';
    overlays.dataset.reactOverlays = '';
    document.body.append(overlays);
  }
  return overlays;
}

/* 所有 React 根共用一个 QueryClient（ADR-0031）：页面级 `prefetch` 写进去的首屏，
 * 组件挂上去就直接读到，同一份数据不会因为挂在哪棵根上而各取一次。
 *
 * EvilCharts 的生长动画由 Motion 逐帧驱动，`web/css/01-base.css` 那条全局 reduced motion
 * 规则只关得掉 CSS 过渡，够不着它。`MotionConfig reducedMotion="user"` 在根上一处接住：
 * 系统设了减弱动态效果，每棵 React 根里的 Motion 动画都按终态直接画。 */
export function Providers({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <MotionConfig reducedMotion="user">
        <UNSAFE_PortalProvider getContainer={overlayContainer}>{children}</UNSAFE_PortalProvider>
      </MotionConfig>
    </QueryClientProvider>
  );
}
