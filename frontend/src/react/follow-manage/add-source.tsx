/* 添加关注：把一条链接、一个名字或一个 id 变成已关注的来源。
 *
 * 查找只是列出候选、不写任何东西：发现要联网，结果也可能不止一个，替用户决定「就是
 * 这个」是错的。真正登记仍然要在候选里点。
 *
 * 查找那一趟在后台跑，页面关掉也还在跑。首屏读到的旧终态不冒充新结果：只有本次点过
 * 查找、或者本次亲眼见过它在跑，结果才摆出来（ADR-0031）。 */
import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent, RefObject } from 'react';
import { RiFilter3Line, RiSearchLine } from '@remixicon/react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Dialog, Popover } from 'react-aria-components';

import { Button } from '@/components/base/buttons/button';
import { Checkbox } from '@/components/base/checkbox/checkbox';
import {
  MENU_ITEM, MENU_ITEM_INTERACTIVE, MENU_POPOVER_SURFACE,
} from '@/components/base/dropdown/menu-styles';
import { Input } from '@/components/base/input/input';
import { cx } from '@/utils/cx';

import { errorMessage } from '../../api';
import { useBackgroundJob } from '../background-job';
import { cardClass } from '../components/card';
import { LoadingDots } from '../components/loading-dots';
import { Note } from '../components/note';
import { TaskProgress } from '../components/task-progress';
import { busyProps } from '../settings/use-action';
import { ExternalLink, FieldLabel, Help } from '../settings/section';
import {
  addSource, fetchResolveJob, fetchSuggestions, FOLLOW_RESOLVE_KEY, followSuggestKey,
  reloadFollowManage, startCheck, startResolve, SUGGEST_DEBOUNCE_MS,
  type CredentialData, type FollowData, type ResolveCandidate, type ResolveJob, type ResolveRow,
} from './follow-manage';
import { SourceIcon } from './source-view';

const MENU_ROW = cx(MENU_ITEM, MENU_ITEM_INTERACTIVE, 'text-body-2-medium');
const PLACEHOLDER = '粘贴来源链接，或输入创作者名、id…';
// 索引下载的提醒只在真按名字查时出现；常驻成一句说明就是噪音。
const BY_NAME_HINT = '查找中…（首次按名字查要下载创作者索引，可能几十秒）';
const BY_LINK_HINT = '识别中…';
const NO_CANDIDATE = '站内没有查到来源';

export interface AddSourceProps {
  data: FollowData;
  credentials: CredentialData;
  readOnly: boolean;
  toast(message: string): void;
  /** 去凭据那一栏配这个站。候选里的「需要配置凭据」按下去就是换页签。 */
  openCredentials(): void;
}

/** 候选的身份是它的地址：同一次查找里同一个地址只会出现一次。 */
const candidateKey = (row: number, candidate: ResolveCandidate) => `${row}:${candidate.url}`;

interface SuggestOption { value: string; matched: string; n: number; group: string }

/** 敲字建议的下拉。只在输入框有焦点时开着，键盘上下键在里面走。
 *
 * 每组一个组名，行里先是名字本身，再是在哪儿见到的（归档站名、站方分类）和作品数：
 * 名字才是选中后要去查的东西，另外两样只帮人认出是不是他。
 *
 * 站上那一路要先问补全再问分类，实测一秒上下，这段时间得看得出在做事：空着像是敲了没反应。
 * 忙的那一行不是候选，上下键和回车这时不该选中一个「正在查找建议」。
 *
 * 定位和来源筛选同一个 react-aria `Popover`：它把高度压到字段下方真正剩下的空间，
 * 装不下就在菜单里滚。不翻到上方：每敲一个字建议条数都在变，翻来翻去读不成一张表。
 * 非模态是为了焦点留在输入框；页面一滚走字段它就收起，菜单自己的滚动不收。 */
