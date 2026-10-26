/* 口味页的三张图：口味维度的雷达与排行条、创作者线索的流向图。浏览活跃的两张热力图与
 * 统计页共用 `../charts/heat-card.tsx`。
 *
 * 雷达与排行条由 EvilCharts 画，悬停浮层是 `../charts/chart-tip.tsx`，差异登记在
 * `../evilcharts/ORIGIN.md`。雷达的半径是平方根刻度，浮层仍读原始次数。流向图保留 `d3-sankey` 自绘（ADR-0076）：几何全写成属性，流的粗细
 * 是 `stroke-width`，颜色只取 BoardUI 的 `chart-*` 档；指向一条流或一个节点时读数换成它的，
 * 换的是 React 状态，指针离开或焦点移走就回到总数。差异登记在 `../boardui/ORIGIN.md`。 */
import { useState } from 'react';
import { Text } from 'recharts';

import { EvilBarChart } from '@/registry/charts/recharts-bar-chart';
import { EvilRadarChart } from '@/registry/charts/recharts-radar-chart';

import { BAR_DOMAIN, BAR_LABEL_ROOM, barLabel } from '../charts/bar-card';
import {
  CategoryTick, categoryAxisWidth, CHART_CARD, CHART_COLORS, ChartHead, fitLabel, tone,
} from '../charts/chart-card';
import { ChartTip } from '../charts/chart-tip';
import {
  flowGraph, flowLabel, FLOW_LABEL_X, FLOW_NODE_WIDTH, FLOW_VIEWBOX, radarRows, RANK_MAX, topScores,
  type CreatorFlow, type RankRow,
} from './taste';

/** 雷达顶点外侧的维度名。左右两侧的名字往外排，能用的宽度是顶点到图框边；上下两个居中排，
 *  能用的是顶点到较近那条边的两倍。放不下就截断，全名在 `<title>`、浮层与读屏名称里。 */
function RadarTick(
  props: { x?: number; cx?: number; textAnchor?: string; payload?: { value?: unknown } } & Record<string, unknown>,
) {
  const { x = 0, cx, textAnchor, payload, ...rest } = props;
  const name = String(payload?.value ?? '');
  const width = typeof cx === 'number' ? cx * 2 : 0;
  const room = !width ? 56
    : textAnchor === 'start' ? width - x
    : textAnchor === 'end' ? x
    : 2 * Math.min(x, width - x);
  return (
    <g>
      <title>{name}</title>
      <Text {...(rest as object)} x={x} textAnchor={textAnchor as 'start' | 'middle' | 'end' | undefined}
        fill="currentColor" fontSize={12}>{fitLabel(name, room - 4)}</Text>
    </g>
  );
}

/** 六档图表色循环。类名写成整串，Tailwind 扫得到。 */
const FLOW_STROKE = [
  'stroke-chart-1', 'stroke-chart-2', 'stroke-chart-3',
  'stroke-chart-4', 'stroke-chart-5', 'stroke-chart-6',
];
const FLOW_FILL = [
  'fill-chart-1', 'fill-chart-2', 'fill-chart-3',
  'fill-chart-4', 'fill-chart-5', 'fill-chart-6',
];

/** 口味维度两张图的系列：一个维度下命中的标签次数。颜色跟着口味页读数卡的 `chart-4`。 */
const DIMENSION_CONFIG = { value: { label: '标签命中', colors: tone(CHART_COLORS[3]) } };

/** 排行条的最低高度按条数给，一条一格 32：窄屏里排行条独占一行、或维度不足三个画不出雷达时，
 *  它就是这么高。旁边有雷达时整块跟着雷达那一栏拉到等高，多出来的高度拉开条与条的间距。
 *  类名写成整串，Tailwind 扫得到。 */
const RANK_HEIGHT = ['min-h-24', 'min-h-24', 'min-h-24', 'min-h-32', 'min-h-40', 'min-h-48', 'min-h-52', 'min-h-60', 'min-h-64'];

