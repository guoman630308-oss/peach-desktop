/* 雪碧图里的一枚字形，写法同遗留层 `icon()`（`src/core/index.ts`）：尺寸与描边由样式表按位置给。 */
import { iconGeometry } from '../../core/icon-geometry';

export function Icon({ name }: { name: string }) {
  const {wide,viewBox} = iconGeometry(name);
  return <svg viewBox={viewBox} className={wide ? 'iconwide' : undefined} aria-hidden="true"><use href={`#i-${name}`} /></svg>;
}

/* 设置分区那一排取自 Remix：它那套线条件是靠 `fill` 画出来的轮廓，不是描边。全站默认的
   `stroke:currentColor;fill:none` 会让它整枚消失，`settings-panel.css` 给左栏这一列反过来写。 */
export function RemixIcon({ name }: { name: string }) {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><use href={`#${name}`} /></svg>;
}
