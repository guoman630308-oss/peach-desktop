import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { cardArtwork } from '@peach/card-art';

import { LinkButton } from '@/components/base/buttons/link-button';
import { RiArrowRightLine } from '@remixicon/react';

import { apiSend, errorMessage } from '../../api';
import type { MediaItem } from '../catalog-grid/types';
import { Note } from '../components/note';
import { queryClient } from '../query';
import {
  DISCONNECTED_TEXT, fetchLibraryProcessing, LIBRARY_PROCESSING_KEY, LIBRARY_PROCESSING_URL,
  pollInterval, processingActive, type LibraryProcessingCommand, type LibraryProcessingData,
} from './library-processing';
import { ProcessingActions } from './processing-actions';

/** 缺图判据与卡面一致；本机影片可抽帧，有番号的挂载影片可补发行封面。 */
type PreviewItem = Pick<MediaItem, 'id' | 'name' | 'medium' | 'disposal' | 'location' | 'code' | 'is_jav'
  | 'has_cover' | 'cover_key' | 'has_thumb' | 'has_local_poster' | 'follow_thumb_url'>;

export function missingPreviewIds(items: PreviewItem[], javImage: string): number[] {
  return [...new Set(items.filter(item => (!item.medium || item.medium === 'video')
    && (!/\.(strm|iso)$/i.test(item.name || '') || !!item.code)
    && !item.disposal && item.location !== 'online' && (item.location === 'local' || !!item.code)
    && !cardArtwork(item, 'small', false, javImage).html).map(item => item.id))];
}

export function LibraryCompletionPrompt({ assetIds, onComplete }: { assetIds: number[]; onComplete?(): void }) {
  return assetIds.length ? <Completion assetIds={assetIds} onComplete={onComplete} /> : null;
}

function Completion({ assetIds, onComplete }: { assetIds: number[]; onComplete?(): void }) {
  const job = useQuery({ queryKey: LIBRARY_PROCESSING_KEY,
    queryFn: ({ signal }) => fetchLibraryProcessing(signal),
    refetchInterval: query => pollInterval(query.state.data, true) });
  const [started, setStarted] = useState<string | null>(null);
  const [outcome, setOutcome] = useState('');
  const completed = useRef<string | null>(null);
  const start = useMutation({
    mutationFn: (command: LibraryProcessingCommand) =>
      apiSend<LibraryProcessingData>(LIBRARY_PROCESSING_URL, { ...command, asset_ids: assetIds }),
    onSuccess: next => {
      setStarted(next.job_id || null);
      queryClient.setQueryData(LIBRARY_PROCESSING_KEY, next);
      void queryClient.invalidateQueries({ queryKey: LIBRARY_PROCESSING_KEY });
    },
  });
  const state = job.data;
  useEffect(() => {
    if (!state || !started || state.job_id !== started || processingActive(state)
      || completed.current === started) return;
    completed.current = started;
    if (state.status === 'complete') {
      setOutcome(`补全完成：取得 ${state.covers || 0} 张封面，生成 ${state.previews || 0} 部视频预览。`);
    }
    void queryClient.invalidateQueries({ queryKey: ['catalog-grid'] });
    onComplete?.();
  }, [state, started, onComplete]);
  // 运行中的进度与控制由常驻任务卡承接，缺图提示只在可发起补全时呈现。
  if (processingActive(state)) return null;
  const busy = start.isPending || processingActive(state);
  const problem = start.error ? errorMessage(start.error) : job.error ? DISCONNECTED_TEXT
    : started && state?.job_id === started && state.status === 'failed' ? state.error || '补全未完成' : '';
  const message = problem || (start.isPending ? '正在启动补全…'
    : outcome || `这批影片中有 ${assetIds.length.toLocaleString()} 部暂无预览，可补全资料与图片。`);
  return <section aria-label="补全预览" aria-live="polite" data-library-completion="" className="@container mb-4">
    <Note tone={problem ? 'warning' : 'neutral'} action={<div className="flex flex-wrap items-center gap-2"><ProcessingActions busy={busy} metadataOnly={state?.metadata_only} onRun={command => {
      if (busy) return;
      setOutcome('');
      start.mutate(command);
    }} /></div>}>
      {message}{' '}<LinkButton href="/data-cleanup#libraryProcessing" size="small" trailingIcon={RiArrowRightLine}>
      {problem ? '查看并处理' : '查看处理详情'}
    </LinkButton></Note>
  </section>;
}