function SuggestMenu(
  { field, menu, options, active, busy, onPick, onClose }:
  {
    field: RefObject<HTMLDivElement | null>; menu: RefObject<HTMLElement | null>;
    options: SuggestOption[]; active: number; busy: boolean;
    onPick(value: string): void; onClose(): void;
  },
) {
  // 上下键走到菜单可见区外的那一行，把它滚进来；菜单外的页面不动。
  useEffect(() => {
    menu.current?.querySelector('[aria-current="true"]')?.scrollIntoView({ block: 'nearest' });
  }, [menu, active]);
  return (
    <Popover ref={menu} triggerRef={field} isOpen isNonModal shouldFlip={false}
      onOpenChange={(open) => { if (!open) onClose() }}
      placement="bottom start" offset={4} aria-label="来源建议"
      className={cx(MENU_POPOVER_SURFACE, 'flex w-(--trigger-width) flex-col gap-1 overscroll-contain')}>
      {busy && !options.length
        ? <div className="px-2 py-1.5"><LoadingDots label="正在查找建议" /></div>
        : null}
      {options.map((option, at) => (
        <Fragment key={`${option.group}-${option.value}`}>
          {option.group !== options[at - 1]?.group ? (
            <div className="px-2 pt-1 text-caption-1-regular text-text-tertiary">{option.group}</div>
          ) : null}
          <button type="button"
            aria-current={at === active ? 'true' : undefined}
            className={at === active ? cx(MENU_ROW, 'bg-background-secondary-default') : MENU_ROW}
            // 按下就 preventDefault：让下拉把焦点从输入框抢走的话，敲到一半的词就断在那里。
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => onPick(option.value)}>
            <span className="min-w-0 grow truncate text-left">{option.value}</span>
            {option.matched ? (
              <span className="shrink-0 text-caption-1-regular text-text-tertiary">{option.matched}</span>
            ) : null}
            {option.n ? (
              <span className="shrink-0 text-caption-1-regular tabular-nums text-text-tertiary">
                {option.n.toLocaleString()}
              </span>
            ) : null}
          </button>
        </Fragment>
      ))}
    </Popover>
  );
}

/** 来源筛选：列出全部可关注的站，默认全选。取消勾选的站，其查找结果行不显示、也不进
 *  「添加选中」。 */
function SourceFilter(
  { credentials, hidden, onHidden, openCredentials }:
  {
    credentials: CredentialData; hidden: ReadonlySet<string>;
    onHidden(next: Set<string>): void; openCredentials(): void;
  },
) {
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const rows = (credentials.providers || []).filter((row) => row.followable);
  const shown = rows.filter((row) => !hidden.has(row.provider_label)).length;
  const label = shown === rows.length ? '全部来源' : `${shown}/${rows.length} 个来源`;
  const toggle = (provider: string, on: boolean) => {
    const next = new Set(hidden);
    if (on) next.delete(provider); else next.add(provider);
    onHidden(next);
  };
  return (
    <>
      <Button ref={trigger} variant="secondary" leadingIcon={RiFilter3Line}
        aria-haspopup="dialog" aria-expanded={open} aria-label={label}
        onClick={() => setOpen(true)}>{label}</Button>
      <Popover triggerRef={trigger} isOpen={open} onOpenChange={setOpen}
        placement="bottom end" offset={4} className={MENU_POPOVER_SURFACE}>
        <Dialog aria-label="来源筛选" className="flex w-64 flex-col gap-1 outline-none">
          {rows.length ? (
            <div className="flex items-center gap-2 pb-1">
              <Button variant="ghost" size="small"
                onClick={() => onHidden(new Set())}>全选</Button>
              <Button variant="ghost" size="small"
                onClick={() => onHidden(new Set(rows.map((row) => row.provider_label)))}>全不选</Button>
            </div>
          ) : <Help>暂无可用来源</Help>}
          {rows.map((row) => {
            const needsCredential = row.requirement === 'required'
              && (!row.present || (row.missing || []).length > 0);
            return (
              <div key={row.provider} className="flex items-center gap-2">
                <Checkbox isSelected={!hidden.has(row.provider_label)}
                  onChange={(on) => toggle(row.provider_label, on)}>
                  <span className="flex min-w-0 items-center gap-1.5">
                    <SourceIcon provider={row.provider} />{row.provider_label}
                  </span>
                </Checkbox>
                {needsCredential ? (
                  <Button variant="ghost" size="xs" className="ml-auto"
                    onClick={() => { setOpen(false); openCredentials() }}>需要配置凭据</Button>
                ) : null}
              </div>
            );
          })}
        </Dialog>
      </Popover>
    </>
  );
}

