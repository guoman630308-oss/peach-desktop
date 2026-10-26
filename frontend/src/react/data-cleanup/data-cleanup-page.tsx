/* 数据管理页：库里已经有的东西怎么收拾。
 *
 * 照 Board 的 dashboard 模板排：顶上一排读数卡，每张是一条内容在库里的经过、兼那一页的入口；
 * 下面一列是要在这页上做的几件事（扫描与采集、媒体修复、整理），再往下是链接
 * 管理、只在有文件已消失的作品时才出现的孤儿记录，最后是只在有来源配了根目录时才出现的
 * 资源同步。 */
import { useQuery } from '@tanstack/react-query';
import { fmtSize } from '@peach/legacy/core';

import type { DataCleanupProps, DataCleanupSection } from '../bundle';
import { Page } from '../components/page';
import { spriteGlyph, type Glyph } from '../components/sprite-glyph';
import { PLAIN_STAT_STRIP, PlainStat, plainStatClass } from '../components/stat-card';
import { DUPLICATES_KEY, fetchDuplicates } from '../duplicates/duplicates';
import { LibraryProcessingCard } from '../library-processing/library-processing-card';
import { MediaRepairCard } from '../media-repair/media-repair-card';
import { fetchMediaSources, MEDIA_SOURCES_KEY } from '../media-sources';
import { fetchQualityGoals, QUALITY_GOALS_KEY } from '../quality-goals/quality-goals';
import { queryClient } from '../query';
import {
  fetchJunkSummary, fetchReviewCounts, fetchTrashSummary, JUNK_KEY, junkBreakdown,
  REVIEW_COUNTS_KEY, reviewSummary, TRASH_KEY,
} from './data-cleanup';
import { LinkManager } from './link-manager';
import { OrganizeCard } from './organize-card';
import { OrphanRecordsCard } from './orphan-records-card';
import { hasResourceRoots } from './resource-sync';
import { ResourceSyncCard } from './resource-sync-card';

interface Reading { figure: string; meta?: string }

/** 一张读数卡的三种下场：还在取、取到了、取不到。各卡各自失败各自算。 */
function reading<T>(
  query: { data: T | undefined; isError: boolean }, read: (data: T) => Reading,
): Reading {
  if (query.data !== undefined) return read(query.data);
  return { figure: query.isError ? '读取失败' : '—' };
}

function EntryCard(
  { section, label, icon, value, open }:
  { section: DataCleanupSection; label: string; icon: Glyph; value: Reading; open(section: DataCleanupSection): void },
) {
  return (
    <button type="button" className={plainStatClass({ interactive: true })} data-cleanup-go={section}
      onClick={() => open(section)}>
      <PlainStat label={label} icon={icon} figure={value.figure} meta={value.meta} />
    </button>
  );
}

const REVIEW = spriteGlyph('square-check-big');
const QUALITY = spriteGlyph('sparkles');
const DUPLICATES = spriteGlyph('file-stack');
const JUNK = spriteGlyph('file-archive');
const TRASH = spriteGlyph('trash');

/* 扫描与采集跑完，库里多出来的东西会落进这三张读数：复核、高清版与重复文件。 */
const PROCESSING_READINGS = [REVIEW_COUNTS_KEY, QUALITY_GOALS_KEY, DUPLICATES_KEY];
const refreshReadings = () => {
  for (const queryKey of PROCESSING_READINGS) void queryClient.invalidateQueries({ queryKey, exact: true });
};

export function DataCleanupPage({ toast, failure, open }: DataCleanupProps) {
  const review = useQuery({ queryKey: REVIEW_COUNTS_KEY, queryFn: ({ signal }) => fetchReviewCounts(signal) });
  const quality = useQuery({ queryKey: QUALITY_GOALS_KEY, queryFn: ({ signal }) => fetchQualityGoals(signal) });
  const duplicates = useQuery({ queryKey: DUPLICATES_KEY, queryFn: ({ signal }) => fetchDuplicates(signal) });
  const junk = useQuery({ queryKey: JUNK_KEY, queryFn: ({ signal }) => fetchJunkSummary(signal) });
  const trash = useQuery({ queryKey: TRASH_KEY, queryFn: ({ signal }) => fetchTrashSummary(signal) });
  const sources = useQuery({ queryKey: MEDIA_SOURCES_KEY, queryFn: ({ signal }) => fetchMediaSources(signal) });
  const notify = (message: string) => toast(message);

  return (
    <Page>
      <div className={PLAIN_STAT_STRIP}>
        <EntryCard section="review" label="人工复核" icon={REVIEW} open={open}
          value={reading(review, reviewSummary)} />
        <EntryCard section="quality" label="高清版" icon={QUALITY} open={open}
          value={reading(quality, (data) => ({ figure: `${Number(data.total || 0).toLocaleString()} 个待升级` }))} />
        <EntryCard section="duplicates" label="重复文件" icon={DUPLICATES} open={open}
          value={reading(duplicates, (data) => (Number(data.total || 0)
            ? {
                figure: `${Number(data.total).toLocaleString()} 组 · ${Number(data.files || 0).toLocaleString()} 个文件`,
                meta: `可回收 ${fmtSize(data.reclaimable || 0)}`,
              }
            : { figure: '没有重复内容' }))} />
        <EntryCard section="ads" label="垃圾文件" icon={JUNK} open={open}
          value={reading(junk, (data) => ({
            figure: `${Number(data.pending_total || 0).toLocaleString()} 个待判断`, meta: junkBreakdown(data),
          }))} />
        <EntryCard section="trash" label="回收站" icon={TRASH} open={open}
          value={reading(trash, (data) => ({
            figure: `${Number(data.total || 0).toLocaleString()} 项在回收站`,
            meta: data.total ? `占用 ${fmtSize(data.bytes || 0)}` : '',
          }))} />
      </div>
      <div className="flex flex-col gap-4 max-cleanup-tight:gap-3">
        {/* 卡片和它的提示是两件东西，提示挂在卡片外面：`#libraryProcessing` 是这一格本身，
            地址栏带着它进来时滚到这里。 */}
        <div id="libraryProcessing" className="scroll-mt-20">
          <LibraryProcessingCard toast={notify} monitor onComplete={refreshReadings} />
        </div>
        <div id="mediaRepair"><MediaRepairCard /></div>
        <OrganizeCard toast={notify} failure={failure} />
      </div>
      <LinkManager />
      <OrphanRecordsCard toast={notify} />
      {hasResourceRoots(sources.data) ? <ResourceSyncCard toast={toast} /> : null}
    </Page>
  );
}
