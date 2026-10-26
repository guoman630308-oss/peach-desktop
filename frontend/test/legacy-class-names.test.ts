/* React 样式产物里的类名不能和旧样式表的类选择器同名。
 *
 * 两份样式表都不分层，`board.css` 与 `/app.css` 排在 `peach-app.css` 后面，同名类按先后由旧规则
 * 胜出：卡片网格的 `.grid` 会把 `grid-cols-7` 压成一列、把 `gap-1` 撑到 16px。反过来，生成出来的
 * `.ring` 也会落到旧页面的 `.ring` 元素上。React 侧真要用的工具类换一种写法（flex 父元素里的网格
 * 容器写 `inline-grid`）；注释里的英文词被扫成工具类的，在 `styles.css` 用 `@source not inline` 排除。 */
import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { expect, it } from 'vitest';

const web = resolve(process.cwd(), '../web');
const read = (...path: string[]) => readFileSync(resolve(web, ...path), 'utf8');

/** 样式表里类选择器用到的类名，转义字符还原。 */
function classNames(css: string): Set<string> {
  const names = new Set<string>();
  const selectors = css.replace(/\/\*[\s\S]*?\*\//g, '').match(/[^{};]+(?=\{)/g) ?? [];
  for (const selector of selectors) {
    if (selector.trim().startsWith('@')) continue;
    for (const [, name = ''] of selector.matchAll(/\.((?:[A-Za-z_-]|\\.)(?:[\w-]|\\.)*)/g)) {
      names.add(name.replace(/\\(.)/g, '$1'));
    }
  }
  return names;
}

it('React 样式产物的类名不和旧样式表同名', () => {
  const legacy = new Set([
    ...classNames(read('board.css')),
    ...readdirSync(resolve(web, 'css')).filter((name) => name.endsWith('.css'))
      .flatMap((name) => [...classNames(read('css', name))]),
  ]);
  /* 这两枚是两边约好的名字，同名正是契约：`.peach-react` 让旧样式表把 React 子树排除在
     全局焦点环之外；`.dark` 是 `src/appearance/theme.ts` 的 `applyTheme` 挂在 <html> 上的主题标记，旧
     样式表按它给浅色单独换面，React 侧 Tailwind 的 dark 变体读的也是它。 */
  const agreed = new Set(['peach-react', 'dark']);
  /* `video-js` 与 `vjs-*` 是 Video.js 的组件契约：类由它自己的组件挂上，播放器样式
     （`src/player/player.css`、沉浸岛的 `immerse.css`）只能按这些类写；旧样式表里的同名规则选中的
     是同一种元素，同名正是契约。 */
  const videojs = (name: string) => name === 'video-js' || name.startsWith('vjs-');
  const shared = [...classNames(read('dist', 'peach-app.css'))]
    .filter((name) => !agreed.has(name) && !videojs(name) && legacy.has(name));
  expect(shared).toEqual([]);
});
