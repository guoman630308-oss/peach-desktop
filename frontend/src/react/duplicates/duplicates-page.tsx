/* 重复文件页：同番号、时长相近、分卷标记一致的几份文件归成一组，每组挑一个留下。
 *
 * 顶上一块汇总、一条批量保留的玻璃条，下面每组一张卡：组头说明判据与可回收多少，行里是
 * 每一份文件。留谁由人点；其余的走 `/api/batch` 的 `dispose` 进回收站，回执上带撤销。 */
import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { confirmModal, MEDIA_SOURCE_ICONS } from '@peach/legacy/ui';
import { fmtDur, fmtSize, LOC } from '@peach/legacy/core';

import { Button } from '@/components/base/buttons/button';

import { errorMessage } from '../../api';
import type { DuplicatesProps } from '../bundle';
import { cardClass } from '../components/card';
import { CollectionSummary } from '../components/collection-summary';
import { EmptyState } from '../components/empty-state';
import { FilterGlass, GlassPill } from '../components/filter-glass';
import { Note } from '../components/note';
import { SourceLabel } from '../components/source-label';
import { spriteGlyph } from '../components/sprite-glyph';
import { TRASH_KEY } from '../data-cleanup/data-cleanup';
import { cloudLocations, fetchMediaSources, MEDIA_SOURCES_KEY } from '../media-sources';
import { queryClient } from '../query';
import { SourceMark } from '../settings/section';
import { busyProps } from '../settings/use-action';
import {
  batchOperate, bulkCloudPreferences, distinctName, DUPLICATES_KEY, duplicateVictims, fetchDuplicates, KEEP_LABELS,
  mixedCloudPreferences, sharedNamePrefix, victimBytes, type DuplicateFile, type DuplicateGroup, type KeepRule,
} from './duplicates';

const PLAY = spriteGlyph('play');
const STACK = spriteGlyph('file-stack');

const GROUP = cardClass({
  padding: 'none', radius: 'surface',
  className: 'mb-6 overflow-hidden dark:bg-background-primary-default',
});
const MARK = 'rounded-sm px-1.5 py-px text-caption-1-regular leading-5';
const FLAG = 'rounded-sm px-2 py-0.5 text-caption-1-regular leading-5';
const MONO = 'font-mono text-caption-1-regular leading-5 text-text-secondary';

/** 改完之后要重读的两份：这一页本身，和数据管理页那张回收站读数。 */
const refresh = () => {
  void queryClient.invalidateQueries({ queryKey: DUPLICATES_KEY, exact: true });
  void queryClient.invalidateQueries({ queryKey: TRASH_KEY, exact: true });
};

interface Disposal { groups: readonly DuplicateGroup[]; keep: KeepRule; key: string }

function evidenceFlag(group: DuplicateGroup) {
  if (group.identical) return <span className={`${FLAG} text-(--keep)`}>sha1 一致</span>;
  return <span className={`${FLAG} text-text-secondary`}>
    {group.evidence === 'same_code_short_copy' ? '同番号短版本' : '时长推断'}
  </span>;
}

