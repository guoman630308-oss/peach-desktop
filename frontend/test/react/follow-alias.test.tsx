/* 创作者别名：合并范围由勾选决定，每一次合并都等人点确认，半路断了要说清还剩几组。
 *
 * 合并是「把两个名字认成同一个人」，改的是归组口径，所以这里只测范围与确认，不测排版。 */
import { notifyManager, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import * as legacyUi from '@peach/legacy/ui';

import { queryClient } from '../../src/react/query';
import { AliasManager, type AliasManagerProps } from '../../src/react/follow-manage/alias-manager';
import {
  FOLLOW_ALIAS_URL, type AliasGroup, type AliasSuggestion,
} from '../../src/react/follow-manage/follow-manage';

import { buttonNamed, click, mountRoot, settle, type } from './render';

afterEach(() => queryClient.clear());
notifyManager.setScheduler((notify) => notify());

const GROUPS: AliasGroup[] = [
  {
    canonical_key: 'name:作者 A', canonical_name: '作者 A',
    aliases: [{ key: 'name:已存别名', name: '已存别名' }],
  },
];

const SUGGESTIONS: AliasSuggestion[] = [
  { canonical: '作者 A', alias: '别名 A', evidence: '来源 A' },
  { canonical: '作者 B', alias: '别名 B', evidence: '来源 B' },
];

type Confirmation = { title: string; body: string; items?: readonly string[]; onConfirm(): Promise<unknown> };

/** 确认框只记下参数不自己点：合并由人点这一条是这里要守住的东西。 */
function stubConfirm() {
  const asked: Confirmation[] = [];
  vi.spyOn(legacyUi, 'confirmModal').mockImplementation((async (options: Confirmation) => {
    asked.push(options);
    return { confirmed: false };
  }) as typeof legacyUi.confirmModal);
  return asked;
}

async function open(fetcher: ReturnType<typeof vi.fn>, over: Partial<AliasManagerProps> = {}) {
  vi.stubGlobal('fetch', fetcher);
  const toast = vi.fn();
  const render = (props = over) => (
    <QueryClientProvider client={queryClient}>
      <AliasManager groups={GROUPS} suggestions={SUGGESTIONS} readOnly={false} toast={toast} {...props} />
    </QueryClientProvider>);
  const mounted = await mountRoot(render());
  await settle();
  return { toast, ...mounted, renderProps: (props: Partial<AliasManagerProps>) =>
    mounted.rerender(render({ ...over, ...props })) };
}

const okFetch = () => vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ ok: true }) }));

const box = (root: ParentNode, name: string) =>
  root.querySelector<HTMLInputElement>(`input[type="checkbox"][aria-label="${name}"]`);

const aliasWrites = (fetcher: ReturnType<typeof vi.fn>) =>
  fetcher.mock.calls.filter(([input]) => String(input) === FOLLOW_ALIAS_URL)
    .map(([, init]) => JSON.parse(String((init as RequestInit).body)));

it('勾选决定合并范围，表头全选能一并取消', async () => {
  const asked = stubConfirm();
  const fetcher = okFetch();
  const { host } = await open(fetcher);

  expect(buttonNamed('合并所选（0）', host)).toBeNull();

  await click(box(host, '选择别名 别名 B，归入 作者 B'));
  expect(box(host, '全选待合并别名')?.indeterminate).toBe(true);

  await click(buttonNamed('合并所选（1）', host));
  // 还没点确认，所以一条都不该发出去。
  expect(aliasWrites(fetcher)).toHaveLength(0);
  expect(asked[0]!.body).toContain('「别名 B」归入「作者 B」');
  expect(asked[0]!.body).not.toContain('别名 A');

  await asked[0]!.onConfirm();
  await settle();
  expect(aliasWrites(fetcher).map((sent) => sent.alias)).toEqual(['别名 B']);

  await click(box(host, '全选待合并别名'));
  expect(buttonNamed('合并所选（2）', host)).not.toBeNull();
  expect(box(host, '全选待合并别名')?.checked).toBe(true);
  expect(box(host, '全选待合并别名')?.indeterminate).toBe(false);
  await click(box(host, '全选待合并别名'));
  expect(buttonNamed('合并所选（0）', host)).toBeNull();
});

it('待合并清单换序和补充后按别名保留勾选，合并使用当前规范名', async () => {
  const asked = stubConfirm();
  const fetcher = okFetch();
  const { host, renderProps } = await open(fetcher);
  await click(box(host, '选择别名 别名 B，归入 作者 B'));
  await renderProps({ suggestions: [
    { ...SUGGESTIONS[1]!, canonical: '作者 B 的规范名' },
    { canonical: '作者 C', alias: '别名 C', evidence: '来源 C' },
    SUGGESTIONS[0]!,
  ] });
  expect(box(host, '选择别名 别名 B，归入 作者 B 的规范名')?.checked).toBe(true);
  expect(box(host, '选择别名 别名 C，归入 作者 C')?.checked).toBe(false);
  expect(box(host, '全选待合并别名')?.indeterminate).toBe(true);
  await click(buttonNamed('合并所选（1）', host));
  await asked[0]!.onConfirm();
  await settle();
  expect(aliasWrites(fetcher)).toEqual([
    { action: 'add', canonical: '作者 B 的规范名', alias: '别名 B' },
  ]);
  expect(buttonNamed('合并所选（0）', host)).toBeNull();
});

