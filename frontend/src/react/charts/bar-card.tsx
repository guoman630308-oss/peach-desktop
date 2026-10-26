/* 分布柱状图：一类一根柱子，画的是 EvilCharts 的 `EvilBarChart`。
 *
 * 页头的大数是各类合计。每根柱子的数标在柱端，数值轴不画：几类之间差一两个数量级时，
 * 短柱子从刻度上读不出数。柱子由 Motion 逐根长出来，系统开了减弱动态效果时直接画终态
 * （`../entry.tsx` 的 `MotionConfig`）；悬停浮层是 `./chart-tip.tsx`。 */
import { EvilBarChart } from '@/registry/charts/recharts-bar-chart';

import { CategoryTick, categoryAxisWidth, CHART_CARD, ChartEmpty, ChartHead, tone } from './chart-card';
import { ChartTip } from './chart-tip';

/** 一根柱子：显示的名字和它的数。写成类型别名，EvilCharts 的 `data` 要能当 `Record` 收。 */
export type BarRow = { name: string; value: number };

/** 数值轴留两成余量，最长那根柱子端上的数不被图框切掉。 */
export const BAR_DOMAIN: [number, (max: number) => number] = [0, (max) => max * 1.2];

/** 横条右侧给柱端那个数留的位置（像素）：最长那一条的数落在这一截里，不被图框切掉。 */
export const BAR_LABEL_ROOM = 56;

/** 柱端的数：十万以上读成「万」「亿」，柱端那一小截放得下；全数在悬停浮层里。 */
export function shortCount(value: number): string {
  const abs = Math.abs(value);
  const scaled = (unit: number, name: string) => {
    const figure = value / unit;
    return `${figure.toLocaleString(undefined, { maximumFractionDigits: Math.abs(figure) < 100 ? 1 : 0 })}${name}`;
  };
  if (abs >= 1e8) return scaled(1e8, '亿');
  if (abs >= 1e5) return scaled(1e4, '万');
  return value.toLocaleString();
}

/** 柱端的数。`position` 跟着柱子的走向：竖柱标在顶上，横条标在右端。 */
export const barLabel = (position: 'top' | 'right') => ({
  position,
  className: 'fill-text-secondary',
  formatter: (value: unknown) => shortCount(Number(value)),
});

/** 浮层那一行的名字：`series` 是这一组数的叫法（「视频」「作品」），不是单位。
 *  一根柱子都没有数时，卡片照留，图的位置换成 `empty` 那句话。 */
export function BarCard(
  { title, unit, series, rows, color, empty, layout = 'vertical' }:
  {
    title: string; unit: string; series: string; rows: BarRow[]; color: string; empty: string;
    layout?: 'vertical' | 'horizontal';
  },
) {
  const data = rows
    .filter((row) => Number.isFinite(row.value) && row.value >= 0)
    .map((row) => ({ name: row.name, value: row.value }));
  const total = data.reduce((sum, row) => sum + row.value, 0);
  if (!total) return <ChartEmpty title={title}>{empty}</ChartEmpty>;
  const horizontal = layout === 'horizontal';
  /* 图的容器自带 `flex-1`，放进卡片这一列 flex 里会按 0 起算、把 `h-*` 压成 0 高，换成 `flex-none`。
     类别轴 `interval={0}`：每根柱子都要有名字，Recharts 默认会把挤在一起的名字隔一个藏掉；
     名字放不下就由 `CategoryTick` 截断。 */
  return (
    <section className={CHART_CARD} aria-label={title}>
      <ChartHead title={title} figure={total.toLocaleString()} note={unit} />
      <EvilBarChart data={data} config={{ value: { label: series, colors: tone(color) } }}
        layout={layout} barRadius={4}
        chartProps={horizontal ? { margin: { top: 5, right: BAR_LABEL_ROOM, bottom: 5, left: 5 } } : undefined}
        className={horizontal
          ? 'aspect-auto h-64 flex-none text-text-secondary'
          : 'aspect-auto h-56 flex-none text-text-secondary'}>
        <EvilBarChart.Grid />
        {horizontal
          ? <EvilBarChart.YAxis dataKey="name" interval={0} tick={<CategoryTick along="y" />}
              width={categoryAxisWidth(data.map((row) => row.name))} />
          : <EvilBarChart.XAxis dataKey="name" interval={0} tick={<CategoryTick along="x" />} />}
        {horizontal
          ? <EvilBarChart.XAxis hide domain={BAR_DOMAIN} />
          : <EvilBarChart.YAxis hide domain={BAR_DOMAIN} />}
        <EvilBarChart.Bar dataKey="value" enableHoverHighlight
          barProps={{ dataKey: 'value', label: barLabel(horizontal ? 'right' : 'top') }} />
        <ChartTip />
      </EvilBarChart>
    </section>
  );
}
