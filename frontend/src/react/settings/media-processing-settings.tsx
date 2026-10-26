import { useQuery } from '@tanstack/react-query';
import { SettingsRow } from '@/components/application/settings/settings-rows';
import { Switch } from '@/components/base/switch/switch';

import { apiGet, apiSend, errorMessage } from '../../api';
import { queryClient } from '../query';
import { THUMBNAIL_JOBS_KEY } from '../settings-panel/settings-data';
import { LIBRARY_PROCESSING_KEY } from '../library-processing/library-processing';
import { ErrorText, Help, Rows, Section, Stack } from './section';
import { useAction } from './use-action';
import { VideoThumbnailControls } from './service-preferences';

const KEY = ['media-processing'] as const;
interface Policy { metadata_only: boolean }

export function MediaProcessingSettings({ receipt }: { receipt(message: string): void }) {
  const policy = useQuery({ queryKey: KEY, queryFn: ({ signal }) => apiGet<Policy>('/api/media-processing', signal) });
  const action = useAction();
  const error = action.error || (policy.error ? errorMessage(policy.error) : '');
  return <Section title="视频预览">
    <Rows>
      <SettingsRow label="生成视频预览" description="为本地视频生成九宫格与时间轴预览；关闭后仍按入库规则探测时长与分辨率。">
        <Switch aria-label="生成视频预览" isSelected={!!policy.data && !policy.data.metadata_only}
          isDisabled={!policy.data || policy.isFetching || !!action.busy}
          onChange={(value) => void action.run('save', (signal) =>
            apiSend<Policy>('/api/media-processing', { metadata_only: !value }, 'POST', signal), (saved) => {
              queryClient.setQueryData(KEY, saved);
              void queryClient.invalidateQueries({ queryKey: THUMBNAIL_JOBS_KEY });
              void queryClient.invalidateQueries({ queryKey: LIBRARY_PROCESSING_KEY });
              receipt(saved.metadata_only ? '已关闭视频预览生成' : '已开启视频预览生成');
            })} />
      </SettingsRow>
    </Rows>
    <VideoThumbnailControls disabled={!policy.data || policy.data.metadata_only || !!action.busy} />
    {action.busy ? <Stack divided><Help role="status">正在保存…</Help></Stack> : null}
    {error ? <Stack divided><ErrorText>{error}</ErrorText></Stack> : null}
  </Section>;
}
