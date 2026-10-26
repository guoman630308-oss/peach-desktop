/* 悬停一叠卡片时逐张翻走：时序、在途请求的丢弃、坏图出队与离开即停。
 *
 * `Image` 换成可控的替身：赋 `src` 之后由用例决定这一张是解码好了还是坏了，量的是钩子
 * 自己的计时器与状态，不是浏览器的解码。 */
import { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { MIX_FLIP_LEAD_MS, MIX_FLIP_MS, useStackFlip, type StackFlip } from '../../src/react/components/use-stack-flip';
import { mount } from './render';

/** 指针停多久才开始，同钩子里的 `MIX_FLIP_ARM_MS`。 */
const ARM_MS = 340;

let decoders: Array<{ url: string; resolve(): void; reject(): void }> = [];
let autoDecode: ((url: string) => boolean) | null = null;

class FakeImage {
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  naturalWidth = 320;
  referrerPolicy = '';
  #src = '';
  get src() { return this.#src }
  set src(url: string) {
    this.#src = url;
    queueMicrotask(() => this.onload?.());
  }
  decode(): Promise<void> {
    const url = this.#src;
    if (autoDecode) return autoDecode(url) ? Promise.resolve() : Promise.reject(new Error('image'));
    return new Promise((resolve, reject) => { decoders.push({ url, resolve, reject }) });
  }
}

let flip!: StackFlip;
function Probe({ load, canFlip = () => true }: { load(): Promise<readonly string[]>; canFlip?(): boolean }) {
  flip = useStackFlip({ load, canFlip });
  return <div data-faces={flip.faces.join(' ')} data-current={flip.current} />;
}

const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) });
const enter = () => act(async () => flip.onPointerEnter());
const leave = () => act(async () => flip.onPointerLeave());
const onStage = () => flip.faces[flip.current];

describe('悬停翻卡', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('Image', FakeImage);
    decoders = [];
    autoDecode = () => true;
  });
  afterEach(() => { autoDecode = null });

  it('全部图片解码后才盖住静止封面，离开清掉所有计时器', async () => {
    autoDecode = null;
    await mount(<Probe load={async () => ['/a.jpg', '/b.jpg']} />);
    await enter();
    await advance(ARM_MS);
    expect(flip.faces).toEqual([]);
    await act(async () => decoders[0]!.resolve());
    await advance(0);
    expect(flip.faces).toEqual([]);
    await act(async () => decoders[1]!.resolve());
    await advance(0);
    expect(flip.faces).toEqual(['/a.jpg', '/b.jpg']);
    await leave();
    expect(flip.faces).toEqual([]);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('移出再移入时丢弃旧请求，只留一套翻动计时器', async () => {
    let stale!: (value: string[]) => void;
    const load = vi.fn<() => Promise<readonly string[]>>()
      .mockImplementationOnce(() => new Promise((resolve) => { stale = resolve }))
      .mockResolvedValue(['/a.jpg', '/b.jpg', '/c.jpg']);
    await mount(<Probe load={load} />);
    await enter();
    await advance(ARM_MS);
    await leave();
    await enter();
    await advance(ARM_MS);
    await act(async () => stale(['/stale.jpg', '/old.jpg']));
    await advance(MIX_FLIP_LEAD_MS);
    expect(flip.faces).toEqual(['/a.jpg', '/b.jpg', '/c.jpg']);
    expect(onStage()).toBe('/b.jpg');
    await advance(MIX_FLIP_MS);
    expect(onStage()).toBe('/c.jpg');
    await leave();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('解码失败的那一张不进翻动队列', async () => {
    autoDecode = (url) => url !== '/b.jpg';
    await mount(<Probe load={async () => ['/a.jpg', '/b.jpg', '/c.jpg']} />);
    await enter();
    await advance(ARM_MS + MIX_FLIP_LEAD_MS);
    expect(flip.faces).toEqual(['/a.jpg', '/c.jpg']);
    expect(onStage()).toBe('/c.jpg');
    await leave();
  });

  it('不足两张就不翻；门槛不放行时连图都不取', async () => {
    await mount(<Probe load={async () => ['/a.jpg']} />);
    await enter();
    await advance(10_000);
    expect(flip.faces).toEqual([]);
    expect(vi.getTimerCount()).toBe(0);
    const load = vi.fn(async () => ['/a.jpg', '/b.jpg']);
    await mount(<Probe load={load} canFlip={() => false} />);
    await enter();
    await advance(10_000);
    expect(load).not.toHaveBeenCalled();
    expect(flip.faces).toEqual([]);
  });
});
