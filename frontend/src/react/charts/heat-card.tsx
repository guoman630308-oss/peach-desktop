/* 热力图：统计页的播放时间与口味页的浏览时间共用。
 *
 * Recharts 没有热力图，EvilCharts 的 Recharts 分支也没有，所以格子仍是自绘的 SVG：一格一个
 * `rect`，浓度是 `fill-opacity`，几何全写成属性。外观对齐同页的 EvilCharts 图：颜色只取
 * `chart-*`，指到一格时其余格子淡下去，浮层与柱状、径向图共用 `./chart-tip.tsx` 那一块面；
 * 页头读数也跟着换，键盘聚焦一格时读的是它。取舍见 ADR-0076。 */
import { useState } from 'react';
import type { ReactNode } from 'react';

import { CHART_CARD, ChartEmpty, ChartHead } from './chart-card';
import { TIP_SURFACE, TIP_SWATCH, TipRow } from './chart-tip';
import { dayCalendar, hourGrid, WEEKDAYS, type ActivityCounts, type HeatCell } from './heat';

const SIZE = 14;
const STEP = 17;

/** 格子与浮层色点的颜色。类名写成整串，Tailwind 扫得到。 */
const TONES = {
  4: { fill: 'fill-chart-4', dot: 'bg-chart-4' },
  5: { fill: 'fill-chart-5', dot: 'bg-chart-5' },
} as const;
export type HeatTone = keyof typeof TONES;

/** 浮层靠哪一边对齐指到的那一格：两侧各留一成宽，靠边的格子浮层往里放，不被卡片切掉。 */
const ANCHOR = {
  start: 'translate-x-0',
  center: '-translate-x-1/2',
  end: '-translate-x-full',
} as const;

function HeatTooltip(
  { cell, series, tone, left, top, anchor }:
  { cell: HeatCell; series: string; tone: HeatTone; left: number; top: number; anchor: keyof typeof ANCHOR },
) {
  return (
    <div aria-hidden
      // oxlint-disable-next-line shadcn/no-inline-styles -- 浮层跟着指到的那一格走，位置是运行期算出的百分比
      style={{ left: `${left}%`, top: `${top}%` }}
      className={`pointer-events-none absolute z-10 -mt-2 -translate-y-full ${TIP_SURFACE} ${ANCHOR[anchor]}`}>
      <div className="font-medium text-foreground">{cell.label}</div>
      <TipRow name={series} value={cell.count} swatch={<i className={`${TIP_SWATCH} ${TONES[tone].dot}`} />} />
    </div>
  );
}

/** 读数的三处文字：页头大数旁的单位、浮层那一行的名字、读屏时每一格计数后面的量词。 */
export interface HeatWords { unit: string; series: string; cellUnit: string }

