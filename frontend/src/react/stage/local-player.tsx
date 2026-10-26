import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Button } from '@/components/base/buttons/button';
import { apiSend } from '../../api';
import { localPlayerPolicy } from '../../local-player';
import { busyProps, useAction } from '../settings/use-action';
import type { StagePlayerOptions } from './stage-player';

export function LocalPlayerControls({ options, onInternal, onExternal, isReleased }: {
  options: StagePlayerOptions;
  onInternal(): void;
  onExternal(): void;
  isReleased(): boolean;
}) {
  const action = useAction();
  const [internal, setInternal] = useState(false);
  const [message, setMessage] = useState('');
  const started = useRef(false);
  const disc = options.kind === 'item' && String(options.item.name || '').toLowerCase().endsWith('.iso');
  const play = () => {
    if (isReleased()) return;
    onExternal(); setInternal(false); setMessage('');
    void action.run('play', signal => apiSend<{ opened: boolean; player: string }>('/api/local-player/play', {
      kind: options.kind, id: options.item.id, media: options.media?.index ?? null,
    }, 'POST', signal), result => setMessage(`已打开 ${result.player}`));
  };
  useEffect(() => {
    if (!started.current && !isReleased()) { started.current = true; play() }
  }, []);
  return <div data-local-player-controls="" aria-label="播放方式">
    {!internal ? <p role="status">{action.busy ? `正在打开 ${localPlayerPolicy.label}…` : message || '外部播放'}</p> : null}
    <div className="flex flex-wrap items-center justify-center gap-3">
      <Button {...busyProps(action.busy === 'play')} onClick={play}>{localPlayerPolicy.label} 播放</Button>
      {!disc ? <Button variant="secondary" aria-pressed={internal} onClick={() => {
        if (action.busy || internal) return;
        action.setError(''); onInternal(); setInternal(true);
      }}>内置播放</Button> : null}
    </div>
    {action.error ? <p role="alert">{action.error}</p> : null}
  </div>;
}

/** 控件与 Video.js 都只操作媒体框中各自创建的节点。 */
export function mountLocalPlayer(frame: HTMLElement, options: StagePlayerOptions,
  mountInternal: () => () => void): () => void {
  const container = document.createElement('div');
  container.className = 'peach-react';
  container.dataset.localPlayerHost = '';
  frame.append(container);
  const root = createRoot(container);
  let released = false;
  let detach: (() => void) | null = null;
  root.render(<LocalPlayerControls options={options} isReleased={() => released}
    onInternal={() => { container.dataset.internal = ''; detach ??= mountInternal() }}
    onExternal={() => { detach?.(); detach = null; delete container.dataset.internal }} />);
  return () => {
    if (released) return;
    released = true;
    detach?.(); detach = null;
    container.remove();
    queueMicrotask(() => root.unmount());
  };
}
