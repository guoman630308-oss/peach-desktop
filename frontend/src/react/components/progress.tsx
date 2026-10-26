/* 已知总量的进度条。更新下载、任务活动与统计页的覆盖率、容量共用这一份。
 *
 * 注册表里没有进度组件，用 SVG 矩形画：宽度按比例写进 `rect` 的属性，不用内联样式，
 * 也就不必给 `no-inline-styles` 开例外。差异登记在 `../boardui/ORIGIN.md`。
 *
 * 画布固定 `SCALE` 格宽，填充与刻度都先换算成它的份数；读屏值照给真实单位。按字节计的
 * 容量是万亿量级，把它直接写成 `viewBox` 的宽，Chrome 画出来的矩形是零宽。 */

/** 画布宽度：填充和刻度都换算成这么多份。 */
const SCALE = 100;
/** 一道刻度的宽度，按 `SCALE` 计。 */
const STOP_WIDTH = 0.4;

export function Progress({ label, value, max = 100, stops = [] }: { label: string; value: number; max?: number; stops?: number[] }) {
  const total = max > 0 ? max : 1;
  const clamp = (at: number) => Math.min(Math.max(Number.isFinite(at) ? at : 0, 0), total);
  const share = (at: number) => (clamp(at) / total) * SCALE;
  /* 读屏值同画面一样夹在 [0, max]：超额完成读成「150 / 100」会被当成算错。 */
  return (
    <svg role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={total} aria-valuenow={clamp(value)}
      viewBox={`0 0 ${SCALE} 1`} preserveAspectRatio="none" className="h-1.5 w-full overflow-hidden rounded-full">
      <rect width={SCALE} height={1} className="fill-background-tertiary-default" />
      <rect width={share(value)} height={1} className="fill-border-focus-ring" />
      {stops.map((stop) => <rect key={stop} x={share(stop)} width={STOP_WIDTH} height={1} className="fill-background-secondary-default" />)}
    </svg>
  );
}