/** 一条柱的粗细上限，与一格 32 里默认留出的粗细相同：图拉高时条不跟着变粗。 */
const RANK_BAR_SIZE = 26;

/** 雷达图画的是 `radius` 列，名字与颜色跟口味维度的另外两张图一致。 */
const RADAR_CONFIG = { radius: DIMENSION_CONFIG.value };

/** 雷达图的半径刻度，也是这张图唯一一处换算：`√(次数 / 最大次数)`，最大的维度落在外圈（1）。
 * 头一个维度常是其余的十倍以上，线性半径会把面压成一根尖刺，读不出次要维度的形状；开方后
 * 它们才撑得开。原始次数留在 `value` 列，浮层读它，数值本身由旁边的排行条给。 */
const radarRadius = (value: number, top: number) => Math.sqrt(value / top);

/** 主要口味维度的雷达图，画的是 EvilCharts 的 `EvilRadarChart`。三个维度以下画不成面，那时整块不出现。 */
export function TasteRadar({ rows, label }: { rows: RankRow[]; label: string }) {
  const points = radarRows(rows);
  if (!points.length) return null;
  const top = points[0]!.value;
  const data = points.map((row) => ({ ...row, radius: radarRadius(row.value, top) }));
  return (
    <div role="img" aria-label={`${label}：${data.map((row) => row.name).join('，')}`}>
      {/* 半径压到六成：维度名排在顶点外侧，七八个字的名字在窄栏里要放得下，不被图框切掉。 */}
      <EvilRadarChart data={data} config={RADAR_CONFIG} chartProps={{ outerRadius: '58%' }}
        className="aspect-auto h-70 text-text-secondary">
        <EvilRadarChart.PolarGrid />
        <EvilRadarChart.PolarAngleAxis dataKey="name" tick={<RadarTick />} />
        {/* 半径轴定死在 0 到 1：外圈就是最大的维度，不让 Recharts 往上取整留出空圈。网格圈跟着
            这根轴的刻度走，Recharts 默认只取整数刻度，要放开小数才有四等分的圈。 */}
        <EvilRadarChart.PolarRadiusAxis domain={[0, 1]} allowDecimals tick={false} />
        <EvilRadarChart.Radar dataKey="radius" />
        <ChartTip valueKey="value" />
      </EvilRadarChart>
    </div>
  );
}

/** 口味维度排行：一个维度一条横向的柱，画的是 EvilCharts 的 `EvilBarChart`，数标在柱尾。
 *  整块在纵向 flex 里撑满父级给的高度，图表容器本身是 `flex-1`。维度名那一栏有宽度上限，
 *  名字再长也只截断，不把柱子挤没。 */
export function RankedBars({ rows, label }: { rows: RankRow[]; label: string }) {
  const data = topScores(rows, RANK_MAX);
  if (!data.length) return null;
  return (
    <section aria-label={label} className={`flex grow flex-col ${RANK_HEIGHT[data.length]}`}>
      <EvilBarChart data={data} config={DIMENSION_CONFIG} layout="horizontal" barRadius={4}
        chartProps={{ maxBarSize: RANK_BAR_SIZE, margin: { top: 5, right: BAR_LABEL_ROOM, bottom: 5, left: 5 } }}
        className="aspect-auto text-text-secondary">
        <EvilBarChart.YAxis dataKey="name" interval={0} tick={<CategoryTick along="y" />}
          width={categoryAxisWidth(data.map((row) => row.name))} />
        <EvilBarChart.XAxis hide domain={BAR_DOMAIN} />
        <EvilBarChart.Bar dataKey="value" enableHoverHighlight
          barProps={{ dataKey: 'value', label: barLabel('right') }} />
        <ChartTip />
      </EvilBarChart>
    </section>
  );
}

