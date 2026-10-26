/* 复核卡片中段那一框证据：来源图片、原视频入口、创作者入口、身份证据、作品样本九宫格。
 *
 * 看这几帧、然后读这一句，是同一件事的两半，所以它们合成卡片中段那一个框。候选表单和
 * 身份证据不进框：它们每一项自己就是一个框，再套一层就是框中框。 */
import { useEffect, useState } from 'react';
import { RiExternalLinkLine, RiPlayLine } from '@remixicon/react';
import { VisuallyHidden } from 'react-aria-components';
import { spinnerHtml } from '@peach/legacy/ui';

import { Button, ButtonLink } from '@/components/base/buttons/button';
import { CheckboxGlyph } from '@/components/base/checkbox/checkbox-glyph';
import { LEGACY_AVATAR_IMG } from '../components/legacy-avatar';
import { busyProps } from '../settings/use-action';
import type { ReviewAsset, ReviewRow } from './review';

/** 只放行 http(s) 与站内绝对路径。候选文件里那一列什么都可能有。 */
const webUrl = (value = '') => (/^https?:\/\//i.test(value) ? value : '');
const imageUrl = (value = '') =>
  webUrl(value) || (value.startsWith('/') && !value.startsWith('//') ? value : '');

/** 来源给的那张图。取不到时说清楚是「未取得」，不留一格空白让人以为界面坏了。 */
export function SourceImage({ url }: { url?: string }) {
  const source = imageUrl(url || '');
  return <RequestedSourceImage key={source} source={source} />;
}

function RequestedSourceImage({ source }: { source: string }) {
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'failed'>('idle');
  useEffect(() => {
    if (status !== 'loading') return;
    const timer = setTimeout(() => setStatus('failed'), 30_000);
    return () => clearTimeout(timer);
  }, [status]);
  return (
    <div className="relative flex h-review-image items-center justify-center overflow-hidden rounded-surface bg-background-secondary-default">
      {status === 'loading' ? <span role="status" className="absolute flex items-center gap-2 text-body-2-regular text-text-secondary">
        <span dangerouslySetInnerHTML={{ __html: spinnerHtml('正在加载图片') }} />正在加载图片
      </span> : null}
      {source && (status === 'loading' || status === 'ready')
        ? <img src={source} alt="来源提供的备选图片" referrerPolicy="no-referrer" decoding="async"
            className={`size-full object-contain ${status === 'loading' ? 'invisible' : ''}`} onLoad={() => setStatus('ready')}
            onError={() => setStatus('failed')} />
        : source ? <div className="flex flex-col items-center gap-2">
            {status === 'failed' ? <span className="text-body-2-regular text-text-secondary">未取得来源图片</span> : null}
            <Button variant="secondary" size="small" onClick={() => setStatus('loading')}>
              {status === 'failed' ? '重新加载图片' : '查看来源图片'}
            </Button>
          </div> : <span className="text-body-2-regular text-text-secondary">未取得来源图片</span>}
    </div>
  );
}

/** 一条原视频：封面、文件名、打开。 */
export function OriginAsset(
  { asset, openItem }: { asset: ReviewAsset; openItem(id: number): void },
) {
  const name = asset.name || '';
  return (
    <div className="flex min-w-0 items-center gap-2.5 rounded-surface bg-background-secondary-default p-2">
      <button type="button" onClick={() => openItem(asset.id)}
        className="flex aspect-card-cover w-review-cover-narrow shrink-0 review-tight:w-review-cover cursor-pointer items-center justify-center overflow-hidden rounded-2lg bg-background-tertiary-default outline-none focus-visible:ring-2 focus-visible:ring-border-focus-ring">
        {asset.preview_url
          ? <img src={asset.preview_url} alt="" loading="lazy" className="size-full object-cover" />
          : <span className="text-caption-1-regular text-text-secondary">无封面</span>}
        <VisuallyHidden>{`打开原视频 ${name}`}</VisuallyHidden>
      </button>
      <div className="flex min-w-0 flex-col items-start gap-1">
        <b title={name} className="w-full truncate text-body-medium text-text-primary">
          {asset.code || name || '原视频'}
        </b>
        <Button variant="secondary" size="small" leadingIcon={RiPlayLine}
          onClick={() => openItem(asset.id)}>打开原视频</Button>
      </div>
    </div>
  );
}

/** 主体是一位创作者而不是某一条作品时的卡头：脸、名字、作品数。
 *
 *  这类候选判的是「这位创作者」——创作者标签看的是他全部作品该打什么标签，西方身份回配
 *  看的是这个人对不对得上。从样本里挑一条画成「原视频」读起来就是错的：下面 60 个样本、
 *  上面 1 个视频。 */
export function EntityHead(
  { kind, name, works, avatar, openEntity }: {
    kind: string; name: string; works: number; avatar: string;
    openEntity(kind: string, name: string): void;
  },
) {
  return (
    <div className="flex min-w-0 items-center gap-3">
      {/* `card-art` 的 `avatarInner` 是唯一那份「有图走图、没图退首字母」的实现，页面不重画一遍。 */}
      <button type="button" aria-label={`打开创作者页：${name}`} onClick={() => openEntity(kind, name)}
        className={`relative inline-grid size-11 shrink-0 cursor-pointer place-items-center overflow-hidden rounded-full bg-background-secondary-default text-caption-1-medium text-text-secondary outline-none focus-visible:ring-2 focus-visible:ring-border-focus-ring ${LEGACY_AVATAR_IMG}`}
        dangerouslySetInnerHTML={{ __html: avatar }} />
      <div className="flex min-w-0 flex-col items-start">
        <button type="button" onClick={() => openEntity(kind, name)}
          className="max-w-full cursor-pointer truncate text-body-medium text-text-primary underline-offset-2 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-border-focus-ring">
          {name}
        </button>
        {works
          ? <small className="tabular-nums text-body-2-regular text-text-secondary">{`${works.toLocaleString()} 部作品`}</small>
          : null}
      </div>
    </div>
  );
}

/** 西方身份回配的证据：候选身份、来源资料链接、来源图片、本地作品样本。 */
export function IdentityEvidence(
  { row, openItem, openEntity, onReveal, revealing, note }: {
    row: ReviewRow;
    openItem(id: number): void;
    openEntity(kind: string, name: string): void;
    onReveal(id: number): void;
    revealing: number;
    note: string;
  },
) {
  const profile = webUrl(row.profile_url || '');
  const samples = (row.preview_assets || []).slice(0, 6);
  const works = Math.max(0, Number(row.video_count || row.videos || 0));
  return (
    <section className="flex flex-col gap-3">
      <h5 className="text-body-medium text-text-primary">{`待确认人物：${row.babepedia_name || '未标注'}`}</h5>
      <div className="flex flex-wrap items-center gap-2">
        {profile
          ? <ButtonLink variant="secondary" size="small" trailingIcon={RiExternalLinkLine}
              href={profile} target="_blank" rel="noopener noreferrer">来源资料</ButtonLink>
          : null}
        <Button variant="secondary" size="small"
          onClick={() => openEntity('creator', row.creator || '')}>
          {`查看全部 ${works.toLocaleString()} 部作品`}
        </Button>
      </div>
      <SourceImage url={row.preview_url} />
      <p className="text-body-2-regular text-text-secondary">通过后记录身份判断。</p>
      {samples.length ? <>
        <h5 className="text-body-medium text-text-primary">本地作品样本</h5>
        <ul className="flex flex-col gap-1">
          {samples.map((asset) => (
            <li key={asset.id} className="flex min-w-0 items-center gap-2">
              <Button variant="ghost" size="small" className="min-w-0"
                onClick={() => openItem(asset.id)}>
                <span title={asset.name} className="block max-w-full truncate">{asset.name}</span>
              </Button>
              <Button variant="ghost" size="small" aria-label={`打开 ${asset.name} 的文件位置`}
                {...busyProps(revealing === asset.id)}
                onClick={() => onReveal(asset.id)}>文件位置</Button>
            </li>
          ))}
        </ul>
      </> : <p className="text-body-2-regular text-text-secondary">暂无本地作品样本，打开全部作品核对。</p>}
      {note ? <p role="status" className="text-body-2-regular text-text-error-primary">{note}</p> : null}
    </section>
  );
}

/** 创作者标签那一类的作品样本：点一下切换，Shift 选一段——和主网格同一套选择语义，
 *  不为这一页另造一个「多选模式」。 */
export function AssetPicker(
  { assets, picked, onPicked, locked }: {
    assets: ReviewAsset[];
    picked: number[];
    onPicked(next: number[]): void;
    locked: boolean;
  },
) {
  const [anchor, setAnchor] = useState<number | null>(null);
  const chosen = new Set(picked);
  const toggle = (index: number, range: boolean) => {
    if (locked) return;
    const next = new Set(chosen);
    if (range && anchor !== null) {
      const [from, to] = [Math.min(anchor, index), Math.max(anchor, index)];
      for (let at = from; at <= to; at++) next.add(assets[at]!.id);
    } else {
      const id = assets[index]!.id;
      if (next.has(id)) next.delete(id); else next.add(id);
    }
    setAnchor(index);
    onPicked(assets.filter((asset) => next.has(asset.id)).map((asset) => asset.id));
  };
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="tabular-nums text-body-2-regular text-text-secondary">
          {`已选 ${chosen.size} / ${assets.length}`}
        </span>
        <Button variant="ghost" size="small" disabled={locked}
          onClick={() => onPicked(assets.map((asset) => asset.id))}>全选</Button>
        <Button variant="ghost" size="small" disabled={locked}
          onClick={() => onPicked([])}>清空</Button>
      </div>
      <div className="review-asset-grid gap-1.5">
        {assets.map((asset, index) => {
          const on = chosen.has(asset.id);
          return (
            <button key={asset.id} type="button" aria-pressed={on} title={asset.name}
              onClick={(event) => toggle(index, event.shiftKey)}
              className={`relative aspect-card-cover cursor-pointer overflow-hidden rounded-surface bg-background-tertiary-default outline-none transition-opacity focus-visible:ring-2 focus-visible:ring-border-focus-ring ${on ? 'opacity-100 ring-2 ring-border-button-active ring-inset' : 'opacity-50'}`}>
              <img src={`/poster?id=${asset.id}&c=4`} alt="" loading="lazy"
                className="size-full object-cover" />
              {/* 勾选的样子归注册表里那一枚：这里不另画一个，免得同一页出现两种「已选」。 */}
              <span className="absolute top-1 right-1">
                <CheckboxGlyph size="sm" state={{ isSelected: on, isIndeterminate: false,
                  isFocusVisible: false, isDisabled: locked, isHovered: false }} />
              </span>
              <VisuallyHidden>{`${on ? '已选' : '未选'} ${asset.name || asset.id}`}</VisuallyHidden>
            </button>
          );
        })}
      </div>
    </div>
  );
}
