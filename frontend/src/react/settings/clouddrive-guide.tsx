/* 媒体来源选了网盘时的 CloudDrive 起步配置。
 *
 * 页内只留填哪里、填多少、怎么确认；三处缓存的分工、换算与起步值的来源在
 * `docs/CLOUDDRIVE.md`，这里链过去，同一段话不在两处维护。 */
import { Disclosure, ExternalLink, Help } from './section';

export const CLOUDDRIVE_PROFILES = [
  { name: '机械硬盘，或内存 8 GB 以内', cache: '10–20 GiB', read: '256 / 128 KB', task: '1 个' },
  { name: 'SATA SSD，内存 8–16 GB', cache: '20–50 GiB', read: '256 / 128 KB', task: '1–2 个' },
  { name: 'NVMe SSD，内存 16 GB 以上', cache: '50–100 GiB', read: '256 / 128 KB', task: '2 个起步' },
] as const;

const READINGS = [['cache', '缓存上限'], ['read', '读取长度（默认 / 最小）'], ['task', '同时处理视频']] as const;

export function CloudDriveGuide() {
  return (
    <div className="@container flex flex-col gap-3">
      <Disclosure summary="CloudDrive 设置帮助">
        <Help>
          先将 CloudDrive2 或 OpenList 中的网盘挂载为本机文件夹，再在这里选择。
          <ExternalLink href="https://www.clouddrive2.com/help.html">挂载帮助</ExternalLink>
        </Help>
        <Help>缓存按所在硬盘和可用内存选起步值，再根据播放表现调整。</Help>
        <Help>文件操作需要挂载工具和网盘账号授予写入权限。光鸭云盘的上传、建文件夹、改名和移动需要 CloudDrive2 v1.1.0 或更高版本，删除和复制暂不支持。</Help>
        {/* 三档同形、跨档比较：窄处每档一块纵排，宽处三项读数排成三列对齐。 */}
        <ul aria-label="按缓存所在硬盘分档" className="flex flex-col gap-2">
          {CLOUDDRIVE_PROFILES.map((profile) => (
            <li key={profile.name} className="flex flex-col gap-2 rounded-2lg border border-separator-border bg-background-primary-default p-3">
              <p className="text-body-medium text-text-primary">{profile.name}</p>
              <dl className="inline-grid grid-cols-1 gap-2 @md:grid-cols-3">
                {READINGS.map(([key, term]) => (
                  <div key={key} className="flex flex-col">
                    <dt className="text-caption-1-regular text-text-secondary">{term}</dt>
                    <dd className="text-body-regular text-text-primary">{profile[key]}</dd>
                  </div>
                ))}
              </dl>
            </li>
          ))}
        </ul>
        <ul className="flex list-disc flex-col gap-1 pl-5 text-body-2-regular text-text-secondary">
          <li>缓存上限和清理方式填在 CloudDrive「设置」里，清理方式选 LRU。上限不要填 0，系统盘至少留 40 GiB；填完重开设置页确认存住了。</li>
          <li>读取长度和下载线程填在每个网盘各自的下载设置里，线程都从 2 开始。</li>
          <li>Buffer Cache 占内存，磁盘缓存和文件夹缓存占硬盘，改一个管不住另外两个。</li>
        </ul>
        <Help>
          <ExternalLink href="https://github.com/peach-mitao/peach/blob/master/docs/CLOUDDRIVE.md">完整配置与调优说明</ExternalLink>
        </Help>
      </Disclosure>
    </div>
  );
}