/** 一张热力图：一格一个计数，指到哪一格读数就换成哪一格。 */
export function HeatCard(
  { title, words, cells, total, columns, rows, axis, footer, tone }:
  {
    title: string; words: HeatWords; cells: HeatCell[]; total: number;
    columns: number; rows: number; axis?: ReactNode; footer: ReactNode; tone: HeatTone;
  },
) {
  const [shown, setShown] = useState(-1);
  const left = axis ? 30 : 0;
  const top = axis ? 18 : 0;
  const width = left + columns * STEP;
  const height = top + rows * STEP;
  const at = (index: number) => ({
    x: left + (index % columns) * STEP, y: top + Math.floor(index / columns) * STEP,
  });
  const cell = cells[shown];
  const pin = cell ? at(shown) : null;
  const share = pin ? (pin.x + SIZE / 2) / width : 0;
  const anchor = share < 0.1 ? 'start' : share > 0.9 ? 'end' : 'center';
  const edge = pin ? { start: pin.x, center: pin.x + SIZE / 2, end: pin.x + SIZE }[anchor] : 0;
  return (
    <section className={CHART_CARD}>
      <ChartHead title={title} figure={(cell ? cell.count : total).toLocaleString()}
        note={cell ? cell.label : words.unit} />
      {/* 带坐标轴的星期 × 小时有 24 列，压到 320 以下格子和字都读不清，宁可在卡里横滚；
          每日那张只有 13 列，跟着卡片缩，最近几天不会藏到右边看不见的地方。 */}
      <div className="relative">
        <div className="min-w-0 overflow-x-auto">
          <svg role="img" aria-label={title} onPointerLeave={() => setShown(-1)}
            viewBox={`0 0 ${width} ${height}`} className={axis ? 'block h-auto min-w-80 w-full' : 'block h-auto w-full'}>
            {axis}
            {cells.map((item, index) => {
              const { x, y } = at(index);
              return (
                <rect key={item.key} tabIndex={0} role="img" aria-label={`${item.label}，${item.count} ${words.cellUnit}`}
                  x={x} y={y} width={SIZE} height={SIZE} rx={3} fillOpacity={item.share || 1}
                  opacity={shown >= 0 && shown !== index ? 0.4 : 1}
                  className={item.share
                    ? `outline-none transition-opacity duration-200 focus-visible:stroke-border-focus-ring ${TONES[tone].fill}`
                    : 'fill-chart-track outline-none transition-opacity duration-200 focus-visible:stroke-border-focus-ring'}
                  onPointerEnter={() => setShown(index)}
                  onFocus={() => setShown(index)} onBlur={() => setShown(-1)} />
              );
            })}
          </svg>
        </div>
        {cell && pin ? (
          <HeatTooltip cell={cell} series={words.series} tone={tone} anchor={anchor}
            left={edge / width * 100} top={pin.y / height * 100} />
        ) : null}
      </div>
      <footer className="flex flex-wrap justify-between gap-3 text-caption-1-regular text-text-secondary">
        {footer}
      </footer>
    </section>
  );
}

/** 星期 × 小时的坐标：左边一列星期，顶上每隔两小时一个钟点。 */
const HOUR_AXIS = (
  <>
    {WEEKDAYS.map((name, day) => (
      <text key={name} x={0} y={18 + day * STEP + 7} dominantBaseline="middle"
        className="fill-text-secondary text-caption-1-regular">{name}</text>
    ))}
    {Array.from({ length: 24 }, (_, hour) => (hour % 2 ? null : (
      <text key={hour} x={30 + hour * STEP} y={8} dominantBaseline="middle"
        className="fill-text-secondary text-caption-1-regular">{hour}</text>
    )))}
  </>
);

/** 一份活跃计数的两张热力图：星期 × 小时，和最近 91 天。
 *
 * 没有可用记录时留一张只有 `empty` 那句话的卡。 */
export function ActivityHeat(
  { activity, title, dailyTitle, words, tone, empty }:
  {
    activity: ActivityCounts | undefined; title: string; dailyTitle: string;
    words: HeatWords; tone: HeatTone; empty: string;
  },
) {
  const hours = hourGrid(activity);
  const days = dayCalendar(activity);
  if (!days.cells.length) return <ChartEmpty title={title}>{empty}</ChartEmpty>;
  /* 两张卡按各自内容的高度排：星期 × 小时有 24 列，格子比每日那张小一半，同行拉成等高
     会在它下面空出半张卡。 */
  return (
    <div className="inline-grid w-full items-start gap-5 lg:grid-cols-2">
      <HeatCard title={title} words={words} tone={tone}
        cells={hours.cells} total={hours.total} columns={24} rows={7} axis={HOUR_AXIS}
        footer={<><span>星期 × 小时</span><span>{activity?.timezone || 'UTC+08:00'}</span></>} />
      <HeatCard title={dailyTitle} words={words} tone={tone}
        cells={days.cells} total={days.total} columns={13} rows={7}
        footer={<><span>{days.start}</span><span>{days.end}</span></>} />
    </div>
  );
}