it('消失的候选不计入合并范围，重新全选只处理当前清单', async () => {
  const asked = stubConfirm();
  const fetcher = okFetch();
  const { host, renderProps } = await open(fetcher);
  await click(box(host, '选择别名 别名 B，归入 作者 B'));
  await renderProps({ suggestions: [SUGGESTIONS[0]!] });
  expect(buttonNamed('合并所选（0）', host)).toBeNull();
  expect(box(host, '全选待合并别名')?.indeterminate).toBe(false);
  await click(box(host, '全选待合并别名'));
  expect(box(host, '全选待合并别名')?.checked).toBe(true);
  await click(buttonNamed('合并所选（1）', host));
  await asked[0]!.onConfirm();
  await settle();
  expect(aliasWrites(fetcher).map((sent) => sent.alias)).toEqual(['别名 A']);
});

it('已保存别名清单刷新和只读状态都更新到单元格操作', async () => {
  const asked = stubConfirm();
  const fetcher = okFetch();
  const { host, renderProps } = await open(fetcher);
  const groups = [{ ...GROUPS[0]!, canonical_name: '作者 A 的规范名',
    aliases: [{ key: 'name:新别名', name: '新别名' }] }];
  await renderProps({ groups, readOnly: true });
  const remove = () => host.querySelector<HTMLButtonElement>('button[aria-label="移除别名 新别名"]');
  expect(host.querySelector('button[aria-label="移除别名 已存别名"]')).toBeNull();
  expect(host.textContent).toContain('作者 A 的规范名');
  expect(remove()?.disabled).toBe(true);
  await renderProps({ groups, readOnly: false });
  expect(remove()?.disabled).toBe(false);
  await click(remove());
  expect(asked[0]!.body).toContain('「新别名」');
  await asked[0]!.onConfirm();
  await settle();
  expect(aliasWrites(fetcher)).toEqual([{ action: 'remove', alias: '新别名' }]);
});

it('手动保存别名提交输入并在成功后清空', async () => {
  const fetcher = okFetch();
  const { host, toast } = await open(fetcher);
  const canonical = () => host.querySelector<HTMLInputElement>('input[aria-label="规范创作者名"]');
  const alias = () => host.querySelector<HTMLInputElement>('input[aria-label="平台别名"]');
  await type(canonical(), '规范名');
  await type(alias(), '平台上的名字');
  await click(buttonNamed('保存别名', host));
  await settle();
  expect(aliasWrites(fetcher)).toEqual([{ action: 'add', canonical: '规范名', alias: '平台上的名字' }]);
  expect(canonical()?.value).toBe('');
  expect(alias()?.value).toBe('');
  expect(toast).toHaveBeenCalledWith('已保存创作者别名');
});

it('全部合并一条一条发，顺序照着待合并列表', async () => {
  const asked = stubConfirm();
  const fetcher = okFetch();
  const { toast } = await open(fetcher);

  await click(buttonNamed('全部合并（2）', document));
  expect(asked[0]!.title).toBe('合并全部创作者别名');
  await asked[0]!.onConfirm();
  await settle();

  expect(aliasWrites(fetcher)).toEqual([
    { action: 'add', canonical: '作者 A', alias: '别名 A' },
    { action: 'add', canonical: '作者 B', alias: '别名 B' },
  ]);
  expect(toast).toHaveBeenCalledWith('已合并 2 组创作者别名');
});

it('合并半路断了，报出还剩几组没有合并', async () => {
  const asked = stubConfirm();
  let call = 0;
  const fetcher = vi.fn(async () => {
    call += 1;
    if (call === 2) throw new TypeError('Failed to fetch');
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  });
  const { host, toast } = await open(fetcher);

  await click(buttonNamed('全部合并（2）', document));
  await expect(asked[0]!.onConfirm()).rejects.toThrow('还有 1 组没有合并');
  await settle();

  expect(aliasWrites(fetcher)).toHaveLength(2);
  expect(toast).not.toHaveBeenCalled();
  expect(host.textContent).toContain('还有 1 组没有合并');
});

it('移除已保存的别名也要先问一句', async () => {
  const asked = stubConfirm();
  const fetcher = okFetch();
  const { host, toast } = await open(fetcher);

  await click(host.querySelector('button[aria-label="移除别名 已存别名"]'));
  expect(aliasWrites(fetcher)).toHaveLength(0);
  expect(asked[0]!.body).toContain('「已存别名」');

  await asked[0]!.onConfirm();
  await settle();
  expect(aliasWrites(fetcher)).toEqual([{ action: 'remove', alias: '已存别名' }]);
  expect(toast).toHaveBeenCalledWith('已移除创作者别名');
});

it('别名分页保留跨页选择，已保存别名批量移除只处理所选组', async () => {
  const asked = stubConfirm();
  const fetcher = okFetch();
  const { host, renderProps } = await open(fetcher);
  const groups = Array.from({ length: 22 }, (_, index) => ({
    canonical_key: `name:${index}`, canonical_name: `创作者 ${index}`,
    aliases: [{ key: `alias:${index}`, name: `别名 ${index}` }],
  }));
  await renderProps({ groups });
  const saved = () => host.querySelector('[aria-label="已保存创作者别名"]')!;
  expect(saved().querySelectorAll('tbody tr')).toHaveLength(20);
  await click(box(host, '选择已保存别名 创作者 0'));
  await click(buttonNamed('下一页', host));
  await click(box(host, '选择已保存别名 创作者 20'));
  await click(buttonNamed('移除所选别名（2）', host));
  expect(aliasWrites(fetcher)).toEqual([]);
  expect(asked[0]!.items).toEqual(['别名 0', '别名 20']);
  await asked[0]!.onConfirm();
  await settle();
  expect(aliasWrites(fetcher)).toEqual([
    { action: 'remove', alias: '别名 0' }, { action: 'remove', alias: '别名 20' },
  ]);
  await renderProps({ groups: groups.slice(0, 2) });
  expect(saved().querySelectorAll('tbody tr')).toHaveLength(2);
  expect(host.textContent).toContain('1–2 / 2 组');
});
