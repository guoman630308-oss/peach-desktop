/* 进度条的读屏值与填充宽度都夹在 [0, max]：超额、负数和坏值不画出界，也不读成算错的数。 */
import { expect, it } from 'vitest';

import { Progress } from '../../src/react/components/progress';
import { mountRoot } from './render';

const fillOf = (bar: Element) => Number(bar.querySelectorAll('rect')[1]?.getAttribute('width'));

it('超额完成按满格读，负数与坏值按零读', async () => {
  const { host } = await mountRoot(
    <>
      <Progress label="超额" value={150} max={100} />
      <Progress label="负数" value={-5} max={100} />
      <Progress label="坏值" value={Number.NaN} max={100} />
      <Progress label="正常" value={25} max={50} />
    </>,
  );
  const bars = [...host.querySelectorAll('[role="progressbar"]')];
  expect(bars.map((bar) => bar.getAttribute('aria-valuenow'))).toEqual(['100', '0', '0', '25']);
  expect(bars.map(fillOf)).toEqual([100, 0, 0, 50]);
});

it('总量为零时不除零，满格读作 1', async () => {
  const { host } = await mountRoot(<Progress label="空" value={3} max={0} />);
  const bar = host.querySelector('[role="progressbar"]')!;
  expect(bar.getAttribute('aria-valuemax')).toBe('1');
  expect(bar.getAttribute('aria-valuenow')).toBe('1');
});
