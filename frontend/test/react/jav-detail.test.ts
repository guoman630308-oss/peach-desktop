import { describe, expect, it } from 'vitest';
import { nextReviewPage, type JavReviews } from '../../src/react/jav-detail/jav-detail';
import { overlayMatch } from '../../src/history/overlay';

const page = (n: number, ids: string[], more = true): JavReviews => ({ ok: true, page: n, more,
  items: ids.map(id => ({ id, username: '示例用户', content: '示例评论', created_at: '' })) });
describe('库外作品与评论身份', () => {
  it('番号路由保持独立身份并拒绝路径片段', () => {
    expect(overlayMatch('/works/EXM-001')?.target).toEqual({ kind: 'jav', code: 'EXM-001' });
    expect(overlayMatch('/works/../../file')).toBeNull();
    expect(overlayMatch('/assets/3')?.target).toEqual({ kind: 'item', id: 3 });
  });
  it('重复页与终页结束翻页', () => {
    const first = page(1, ['1', '2']);
    expect(nextReviewPage(first, [first])).toBe(2);
    const duplicate = page(2, ['2', '1']);
    expect(nextReviewPage(duplicate, [first, duplicate])).toBeUndefined();
    const last = page(2, ['3'], false);
    expect(nextReviewPage(last, [first, last])).toBeUndefined();
  });
});
