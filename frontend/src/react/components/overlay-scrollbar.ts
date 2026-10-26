/* 自己滚的那一块换成全站那条覆盖式滚动条。
 *
 * 遗留层按名单批量挂的是 `wireOverlayScrollbars`，island 自己画的 DOM 不在它的扫描范围里，
 * 所以每个会滚的块都得自己说一声——否则那一块露的就是系统滚动条：占一列宽度、配色跟着
 * 操作系统走，和站上别处那条 3px 的浮动滑块不是一个东西。 */
import { useEffect, useRef } from 'react';

import { attachOverlayScrollbar } from '@peach/legacy/ui';

/** 会滚的那个元素的 ref。轨道挂在它父元素上，所以那个父元素要只裹着它一个，并且自己
 *  定位（`relative`）——轨道按 `absolute right-0 top-2 bottom-2` 铺，找错定位祖先就会
 *  跑到整页的右边缘去。 */
export function useOverlayScrollbar<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  /* 每次渲染都试一次，不给依赖数组：`attachOverlayScrollbar` 自带「已挂过」标记，
     而这一块常常是内容到位的那一次渲染才出现在 DOM 里。 */
  useEffect(() => { attachOverlayScrollbar(ref.current) });
  return ref;
}
