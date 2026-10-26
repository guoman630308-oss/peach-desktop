/* 数据管理页上的「链接管理」。
 *
 * 三份真相三个键（见 `links.ts`）：库里链接的现状、那趟联网检查、那趟删除。检查与删除
 * 各是一趟后台任务，都走 `useBackgroundJob`。检查的结论是页面进来就要看的东西（上一趟判出
 * 的失效链接还等着删），所以画的是当前快照；删除只报本次跟完的那一趟。
 *
 * 结果分两张表不是为了好看：`linktr.ee` 回 403 是挡爬虫、`x.com` 回 500 是临时错误，
 * 链接本身好好的。混成一张会让人顺手把好链接一起删掉，所以「地址已失效」和「本次未
 * 访问成功」各占一张，只有前一张接得上删除。 */
import { useEffect, useState, type ReactNode } from 'react';
import { RiExternalLinkLine } from '@remixicon/react';
import { useQuery } from '@tanstack/react-query';
import { confirmModal } from '@peach/legacy/ui';

import { Button } from '@/components/base/buttons/button';

import { errorMessage } from '../../api';
import { ErrorExcerpt } from '../activity/error-excerpt';
import { useBackgroundJob } from '../background-job';
import { DataTable, type DataColumn } from '../components/data-table';
import {
  Fieldset, FieldsetTitle, PanelFooter, RESULT_PANEL, RESULT_SECTION,
} from '../components/fieldset';
import { Note } from '../components/note';
import { TaskProgress } from '../components/task-progress';
import { spriteGlyph } from '../components/sprite-glyph';
import { queryClient } from '../query';
import { busyProps } from '../settings/use-action';
import {
  checkLine, fetchLinkCheck, fetchLinkPrune, fetchLinkStats, LINK_CHECK_KEY, LINK_KINDS,
  LINK_PRUNE_KEY, LINKS_KEY, pruneText, startLinkCheck, startLinkPrune,
  type LinkCheckState, type LinkPruneState, type LinkRow,
} from './links';

const UNLINK = spriteGlyph('unlink');
const RETRY = spriteGlyph('rotate-cw');

const GONE_HINT = '站点返回 404 或 410，确认这个地址不存在。';
/* 这一组大多是站点拒绝程序访问或一次抖动，换个时间再问一次就通了。 */
const UNCLEAR_HINT = '站点拒绝程序访问或一次临时故障都会落在这里。链接保留，可勾选后单独重试。';
const PRUNE_HINT = '删除前会逐条重验一次；此操作不可撤销。';
const DISCONNECTED = '暂时无法读取进度，正在重新连接…';

const count = (value: number | undefined) => Number(value || 0).toLocaleString();

/** 一格读数：名目、数字、可选的一句注。每一类各占一格：挤成一行时标签和数字之间只剩
 *  间隔点，数字归谁全靠猜。 */
function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="text-caption-1-regular leading-5 text-text-secondary">{label}</span>
      <b className="text-title-2-semibold font-bold leading-5 tabular-nums text-text-primary">{value}</b>
      {note ? <small className="text-caption-1-regular leading-5 text-text-secondary">{note}</small> : null}
    </div>
  );
}

function LinkTable(
  { title, items, hint, picked, onPick, children, footer }:
  {
    title: string; items: LinkRow[]; hint: string;
    picked: readonly number[]; onPick(next: number[]): void; children?: ReactNode; footer?: ReactNode;
  },
) {
  if (!items.length) return null;
  const columns: DataColumn<LinkRow>[] = [
    { id: 'entity', label: '所属', value: (item) => item.entity,
      render: (item) => <span className="block min-w-24 wrap-anywhere">{item.entity}</span> },
    { id: 'kind', label: '类型', value: (item) => LINK_KINDS[item.link_kind] || item.link_kind,
      render: (item) => LINK_KINDS[item.link_kind] || item.link_kind },
    { id: 'label', label: '标签', value: (item) => item.label || '',
      render: (item) => <span className="block min-w-24 wrap-anywhere">{item.label || ''}</span> },
    { id: 'note', label: '结果', value: (item) => item.note,
      render: (item) => <div className="min-w-40 max-w-80"><ErrorExcerpt text={item.note} className="tabular-nums text-text-error-primary" /></div> },
    { id: 'url', label: '地址', value: (item) => item.url,
      render: (item) => <a href={item.url} target="_blank" rel="noreferrer" title={item.url}
        className="flex max-w-90 min-w-0 items-center gap-1 text-text-secondary hover:text-text-primary">
        <span data-middle-truncate className="min-w-0 truncate">{item.url}</span>
        <RiExternalLinkLine aria-hidden className="size-3.5 shrink-0" />
      </a> },
  ];
  return (
    <div className={RESULT_SECTION}>
      <DataTable label={title} items={items} columns={columns} rowId={(item) => String(item.id)}
        description={hint} unit="条链接" panel={false} footerActions={footer}
        rowLabel={(item) => `${item.entity} 的${item.label || item.url}`}
        selected={picked.map(String)} onSelected={(ids) => onPick(ids.map(Number))}
        actions={() => children} />
    </div>
  );
}