/** 一份副本。`prefix` 是组内共有的名字开头，显示时用「…」顶替，整名在 title 里。 */
function FileRow({ file, prefix, openItem }: { file: DuplicateFile; prefix: string; openItem(id: number): void }) {
  const open = () => openItem(file.id);
  const location = LOC[file.location] || file.location || file.drive;
  return (
    <div className="duplicate-row items-center gap-3 border-t border-separator-border px-5 py-4 max-compact:p-4">
      {/* 封面格居中用 `inline-grid`：`grid` 与旧样式表同名，不生成这个工具类。 */}
      <button type="button" onClick={open} aria-label={`预览 ${file.name}`}
        className="relative row-span-2 inline-grid aspect-16/10 w-full cursor-pointer place-items-center overflow-hidden rounded-2lg bg-background-tertiary-default text-text-secondary">
        <PLAY aria-hidden className="size-6" />
        <img src={`/thumb?id=${file.id}&c=4`} alt="" loading="lazy"
          onError={(event) => event.currentTarget.remove()}
          className="absolute inset-0 size-full object-contain" />
      </button>
      <span className="flex min-w-0 items-center gap-2 max-duplicate-narrow:col-start-2 max-duplicate-narrow:-col-end-1">
        <button type="button" onClick={open} title={file.name} data-middle-truncate data-middle-truncate-within
          className="min-w-0 shrink cursor-pointer truncate text-left text-body-regular hover:text-text-secondary">
          {distinctName(file.name, prefix)}
        </button>
        {file.is_largest || file.is_longest
          ? <span className="flex flex-none gap-1">
              {file.is_largest ? <i className={`${MARK} not-italic bg-background-primary-hover text-text-secondary`}>最大</i> : null}
              {file.is_longest ? <i className={`${MARK} not-italic bg-(--keep)/22 text-(--keep)`}>最长</i> : null}
            </span>
          : null}
      </span>
      <span className={`${MONO} flex min-w-0 items-center gap-1.5`}>
        <SourceMark mark={MEDIA_SOURCE_ICONS[file.location] || 'database'} />
        <span title={location} className="min-w-0 truncate">{location}</span>
      </span>
      <span className={MONO}>{fmtSize(file.size || 0)}</span>
      <span className={MONO}>{fmtDur(file.duration)}</span>
      {/* 路径整条折行显示：同一组的几份常只在中段不同，截断会让它们看起来一模一样。 */}
      <span className={`${MONO} col-start-2 -col-end-1 min-w-0 pt-0.5 wrap-anywhere max-duplicate-narrow:col-span-full`}>
        {file.path ?? ''}
      </span>
    </div>
  );
}

