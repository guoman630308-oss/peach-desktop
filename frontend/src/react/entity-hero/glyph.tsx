/* 雪碧图（`web/index.html` 的 `#i-*` symbol）里的一枚字形，写法同遗留层 `icon()`：尺寸与描边
 * 由 `./entity-hero.css` 按位置给。横向的站点标识（JavDB 那枚是 326:111）viewBox 跟着 symbol
 * 的比例走，槽位按高定宽。 */
import { iconGeometry } from '../../core/icon-geometry';

export function Glyph({ name }: { name: string }) {
  const {wide,viewBox} = iconGeometry(name);
  return (
    <svg viewBox={viewBox} aria-hidden="true"
      data-glyph={wide ? 'wide' : undefined}>
      <use href={`#i-${name}`} />
    </svg>
  );
}
