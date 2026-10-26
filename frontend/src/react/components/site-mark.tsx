import { useState } from 'react';
import { RiBuildingLine, RiGlobalLine } from '@remixicon/react';
import { siteMarkUrl } from '@peach/legacy/core';

/** 采集来源的站标由本站缓存或随应用提供的静态资源承载。 */
export function SiteMark({ source }: { source: string }) {
  const [failed, setFailed] = useState(false);
  if (source === 'makers') return <RiBuildingLine aria-hidden className="size-4 shrink-0" />;
  if (failed) return <RiGlobalLine aria-hidden className="size-4 shrink-0" />;
  const bundled = ['javten', 'fc2ppvdb', 'avwikidb', 'minnano-av', 'github'].includes(source);
  return <img src={bundled ? `/site-icon/${source}.png` : siteMarkUrl({ source })} alt="" width={16} height={16}
    loading="lazy" onError={() => setFailed(true)} className="size-4 shrink-0 rounded-none object-contain" />;
}
