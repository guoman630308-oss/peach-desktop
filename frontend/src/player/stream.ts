/* 流会话与片源判据。
 *
 * 每一路播放带一个会话号，服务端按它登记在途的读取；切走、关闭、离开页面时按会话取消，
 * 已经没人看的读取不再占着 115 或本地盘。详情与沉浸模式共用这一套（沉浸模式每格一个会话）。 */
import { api } from '@peach/legacy/core';

import type { PlayerItem, PlayerSource } from './types';

export function newStreamSession(): string {
  return globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export function directStreamSource(item: PlayerItem, session: string): PlayerSource {
  return {
    src: `/stream?id=${item.id}&session=${encodeURIComponent(session)}`,
    type: String(item.name || '').toLowerCase().endsWith('.webm') ? 'video/webm' : 'video/mp4',
  };
}

/* 在线作品的 `path` 是来源作品页，不是可播地址。能播的那条代理在 `/follow-stream?id=<follow_item>`，
   保存时写了 `follow_item.asset_id`，`/api/item` 反查后回传 `follow_item_id`。 */
function followStreamSource(item: PlayerItem): PlayerSource | null {
  return item.location === 'online' && item.follow_item_id
    ? { src: `/follow-stream?id=${item.follow_item_id}`, type: 'video/mp4' } : null;
}

/** 起播片源只有这一个判据，详情和沉浸模式共用：服务端说要转码分片就给分片，否则直读。
 *  有 B 帧却缺 ctts 的 MP4 直读时浏览器按错的显示顺序丢帧，整片持续卡顿。 */
export async function playableStreamSource(item: PlayerItem, session: string): Promise<PlayerSource> {
  const proxied = followStreamSource(item);
  if (proxied) return proxied;
  try {
    const plan = await api(`/api/stream-plan?id=${item.id}&session=${encodeURIComponent(session)}`) as
      { protocol?: string; src?: string; mime_type?: string } | null;
    if (plan?.src) return { src: plan.src, type: plan.mime_type || (plan.protocol === 'hls' ? 'application/vnd.apple.mpegurl' : 'video/mp4') };
  } catch { /* 取不到计划就直读 */ }
  return directStreamSource(item, session);
}

/** 通知服务端放掉这一路读取。结果写到 `<html data-peach-stream-cancel>` 上，e2e 据此核对。 */
export function cancelStreamSession(session: string): void {
  if (!session) return;
  fetch(`/api/stream-cancel?session=${encodeURIComponent(session)}`, {
    method: 'POST', credentials: 'same-origin', keepalive: true,
  }).then((response) => response.json()).then((result: unknown) => {
    document.documentElement.dataset.peachStreamCancel = JSON.stringify(result);
  }).catch(() => {});
}

/* ── 详情这一路 ──
   舞台与小窗同一时刻只放一条，共用一个会话：从详情进小窗时读取接着用，小窗里换片或关掉时
   才取消。 */
let detailSession = '';

export function detailStreamSession(): string { return detailSession }

function ensureDetailSession(): string {
  if (!detailSession) detailSession = newStreamSession();
  return detailSession;
}

export const directDetailSource = (item: PlayerItem): PlayerSource => directStreamSource(item, ensureDetailSession());
export const detailStreamSource = (item: PlayerItem): Promise<PlayerSource> => playableStreamSource(item, ensureDetailSession());

export function cancelDetailStream(): void {
  const session = detailSession;
  if (!session) return;
  detailSession = '';
  cancelStreamSession(session);
}