/** 创作者线索的来源流向。指一条流或一个节点，读数换成它。 */
export function CreatorSankey({ flows }: { flows: CreatorFlow[] | undefined }) {
  const [shown, setShown] = useState<{ value: number; label: string; node: number | null } | null>(null);
  const graph = flowGraph(flows);
  if (!graph) return null;
  const node = shown?.node ?? null;
  /** 这条流此刻要不要淡下去。指的是节点时留下它两端的流，指的是流时只留它自己。 */
  const linkLit = (link: typeof graph.links[number], key: string) =>
    (!shown ? true
      : node !== null ? link.source === node || link.target === node
      : shown.label === key);
  return (
    <section className={CHART_CARD}>
      <ChartHead title="创作者线索来源" figure={(shown ? shown.value : graph.total).toLocaleString()}
        note={shown ? shown.label : '条线索'} />
      {/* 两侧各留一截给节点名，画布窄于 640 时字缩到读不清：手机上换成按线索数排的一列流向。 */}
      <ol aria-label="来源网站与创作者线索" className="flex flex-col sm:hidden">
        {[...graph.links].sort((a, b) => b.value - a.value).map((link) => (
          <li key={link.key}
            className="flex items-baseline justify-between gap-3 border-b border-separator-border py-2 text-body-2-regular text-text-primary last:border-b-0">
            <span className="min-w-0 wrap-anywhere">{link.label}</span>
            <b className="shrink-0 tabular-nums">{link.value.toLocaleString()}</b>
          </li>
        ))}
      </ol>
      <div className="min-w-0 max-sm:hidden">
        <svg viewBox={`0 0 ${FLOW_VIEWBOX.width} ${graph.height}`} fill="none"
          role="img" aria-label="来源网站与创作者线索"
          className="block h-auto w-full" onPointerLeave={() => setShown(null)}>
          <g>
            {graph.links.map((link) => (
              <path key={link.key} d={link.d} strokeWidth={link.width} tabIndex={0} role="img"
                aria-label={`${link.label}：${link.value} 条线索`}
                fillOpacity={0} strokeOpacity={linkLit(link, link.label) ? 0.55 : 0.08}
                className={`outline-none transition-opacity ${FLOW_STROKE[link.color]}`}
                onPointerEnter={() => setShown({ value: link.value, label: link.label, node: null })}
                onFocus={() => setShown({ value: link.value, label: link.label, node: null })}
                onBlur={() => setShown(null)} />
            ))}
          </g>
          <g>
            {graph.nodes.map((item) => (
              <g key={item.id} tabIndex={0} role="img"
                aria-label={`${item.name}：${item.value} 条线索`}
                className="outline-none focus-visible:opacity-100"
                onPointerEnter={() => setShown(
                  { value: item.value, label: item.name, node: item.index })}
                onFocus={() => setShown({ value: item.value, label: item.name, node: item.index })}
                onBlur={() => setShown(null)}>
                <rect x={item.x} y={item.y} width={FLOW_NODE_WIDTH} height={item.height} rx={5}
                  className={item.side === 'source' ? FLOW_FILL[item.color] : 'fill-text-secondary'} />
                <text x={FLOW_LABEL_X[item.side]} y={item.y + item.height / 2}
                  textAnchor={item.side === 'source' ? 'end' : 'start'} dominantBaseline="middle"
                  className="fill-text-secondary text-caption-1-regular">
                  {flowLabel(item.name)}
                  <tspan x={FLOW_LABEL_X[item.side]} dy={15}>
                    {item.side === 'source'
                      ? item.value.toLocaleString()
                      : `${item.share.toFixed(1)}%`}
                  </tspan>
                </text>
              </g>
            ))}
          </g>
        </svg>
      </div>
      <footer className="flex justify-between gap-3 text-caption-1-regular text-text-secondary max-sm:hidden">
        <span>来源网站</span><span>创作者 · 线索占比</span>
      </footer>
    </section>
  );
}
