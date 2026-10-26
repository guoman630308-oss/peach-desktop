import { avatarInner, faceOrigin, imageFallbackAttrs, type FaceRef } from '@peach/card-art';
import { esc } from '@peach/legacy/core';
import type { OnlineAuthor } from '../follow/online-vocab';
import type { FollowView } from '../follow-feed/follow-feed';
import { followAuthorView, followTagView, followViewPath } from '../follow-feed/follow-view';
import type { IndexPerson, IndexProps, IndexRoute, PersonAvatar } from './index-data';

export function indexPath({ kind, q, scope, view, category }: IndexRoute): string {
  const params = new URLSearchParams();
  if (q) params.set('q', q);
  if (kind === 'tags') {
    params.set('view', view);
    if (scope === 'online') params.set('scope', scope);
    if (category !== 'all') params.set('category', category);
  }
  if (kind === 'performers' && scope === 'online') params.set('scope', scope);
  if (kind === 'performers' && scope !== 'online' && category !== 'all') params.set('category', category);
  return `/${kind}` + (params.size ? `?${params}` : '');
}

/** 公司认自有标识；人的代表作头像保留服务端的占位说明与人脸取景。 */
export function personAvatar(item: IndexPerson & Pick<FaceRef, 'logo_version'>, kind: string, big: boolean): PersonAvatar {
  const id = item.entity_id || item.id;
  const company = kind === 'studio' || kind === 'agency';
  return {
    html: avatarInner(item.k, id ? { id, has_image: item.has_image, image_version: item.image_version,
      logo_version: item.logo_version, avatar_stand_in: item.avatar_stand_in } : null,
    item.has_avatar && !company ? item.rep : null, kind, item.mark, item.has_logo ? item.k : '',
    company && big ? 'large' : 'ring', company ? null : item.avatar_focus, true),
    face: faceOrigin(item.avatar_focus),
  };
}

export function onlineAuthorRingHtml(author: OnlineAuthor): string {
  const name = String(author.k || '');
  const initial = (name.match(/[A-Za-z0-9]/)?.[0] || Array.from(name)[0] || '?').toUpperCase();
  const image = author.avatar ? `<img src="${esc(author.avatar)}" alt="" loading="lazy" referrerpolicy="no-referrer" ${
    imageFallbackAttrs({ fallbacks: [author.avatar_fallback || ''] })}>` : '';
  return `<span class="ini">${esc(initial)}</span>${image}`;
}

export interface IndexControllerPorts {
  route(path: string, replace: boolean): void;
  followView(): FollowView;
  adoptFollowView(view: FollowView): void;
  hideIndex(): void;
  openPage(path: string): void;
}

export function createIndexController(ports: IndexControllerPorts) {
  const openFollow = (view: FollowView) => {
    ports.adoptFollowView(view);
    ports.hideIndex();
    ports.openPage(followViewPath(view));
  };
  return {
    route(next: IndexRoute, { replace = false }: { replace?: boolean } = {}) { ports.route(indexPath(next), replace) },
    personAvatar, authorAvatar: onlineAuthorRingHtml,
    openFollowAuthor(key: string) { openFollow(followAuthorView(ports.followView(), key)) },
    openFollowTag(tag: string) { openFollow(followTagView(ports.followView(), tag)) },
  } satisfies Pick<IndexProps, 'route' | 'personAvatar' | 'authorAvatar' | 'openFollowAuthor' | 'openFollowTag'>;
}
