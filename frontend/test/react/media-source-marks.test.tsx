import { expect, it, vi } from 'vitest';
import { MEDIA_SOURCE_REGISTRY } from '../../src/core/media-sources';
import { MEDIA_SOURCE_ICONS } from '../../src/ui-kit/media-source-icons';
import { SourceSelect } from '../../src/react/settings/folder-rows';
import { SourceMark } from '../../src/react/settings/section';
import { mount } from './render';

vi.mock('@peach/legacy/ui', async (original) => ({
  ...await original<Record<string, unknown>>(),
  MEDIA_SOURCE_ICONS: (await import('../../src/ui-kit/media-source-icons')).MEDIA_SOURCE_ICONS,
}));

it.each(MEDIA_SOURCE_REGISTRY)('$label 的选择项与状态使用同一来源标识', async (source) => {
  const mark = MEDIA_SOURCE_ICONS[source.id]!;
  if (['115', 'pikpak', 'guangya', 'onedrive'].includes(source.id)) {
    expect(mark).toMatch(/^data:image\/png;base64,/);
  }
  const host = await mount(<>
    <SourceSelect index={0} value={source.id} onChange={() => undefined} />
    <SourceMark mark={mark} />
  </>);
  if (mark.startsWith('data:')) {
    expect([...host.querySelectorAll('img')].map((image) => image.getAttribute('src'))).toEqual([mark, mark]);
  } else {
    expect(host.querySelectorAll(`use[href="#i-${mark}"]`)).toHaveLength(2);
  }
  expect(host.querySelector('[src*="studio="]')).toBeNull();
});
