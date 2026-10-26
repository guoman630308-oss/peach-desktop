import { entityFaceImg, facePos, imageFallbackAttrs } from '@peach/card-art';
import { esc } from '@peach/legacy/core';
import type { EntityPageData } from './entity-page';

export function entityPortraitImg(kind: string, entity: EntityPageData & { logo_version?: string }): string {
  const company = kind === 'studio' || kind === 'agency';
  const own = entity.id ? entityFaceImg({ kind, id: entity.id, hasImage: entity.has_image, version: entity.image_version,
    rep: company || !entity.has_avatar ? null : entity.representative_asset_id, standIn: entity.avatar_stand_in,
    mark: kind === 'agency' ? entity.mark_link_id : null,
    logo: company && entity.has_logo ? entity.canonical_name : '', logoVersion: entity.logo_version, logoVariant: 'large',
    alt: esc(entity.canonical_name), lazy: false, style: company ? '' : facePos(entity.avatar_focus),
    focus: company ? null : entity.avatar_focus, dropStyle: true }) : '';
  const follow = kind === 'creator' ? entity.follow : null;
  return own || (follow?.avatar ? `<img src="${esc(follow.avatar)}" alt="${esc(entity.canonical_name)}" referrerpolicy="no-referrer" ${
    imageFallbackAttrs({ fallbacks: [follow.avatar_fallback || ''] })}>` : '');
}
