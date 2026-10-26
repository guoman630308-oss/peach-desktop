/* React 样式产物不能留下 `light-dark()` 的改写结果。
 *
 * `cssTarget` 早于原生支持 `light-dark()` 的浏览器，lightningcss 把它改写成
 * `var(--lightningcss-light,…)var(--lightningcss-dark,…)`，两个变量只由 `color-scheme` 声明给出。
 * 这份样式表里没有那一条声明，变量一直未定义，整条声明在计算值阶段失效：`box-shadow` 变成
 * none，颜色退回继承值。主题分支写成 `.dark` 祖先选择器配自定义属性，见灯箱与资料卡浮层。 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { expect, it } from 'vitest';

const css = readFileSync(resolve(process.cwd(), '../web/dist/peach-app.css'), 'utf8');

it('React 样式产物里没有依赖 color-scheme 的 lightningcss 变量', () => {
  const rules = css.match(/[^{}]*\{[^{}]*--lightningcss-(?:light|dark)[^{}]*\}/g) ?? [];
  expect(rules.map((rule) => rule.trim().slice(0, 200))).toEqual([]);
});
