/* 悬停一叠卡片时把它里面的前几张逐张翻走，说明它是一叠而不是某一个视频。
 *
 * 时序与门槛（`frontend/test/react/use-stack-flip.test.tsx`）：指针停 340ms 才开始，面板建好
 * 420ms 后翻第一下，之后每 1100ms 翻一张；翻出去的那张演完（间隔减 120ms）才卸掉离场态，
 * 否则会当场弹回原位。多选、遮挡、减少动效与滚动中都不启动，这些判据归壳，经 `canFlip`
 * 递进来；取图前后各再问一次。离开即停并还原静止封面。
 *
 * 翻哪些由调用方给：播放列表翻它自己的封面，首页 Mix 现拉相关作品。能用的图少于两张就
 * 不翻：一张图翻过去还是它自己。 */
import { useCallback, useEffect, useRef, useState } from 'react';

/** 一张停多久再翻走。 */
export const MIX_FLIP_MS = 1100;
/** 面板建好到第一次翻动。直接用 1.1 秒间隔等第一张，指针停下到有反应要接近两秒。 */
export const MIX_FLIP_LEAD_MS = 420;
/** 最多预备几张，一次悬停不拉一整批封面。 */
export const MIX_FLIP_FACES = 9;
/** 指针停多久才算「在看这一叠」，扫过去的不启动。 */
const MIX_FLIP_ARM_MS = 340;
/** 一张图等多久还没解码好就不要它。 */
const MIX_FLIP_READY_MS = 5000;

export interface StackFlipOptions {
  /** 这一叠要翻的图地址，按翻动顺序。 */
  load(): Promise<readonly string[]>;
  /** 此刻能不能翻：多选、遮挡、减少动效、滚动中都回 false。 */
  canFlip(): boolean;
  /** 预取解码时带的来路策略，要和台上那几张 `<img>` 一致，否则解码的和显示的是两次请求。
   *  关注页的外站图不带来路。 */
  referrerPolicy?: ReferrerPolicy;
}

export interface StackFlip {
  /** 已经解码好的那几张；空数组就是不在翻，静止封面照常露着。 */
  faces: readonly string[];
  /** 正在台上的那一张。 */
  current: number;
  /** 刚翻出去、还在演离场的那一张。 */
  leaving: number | null;
  onPointerEnter(): void;
  onPointerLeave(): void;
}

interface Run {
  live: boolean;
  generation: number;
  timers: Set<ReturnType<typeof setTimeout>>;
  cycle: ReturnType<typeof setInterval> | null;
  cancels: Set<() => void>;
  /** 指针进来后那一段 340ms 的等待。 */
  armed: ReturnType<typeof setTimeout> | null;
  count: number;
  at: number;
}

/** 等一张图载入并解码。取不到、宽为 0 或超时都算不能用。 */
function readyImage(url: string, run: Run, referrerPolicy?: ReferrerPolicy): Promise<boolean> {
  return new Promise((resolve) => {
    const image = new Image();
    if (referrerPolicy) image.referrerPolicy = referrerPolicy;
    let settled = false;
    const finish = (ok: boolean) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      image.onload = null;
      image.onerror = null;
      run.cancels.delete(cancel);
      resolve(ok);
    };
    const cancel = () => finish(false);
    const timeout = setTimeout(cancel, MIX_FLIP_READY_MS);
    run.cancels.add(cancel);
    image.onerror = cancel;
    image.onload = () => {
      if (!image.naturalWidth) { finish(false); return }
      if (typeof image.decode === 'function') image.decode().then(() => finish(true), cancel);
      else finish(true);
    };
    image.src = url;
  });
}

export function useStackFlip({ load, canFlip, referrerPolicy }: StackFlipOptions): StackFlip {
  const [faces, setFaces] = useState<readonly string[]>([]);
  const [current, setCurrent] = useState(0);
  const [leaving, setLeaving] = useState<number | null>(null);
  const run = useRef<Run>({
    live: false, generation: 0, timers: new Set(), cycle: null, cancels: new Set(), armed: null, count: 0, at: 0,
  });
  /* 回调里读的是最新的 `load`／`canFlip`：卡片重画时换了闭包，正在进行的那一次也跟着换。 */
  const latest = useRef({ load, canFlip });
  latest.current = { load, canFlip };

  const clear = useCallback(() => {
    const state = run.current;
    state.live = false;
    state.generation += 1;
    state.timers.forEach(clearTimeout);
    state.timers.clear();
    state.armed = null;
    if (state.cycle !== null) clearInterval(state.cycle);
    state.cycle = null;
    state.cancels.forEach((cancel) => cancel());
    state.count = 0;
    state.at = 0;
  }, []);

  const later = (ms: number, task: () => void) => {
    const timer = setTimeout(() => { run.current.timers.delete(timer); task() }, ms);
    run.current.timers.add(timer);
    return timer;
  };

  const step = () => {
    const state = run.current;
    if (!state.live || state.count < 2) return;
    const out = state.at;
    state.at = (out + 1) % state.count;
    setLeaving(out);
    setCurrent(state.at);
    later(MIX_FLIP_MS - 120, () => setLeaving((now) => (now === out ? null : now)));
  };

  const start = async () => {
    const state = run.current;
    if (state.live || !latest.current.canFlip()) return;
    state.live = true;
    const generation = ++state.generation;
    const alive = () => state.live && generation === state.generation;
    let pool: readonly string[] = [];
    try {
      pool = (await latest.current.load()).slice(0, MIX_FLIP_FACES);
    } catch {
      return;
    }
    if (!alive() || !latest.current.canFlip() || pool.length < 2) return;
    const ready = await Promise.all(pool.map((url) => readyImage(url, state, referrerPolicy)));
    if (!alive() || !latest.current.canFlip()) return;
    const usable = pool.filter((_url, index) => ready[index]);
    if (usable.length < 2) return;
    state.count = usable.length;
    state.at = 0;
    setFaces(usable);
    setCurrent(0);
    setLeaving(null);
    later(MIX_FLIP_LEAD_MS, () => {
      step();
      state.cycle = setInterval(step, MIX_FLIP_MS);
    });
  };

  const onPointerEnter = () => {
    const state = run.current;
    if (state.armed !== null) { clearTimeout(state.armed); state.timers.delete(state.armed) }
    state.armed = later(MIX_FLIP_ARM_MS, () => { state.armed = null; void start() });
  };
  const onPointerLeave = () => {
    clear();
    setFaces([]);
    setCurrent(0);
    setLeaving(null);
  };

  /* 卸载时只收计时器与在途的图，不再改状态。 */
  useEffect(() => clear, [clear]);

  return { faces, current, leaving, onPointerEnter, onPointerLeave };
}
