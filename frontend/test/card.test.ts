/* 共用卡面。
 *
 * 钉的是「卡片是一块填充面」这件事本身：Board 的卡从来不是描边空心壳，迁移时每页各拼一串
 * `rounded-2xl border border-separator-border`，十个文件二十处，整个管理区掉了一层底色。
 * 所以这里除了变体，还扫一遍 React 子树，拦住下一个自己拼卡面的人。 */
import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { expect, it } from 'vitest';

import { cardClass } from '../src/react/components/card';

const classes = (options?: Parameters<typeof cardClass>[0]) =>
  new Set(cardClass(options).split(' '));

it('默认那一档卡是填充的，没有描边', () => {
  const card = classes();
  expect(card).toContain('bg-background-secondary-default');
  expect(card).toContain('shadow-card');
  expect([...card].filter((name) => name.startsWith('border'))).toEqual([]);
});

it('圆角与内边距按变体给', () => {
  expect(classes()).toContain('rounded-2xl');
  expect(classes()).toContain('p-5');
  // 图表卡大一档，和旧 `.board-radial-card{border-radius:20px}` 一致。
  expect(classes({ radius: 'chart' })).toContain('rounded-2-5xl');
  // 头、体、脚各自排内边距的卡（读数卡、分区）自己不留。
  expect(classes({ padding: 'none' })).not.toContain('p-5');
});

it('描边卡是 primary 底加一条发丝线，不是透明空心壳', () => {
  const outlined = classes({ variant: 'outlined' });
  expect(outlined).toContain('bg-background-primary-default');
  expect(outlined).toContain('border-separator-border');
  expect(outlined).not.toContain('bg-background-secondary-default');
});

/* 旧 `.reviewitem` 收的是 `--surface-radius` 那一档，也没有接触阴影：阴影是给填充卡
   「浮在次级底上」用的，描边卡本来就靠线收边，再压一层影子等于两套收边办法叠着用。 */
it('描边卡自己带 14px 圆角，不跟填充卡那一档走，也不压阴影', () => {
  const outlined = classes({ variant: 'outlined' });
  expect(outlined).toContain('rounded-surface');
  expect(outlined).not.toContain('shadow-card');
  // 点名要别的圆角时仍然听调用方的。
  expect(classes({ variant: 'outlined', radius: 'chart' })).toContain('rounded-2-5xl');
});

it('卡里再浮一层是 primary 底、不画线', () => {
  const raised = classes({ variant: 'raised' });
  expect(raised).toContain('bg-background-primary-default');
  expect([...raised].filter((name) => name.startsWith('border'))).toEqual([]);
});

it('填充卡之上另加的那条描边不换底', () => {
  const fieldset = classes({ bordered: 'line' });
  expect(fieldset).toContain('bg-background-secondary-default');
  expect(fieldset).toContain('border-border-button-default');
  expect(classes({ bordered: 'soft' })).toContain('border-separator-border');
});

/* 环画在 `::after` 覆盖层上：inset 的 box-shadow 画在子元素底下，脚注带一铺底色就盖掉环的下半截。 */
it('选中态在最上层压一圈 2px 描边，收掉接触阴影，悬停不盖选中', () => {
  const tab = cardClass({ interactive: true, selectable: true });
  expect(tab).toContain('relative');
  expect(tab).toContain('data-selected:shadow-none');
  expect(tab).toContain('data-selected:after:absolute');
  expect(tab).toContain('data-selected:after:inset-0');
  expect(tab).toContain('data-selected:after:z-1');
  expect(tab).toContain('data-selected:after:rounded-[inherit]');
  expect(tab).toContain('data-selected:after:border-2');
  expect(tab).toContain('data-selected:after:border-border-focus-ring');
  expect(tab).not.toContain('ring-inset');
  // 悬停那条写成 `not-data-selected:`，不指望工具类的先后顺序。
  expect(tab).toContain('not-data-selected:hover:bg-card-hover');
  expect(tab).not.toContain(' hover:bg-card-hover');
});

/** React 子树里除 BoardUI 源码外的 `.tsx`。 */
function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(dir, entry.name);
    if (entry.isDirectory()) return entry.name === 'boardui' ? [] : sources(path);
    return entry.name.endsWith('.tsx') ? [path] : [];
  });
}

it('页面不自己拼卡面', () => {
  const offenders = sources(resolve(process.cwd(), 'src/react'))
    .filter((path) => {
      const code = readFileSync(path, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
      // 描边配上卡片那两档圆角（16px、20px）就是一张卡，卡面走 `components/card.tsx`。
      // 剩下的 `border-t` / `border-b` / `border-y` 是分隔线，`rounded-2lg` 是卡里的小块，
      // `shadow-dropdown` 是浮层——浮层本来就描边。
      return /className=\{?"[^"]*\bborder border-/.test(code)
        && /className=\{?"[^"]*\brounded-(?:2xl|2-5xl)\b/.test(code)
        && !code.includes('shadow-dropdown');
    })
    .map((path) => path.split(/[\\/]/).slice(-2).join('/'));
  expect(offenders).toEqual([]);
});
