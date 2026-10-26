import { faceBoxAttrs, facePos } from '@peach/card-art';
import { esc, leadingGraphemes } from '@peach/legacy/core';
import { tagLabel } from '@peach/legacy/tags';
import type { FollowFeedHelpers, FollowWorkRow } from './follow-feed';

export function followWorkMark([key, label, , icon, focus]: FollowWorkRow): string {
  return icon ? `<img src="/work-icon?work=${encodeURIComponent(key)}" width="128" height="128" alt="" loading="lazy"${
    facePos(focus)}${faceBoxAttrs(focus)}>` : esc(leadingGraphemes(label, 2));
}

export function createFollowFeedHelpers(delegated: Omit<FollowFeedHelpers, 'workMark' | 'tagLabel'>): FollowFeedHelpers {
  return { ...delegated, workMark: followWorkMark, tagLabel };
}
