/** 数值偏好的读回判据：整数且落在区间里才算数，否则退回 `fallback`。
 *  壳在启动时归一化 `appSettings` 用它，设置面板的数值控件（`react/settings-panel/number-setting.tsx`）也用它。 */
export function boundedPreference(value: number, min: number, max: number, fallback: number): number {
  return Number.isInteger(value) && value >= min && value <= max ? value : fallback;
}
