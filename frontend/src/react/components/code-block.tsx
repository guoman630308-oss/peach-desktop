import { useEffect, useState } from 'react';
import { Button } from '@/components/base/buttons/button';
import { cx } from '@/utils/cx';
import { cardClass } from './card';
import { useOverlayScrollbar } from './overlay-scrollbar';

/** JSON 与配置文本共用的只读代码块，复制始终使用完整原文。 */
export function CodeBlock({ code, language, label, maxCharacters, bounded = true, copyLabel, onCopy }: {
  code: string; language: 'json' | 'toml'; label: string; maxCharacters?: number;
  bounded?: boolean; copyLabel?: string; onCopy?(): void;
}) {
  const scroll = useOverlayScrollbar<HTMLPreElement>();
  const [status, setStatus] = useState('');
  const [failed, setFailed] = useState(false);
  useEffect(() => { setStatus(''); setFailed(false) }, [code]);
  const truncated = maxCharacters !== undefined && code.length > maxCharacters;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setFailed(false); setStatus('已复制'); onCopy?.();
    } catch {
      setFailed(true); setStatus('复制失败，请选中文本复制。');
    }
  };
  return <div className={cx(cardClass({ bordered: 'soft', radius: 'plain', padding: 'none' }), 'overflow-hidden bg-background-tertiary-default')} data-code-block>
    <div className="flex min-w-0 items-center justify-between gap-3 border-b border-separator-border px-3 py-2">
      <span className="text-caption-1-medium text-text-secondary">{language.toUpperCase()}</span>
      <Button variant="secondary" size="small" onClick={() => void copy()}>{copyLabel || (truncated ? '复制完整内容' : '复制代码')}</Button>
    </div>
    <div className="relative min-w-0">
      <pre ref={scroll} tabIndex={0} aria-label={label}
        className={cx('m-0 overflow-auto overscroll-contain p-3 font-mono text-caption-1-regular whitespace-pre text-text-primary', bounded && 'max-h-80')}>
        <code>{maxCharacters === undefined ? code : code.slice(0, maxCharacters)}</code>
      </pre>
    </div>
    {status ? <p role={failed ? 'alert' : 'status'} className="m-0 px-3 pb-2 text-caption-1-regular text-text-secondary">{status}</p> : null}
  </div>;
}