/** 一条查找结果。候选逐条列，已经关注的列出来但不许再勾——否则人以为没查到。 */
function PickRow(
  { row, at, hidden, unpicked, onPick }:
  {
    row: ResolveRow; at: number; hidden: ReadonlySet<string>; unpicked: ReadonlySet<string>;
    onPick(key: string, on: boolean): void;
  },
) {
  if (row.kind === 'error') {
    return <Note tone="error" title={row.line}>{row.error || '查找失败'}</Note>;
  }
  const candidates = (row.candidates || []).filter(
    (candidate) => !hidden.has(candidate.provider_label || ''));
  const failures = Object.entries(row.failures || {});
  return (
    <div className={cardClass({ variant: 'outlined', padding: 'none', className: 'flex flex-col gap-2 p-4' })}>
      <b className="text-body-medium text-text-primary">{row.line}</b>
      {candidates.length ? candidates.map((candidate) => {
        const key = candidateKey(at, candidate);
        return (
          <Checkbox key={key} isSelected={!candidate.known && !unpicked.has(key)}
            isDisabled={candidate.known} onChange={(on) => onPick(key, on)}>
            <span className="flex min-w-0 flex-wrap items-center gap-1.5">
              <SourceIcon provider={candidate.provider} />
              <b className="text-body-medium">{candidate.provider_label}</b>
              <span className="min-w-0 break-words">{candidate.label}</span>
              <small className="text-caption-1-regular text-text-secondary">
                {candidate.known ? '已经关注' : candidate.evidence}
              </small>
            </span>
          </Checkbox>
        );
      }) : <Help>{NO_CANDIDATE}</Help>}
      {(row.external_searches || []).map((search) => (
        <div key={search.url} className="flex flex-wrap items-center gap-2">
          <small className="text-caption-1-regular text-text-secondary">{search.evidence}</small>
          {/* 这一条会离开 Peach，所以带外链标；标由共用的 `ExternalLink` 给，全站一个写法。 */}
          <ExternalLink href={search.url}>{`${search.label}：${search.query}`}</ExternalLink>
        </div>
      ))}
      {failures.length ? (
        <Help>{failures.map(([name, reason]) => `${name}：${reason}`).join('；')}</Help>
      ) : null}
    </div>
  );
}

