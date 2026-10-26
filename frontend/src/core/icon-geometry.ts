/* 字形的宽高比属于图标本身；导航、骨架与正文共用，控件仍各自决定图标高度。 */
const WIDE: Readonly<Record<string, number>> = { 'text-aa': 1.435, 'mark-javdb': 326 / 111 };

export function iconGeometry(name: string) {
  const ratio = WIDE[name];
  return { wide: Boolean(ratio), viewBox: ratio ? `0 0 ${(24 * ratio).toFixed(2)} 24` : '0 0 24 24' };
}
