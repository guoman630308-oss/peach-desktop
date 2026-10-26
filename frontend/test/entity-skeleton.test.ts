import { describe, expect, it } from 'vitest';
import { entitySkeletonHtml } from '../src/entity-skeleton';

const HEAD = '<div class="entitycollectionhead" data-filter-row="bottom"><h3 class="skeleton">&nbsp;</h3></div>';

describe('实体资料骨架', () => {
  it('事务所使用方形标识、资料头、标签和成员正文', () => {
    const html = entitySkeletonHtml('agency', HEAD, '<div class="igrid">成员</div>');
    expect(html).toContain('data-skeleton="entity/agency"');
    expect(html).toContain('entityportrait square skeleton');
    expect(html).toContain('entityhero');
    expect(html).toContain('data-entity-filter-glass');
    expect(html).toContain('<div class="entitysection"><div class="igrid">');
  });
  it('人物资料使用圆形头像', () => {
    expect(entitySkeletonHtml('performer', HEAD, '')).toContain('entityportrait skeleton');
  });
  it('筛选条和作品抬头装在同一块浮层外框里', () => {
    const html = entitySkeletonHtml('performer', HEAD, '');
    const frame = html.slice(html.indexOf('board-filter-frame'), html.indexOf('<div class="entitysection"'));
    expect(frame).toContain('data-filter-row="top"');
    expect(frame).toContain('data-filter-row="bottom"');
    // 外框只有一个，两条筛选行都在它里面，等待态和内容回来之后是同一个形状。
    expect(html.match(/board-filter-frame/g)).toHaveLength(1);
  });
});
