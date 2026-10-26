import { afterEach, expect, it, vi } from 'vitest';
import { ApplicationEffects } from '../src/application/effects';

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('暂停与销毁隔断监听，恢复保留监听目标身份', () => {
  const effects = new ApplicationEffects(), target = new EventTarget();
  const seen: unknown[] = [];
  effects.listen(target, 'sample', function (this: EventTarget) { seen.push(this); });
  target.dispatchEvent(new Event('sample'));
  effects.pause(); target.dispatchEvent(new Event('sample'));
  effects.resume(); target.dispatchEvent(new Event('sample'));
  effects.dispose(); effects.resume(); target.dispatchEvent(new Event('sample'));
  expect(seen).toEqual([target, target]);
  expect(effects.signal.aborted).toBe(true);
});

it('属性处理器保留 this，清理只恢复自己拥有的属性', () => {
  const effects = new ApplicationEffects(), previous = vi.fn();
  const target = { onclick: previous };
  const seen: unknown[] = [];
  effects.handler(target, 'onclick', function (this: typeof target) { seen.push(this); });
  target.onclick(new Event('click'));
  effects.dispose(); expect(target.onclick).toBe(previous); expect(seen).toEqual([target]);
  const next = new ApplicationEffects(), replacement = vi.fn();
  next.handler(target, 'onclick', vi.fn()); target.onclick = replacement;
  next.dispose(); expect(target.onclick).toBe(replacement);
});

it('已完成调度释放句柄，销毁取消未完成定时与帧', () => {
  vi.useFakeTimers();
  const effects = new ApplicationEffects(), called = vi.fn();
  effects.delay(called, 10); vi.advanceTimersByTime(10);
  expect(called).toHaveBeenCalledTimes(1);
  effects.delay(called, 50); effects.frame(called); effects.dispose();
  vi.runAllTimers(); expect(called).toHaveBeenCalledTimes(1);
});

it('观察者销毁后不能再次观察，晚到回调不发布', () => {
  const observe = vi.fn(), disconnect = vi.fn(), changed = vi.fn();
  let fire = () => {};
  vi.stubGlobal('MutationObserver', class {
    observe = observe; disconnect = disconnect; takeRecords = () => [];
    constructor(callback: MutationCallback) { fire = () => callback([], this); }
  });
  const effects = new ApplicationEffects(), observer = effects.mutation(changed);
  observer.observe(document.body, { childList: true }); fire();
  effects.dispose(); observer.observe(document.body, { childList: true }); fire();
  expect(observe).toHaveBeenCalledTimes(1); expect(disconnect).toHaveBeenCalledTimes(1);
  expect(changed).toHaveBeenCalledTimes(1);
});

it('清理异常仍释放其他资源，晚交资源立即释放一次', () => {
  const effects = new ApplicationEffects(), first = vi.fn(), last = vi.fn(), late = vi.fn();
  effects.own(first); effects.own(() => { throw new Error('清理失败'); }); effects.own(last);
  expect(() => effects.dispose()).toThrow(AggregateError);
  expect(first).toHaveBeenCalledTimes(1); expect(last).toHaveBeenCalledTimes(1);
  const release = effects.own(late); release(); effects.dispose();
  expect(late).toHaveBeenCalledTimes(1);
});
