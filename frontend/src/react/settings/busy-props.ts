/* 等待期按钮的属性。单独成一个模块：首启页的独立页面包要用它，却不该连带 `use-action` 背后的请求封装。 */

/** 等待期的按钮：写 `aria-busy` 与 `aria-disabled`、保持可聚焦，不用原生 `disabled`。 */
export function busyProps(busy: boolean) {
  return busy ? { 'aria-busy': true, 'aria-disabled': true } as const : {};
}