export function DuplicatesPage({ openItem, toast, failure }: DuplicatesProps) {
  const duplicates = useQuery({ queryKey: DUPLICATES_KEY, queryFn: ({ signal }) => fetchDuplicates(signal) });
  const sources = useQuery({ queryKey: MEDIA_SOURCES_KEY, queryFn: ({ signal }) => fetchMediaSources(signal) });
  const [pending, setPending] = useState('');
  const dispose = useMutation({
    mutationFn: (ids: readonly number[]) => batchOperate(ids, 'dispose'),
    onSuccess: (_result, ids) => {
      refresh();
      toast(`已把 ${ids.length} 项移入回收站`, {
        undo: async () => {
          try {
            await batchOperate(ids, 'restore');
          } catch (cause) {
            failure('撤销移入回收站', cause);
          } finally {
            refresh();
          }
        },
      });
    },
    onSettled: () => setPending(''),
  });

  const data = duplicates.data;
  if (!data) {
    return (
      <div className="mx-auto w-full max-w-board">
        <Note tone="error" title="重复文件读取失败">{duplicates.error ? errorMessage(duplicates.error) : '未取得重复文件列表，请刷新页面重试。'}</Note>
      </div>
    );
  }
  const groups = data.groups ?? [];
  const configured = cloudLocations(sources.data);

  const run = ({ groups: chosen, keep, key }: Disposal) => {
    if (dispose.isPending) return;
    const ids = duplicateVictims(chosen, keep);
    if (!ids.length) return;
    void confirmModal({
      title: '移入回收站',
      body: `将把 ${ids.length} 个重复文件的馆藏记录移入回收站，每组保留${KEEP_LABELS[keep] ?? KEEP_LABELS.largest}。`
        + `文件共 ${fmtSize(victimBytes(chosen, ids))}，记录可从回收站还原。`,
      confirmLabel: '移入回收站',
      onConfirm: () => { setPending(key); return dispose.mutateAsync(ids) },
    });
  };
  const busy = (key: string) => busyProps(pending === key && dispose.isPending);

  return (
    <div className="mx-auto w-full max-w-board pb-10.5">
      <CollectionSummary label="重复内容" figure={`${Number(data.total || 0).toLocaleString()} 组`}
        detail={`${Number(data.files || 0).toLocaleString()} 个文件 · 可回收 ${fmtSize(data.reclaimable)}`} />
      {groups.length
        ? <FilterGlass title="批量保留">
            <GlassPill busy={pending === 'all:largest'} onPress={() => run({ groups, keep: 'largest', key: 'all:largest' })}>
              全部保留最大
            </GlassPill>
            <GlassPill busy={pending === 'all:longest'} onPress={() => run({ groups, keep: 'longest', key: 'all:longest' })}>
              全部保留最长
            </GlassPill>
            {bulkCloudPreferences(groups, configured).map((location) => (
              <GlassPill key={location} busy={pending === `all:${location}`}
                onPress={() => run({ groups, keep: location, key: `all:${location}` })}>
                <SourceLabel source={location} label={`全部优先 ${LOC[location] || location}`} />
              </GlassPill>
            ))}
          </FilterGlass>
        : null}
      {dispose.isError
        ? <div className="mb-5"><Note tone="error" title="移入回收站失败">{errorMessage(dispose.error)}</Note></div>
        : null}
      {groups.length
        ? groups.map((group, index) => {
            const one = [group];
            const prefix = sharedNamePrefix(group.files.map((file) => file.name));
            const key = (keep: string) => `${index}:${keep}`;
            return (
              <section key={`${group.code}-${index}`} aria-label={group.code} className={GROUP}>
                <div className="flex flex-wrap items-center gap-3 bg-background-tertiary-default p-5 max-compact:p-4 dark:bg-background-secondary-default">
                  <b className="text-title-2-medium text-text-primary">{group.code}</b>
                  <span className="font-mono text-caption-1-regular leading-5 text-text-primary">
                    {`${Number(group.count || 0).toLocaleString()} 个 · 可回收 ${fmtSize(group.reclaimable)}`}
                  </span>
                  {evidenceFlag(group)}
                  {group.cross_drive
                    ? <span className={`${FLAG} text-text-secondary`}>{`跨盘 ${group.drives.join(' ')}`}</span>
                    : null}
                  <span className="ml-auto flex flex-wrap gap-2 max-compact:ml-0 max-compact:w-full">
                    <Button variant="secondary" size="small" {...busy(key('largest'))}
                      onClick={() => run({ groups: one, keep: 'largest', key: key('largest') })}>留最大</Button>
                    <Button variant="secondary" size="small" {...busy(key('longest'))}
                      onClick={() => run({ groups: one, keep: 'longest', key: key('longest') })}>留最长</Button>
                    {mixedCloudPreferences(group.files, configured).map((location) => (
                      <Button key={location} variant="secondary" size="small" {...busy(key(location))}
                        onClick={() => run({ groups: one, keep: location, key: key(location) })}>
                        <SourceLabel source={location} label={`留 ${LOC[location] || location}`} />
                      </Button>
                    ))}
                    <Button variant="danger" size="small" {...busy(key('all'))}
                      onClick={() => run({ groups: one, keep: 'all', key: key('all') })}>整组回收</Button>
                  </span>
                </div>
                <div>{group.files.map((file) => <FileRow key={file.id} file={file} prefix={prefix} openItem={openItem} />)}</div>
              </section>
            );
          })
        : <EmptyState icon={STACK} title="没有找到重复文件">
            所有来源之间没有检测到内容相同的文件。扫描新来源后，这里会自动更新。
          </EmptyState>}
      {groups.length > 0 && data.total > groups.length
        ? <p className="text-center text-caption-1-regular text-text-secondary">
            {`显示可回收最多的前 ${groups.length.toLocaleString()} 组，共 ${Number(data.total).toLocaleString()} 组；处理掉这些后，其余的会补上来。`}
          </p>
        : null}
    </div>
  );
}
