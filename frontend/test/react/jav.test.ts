import { expect, it } from 'vitest';
import { discoveryArtwork, javLibraryPath, javPath, javTab, type Discovery } from '../../src/react/jav/jav';
import { discoveryKey, fetchDiscoveries } from '../../src/react/jav/jav';
import { discoveryScore, recommendationScore } from '../../src/react/jav/discovery-sorts';
import { vi } from 'vitest';

it('口味分保留零值、小数与负排序分，未取得读数不伪装成零', () => {
  expect(discoveryScore(0)).toBe('0.000');
  expect(discoveryScore(0.004)).toBe('0.004');
  expect(discoveryScore(-0.125)).toBe('-0.125');
  for (const value of [undefined, null, NaN, Infinity]) expect(discoveryScore(value)).toBe('—');
});
it('推荐标签使用固定百分制与档次，未知值和越界值有明确读数', () => {
  expect(recommendationScore(0.337)).toEqual({ score: '34', label: '值得看看', color: 'yellow' });
  for (const [input, score, label] of [[0, '0', '探索'], [-0.1, '0', '探索'], [0.5, '50', '推荐'], [0.75, '75', '强烈推荐'], [1.2, '100', '强烈推荐']] as const) {
    expect(recommendationScore(input)).toMatchObject({ score, label });
  }
  for (const value of [undefined, null, NaN, Infinity]) expect(recommendationScore(value)).toMatchObject({ score: '—', label: '资料未取得' });
});
it('发现查询隔离排序缓存，并在同一资料任务上轮询、停止与显式更新', async () => {
  const requests: URL[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    requests.push(new URL(input, 'https://peach.test'));
    return { ok: true, status: 200, json: async () => ({ items: [], candidate_total: 0, limited: false }) };
  }));
  try {
    const signal = new AbortController().signal;
    await fetchDiscoveries('external', signal, { sort: 'source_score', direction: 'asc', jobId: 'current', cancelMetadata: true });
    expect(Object.fromEntries(requests[0]!.searchParams)).toEqual({ scope: 'external', limit: '100', sort: 'source_score', direction: 'asc', metadata_job_id: 'current', metadata_cancel: '1' });
    await fetchDiscoveries('external', signal, { refreshMetadata: true });
    expect(requests[1]!.searchParams.get('metadata_refresh')).toBe('1');
    expect(requests[1]!.searchParams.has('metadata_job_id')).toBe(false);
    expect(discoveryKey('external', 'taste')).not.toEqual(discoveryKey('external', 'duration'));
  } finally { vi.unstubAllGlobals() }
});

it('发现封面优先本机缓存，远程封面保留原地大小图取景身份', () => {
  const item = { id: 1, code: 'ABC-001', has_cover: true, cover_url: 'https://example.test/cover.jpg' } as Discovery;
  const local = document.createElement('div');
  local.innerHTML = discoveryArtwork(item, 'big', 'cover');
  expect(local.querySelector('img')?.getAttribute('src')).toContain('/cover?code=ABC-001');
  local.innerHTML = discoveryArtwork({ ...item, has_cover: false }, 'small', 'cover');
  expect(local.querySelector('img')?.getAttribute('src')).toBe(item.cover_url);
  expect(local.querySelector('img')?.dataset.javImageLayout).toBe('small');
  expect(local.querySelector('img')?.dataset.javCover).toBe(item.cover_url);
});
it('JAV 分区从深链恢复，未知分区落在馆藏', () => {
  expect(javTab('discover')).toBe('discover'); expect(javTab('wants')).toBe('wants'); expect(javTab('other')).toBe('library');
});
it('JAV 馆藏深链保留搜索和筛选条件，使用目录入口', () => {
  const url = new URL(javLibraryPath('?tab=library&q=ABC-123&studio=演示厂牌&loc=cloud&sort=new&jav=0'), 'https://peach.test');
  expect(url.pathname).toBe('/');
  expect(Object.fromEntries(url.searchParams)).toEqual({ q: 'ABC-123', studio: '演示厂牌', loc: 'cloud', sort: 'new', jav: '1' });
  expect(javPath('library')).toBe('/?jav=1');
  expect(javPath('unknown')).toBe('/?jav=1');
  expect(javPath('discover')).toBe('/jav?tab=discover');
  expect(javPath('wants')).toBe('/jav?tab=wants');
});
