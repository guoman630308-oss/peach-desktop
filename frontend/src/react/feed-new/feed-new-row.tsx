/* 新作那一行的卡片区，首页与资料页共用：画进壳留好的 `section.feednew` 宿主里，卡片是壳拼的
 * 那一份模板（`feedNewCardHtml`）。忽略的那条当场消失，已看过的留在原位只是变淡，想要那颗键
 * 按下去换成填实的星；三样都直接改这一行的节点，不重画整行——重画会把横滚的位置和自动滚动
 * 一起打回起点。一条都没有就整块不出。 */
import { useContext, useEffect, useLayoutEffect, useRef, useState, type MouseEvent } from 'react';
import { createPortal } from 'react-dom';
import { ShellActionsContext } from '../router/router';
import { SourceAvatar } from '../components/source-avatar';

import type { FeedNew } from './feed-new';

export function FeedNewRow({ feedNew, host, wire, act, settled }: {
  feedNew: FeedNew | null;
  host: HTMLElement;
  /** 拖动、滚轮，按设置接自动滚动。 */
  wire(row: Element | null): void;
  /** 卡上那三颗键的写操作。 */
  act(feedId: number, action: string): Promise<void>;
  /** 有没有卡：画出来、或最后一张被收起时报一次。 */
  settled?(hasItems: boolean): void;
}) {
  const actions = useContext(ShellActionsContext);
  const box = useRef<HTMLDivElement>(null);
  const [sourceFaces, setSourceFaces] = useState<HTMLElement[]>([]);
  const html = feedNew?.items.length ? feedNew.html : '';
  useLayoutEffect(() => {
    host.removeAttribute('aria-busy');
    host.hidden = !html;
    if (feedNew) settled?.(!!html);
  }, [host, html, feedNew, settled]);
  useEffect(() => {
    if (html) wire(box.current?.querySelector('.feednewrow') ?? null);
    setSourceFaces([...box.current?.querySelectorAll<HTMLElement>('[data-feed-source-avatar]') ?? []]);
  }, [wire, html]);
  const click = async (event: MouseEvent<HTMLDivElement>) => {
    const link = (event.target as Element).closest<HTMLAnchorElement>('a.feednewopen');
    if (link && actions && !event.button && !event.metaKey && !event.ctrlKey && !event.altKey && !event.shiftKey) {
      event.preventDefault(); actions.navigate(link.getAttribute('href')!); return;
    }
    const button = (event.target as Element).closest<HTMLElement>('[data-feed-action]');
    const card = button?.closest<HTMLElement>('[data-feed-id]');
    if (!button || !card) return;
    const action = button.dataset.feedAction || '';
    await act(Number(card.dataset.feedId), action);
    if (action === 'want' || action === 'unwant') {
      // 想要是开关：同一颗键换成另一头，卡片不收起也不变淡。
      const wanted = action === 'want';
      button.dataset.feedAction = wanted ? 'unwant' : 'want';
      button.setAttribute('aria-pressed', String(wanted));
      button.title = wanted ? '取消想要' : '想要';
      return;
    }
    if (action === 'ignore') card.remove();
    else card.classList.add('isread');
    if (!box.current?.querySelector('[data-feed-id]')) {
      host.hidden = true;
      settled?.(false);
    }
  };
  return html ? <><div ref={box} className="contents" onClick={(event) => { void click(event) }}
    dangerouslySetInnerHTML={{ __html: html }} />{sourceFaces.map((face, index) => createPortal(
      <SourceAvatar code={face.dataset.code!} person={{ name: face.dataset.name!, source_actor_id: face.dataset.actor! }} />,
      face, `${html.length}:${index}`))}</> : null;
}
