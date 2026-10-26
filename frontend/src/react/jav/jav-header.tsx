import { useState } from 'react';
import { Button } from '@/components/base/buttons/button';
import { CloudDownloadDialog, type DownloadPrefill } from '../activity/cloud-download-dialog';
import { JAV_TABS, javPath } from './jav';
import { BoardTabs } from '../components/board-tabs';

export function JavHeader({ tab, navigate, find }: {
  tab: string; navigate(path: string): void; find(): void;
}) {
  return <div className="flex w-full flex-col gap-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 data-page-title="" className="text-display-4-medium text-text-primary max-board-narrow:text-title-1-medium">JAV</h2>
      <Button variant="primary" onClick={find}>查找资源</Button>
    </div>
    <BoardTabs tabs={JAV_TABS.map(([value, label]) => ({ value, label }))} value={tab}
      label="JAV 分区" className="" onChange={key => navigate(javPath(key))} />
  </div>;
}

/** 馆藏沿用目录的网格、筛选和搜索，分区导航与资源查找放在目录页头。 */
export function JavLibraryHeader({ navigate, receipt }: { navigate(path: string): void; receipt(message: string): void }) {
  const [download, setDownload] = useState<DownloadPrefill | null>(null);
  return <>
    <JavHeader tab="library" navigate={navigate} find={() => setDownload({})} />
    <CloudDownloadDialog prefill={download} close={() => setDownload(null)} receipt={receipt} />
  </>;
}
