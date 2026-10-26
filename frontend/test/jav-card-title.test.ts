import { expect, it } from 'vitest';
import { javTitleHtml } from '../src/core/jav-title';

it('已确认的版本标签与 4K 位于标题，分辨率未取得时不添加画质标记', () => {
  const host = document.createElement('div');
  const item = { is_jav: true, code: 'EXM-001', catalog_title: '示例作品', edition_badges: ['中字', '无码'] };
  host.innerHTML = javTitleHtml({ ...item, width: 3840, height: 2160 });
  expect([...host.querySelectorAll('.javedition')].map(node => node.textContent)).toEqual(['中字', '无码', '4K']);
  expect(host.querySelector('.ui-javtitle')?.textContent).toBe('示例作品');
  for (const dimensions of [{}, { width: 1920, height: 1080 }, { width: 3840, height: 1080 }]) {
    host.innerHTML = javTitleHtml({ ...item, ...dimensions });
    expect(host.querySelector('.quality')).toBeNull();
  }
});
