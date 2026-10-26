import { expect, it } from 'vitest';

import { feedIdentity } from '../src/card-art/feed-identity';

const render = (item: Parameters<typeof feedIdentity>[0]) => {
  const host = document.createElement('div');
  host.innerHTML = feedIdentity(item);
  return host;
};

it('新作人物头像携带版本与取景并进入规范身份页', () => {
  const host = render({ performers: '来源名字', performer_refs: [{ id: 42, name: '规范名字', has_image: true,
    image_version: 'v7', avatar_focus: { axis: 'y', pct: 20 } }] });
  expect(host.querySelector('a')?.getAttribute('href')).toBe('/performers/42');
  expect(host.querySelector('img')?.getAttribute('src')).toBe('/entity-image?kind=performer&id=42&v=v7');
  expect(host.querySelector('img')?.style.objectPosition).toBe('50% 20%');
});

it('没有实体图的新作留首字，不请求不存在的头像', () => {
  const host = render({ performers: '甲、乙', performer_refs: [{ id: 1, name: '甲', has_image: false }] });
  expect(host.querySelector('img')).toBeNull();
  expect(host.querySelector('.ini')?.textContent).toBe('甲');
});

it('共演新作来源头像槽保留规范人物链接和作品身份', () => {
  const host = render({ code: 'EXM-001', performer_refs: [
    { id: 1, name: '甲', has_image: true },
    { id: 2, name: '乙', has_image: false, source_avatar: true, source_actor_id: 'Actor2' },
    { name: '未建档', source_avatar: true, source_actor_id: 'Actor3' },
  ] });
  const slots = [...host.querySelectorAll<HTMLElement>('[data-feed-source-avatar]')];
  expect(slots).toHaveLength(2);
  expect(slots[0]?.getAttribute('href')).toBe('/performers/2');
  expect(slots.map(slot => slot.dataset.actor)).toEqual(['Actor2', 'Actor3']);
  expect(slots.every(slot => slot.dataset.code === 'EXM-001')).toBe(true);
});

it('未建档多人署名最多叠三枚首字并转义名字', () => {
  const host = render({ performers: '<甲>、乙、丙、丁' });
  expect(host.querySelectorAll('.mav')).toHaveLength(3);
  expect(host.querySelector('.ini')?.textContent).toBe('<');
  expect(host.querySelector('a')).toBeNull();
  expect(host.querySelector('甲')).toBeNull();
  expect(render({}).children).toHaveLength(0);
});
