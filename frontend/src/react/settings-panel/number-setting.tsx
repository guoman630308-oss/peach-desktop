/* 带单位、可选开关与字段错误反馈的数值设置。
 *
 * 可选的那几项（悬停放大、相关推荐、搜索记录、关注自动更新）前面有一枚开关：关掉就是 0，
 * 数字框收起来；再打开时填回关掉前的那个数。那个数记在 `peach.number.<id>`，跟着这台浏览器走。
 * 提交时机跟原生数字框的 `change` 一致：失焦或回车，打字途中不提交。 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react';

import { boundedPreference } from '../../number-setting';

export interface NumberSpec { min: number; max: number; unit: string; optional?: boolean; fallback: number }

export const NUMBER_SPECS = {
  batchSizeSetting: { min: 1, max: 200, unit: '个', fallback: 60 },
  hoverDelaySetting: { min: 1, max: 60, unit: '秒', optional: true, fallback: 5 },
  seekSecondsSetting: { min: 1, max: 300, unit: '秒', fallback: 10 },
  relatedLimitSetting: { min: 1, max: 60, unit: '个', optional: true, fallback: 20 },
  searchHistoryLimitSetting: { min: 1, max: 50, unit: '条', optional: true, fallback: 10 },
  followScheduleSetting: { min: 15, max: 10080, unit: '分钟', optional: true, fallback: 60 },
} as const satisfies Record<string, NumberSpec>;

export type NumberSettingId = keyof typeof NUMBER_SPECS;

const storageKey = (id: string) => `peach.number.${id}`;

/** 以分钟计的间隔满一天时，单位旁边再说一遍是几天：「10080 分钟」读不出是一周。
 *  不满一天、不是整数或不是分钟的读数返回空串。 */
export function dayReading(value: number, unit: string): string {
  if (unit !== '分钟' || !Number.isInteger(value) || value < 1440) return '';
  const days = value / 1440;
  return Number.isInteger(days) ? `${days.toLocaleString()} 天` : `约 ${days.toFixed(1)} 天`;
}

export function NumberSetting({ id, label, value, disabled = false, onApply }: {
  id: NumberSettingId; label: string;
  /** 当前值；null 表示还没取到（关注自动更新那一项），此时整块禁用、不改显示。 */
  value: number | null;
  disabled?: boolean;
  onApply(value: number): void;
}) {
  const spec: NumberSpec = NUMBER_SPECS[id];
  const { min, max, unit, fallback } = spec;
  const optional = spec.optional === true;
  const remembered = useRef(0);
  if (!remembered.current) {
    remembered.current = boundedPreference(Number(localStorage.getItem(storageKey(id))), min, max,
      value && value > 0 ? value : fallback);
  }
  const [shown, setShown] = useState(value ?? 0);
  const [draft, setDraft] = useState(String(value && value > 0 ? value : remembered.current));
  const [error, setError] = useState('');
  const input = useRef<HTMLInputElement | null>(null);
  const apply = useRef(onApply);
  apply.current = onApply;

  /* 外面换了值（数据库对账、另一台设备改过、请求回来）就跟上；null 只禁用，不动显示。 */
  useLayoutEffect(() => {
    if (value === null) return;
    setShown(value);
    if (value > 0) setDraft(String(value));
  }, [value]);

  const commit = () => {
    const node = input.current;
    if (!node) return;
    if (!node.value || !node.checkValidity()) {
      node.setAttribute('aria-invalid', 'true');
      setError(`请输入 ${min}–${max} 的整数（${unit}）`);
      return;
    }
    node.removeAttribute('aria-invalid');
    setError('');
    remembered.current = Number(node.value);
    localStorage.setItem(storageKey(id), String(remembered.current));
    apply.current(remembered.current);
  };
  const commitRef = useRef(commit);
  commitRef.current = commit;
  /* 原生 `change`：React 的 onChange 是每一次按键的 `input`。 */
  useEffect(() => {
    const node = input.current;
    if (!node) return undefined;
    const listener = () => commitRef.current();
    node.addEventListener('change', listener);
    return () => node.removeEventListener('change', listener);
  }, []);

  const on = shown > 0;
  const toggle = () => {
    const node = input.current;
    node?.removeAttribute('aria-invalid');
    setError('');
    if (!on) {
      setShown(remembered.current);
      setDraft(String(remembered.current));
      apply.current(remembered.current);
      return;
    }
    if (node && node.value && node.checkValidity()) remembered.current = Number(node.value);
    localStorage.setItem(storageKey(id), String(remembered.current));
    setShown(0);
    apply.current(0);
  };

  const blocked = disabled || value === null;
  return (
    <div data-number-setting="">
      {optional
        ? <input type="checkbox" data-toggle="" role="switch" aria-label={`启用${label}`}
            checked={on} disabled={blocked} onChange={toggle} />
        : null}
      <div data-number-fields="" hidden={optional && !on}>
        <div data-number-control="">
          <input ref={input} type="number" inputMode="numeric" className="geist-input" min={min} max={max} step={1}
            value={draft} disabled={blocked} aria-label={label} aria-describedby={`${id}-error`}
            onChange={(event) => {
              setDraft(event.target.value);
              if (event.target.value && event.target.checkValidity()) {
                event.target.removeAttribute('aria-invalid');
                setError('');
              }
            }}
            onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); commit() } }} />
          <span aria-hidden="true">{unit}{dayReading(Number(draft), unit) ? ` · ${dayReading(Number(draft), unit)}` : ''}</span>
        </div>
        <small id={`${id}-error`} data-number-error="" role="status" hidden={!error}>{error}</small>
      </div>
    </div>
  );
}
