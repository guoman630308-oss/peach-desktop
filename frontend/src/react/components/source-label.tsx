import { MEDIA_SOURCE_ICONS } from '@peach/legacy/ui';
import { useState } from 'react';
import { SourceMark } from '../settings/section';

function Cloud115Icon({ className }: { className?: string }) {
  return <span className={className}><SourceMark mark={MEDIA_SOURCE_ICONS['115']} /></span>;
}
function PikPakIcon({ className }: { className?: string }) {
  return <span className={className}><SourceMark mark={MEDIA_SOURCE_ICONS.pikpak} /></span>;
}
export const PROVIDER_ICONS = { '115': Cloud115Icon, pikpak: PikPakIcon };

/** 站点标识经本站缓存读取。 */
export function SiteLabel({ site, label }: { site: string; label: string }) {
  const [failed, setFailed] = useState(false);
  const source = site === 'javdb' ? '/site-mark?source=javdb' : '/site-mark?domain=missav.com';
  return <span className="inline-flex min-w-0 items-center gap-1.5">
    {failed ? <SourceMark mark="globe" /> : <img src={source} alt="" className="size-4 shrink-0 object-contain" onError={() => setFailed(true)} />}
    <span>{label}</span>
  </span>;
}

/** 网盘与本地来源共用已登记的标识。 */
export function SourceLabel({ source, label }: { source: string; label: string }) {
  return <span className="inline-flex min-w-0 items-center gap-1.5">
    <SourceMark mark={MEDIA_SOURCE_ICONS[source] || (source === 'local' ? 'hard-drive' : 'database')} />
    <span>{label}</span>
  </span>;
}
