/* 图表卡的外壳、页头与 EvilCharts 配色，统计页与口味页共用。
 *
 * 图表卡比读数卡大一档圆角：旧 `.board-radial-card`／`.board-heat-card`／`.board-sankey-card`
 * 都是 20px。页头是两列：标题压着大数在左，那句注解贴右下角对齐大数的基线，注解长短不一
 * 也不会把标题挤走。 */
import type { ReactNode } from 'react';
import { Text } from 'recharts';

import type { ChartConfig } from '@/registry/ui/recharts-chart';

import { cardClass } from '../components/card';

export const CHART_CARD = `${cardClass({ radius: 'chart' })} flex flex-col gap-4 max-sm:p-4`;

/** 一张没有可画数据的图：标题照旧，图的位置换成一句说明。统计页与口味页的图共用。 */
export function ChartEmpty({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className={CHART_CARD} aria-label={title}>
      <h3 className="text-title-2-medium text-text-primary">{title}</h3>
      <p className="text-body-2-regular text-text-secondary">{children}</p>
    </section>
  );
}

/** 量不了字宽时（没有 canvas）一个字形按多宽估，宁宽勿窄：中日文、全角符号与 emoji 按 13 算，其余按 7 算。 */
const glyphWidth = (glyph: string) => ((glyph.codePointAt(0) ?? 0) > 0x2e7f ? 13 : 7);

const graphemes = (text: string): string[] =>
  [...new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(text)].map((part) => part.segment);

/** 坐标轴文字（12px、页面字体）的量尺，第一次用到时建。取不到 2D 画布就是 `null`。 */
let ruler: CanvasRenderingContext2D | null | undefined;
function measureText(text: string): number | null {
  if (ruler === undefined) {
    try {
      ruler = document.createElement('canvas').getContext('2d');
      if (ruler) ruler.font = `12px ${getComputedStyle(document.body).fontFamily || 'sans-serif'}`;
    } catch {
      ruler = null;
    }
  }
  return ruler ? ruler.measureText(text).width : null;
}

/** 一段坐标轴文字的宽度（像素）：浏览器里实量，量不了就按字形估。 */
export const labelWidth = (text: string): number =>
  measureText(text) ?? graphemes(text).reduce((sum, glyph) => sum + glyphWidth(glyph), 0);

/** 把一个类别名截到 `room` 像素以内，截掉的部分换成省略号；放得下就原样返回。 */
export function fitLabel(text: string, room: number): string {
  if (labelWidth(text) <= room) return text;
  const glyphs = graphemes(text);
  let kept = 0;
  while (kept < glyphs.length && labelWidth(`${glyphs.slice(0, kept + 1).join('')}…`) <= room) kept += 1;
  return `${glyphs.slice(0, kept).join('')}…`;
}

/** 横向柱状图类别轴的宽度：跟着最长的名字走，但不超过 `cap`，柱子不被名字挤没。 */
export const categoryAxisWidth = (names: string[], cap = 104): number =>
  Math.min(cap, Math.max(24, ...names.map(labelWidth)) + 12);

/** 类别轴上的一个名字。Recharts 把刻度的坐标、轴宽与可见刻度数交进来：竖柱按每根柱分到的
 *  宽度截断，横条按轴宽截断。全名在 `<title>` 里，悬停浮层读的也是全名。 */
export function CategoryTick(
  props: {
    along: 'x' | 'y'; payload?: { value?: unknown }; width?: number; visibleTicksCount?: number;
  } & Record<string, unknown>,
) {
  const { along, payload, width = 0, visibleTicksCount = 1, ...rest } = props;
  const name = String(payload?.value ?? '');
  // 竖柱的名字在一根柱分到的那一格里居中，左右各留 1px；横条的名字与条之间隔着 8px 的 tickMargin。
  const room = along === 'x' ? width / Math.max(1, visibleTicksCount) - 2 : width - 8;
  return (
    <g>
      <title>{name}</title>
      <Text {...(rest as object)}>{fitLabel(name, room)}</Text>
    </g>
  );
}

/** 一张图的头：一个标题、一个大数、一句它此刻指的是什么。 */
export function ChartHead({ title, figure, note }: { title: string; figure: string; note: string }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-x-4 gap-y-1">
      <span className="flex min-w-0 flex-col gap-1">
        <h3 className="text-title-2-medium text-text-primary">{title}</h3>
        <b className="text-display-4-medium tabular-nums text-text-primary">{figure}</b>
      </span>
      <small className="min-w-0 text-caption-1-regular break-words text-text-secondary">{note}</small>
    </header>
  );
}

/** BoardUI 的六档图表色，`ChartConfig` 里只写这几个变量，深浅两档跟着 token 走。 */
export const CHART_COLORS = [
  'var(--color-chart-1)', 'var(--color-chart-2)', 'var(--color-chart-3)',
  'var(--color-chart-4)', 'var(--color-chart-5)', 'var(--color-chart-6)',
] as const;

/** 一种颜色的 EvilCharts 配色。 */
export const tone = (color: string): ChartConfig[string]['colors'] => ({ light: [color] });

/** 分类图（径向、每段一色）的键。
 *
 * EvilCharts 拿键拼 CSS 变量名和 SVG 渐变 id，媒体库名、来源名里的空格与中文进不了标识符，
 * 所以键一律是 `s0`、`s1`…，显示的名字放在 `label` 里。 */
export const sliceKey = (index: number) => `s${index}`;

export function sliceConfig(names: string[]): ChartConfig {
  return Object.fromEntries(names.map((label, index) => [
    sliceKey(index), { label, colors: tone(CHART_COLORS[index % CHART_COLORS.length]!) },
  ]));
}
