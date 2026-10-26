/* 图表的悬停浮层：柱状、径向、雷达与热力图共用这一块面。
 *
 * 面、边线、阴影、字阶与一行的排法照 EvilCharts 的 `ChartTooltipContent`。那一份的容器写的是
 * `grid` 类，和旧样式表卡片网格的 `.grid` 同名：落到页面上，浮层会被撑成一排卡片宽的格子，
 * 行距也变成 24px。Tailwind 这边又不生成 `grid`（`../styles.css`），所以这里按同一套类名重组，
 * 容器换成 `flex flex-col`，Recharts 那一侧仍由上游的 `ChartTooltip` 接进图里。
 * 差异登记在 `../evilcharts/ORIGIN.md`。 */
import type { ReactNode } from 'react';
import type { TooltipContentProps } from 'recharts';

import { getPayloadConfigFromPayload, useChart } from '@/registry/ui/recharts-chart';
import { ChartTooltip } from '@/registry/ui/recharts-tooltip';

/** 浮层的面。热力图的浮层自己定位，拼在这串后面。 */
export const TIP_SURFACE =
  'flex min-w-32 flex-col gap-1.5 rounded-lg border border-border/50 bg-background px-2.5 py-1.5 text-xs shadow-xl';

/** 浮层里的一行：色点、这一组数的叫法、数。 */
export function TipRow({ swatch, name, value }: { swatch: ReactNode; name: ReactNode; value: number }) {
  return (
    <div className="flex w-full items-center gap-2">
      {swatch}
      <div className="flex flex-1 items-center justify-between gap-4 leading-none">
        <span className="text-muted-foreground">{name}</span>
        <span className="font-mono font-medium tabular-nums text-foreground">{value.toLocaleString()}</span>
      </div>
    </div>
  );
}

export const TIP_SWATCH = 'size-2.5 shrink-0 rounded-xs';

type TipProps = Partial<Pick<TooltipContentProps<number, string>, 'active' | 'payload' | 'label'>> & {
  nameKey?: string; valueKey?: string; hideLabel?: boolean;
};

function TipContent({ active, payload, label, nameKey, valueKey, hideLabel }: TipProps) {
  const { config } = useChart();
  const items = (payload ?? []).filter((item) => item.type !== 'none');
  /* 上游同款：没有内容时留一块空的占位，浮层下一次出现才不会从图的左上角滑过来。 */
  if (!active || !items.length) return <span className="p-4" />;
  const title = hideLabel || typeof label !== 'string' ? null : (config[label]?.label ?? label);
  return (
    <div className={TIP_SURFACE}>
      {title ? <div className="font-medium text-foreground">{title}</div> : null}
      {items.map((item, index) => {
        const row = (item.payload ?? {}) as Record<string, unknown>;
        const named = nameKey ? row[nameKey] : undefined;
        const key = `${named ?? item.name ?? item.dataKey ?? 'value'}`;
        const entry = getPayloadConfigFromPayload(config, item, key);
        const value = valueKey ? row[valueKey] : item.value;
        return (
          <TipRow key={key + index} name={entry?.label ?? item.name} value={Number(value ?? 0)}
            swatch={(
              // oxlint-disable-next-line shadcn/no-inline-styles -- 色点取 ChartStyle 按系列键生成的 CSS 变量，变量名随键变
              <i className={TIP_SWATCH} style={{ background: `var(--color-${key}-0)` }} />
            )} />
        );
      })}
    </div>
  );
}

/** 接进 EvilCharts 图里的浮层。`nameKey` 是径向图那种一段一个名字的数据列；`valueKey` 是
 * 画图的列经过刻度换算时，浮层该读的原始数那一列（雷达图）。 */
export function ChartTip(
  { nameKey, valueKey, hideLabel }: { nameKey?: string; valueKey?: string; hideLabel?: boolean },
) {
  return (
    <ChartTooltip cursor={false}
      content={<TipContent nameKey={nameKey} valueKey={valueKey} hideLabel={hideLabel} />} />
  );
}
