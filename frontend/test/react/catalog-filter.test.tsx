/* 首页筛选条岛：空馆藏与首屏等待时两排、标签条摆什么。
 *
 * 点选、续页、排序、版式与详情往返要真的取数和量布局，由 `frontend/e2e/catalog-filter.test.ts` 在真浏览器里走。 */
import { describe, expect, it, vi } from 'vitest';

import { CatalogFilterPage } from '../../src/react/catalog-filter/catalog-filter-page';
import { EMPTY_SLOTS, type CatalogFilterProps, type TierPerformer } from '../../src/react/catalog-filter/catalog-filter';
import { mount, mountRoot, pending, settle } from './render';

function props(patch: Partial<CatalogFilterProps> = {}): CatalogFilterProps {
  return {
    tiers: { key: 'k', performers: [], studios: [] }, empty: false, tags: [], tagFirst: 0,
    views: [{ k: '', label: '全部', href: '/' }], state: '', count: { total: 0, shown: 0 }, trash: false,
    sorts: [], layout: null, refreshing: false, offscreen: false, combo: [], comboHidden: true, comboHost: null,
    actions: {
      openEntity: vi.fn(), setView: vi.fn(), toggleTag: vi.fn(), clearFilter: vi.fn(), clearAll: vi.fn(),
      setSort: vi.fn(), reshuffle: vi.fn(async () => {}), setLayout: vi.fn(), moreTops: vi.fn(async () => []),
    },
    helpers: { wireDrag: vi.fn(), wireScroller: vi.fn() },
    ...patch,
  };
}

const count = (host: HTMLElement, selector: string) => host.querySelectorAll(selector).length;

describe('空馆藏', () => {
  it('两排头像与标签条各铺一行静止占位，不挂忙态、不接指针也不进读屏', async () => {
    const host = await mount(<CatalogFilterPage {...props({ empty: true })} />);
    for (const kind of ['performer', 'studio']) {
      expect(count(host, `[data-catalog-placeholder="${kind}"]`)).toBe(EMPTY_SLOTS);
    }
    expect(count(host, '[data-catalog-placeholder="tag"] > span')).toBe(EMPTY_SLOTS);
    expect([...host.querySelectorAll('[data-catalog-placeholder]')].every((node) => node.getAttribute('aria-hidden') === 'true'))
      .toBe(true);
    // 空馆藏说的是「这里将来会有东西」，不是「正在取」：没有微光。
    expect(host.querySelector('[data-loading]')).toBeNull();
    expect(host.querySelector('[data-catalog-tier] [aria-busy="true"]')).toBeNull();
  });

  it('收起时两排头像照留，浮层那两排不画', async () => {
    const host = await mount(<CatalogFilterPage {...props({ empty: true, offscreen: true })} />);
    expect(count(host, '[data-catalog-placeholder="performer"]')).toBe(EMPTY_SLOTS);
    expect(host.querySelector('[data-catalog-frame]')).toBeNull();
  });
});

describe('读数', () => {
  it('取数期间是一条微光；这一趟失败时收起，不留着一直转的微光', async () => {
    const waiting = await mount(<CatalogFilterPage {...props({ count: null })} />);
    expect(count(waiting, '[data-skeleton="count"]')).toBe(1);
    document.body.innerHTML = '';
    const failed = await mount(<CatalogFilterPage {...props({ count: false })} />);
    expect(count(failed, '[data-skeleton="count"]')).toBe(0);
    expect(count(failed, '[data-count-readout]')).toBe(0);
    expect(failed.querySelector('[data-catalog-readout]')?.textContent).toBe('');
  });
});

describe('续页', () => {
  it('上一份名单要的那页还在路上时换了名单，新名单照样续页，旧的那页回来不进这一排', async () => {
    // 一排一枚铺不满一行：没溢出就一直停在右端，每次画完都要下一页。
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1000);
    vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockReturnValue(1000);
    const stale = pending<TierPerformer[]>();
    const moreTops = vi.fn<CatalogFilterProps['actions']['moreTops']>()
      .mockReturnValueOnce(stale.answer)
      .mockResolvedValueOnce([{ name: '新二', ringHtml: '' }])
      .mockResolvedValue([]);
    const tiers = (key: string, name: string) => ({ key, performers: [{ name, ringHtml: '' }], studios: [] });
    const base = props();
    const page = (key: string, name: string) =>
      <CatalogFilterPage {...base} tiers={tiers(key, name)} actions={{ ...base.actions, moreTops }} />;
    const { host, rerender } = await mountRoot(page('旧', '旧一'));
    expect(moreTops).toHaveBeenCalledTimes(1);

    await rerender(page('新', '新一'));
    await settle();
    await stale.release([{ name: '旧二', ringHtml: '' }]);
    await settle();

    const names = [...host.querySelectorAll('[data-tier-performer]')].map((node) => node.getAttribute('data-entity-name'));
    expect(names).toEqual(['新一', '新二']);
    expect(moreTops).toHaveBeenCalledTimes(3);
  });
});
