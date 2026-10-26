/* 卡片下方的磁链候选。可见时查询，提交复用云下载的幂等与失败分类。 */
import { useEffect, useMemo, useRef, useState, type ComponentType, type ReactNode } from 'react';
import { RiLink, RiHardDrive2Line, RiFileList2Line,
  RiCalendarLine, RiPriceTag3Line, RiExternalLinkLine, RiFileCopyLine, RiCloudLine, RiRefreshLine } from '@remixicon/react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, ButtonLink } from '@/components/base/buttons/button';
import { Chip } from '@/components/base/badges/chip';
import { apiGet, apiSend, errorMessage } from '../../api';
import type { DownloadsSnapshot, DownloadSubmitResult } from '../bundle';
import { DOWNLOADS_KEY, DOWNLOADS_URL } from '../activity/downloads-panel';
import { LoadingDots } from '../components/loading-dots';
import { Note } from '../components/note';
import { busyProps, useAction } from '../settings/use-action';
import type { Want } from './wants';
import { recordWantMagnets } from './wants';
import { Disclosure } from '../settings/section';
import { cachedResources, rememberResources, resourcesFresh } from './resource-cache';
import { PROVIDER_ICONS } from '../components/source-label';
import { resourceNotice, resourceSize } from './resource-display';
import './want-magnets.css';

export interface MagnetCandidate {
  id: string; protocol: 'magnet' | 'ed2k' | 'url'; info_hash: string; uri: string;
  name: string; size: string; files: string; date: string;
  attributes: string[]; source: string; source_url: string; origins: string[];
}
export interface MagnetResult {
  state: 'ready' | 'error' | 'busy' | 'unavailable'; items: MagnetCandidate[];
  error: string; checked_at: string | null; warnings?: string[];
  source_url?: string;
  cache_stale?: boolean;
  complete?: boolean;
}

function ResourceFact({ icon: Icon, children, title }: { icon: ComponentType<{ className?: string; 'aria-hidden'?: boolean | 'true' | 'false' }>; children: ReactNode; title?: string }) {
  return <span title={title} className="inline-flex items-center gap-1"><Icon aria-hidden className="size-3.5 shrink-0" />{children}</span>;
}