export function LinkManager() {
  const stats = useQuery({ queryKey: LINKS_KEY, queryFn: ({ signal }) => fetchLinkStats(signal) });
  const [picked, setPicked] = useState<number[]>([]);
  const [gonePicked, setGonePicked] = useState<number[]>([]);
  const prune = useBackgroundJob<LinkPruneState, { checkId: string; ids?: number[] }>({
    queryKey: LINK_PRUNE_KEY,
    queryFn: ({ signal }) => fetchLinkPrune(signal),
    start: startLinkPrune,
    /* 删完之后库里的链接少了，那两块读数跟着重取。 */
    onFinish: () => {
      void queryClient.invalidateQueries({ queryKey: LINKS_KEY, exact: true });
      void queryClient.invalidateQueries({ queryKey: LINK_CHECK_KEY, exact: true });
    },
  });
  const check = useBackgroundJob<LinkCheckState, { retry?: readonly number[]; checkId?: string }>({
    queryKey: LINK_CHECK_KEY,
    queryFn: ({ signal }) => fetchLinkCheck(signal),
    start: startLinkCheck,
    /* 又起了一趟检查：上一趟删除的回执说的是旧清单，勾选的行号也不再指向任何东西。 */
    onStarted: () => { prune.dismiss(); setPicked([]); setGonePicked([]) },
  });

  const state = check.job;
  const checkId = state?.check_id ?? '';
  useEffect(() => { setPicked([]); setGonePicked([]) }, [checkId]);

  const running = check.running || check.start.isPending;
  const removing = prune.running || prune.start.isPending;
  const done = state?.status === 'complete';
  const gone = state?.gone ?? [];
  const unclear = state?.unclear ?? [];
  const retryable = done ? unclear : [];
  const selectedGone = gone.filter((item) => gonePicked.includes(item.id)).map((item) => item.id);
  const selectedRetryable = retryable.filter((item) => picked.includes(item.id)).map((item) => item.id);
  const info = stats.data;
  const hosts = (info?.top_hosts ?? []).slice(0, 3).map(([host, n]) => `${host} ${count(n)}`).join(' · ');

  const run = (retry?: readonly number[]) => {
    if (running || (retry && !retry.length)) return;
    check.start.mutate(retry ? { retry, checkId } : {});
  };
  const removeGone = (ids?: number[]) => {
    if (removing || !gone.length) return;
    void confirmModal({
      title: '删除失效链接',
      body: `将删除 ${ids?.length ?? gone.length} 条失效链接。删除前会再次检查，删除后无法恢复。`,
      confirmLabel: '删除失效链接',
      danger: true,
      onConfirm: () => prune.start.mutateAsync({ checkId, ids }),
    });
  };

  /* 删除那一趟跑完，结论就是那一句回执：旧清单里的行已经删掉或者确认保留了。 */
  const pruned = prune.outcome;
  const startError = check.start.error ?? prune.start.error;
  let progress: ReactNode = null;
  let result: ReactNode = null;
  if (pruned?.status === 'failed') {
    result = <Note tone="error" title="链接清理未完成">{pruned.error || '请查看任务记录，核对已处理的链接。'}</Note>;
  } else if (pruned && !gone.length && !unclear.length) {
    result = <Note tone="success" title="完成">{pruneText(pruned)}</Note>;
  } else if (startError) {
    result = <Note tone="error" title="检查失败">{errorMessage(startError)}</Note>;
  } else if (check.query.isError && state) {
    result = <Note tone="neutral" title="任务状态">{DISCONNECTED}</Note>;
  } else if (state?.status === 'failed') {
    result = <Note tone="error" title="检查失败">{state.error || '检查失败'}</Note>;
  } else if (state && state.status !== 'idle') {
    progress = <>
      {state.status === 'running'
        ? <TaskProgress embedded label={checkLine(state)} value={Math.min(state.checked || 0, state.total || 0)} total={state.total} />
        : null}
      {removing
        ? <TaskProgress embedded label={prune.job?.message || '正在重验并删除失效链接…'}
            value={Math.min(prune.job?.checked || 0, prune.job?.total || 0)} total={prune.job?.total} />
        : null}
    </>;
    const pickedLabel = selectedRetryable.length ? `重试选中的 ${selectedRetryable.length} 条` : '重试选中的链接';
    result = done || gone.length || unclear.length ? (
      <div className={RESULT_PANEL}>
        {pruned?.status === 'complete' ? <Note tone="success" title="完成">{pruneText(pruned)}</Note> : null}
        <LinkTable title="地址已失效" items={gone} hint={GONE_HINT} picked={gonePicked} onPick={setGonePicked}>
          <Button variant="danger" size="small" disabled={!done || !selectedGone.length}
            {...busyProps(removing)} onClick={() => removeGone(selectedGone)}>
            {`删除所选（${selectedGone.length}）`}
          </Button>
        </LinkTable>
        <LinkTable title="本次未访问成功" items={unclear} hint={UNCLEAR_HINT}
          picked={picked} onPick={setPicked} footer={retryable.length
            ? <Button leadingIcon={RETRY} {...busyProps(running)}
                onClick={() => run(retryable.map((item) => item.id))}>{`全部重试（${retryable.length}）`}</Button>
            : null}>
          {retryable.length
            ? <>
                  <Button leadingIcon={RETRY} disabled={!selectedRetryable.length} {...busyProps(running)}
                    onClick={() => run(selectedRetryable)}>{pickedLabel}</Button>
              </>
            : null}
        </LinkTable>
        {done && gone.length
          ? <PanelFooter tone="error" status={PRUNE_HINT}>
              <Button variant="danger" {...busyProps(removing)} onClick={() => removeGone()}>
                {`删除 ${gone.length} 条失效链接`}
              </Button>
            </PanelFooter>
          : null}
        {done && !gone.length && !unclear.length
          ? <p className={`${RESULT_SECTION} text-body-regular text-notification-success-foreground`}>全部链接均可访问。</p>
          : null}
      </div>
    ) : null;
  }

  return (
    <section id="link-manager" aria-labelledby="link-manager-title" className="flex scroll-mt-20 flex-col gap-4">
      <Fieldset layout="stack" labelledBy="link-manager-title" footer={
        <Button leadingIcon={UNLINK} {...busyProps(running)} onClick={() => run()}>
          {running ? '检查中' : done ? '重新检查' : '检查死链'}
        </Button>
      }>
        <FieldsetTitle id="link-manager-title" gap="small">链接管理</FieldsetTitle>
        {stats.isError
          ? <Note tone="error" title="链接统计读取失败">{errorMessage(stats.error)}</Note>
          : info
            ? <>
                <div className="link-stat-grid mt-4 gap-x-8 gap-y-3">
                  <Stat label="链接总数" value={count(info.total)}
                    note={`分布在 ${count(info.entities)} 个女优、厂牌与系列`} />
                  {Object.entries(info.by_kind ?? {}).map(([kind, value]) => (
                    <Stat key={kind} label={LINK_KINDS[kind] || kind} value={count(value)} />
                  ))}
                </div>
                {hosts
                  ? <div className="mt-4 flex min-w-0 flex-col gap-0.5">
                      <span className="text-caption-1-regular text-text-secondary">主要站点</span>
                      <b className="text-body-2-medium break-words text-text-primary">{hosts}</b>
                    </div>
                  : null}
              </>
            : null}
        <div aria-live="polite" className="mt-4 flex flex-col gap-3 empty:hidden">{progress}</div>
      </Fieldset>
      <div aria-live="polite" className="empty:hidden">{result}</div>
    </section>
  );
}
