/* 数据管理页上的「孤儿记录」：文件已不在盘上、带着个人记录的作品（ADR-0087）。
 *
 * 每行一条已消失的作品：快照图供认片，后面是它带的记录、在库里可能是它新版本的文件。
 * 选一个文件接过去，记录搬到那个文件上、旧行删掉，整批可按批次号撤回；彻底删除连记录一起
 * 删，过危险档确认弹层。没有已消失的作品时整块不出现。 */
import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { confirmModal } from '@peach/legacy/ui';
import { LOC } from '@peach/legacy/core';

import { Button } from '@/components/base/buttons/button';
import { Select, SelectItem } from '@/components/base/select/select';

import { errorMessage } from '../../api';
import { DataTable, type DataColumn } from '../components/data-table';
import { Fieldset, FieldsetTitle } from '../components/fieldset';
import { Note } from '../components/note';
import { spriteGlyph } from '../components/sprite-glyph';
import { queryClient } from '../query';
import { busyProps } from '../settings/use-action';
import {
  attachOrphan, candidateText, fetchOrphanRecords, ORPHAN_RECORDS_KEY, purgeOrphan, recordsText,
  type OrphanRecord,
} from './orphan-records';

const PLAY = spriteGlyph('play');

const refresh = () => queryClient.invalidateQueries({ queryKey: ORPHAN_RECORDS_KEY, exact: true });

function OrphanActions({ item, toast }: { item: OrphanRecord; toast(message: string): void }) {
  const [target, setTarget] = useState<number | null>(item.candidates[0]?.id ?? null);
  const attach = useMutation({
    mutationFn: () => attachOrphan(item.id, Number(target)),
    onSuccess: (data) => { toast(`记录已接到新文件，批次 ${data.batch}`); void refresh(); },
    onError: (error) => toast(errorMessage(error)),
  });
  const purge = useMutation({
    mutationFn: () => purgeOrphan(item.id),
    onSuccess: (data) => {
      toast(data.blocked.length
        ? `${item.name} 的文件又回到盘上，没有删除；下一次扫描会把它接回在库`
        : `已彻底删除 ${item.name}`);
      void refresh();
    },
  });
  const remove = () => {
    void confirmModal({
      title: '彻底删除',
      body: `将删除「${item.name}」这一条和它带的记录（${recordsText(item.records)}）。此操作不可撤销。`,
      confirmLabel: '彻底删除',
      danger: true,
      onConfirm: () => purge.mutateAsync(),
    });
  };
  const busy = attach.isPending || purge.isPending;
  return (
    <span className="flex min-w-48 flex-wrap items-center gap-2">
        {item.candidates.length
          ? <Select aria-label={`把 ${item.name} 的记录接到哪个文件`} className="w-52" selectedKey={target}
              onSelectionChange={(key) => { if (key !== null) setTarget(Number(key)) }}>
              {item.candidates.map((candidate) => (
                <SelectItem key={candidate.id} id={candidate.id} textValue={candidateText(candidate)}>
                  {candidateText(candidate)}
                </SelectItem>
              ))}
            </Select>
          : <span className="text-text-secondary">没有番号或文件名对得上的文件</span>}
        {item.candidates.length
          ? <Button size="small" disabled={target === null} {...busyProps(busy)}
              onClick={() => attach.mutate()}>接到这个文件</Button>
          : null}
        <Button variant="danger" size="small" {...busyProps(busy)} onClick={remove}>彻底删除</Button>
    </span>
  );
}

function OrphanIdentity({ item }: { item: OrphanRecord }) {
  return (
        <span className="flex min-w-0 items-center gap-3">
          {/* 快照图供认片；没有图或取不到时留一格带描边的占位，和重复文件页的封面格同一个样子。
              窄屏收起，把宽度让给名字与记录。 */}
          <span className="relative inline-grid aspect-16/10 w-24 flex-none place-items-center max-cleanup-tight:hidden overflow-hidden rounded-lg border border-separator-border bg-background-tertiary-default text-text-tertiary">
            <PLAY aria-hidden className="size-5" />
            {item.has_thumb
              ? <img src={`/thumb?id=${item.id}&c=4`} alt="" loading="lazy"
                  onError={(event) => event.currentTarget.remove()}
                  className="absolute inset-0 size-full object-contain" />
              : null}
          </span>
          <span className="flex min-w-0 flex-col gap-0.5">
            <span title={item.name} className="max-w-56 truncate text-body-regular text-text-primary">{item.name}</span>
            <small className="max-w-56 truncate text-caption-1-regular text-text-secondary">
              {[item.code, LOC[item.location] || item.location].filter(Boolean).join(' · ')}
            </small>
          </span>
        </span>
  );
}

export function OrphanRecordsCard({ toast }: { toast(message: string): void }) {
  const [selected, setSelected] = useState<string[]>([]);
  const query = useQuery({ queryKey: ORPHAN_RECORDS_KEY, queryFn: ({ signal }) => fetchOrphanRecords(signal) });
  const items = query.data?.items ?? [];
  const purge = useMutation({
    mutationFn: (ids: number[]) => purgeOrphan(ids),
    onSuccess: (data) => {
      toast(`已彻底删除 ${data.purged} 条记录${data.blocked.length ? `，${data.blocked.length} 条文件已恢复，未删除` : ''}`);
      setSelected([]); void refresh();
    },
  });
  const removeSelected = (chosen: OrphanRecord[]) => void confirmModal({
    title: '彻底删除所选记录',
    body: `将删除以下 ${chosen.length} 条作品和它们带的全部记录。此操作不可撤销。`,
    items: chosen.map((item) => item.name),
    confirmLabel: '彻底删除所选记录', danger: true,
    onConfirm: () => purge.mutateAsync(chosen.map((item) => item.id)),
  });
  const columns: DataColumn<OrphanRecord>[] = [
    { id: 'item', label: '作品', value: (item) => item.name, render: (item) => <OrphanIdentity item={item} /> },
    { id: 'records', label: '带的记录', value: (item) => recordsText(item.records),
      render: (item) => <span className="block min-w-28">{recordsText(item.records) || '无个人记录'}</span> },
    { id: 'target', label: '接到与操作', value: (item) => item.candidates.length,
      render: (item) => <OrphanActions item={item} toast={toast} /> },
  ];
  if (!query.isError && !items.length) return null;
  return (
    <section id="orphan-records" aria-labelledby="orphan-records-title" className="flex scroll-mt-20 flex-col gap-4">
        {query.isError
          ? <Fieldset layout="stack" labelledBy="orphan-records-title">
              <FieldsetTitle id="orphan-records-title">孤儿记录</FieldsetTitle>
              <Note tone="error" title="孤儿记录读取失败">{errorMessage(query.error)}</Note>
            </Fieldset>
          : <DataTable label="文件已消失的作品" items={items} columns={columns}
              title="孤儿记录" titleId="orphan-records-title" unit="条作品"
              description="这些作品的文件已不在盘上，喜欢、稍后看、播放列表与观看历史都还留着。接到新文件后记录跟过去，旧的这一条删掉。"
              rowId={(item) => String(item.id)} rowLabel={(item) => item.name}
              selected={selected} onSelected={setSelected}
              actions={(chosen) => <Button variant="danger" size="small" disabled={!chosen.length}
                {...busyProps(purge.isPending)} onClick={() => removeSelected(chosen)}>
                {`彻底删除所选（${chosen.length}）`}
              </Button>} />}
    </section>
  );
}
