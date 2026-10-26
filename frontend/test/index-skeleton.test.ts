/* 索引五页的进页骨架。
 *
 * 骨架里的字形走壳 `index.html` 那张 sprite（`<use href="#i-…">`），写了表里没有的名字不会报错，
 * 只是那一格空着：页签上「艺人」「卖家」写成 `user` 时，骨架里只剩「在线」带图标。 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { expect, it } from 'vitest';

import { indexParams, indexPlaceholderHtml, type IndexPageKind } from '../src/index-skeleton';

const sprite = new Set(
  [...readFileSync(resolve(__dirname, '../../web/index.html'), 'utf8').matchAll(/<symbol id="i-([^"]+)"/g)]
    .map((match) => match[1]));

const PAGES: [IndexPageKind, string][] = [
  ['performers', ''], ['performers', '?scope=online'], ['creators', ''],
  ['studios', ''], ['agencies', ''], ['tags', ''], ['tags', '?scope=online&view=cloud'],
];

it.each(PAGES)('%s%s 骨架里的每个字形都在 sprite 里', (kind, search) => {
  for (const layout of ['big', 'compact'] as const) {
    const html = indexPlaceholderHtml(indexParams(kind, search), layout);
    const used = [...html.matchAll(/href="#i-([^"]+)"/g)].map((match) => match[1]);
    expect(used.length).toBeGreaterThan(0);
    expect(used.filter((name) => !sprite.has(name))).toEqual([]);
  }
});
