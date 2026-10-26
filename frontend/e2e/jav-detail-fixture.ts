import type { Page } from 'playwright-core';

export const JAV_DETAIL = {
  ok: true, code: 'EXM-001', title: '示例作品资料与长标题，完整详情保持可读', state: 'ready',
  release_date: '2026-10-30', duration: 120, score: 4.4, summary: '', director: '', publisher: '示例发行商',
  performers: '示例人物、第二位人物、待确认出演者', performer_refs: [{ id: null, name: '示例人物', gender: 'female', has_image: false, source_actor_id: 'ExampleActress', source_avatar: true },
    { id: null, name: '第二位人物', gender: 'male', has_image: false, source_actor_id: 'ExampleActor', source_avatar: true },
    { id: null, name: '待确认出演者', gender: 'unknown', has_image: false }],
  studio_refs: [{ id: null, name: '示例厂牌', has_logo: true }], series_refs: [{ id: null, name: '示例系列' }],
  tags: ['荡妇'], tag_refs: [{ name: '痴女', category: 'role', source_name: '荡妇' }],
  has_cover: false, source_image: true, preview_count: 11, comments_count: 12,
  source_url: 'https://javdb.com/v/example', wanted: false, want_id: null,
};
export async function mockJavDetail(page: Page) {
  await page.route(/\/api\/jav\/detail\?/, route => route.fulfill({ json: { ...JAV_DETAIL,
    preview_video_url: `/stream?id=${process.env.PEACH_E2E_ITEM}` } }));
  await page.route(/\/logo\?/, route => route.fulfill({ contentType: 'image/svg+xml', body:
    '<svg xmlns="http://www.w3.org/2000/svg" width="46" height="46"><rect width="46" height="46" fill="#ccc"/></svg>' }));
  await page.route(/\/api\/jav\/image\?/, route => route.fulfill({ json: { ok: true,
    image: 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="900" height="600"><rect width="900" height="600" fill="#777"/></svg>') } }));
  await page.route(/\/api\/jav\/reviews\?/, route => {
    const pageNumber = Number(new URL(route.request().url()).searchParams.get('page'));
    const count = pageNumber === 1 ? 10 : 2;
    return route.fulfill({ json: { ok: true, page: pageNumber, more: pageNumber === 1,
      items: Array.from({ length: count }, (_, index) => ({ id: `${pageNumber}:${index}`, username: '示例评论者',
        content: index === 0 ? '<img src=x onerror=alert(1)> 这是来源正文' : '示例评论正文', score: 4.4, created_at: '2026-10-12T03:56:14.000Z' })) } });
  });
  await page.route(/\/api\/jav\/resources\?/, route => route.fulfill({ json: { state: 'ready', error: '',
    checked_at: '2026-10-12T00:00:00Z', items: Array.from({ length: 6 }, (_, index) => ({
      id: `example:${index}`, protocol: 'magnet', info_hash: String(index).repeat(40),
      uri: `magnet:?xt=urn:btih:${String(index).repeat(40)}`, name: `EXM-001 示例资源 ${index + 1}` + (index === 0 ? ' · MIDA-361-UC.高清资源.very-long-filename.torrent' : ''),
      size: '3 GB', files: '2', date: '2026-10-12', attributes: ['高清'], source: 'JavDB',
      source_url: 'https://javdb.com/v/example', origins: ['JavDB'],
    })) } }));
  await page.route(/\/api\/downloads\?/, route => route.fulfill({ json: { available: false, providers: [], tasks: [] } }));
  await page.route(/\/api\/wants$/, async route => {
    if (route.request().method() !== 'POST') return route.fallback();
    return route.fulfill({ json: { created: true, want: { id: 17, code: 'EXM-001' } } });
  });
}
