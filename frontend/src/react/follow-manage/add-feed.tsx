/* 添加 JAV 订阅：输入女优名，到 JavDB 搜她的演员卡，勾选后登记到她名下并订上（ADR-0083）。
 *
 * 查找只是列出候选、不写任何东西：同名的人站上可能不止一位，替用户决定「就是这个」是错的。
 * 名字对得上、又只有一个人的那几张先勾上，其余的要用户自己点。地址仍由服务端按勾选的 id 拼，
 * 页面从头到尾不送地址。这位在数据库里不存在也可以订：服务端会新建她。 */
import { useState } from 'react';
import type { KeyboardEvent } from 'react';
import { RiSearchLine } from '@remixicon/react';

import { Chip } from '@/components/base/badges/chip';
import { Button } from '@/components/base/buttons/button';
import { Checkbox } from '@/components/base/checkbox/checkbox';
import { Input } from '@/components/base/input/input';

import { cardClass } from '../components/card';
import { Note } from '../components/note';
import { ExternalLink, FieldLabel, Help } from '../settings/section';
import { busyProps, useAction } from '../settings/use-action';
import { followFeedByName, lookupFeedActor, type FeedCandidate, type FeedLookup } from './follow-manage';

const PLACEHOLDER = '输入女优名，到 JavDB 找她的演员页';

export interface AddFeedProps {
  readOnly: boolean;
  toast(message: string): void;
  /** 订上之后让清单重取。 */
  onAdded(): void;
}

/** 勾下去会登记到谁名下：先看勾选的卡已经在谁名下，再看数据库里按名字认得出谁，都没有就新建。 */
function destination(result: FeedLookup, picked: ReadonlySet<string>): { text: string; blocked: boolean } {
  const holders = new Map<number, string>();
  for (const card of result.candidates) {
    if (picked.has(card.id) && card.held_by) holders.set(card.held_by.id, card.held_by.name);
  }
  if (holders.size > 1) {
    return { text: `所勾的卡分属数据库里不同的人：${[...holders.values()].join('、')}，先去合并再订`, blocked: true };
  }
  const [holder] = holders.values();
  if (holder) return { text: `会登记到「${holder}」名下`, blocked: false };
  if (result.known) return { text: `会登记到数据库里的「${result.known.name}」名下`, blocked: false };
  return { text: `数据库里还没有「${result.q}」，会新建一位`, blocked: false };
}

function CandidateRow({ card, picked, onPick }: { card: FeedCandidate; picked: boolean; onPick(on: boolean): void }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Checkbox isSelected={picked} onChange={onPick}>
        <span className="flex min-w-0 flex-wrap items-center gap-1.5">
          <span className="min-w-0 break-words">{card.names.join('、')}</span>
          {card.record ? <Chip variant="caption" color="neutral">{card.record}</Chip> : null}
          {card.held_by ? (
            <small className="text-caption-1-regular text-text-secondary">{`已在「${card.held_by.name}」名下`}</small>
          ) : null}
        </span>
      </Checkbox>
      <ExternalLink href={card.url}>{card.id}</ExternalLink>
    </div>
  );
}

export function AddFeed({ readOnly, toast, onAdded }: AddFeedProps) {
  const [name, setName] = useState('');
  const [result, setResult] = useState<FeedLookup | null>(null);
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set<string>());
  const action = useAction();

  const search = () => {
    const q = name.trim();
    if (!q || readOnly) return;
    void action.run('lookup', (signal) => lookupFeedActor(q, signal), (found) => {
      setResult(found);
      setPicked(new Set(found.suggested));
    });
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); search() }
  };
  const close = () => { setResult(null); setPicked(new Set()); action.setError('') };
  const add = () => {
    if (!result) return;
    void action.run('follow', (signal) => followFeedByName(result.q, [...picked], signal), (done) => {
      toast(done.created
        ? `已新建「${done.entity_name}」并订阅她的新作`
        : `已订阅「${done.entity_name}」的新作`);
      setName('');
      close();
      onAdded();
    });
  };

  const where = result ? destination(result, picked) : null;

  return (
    /* 和「添加关注」那一块同一个形：一张填充卡，标题在卡里。 */
    <div className={cardClass({ padding: 'none', className: 'flex flex-col gap-4 px-6 py-5 max-sm:px-4' })}>
      <h3 className="text-title-2-medium text-text-primary">添加 JAV 订阅</h3>
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-64 grow">
          <Input aria-label="女优名" placeholder={PLACEHOLDER} value={name} leadingIcon={RiSearchLine}
            enterKeyHint="search" isDisabled={readOnly} onChange={setName} onKeyDown={onKeyDown} />
        </div>
        <Button variant="primary" disabled={readOnly || !name.trim()} {...busyProps(action.busy === 'lookup')}
          onClick={search}>查找</Button>
      </div>

      {action.error ? <Note tone="error" title="这一次没有完成">{action.error}</Note> : null}

      {result ? (
        <div className="flex flex-col gap-3">
          <FieldLabel>JavDB 上的演员卡</FieldLabel>
          {result.candidates.length ? result.candidates.map((card) => (
            <CandidateRow key={card.id} card={card} picked={picked.has(card.id)}
              onPick={(on) => setPicked((now) => {
                const next = new Set(now);
                if (on) next.add(card.id); else next.delete(card.id);
                return next;
              })} />
          )) : <Help>JavDB 上没有搜到这个名字。</Help>}
          {result.candidates.length ? (
            <Help role="status">{picked.size ? where!.text : '勾选她的演员卡；有碼与無碼两条记录都是她的话，两张都勾。'}</Help>
          ) : null}
          <div className="flex flex-wrap items-center gap-2">
            {result.candidates.length ? (
              <Button variant="primary" size="small" disabled={readOnly || !picked.size || where!.blocked}
                {...busyProps(action.busy === 'follow')} onClick={add}>{`添加订阅（${picked.size}）`}</Button>
            ) : null}
            <Button variant="secondary" size="small" onClick={close}>关闭</Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
