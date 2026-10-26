/* 高清版目标页：数据库里标记为「还该有更好一版」的那些作品。
 *
 * 一屏回答两件事：还欠多少部、每一部欠在哪里。所以卡片上除了标题就只有来源、时长、
 * 体积和判定原因——决定要不要去找更好一版，看的就是这几项。
 *
 * 番号标题与来源徽标由遗留层以 HTML 字符串传进来：它们是全站语义契约的唯一实现，
 * 在这里重写一份就会漂。时长、体积、来源名走 `src/core/index.ts` 同一套格式化口径，同理。 */
import { useState } from 'react';
import { RiSparklingLine } from '@remixicon/react';
import { useQuery } from '@tanstack/react-query';

import { LOC, fmtDur, fmtSize } from '@peach/legacy/core';

import { Button } from '@/components/base/buttons/button';

import { errorMessage } from '../../api';
import { CloudDownloadDialog, type DownloadPrefill } from '../activity/cloud-download-dialog';
import type { QualityGoalsProps } from '../bundle';
import { cardClass } from '../components/card';
import { CollectionSummary } from '../components/collection-summary';
import { EmptyState } from '../components/empty-state';
import { Note } from '../components/note';
import { Page } from '../components/page';
import {
  fetchQualityGoals, previewUrl, QUALITY_GOALS_KEY, type QualityGoal,
} from './quality-goals';

/** 一部待升级的作品一张卡。 */
function GoalCard({ item, openItem, searchResources, javTitleHtml, javDisplayName, srcBadge }: { item: QualityGoal }
  & Omit<QualityGoalsProps, 'toast'> & { searchResources(prefill: DownloadPrefill): void }) {
  const open = () => openItem(item.id);
  return (
    <li data-goal-id={item.id}
      className={cardClass({ padding: 'none', bordered: 'soft', className: 'flex flex-col gap-3 p-3' })}>
      <div className="flex min-w-0 gap-4">
        {/* 居中那句占位文字用 `inline-grid`：旧样式表里 `.grid` 是海报墙那条带列宽和
            间距的规则，排在 `peach-app.css` 后面会赢。这颗按钮是 flex 子项，行内格
            会被块级化，算出来仍是 `display:grid`。 */}
        <button type="button" onClick={open} aria-label={`打开 ${javDisplayName(item)}`}
          className="relative inline-grid w-card-cover shrink-0 self-start aspect-card-cover cursor-pointer place-items-center overflow-hidden rounded-2lg bg-background-tertiary-default">
          {/* 图片取不到时（onError 把 img 摘掉）露出来的就是这句。 */}
          <span className="text-caption-1-regular text-text-secondary">暂无预览</span>
          <img src={previewUrl(item)} alt="" loading="lazy"
            onError={(event) => event.currentTarget.remove()}
            className="absolute inset-0 size-full object-contain" />
        </button>
        <div className="flex min-w-0 flex-col gap-1.5">
          {/* 标题从番号读起，长了折到第二行再截尾：中段截断会把番号和片名一起切掉。 */}
          <h3 className="text-headline-medium text-text-primary">
            <button type="button" onClick={open} title={javDisplayName(item)}
              className="line-clamp-2 w-full cursor-pointer text-left wrap-anywhere"
              dangerouslySetInnerHTML={{ __html: javTitleHtml(item) }} />
          </h3>
          <p className="flex flex-wrap items-center gap-2 text-body-2-regular text-text-secondary">
            {/* 插原始 HTML 要有个宿主元素，而来源徽标在遗留版本里是这个 flex 容器的直接
                子项。`contents` 让宿主从布局里消失，徽标的对齐与间距保持不变。 */}
            <span className="contents" dangerouslySetInnerHTML={{ __html: srcBadge(item.location, item.cost) }} />
            <span>{LOC[item.location] ?? item.location}</span>
            <span>{fmtDur(item.duration)}</span>
            <span>{Number(item.size) > 0 ? fmtSize(Number(item.size)) : '大小未知'}</span>
          </p>
          {item.reason
            ? <p className="border-t border-separator-border pt-3 text-body-2-regular text-text-secondary wrap-anywhere">
                {item.reason}
              </p>
            : null}
        </div>
      </div>
      <footer className="flex flex-wrap justify-end gap-2">
        {item.code ? <Button variant="secondary" size="small" onClick={() => searchResources({
          code: item.code!, title: javDisplayName(item), origin: `asset:${item.id}`, searchReason: item.reason || '',
          duration: item.duration, cover: previewUrl(item),
        })}>查找资源</Button> : null}
        <Button variant="secondary" size="small" onClick={open}>查看版本</Button>
      </footer>
    </li>
  );
}

export function QualityGoalsPage({ toast, ...props }: QualityGoalsProps) {
  const [download, setDownload] = useState<DownloadPrefill | null>(null);
  const goals = useQuery({ queryKey: QUALITY_GOALS_KEY, queryFn: ({ signal }) => fetchQualityGoals(signal) });
  const data = goals.data;
  if (!data) {
    return (
      <Page>
        <Note tone="error" title="高清版目标读取失败">{goals.error ? errorMessage(goals.error) : '未取得高清版目标，请刷新页面重试。'}</Note>
      </Page>
    );
  }
  const items = data.items || [];
  if (!items.length) {
    return (
      <Page>
        <EmptyState shell="plain" icon={RiSparklingLine} title="没有标记中的高清版目标">
          现有版本都已满足条件，或还没有加入追踪。
        </EmptyState>
      </Page>
    );
  }
  return (
    <Page>
      {/* 总数取服务端的 `total` 而不是这一页的条数：`limit` 截断时两者不是一个数，
          而这一行要回答的是「一共还欠多少部」。 */}
      <CollectionSummary label="待升级" figure={`${Number(data.total || 0).toLocaleString()} 部作品`} flush />
      <ul className="card-grid-cover gap-5">
        {items.map((item) => <GoalCard key={item.id} item={item} {...props} searchResources={setDownload} />)}
      </ul>
      {/* 列表只取前 200 部；总数比这多时说清楚这一页不是全部。 */}
      {Number(data.total || 0) > items.length
        ? <p className="mt-6 text-center text-caption-1-regular text-text-secondary">
            {`显示前 ${items.length.toLocaleString()} 部，共 ${Number(data.total).toLocaleString()} 部。`}
          </p>
        : null}
      <CloudDownloadDialog prefill={download} close={() => setDownload(null)} receipt={toast} />
    </Page>
  );
}
