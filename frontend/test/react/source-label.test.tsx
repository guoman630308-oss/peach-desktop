import { act } from 'react';
import { expect, it } from 'vitest';
import { SiteLabel } from '../../src/react/components/source-label';
import { mount } from './render';

it('站点标识经本站读取，未取得时保留来源名称和通用图标', async () => {
  const host = await mount(<SiteLabel site="missav" label="MISSAV 地址" />);
  const image = host.querySelector('img')!;
  expect(image.getAttribute('src')).toBe('/site-mark?domain=missav.com');
  await act(async () => { image.dispatchEvent(new Event('error')) });
  expect(host.querySelector('img')).toBeNull();
  expect(host.textContent).toContain('MISSAV 地址');
  expect(host.querySelector('svg')).not.toBeNull();
});