export function WantMagnets({ want, readOnly, downloads, provider, toast, onSource, queryUrl }: {
  want: Pick<Want, 'id' | 'code' | 'title' | 'release_date'>; readOnly: boolean; downloads?: DownloadsSnapshot; provider: string; toast(message: string): void;
  onSource?(url: string): void;
  queryUrl?: string;
}) {
  const root = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(typeof IntersectionObserver === 'undefined');
  const [submitted, setSubmitted] = useState<string[]>([]);
  const action = useAction();
  const client = useQueryClient();
  const refresh = useRef(false);
  const cacheCode = queryUrl ? `comment-resources:${queryUrl}:${want.code}` : `all-sources:${want.code || ''}`;
  const cached = useMemo(() => cachedResources(cacheCode, want.release_date), [cacheCode, want.release_date]);
  useEffect(() => {
    if (!root.current || visible) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) { setVisible(true); observer.disconnect() }
    }, { rootMargin: '100px' });
    observer.observe(root.current);
    return () => observer.disconnect();
  }, [visible]);
  const query = useQuery({
    queryKey: queryUrl ? ['work-resources', queryUrl, want.code] : ['want-magnets', want.id, want.code],
    queryFn: async ({ signal }) => {
      const forced = refresh.current;
      const url = queryUrl || `/api/wants/magnets?id=${want.id}`;
      const result = await apiGet<MagnetResult>(`${url}${forced ? `${url.includes('?') ? '&' : '?'}refresh=1` : ''}`, signal);
      refresh.current = forced && result.state === 'busy';
      rememberResources(cacheCode, result);
      return result;
    },
    initialData: cached?.result, initialDataUpdatedAt: cached?.saved,
    enabled: visible, retry: false,
    // 发行日期与来源时间戳判定有效性，定时器只负责一天内的内存回收。
    staleTime: (state) => resourcesFresh(state.state.data,
      Date.parse(state.state.data?.checked_at || '') || state.state.dataUpdatedAt, want.release_date) ? Infinity : 0,
    gcTime: 86_400_000,
    refetchOnMount: (state) => resourcesFresh(state.state.data,
      Date.parse(state.state.data?.checked_at || '') || state.state.dataUpdatedAt, want.release_date) ? false : 'always',
    refetchOnWindowFocus: false,
    refetchInterval: (state) => state.state.data?.state === 'busy' ? 3000 : false,
  });
  useEffect(() => {
    if (query.data?.source_url) onSource?.(query.data.source_url);
  }, [query.data?.source_url, onSource]);
  useEffect(() => {
    if (!queryUrl && query.data && (query.data.state === 'ready' || query.data.items.length > 0))
      recordWantMagnets(want.id, query.data.items.some(item => item.protocol === 'magnet'));
  }, [query.data, queryUrl, want.id]);
  const configured = downloads?.providers.find((row) => row.key === provider && row.configured);
  const add = (candidate: MagnetCandidate) => {
    if (readOnly || !downloads?.available || !configured || candidate.protocol !== 'magnet') return;
    void action.run(candidate.id, async (signal) => {
      const result = await apiSend<DownloadSubmitResult>(DOWNLOADS_URL, {
        magnet: candidate.uri, provider: configured.key, target: configured.target,
        code: want.code, title: want.title, origin: queryUrl ? `work:${want.code}` : `wishlist:${want.id}`,
      }, 'POST', signal);
      if (!result.ok) throw new Error(result.task.failure_detail || result.task.failure_label || '添加下载失败');
      return result;
    }, (result) => {
      setSubmitted((current) => [...current, candidate.id]);
      toast(result.outcome === 'submitted' ? `已添加下载：${candidate.name}` : `已关联现有下载：${candidate.name}`);
      void client.invalidateQueries({ queryKey: DOWNLOADS_KEY });
    });
  };
  const copy = (candidate: MagnetCandidate) => {
    void action.run(`copy:${candidate.id}`, () => navigator.clipboard.writeText(candidate.uri),
      () => toast('已复制链接'));
  };
  const waiting = query.isFetching || query.data?.state === 'busy';
  const failure = query.error ? errorMessage(query.error) : query.data?.error;
  return (
    <div ref={root} data-want-magnets="" className="flex min-w-0 flex-col gap-3 border-t border-separator-border pt-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-body-medium text-text-primary">资源链接{query.data?.items.length ? ` · ${query.data.items.length}` : ''}</h4>
        <Button variant="secondary" size="small" leadingIcon={RiRefreshLine} {...busyProps(waiting)} onClick={() => {
          if (!waiting) { refresh.current = true; void query.refetch() }
        }}>重新查询</Button>
      </div>
      {waiting ? <LoadingDots label="正在查找资源链接…" /> : null}
      {failure ? <Note tone="error" title="资源链接未取得">{failure}</Note> : null}
      {query.data?.warnings?.map((warning) => <Note key={warning} tone="neutral">{resourceNotice(warning)}</Note>)}
      {query.data?.warnings?.length && query.data.source_url ? <ButtonLink variant="secondary" size="small" className="self-start"
        trailingIcon={RiExternalLinkLine} href={query.data.source_url} target="_blank" rel="noopener noreferrer">查看来源页</ButtonLink> : null}
      {query.data?.state === 'ready' && !query.data.items.length && !query.data.warnings?.length
        ? <Note tone="neutral">暂未找到资源链接。</Note> : null}
      {action.error ? <Note tone="error" title="操作未完成">{action.error}</Note> : null}
      {query.data?.items.length ? (() => {
        const rows = query.data.items.map((candidate) => {
          const added = submitted.includes(candidate.id) || downloads?.tasks.some((task) => candidate.protocol === 'magnet' &&
            task.info_hash === candidate.info_hash && (task.cancellable || task.state === 'ingested'));
          return <li key={candidate.id} data-resource-protocol={candidate.protocol}>
            <div data-resource-main="" className="flex min-w-0 flex-col gap-1">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <Chip variant="caption" color={candidate.protocol === 'magnet' ? 'blue' : candidate.protocol === 'ed2k' ? 'purple' : 'neutral'}>
                  {candidate.protocol === 'magnet' ? '磁链' : candidate.protocol === 'ed2k' ? 'ED2K' : '网页链接'}
                </Chip>
                <p data-resource-title="" title={candidate.name} className="min-w-0 flex-1 text-body-2-medium text-text-primary">{candidate.name}</p>
              </div>
              <div data-resource-facts="" className="flex flex-wrap gap-x-2 gap-y-1 text-caption-1-regular text-text-secondary">
                <ResourceFact icon={RiLink}>{candidate.source}</ResourceFact>
                <ResourceFact icon={RiHardDrive2Line} title={`来源大小：${candidate.size}`}>{resourceSize(candidate.size)}</ResourceFact>
                {candidate.files ? <ResourceFact icon={RiFileList2Line}>{candidate.files.replaceAll('個', '个')}</ResourceFact> : null}
                <ResourceFact icon={RiCalendarLine} title={candidate.source === 'JavDB 评论' ? '评论日期' : '收录日期'}>{candidate.date ? candidate.date.slice(0, 10) : '日期未知'}</ResourceFact>
                {candidate.attributes.filter(value => value !== '高清').map((value) => <ResourceFact key={value} icon={RiPriceTag3Line}>{value}</ResourceFact>)}
              </div>
            </div>
            <div data-resource-actions="" className="flex flex-wrap gap-2">
              {candidate.protocol === 'magnet' ?
              <Button variant="primary" size="small" leadingIcon={configured ? PROVIDER_ICONS[configured.key] : RiCloudLine} title={configured ? `下载到 ${configured.label}` : undefined} disabled={readOnly || !downloads?.available || !configured || added}
                {...busyProps(action.busy === candidate.id)} onClick={() => add(candidate)}>
                {added ? '已添加' : '添加下载'}
              </Button> : null}
              <Button variant={candidate.protocol === 'magnet' ? 'secondary' : 'primary'} size="small" leadingIcon={RiFileCopyLine} {...busyProps(action.busy === `copy:${candidate.id}`)}
                onClick={() => copy(candidate)}>复制链接</Button>
              <ButtonLink data-button-link="" variant="secondary" size="small" trailingIcon={RiExternalLinkLine} href={candidate.source_url} target="_blank" rel="noopener noreferrer">链接来源</ButtonLink>
            </div>
          </li>;
        });
        return <>
          <ul className="flex min-w-0 flex-col gap-4">{rows.slice(0, 3)}</ul>
          {rows.length > 3 ? <Disclosure summary={`更多资源（${rows.length - 3} 条）`}>
            <ul className="flex min-w-0 flex-col gap-4">{rows.slice(3)}</ul>
          </Disclosure> : null}
        </>;
      })() : null}
    </div>
  );
}
