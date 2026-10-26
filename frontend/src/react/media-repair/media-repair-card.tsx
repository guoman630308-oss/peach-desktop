/* 数据管理页上的「媒体修复」卡片：选一个媒体库，把里面播不了或播不顺的 MP4 批量修好。
 *
 * 卡片和「扫描与采集」同一副形状：左边讲这件事和此刻的进度，页脚放媒体库与起停键；
 * 这一趟的下场挂在卡片外面。跑动时两秒问一次，空闲时不问。 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { confirmModal, MEDIA_SOURCE_ICONS } from '@peach/legacy/ui';

import { Button } from '@/components/base/buttons/button';
import { Select, SelectItem } from '@/components/base/select/select';

import { apiSend } from '../../api';
import { REPAIR_CARD_TEXT } from '../../management';
import { JOB_RUNNING_POLL_MS } from '../background-job';
import { Fieldset, FieldsetTitle } from '../components/fieldset';
import { Note } from '../components/note';
import { Progress } from '../components/progress';
import { queryClient } from '../query';
import { SourceMark } from '../settings/section';
import { busyProps, useAction } from '../settings/use-action';
import {
  fetchMediaRepair, fetchRepairLibraries, IDLE_REPAIR, MEDIA_REPAIR_KEY, MEDIA_REPAIR_URL,
  missingToolText, REPAIR_LIBRARIES_KEY, statusLine, type MediaRepairState,
} from './media-repair';

const libraryMark = (icon: string) => MEDIA_SOURCE_ICONS[icon] || icon;
const replace = (next: MediaRepairState) => queryClient.setQueryData(MEDIA_REPAIR_KEY, next);

export function MediaRepairCard() {
  const repair = useQuery({
    queryKey: MEDIA_REPAIR_KEY, queryFn: ({ signal }) => fetchMediaRepair(signal),
    refetchInterval: (query) => (query.state.data?.status === 'running' ? JOB_RUNNING_POLL_MS : false),
  });
  const choices = useQuery({
    queryKey: REPAIR_LIBRARIES_KEY, queryFn: ({ signal }) => fetchRepairLibraries(signal),
  });
  const [chosen, setChosen] = useState('');
  const action = useAction();
  const state = repair.data ?? IDLE_REPAIR;
  const libraries = choices.data ?? [];
  const running = state.status === 'running';

  // 跑着的那一轮握着下拉框；空闲时是人选的那个，没选过就是上一轮修的库，再没有就是第一个。
  const selected = libraries.find((row) => row.id === (running ? state.library : chosen))
    ?? libraries.find((row) => row.id === state.library) ?? libraries[0];

  const start = () => {
    if (!selected) return;
    void confirmModal({
      title: `修复媒体库「${selected.name}」`,
      body: '缺时间戳表的片子另存一份修好的头，原文件不动；缺索引的片子重建后替换原文件，'
        + '坏原件改名成同目录的隐藏文件（.文件名.peach-original）留着。'
        + (selected.metered ? '这个库在计费来源上，每修一部都要把片子完整拉一遍。' : ''),
      confirmLabel: '修复媒体库',
      onConfirm: async () => replace(await apiSend<MediaRepairState>(MEDIA_REPAIR_URL,
        { library: selected.id, restart: true })),
    });
  };
  const stop = () => void action.run('stop',
    (signal) => apiSend<MediaRepairState>(MEDIA_REPAIR_URL, { stop: true }, 'POST', signal), replace);

  const line = statusLine(state);
  const complete = state.status === 'complete';
  const problem = action.error || state.error || (repair.error ? '读不到媒体修复的状态' : '');
  return (
    <div className="flex flex-col gap-4">
      <Fieldset layout="split" label="媒体修复" footer={
        <>
          <Select aria-label="媒体库" className="w-48" selectedKey={selected?.id ?? null}
            isDisabled={running || !libraries.length}
            onSelectionChange={(key) => { if (key !== null) setChosen(String(key)); }}>
            {libraries.map((row) => (
              <SelectItem key={row.id} id={row.id} textValue={row.name}>
                <SourceMark mark={libraryMark(row.icon)} />{row.name}
              </SelectItem>
            ))}
          </Select>
          {running
            ? <Button onClick={stop} {...busyProps(action.busy === 'stop')}>停止</Button>
            : <Button onClick={start} disabled={!selected}>开始修复</Button>}
        </>
      }>
        <div className="flex flex-col gap-2">
          <FieldsetTitle>媒体修复</FieldsetTitle>
          <p className="text-body-2-regular text-text-secondary">{REPAIR_CARD_TEXT}</p>
          {running && state.total
            ? <Progress label="修复进度" value={state.checked} max={state.total} />
            : null}
          {line ? <p role="status" className="text-caption-1-regular text-text-secondary">{line}</p> : null}
        </div>
      </Fieldset>
      <div aria-live="polite" className="flex flex-col gap-4 empty:hidden">
        {complete && !state.repaired && !state.failed
          ? <Note tone="info" title="没有要修的">这个库里的片子都能直接播。</Note>
          : null}
        {complete && state.missing_tool
          ? <Note tone="warning" title="缺少 untrunc">{missingToolText(state)}</Note>
          : null}
        {problem ? <Note tone="error">{problem}</Note> : null}
      </div>
    </div>
  );
}