export function AddSource({ data, credentials, readOnly, toast, openCredentials }: AddSourceProps) {
  const [line, setLine] = useState('');
  const [composing, setComposing] = useState(false);
  /** 这一趟查的是什么。等待提示使用已提交的词，输入框可继续编辑并保留重试内容。 */
  const [asked, setAsked] = useState('');
  const [term, setTerm] = useState('');
  const [focused, setFocused] = useState(false);
  /** 在这段输入上收起过下拉（Escape、页面滚走）。再敲一个字或按上下键就重新打开。 */
  const [closedAt, setClosedAt] = useState<string | null>(null);
  const [active, setActive] = useState(-1);
  const field = useRef<HTMLDivElement>(null);
  const menu = useRef<HTMLElement>(null);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set<string>());
  const [unpicked, setUnpicked] = useState<ReadonlySet<string>>(new Set<string>());
  const [problem, setProblem] = useState('');
  const [added, setAdded] = useState(0);

  /* 每一下输入都排一次建议，但只发一次请求：250ms 内继续敲就换掉上一次的排期。
     地址不进这条路——服务端认得出里面的 `/`，那时该做的是解析链接。 */
  useEffect(() => {
    const text = line.trim();
    if (composing || !text || text.includes('/')) { setTerm(''); return }
    const timer = setTimeout(() => setTerm(text), SUGGEST_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [line, composing]);

  const suggest = useQuery({
    queryKey: followSuggestKey(term),
    queryFn: ({ signal }) => fetchSuggestions(term, signal),
    enabled: term.length > 0 && focused && !composing && closedAt !== line,
  });

  const options = useMemo<SuggestOption[]>(() => (suggest.data?.groups || []).flatMap((group) => (
    group.items.map((item) => ({
      value: item.value, matched: item.matched || '', n: item.n || 0, group: group.label,
    }))
  )), [suggest.data]);

  const { job, running, outcome, start: resolve, dismiss } = useBackgroundJob<ResolveJob, string>({
    queryKey: FOLLOW_RESOLVE_KEY,
    queryFn: ({ signal }) => fetchResolveJob(signal),
    start: (text) => startResolve([text]),
    onStarted: () => setUnpicked(new Set()),
    onError: (cause) => setProblem(errorMessage(cause)),
    onFinish: (state) => {
      if (state.status === 'failed') setProblem(state.error || '查找失败');
    },
  });

  const search = (text: string) => {
    const query = text.trim();
    if (!query || readOnly || composing || running || resolve.isPending) return;
    setProblem('');
    setActive(-1);
    setAsked(query);
    setLine(query);
    setTerm('');
    setClosedAt(query);
    resolve.mutate(query);
  };

  const suggesting = suggest.isFetching;
  const menuOpen = focused && !composing && closedAt !== line && (options.length > 0 || suggesting);
  // 收起的同时放掉选中：看不见的那一行不该被回车选去查。
  const closeMenu = () => { setActive(-1); setClosedAt(line) };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (composing || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
    if (event.key === 'Escape' && menuOpen) { closeMenu(); return }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (!options.length) return;
      event.preventDefault();
      if (!menuOpen) { setClosedAt(null); return }
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setActive((now) => {
        const next = now + step;
        if (next < 0) return options.length - 1;
        return next >= options.length ? 0 : next;
      });
      return;
    }
    if (event.key !== 'Enter') return;
    event.preventDefault();
    /* 选中一条建议再回车，等于把那个名字填进来再查找。查找只是列出候选、不写任何
       东西，所以选中即查找是安全的。 */
    search(options[active]?.value || line);
  };

  const rows = outcome?.results || [];
  const picked = rows.flatMap((row, at) => (row.candidates || [])
    .filter((candidate) => !candidate.known && !hidden.has(candidate.provider_label || ''))
    .map((candidate) => ({ candidate, key: candidateKey(at, candidate) }))
    .filter((item) => !unpicked.has(item.key)));

  const register = useMutation({
    mutationFn: async (items: { candidate: ResolveCandidate }[]) => {
      const failures: string[] = [];
      const sources: number[] = [];
      setAdded(0);
      for (const item of items) {
        try {
          const done = await addSource(item.candidate);
          sources.push(done.source);
        } catch (cause) {
          // 一条失败不该把其余的一起丢掉，逐条报。
          failures.push(`${item.candidate.label}：${errorMessage(cause)}`);
        }
        setAdded((now) => now + 1);
      }
      // 新来源登记时按 `defer_check` 跳过了检查，登记完一次性起一趟。
      if (sources.length) {
        try { await startCheck(sources) } catch (cause) { failures.push(errorMessage(cause)) }
      }
      return { failures, sources };
    },
    onSuccess: (result) => {
      void reloadFollowManage();
      if (result.failures.length) { setProblem(result.failures.join('；')); return }
      setProblem('');
      dismiss();
      toast(`已添加 ${result.sources.length} 个关注来源`);
    },
    onError: (cause) => setProblem(errorMessage(cause)),
  });

  const guesses = data.suggestions || [];
  const byName = !asked.includes('/');

  return (
    /* 旧 `.followmanage .fmain>.fsec`：一块分区就是一张填充卡，标题在卡里。 */
    <div className={cardClass({ padding: 'none', className: 'flex flex-col gap-4 px-6 py-5 max-sm:px-4' })}>
      <h3 className="text-title-2-medium text-text-primary">添加关注</h3>

      <div className="flex flex-wrap items-end gap-2">
        {/* 焦点离开的是字段和下拉两处才收，不是离开输入框就收：下拉里的每一条都是真的按钮，
            焦点落进去的那一下收掉的话，正好把要点的东西撤走。下拉挂在浮层容器里，不在
            字段的 DOM 下面，所以两处分开认。 */}
        <div ref={field} className="min-w-64 grow" onFocus={() => setFocused(true)}
          onBlur={(event) => {
            const next = event.relatedTarget;
            if (!event.currentTarget.contains(next) && !menu.current?.contains(next)) setFocused(false);
          }}>
          <Input aria-label="来源链接、名字或 id" placeholder={PLACEHOLDER} value={line}
            leadingIcon={RiSearchLine} enterKeyHint="search" isDisabled={readOnly} onChange={setLine}
            onCompositionStart={() => { setComposing(true); setActive(-1) }}
            onCompositionEnd={(event) => { setComposing(false); setLine(event.currentTarget.value) }}
            onKeyDown={onKeyDown} />
          {menuOpen ? (
            <SuggestMenu field={field} menu={menu} options={options} active={active}
              busy={suggesting} onPick={search} onClose={closeMenu} />
          ) : null}
        </div>
        <SourceFilter credentials={credentials} hidden={hidden} onHidden={setHidden}
          openCredentials={openCredentials} />
        <Button variant="primary" disabled={readOnly || !line.trim()}
          {...busyProps(running || resolve.isPending)} onClick={() => search(line)}>查找</Button>
      </div>

      <div aria-live="polite" className="flex flex-col gap-3 empty:hidden">
        {running || resolve.isPending ? <TaskProgress embedded
          label={job?.message || (job?.total ? `查找中：${job.checked || 0}/${job.total}` : byName ? BY_NAME_HINT : BY_LINK_HINT)}
          value={job?.checked} total={job?.total} /> : null}
        {problem ? <Note tone="error" title="这一次没有完成">{problem}</Note> : null}
      </div>

      {rows.length ? (
        <div className="flex flex-col gap-3">
          <FieldLabel>查找结果</FieldLabel>
          {rows.map((row, at) => (
            <PickRow key={`${row.line}-${at}`} row={row} at={at} hidden={hidden} unpicked={unpicked}
              onPick={(key, on) => {
                const next = new Set(unpicked);
                if (on) next.delete(key); else next.add(key);
                setUnpicked(next);
              }} />
          ))}
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" size="small" disabled={readOnly || !picked.length}
              {...busyProps(register.isPending)}
              onClick={() => register.mutate(picked)}>{`添加选中（${picked.length}）`}</Button>
            <Button variant="secondary" size="small"
              onClick={() => dismiss()}>关闭</Button>
            {register.isPending ? (
              <span role="status" className="text-body-2-regular text-text-secondary">
                {`添加中… ${added}/${picked.length}`}
              </span>
            ) : null}
          </div>
        </div>
      ) : null}

      {guesses.length ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-body-2-regular text-text-secondary">猜你喜欢</span>
          {guesses.map((guess) => (
            <Button key={guess.name} variant="secondary" size="small" disabled={readOnly}
              title={`浏览历史里出现 ${guess.visits} 次${guess.origin ? ` · ${guess.origin}` : ''}`}
              onClick={() => search(guess.name)}>{guess.name}</Button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
