/* 侧栏顺序的那一次写入，侧栏这一列的拖动与设置面板「侧栏」那一页共用。
 *
 * 顺序跟数据库走，不跟浏览器走：在 Windows 上排好，Mac 上就该是同一份。改完先落进本地那份
 * （`store.save()` 通知订阅者，侧栏当场按它重排），再写 `/api/settings`。写服务端失败不回滚也不
 * 打断：只读端会回 409，本地顺序照样已经生效，只是这次改动不跨机同步——那是只读端的既定约束，
 * 不是操作失败。 */
import { useCallback } from 'react';

import type { SettingsStore } from '../../settings-store';
import { useSaveSettings } from '../settings-panel/settings-data';

export function useCommitSidebarOrder(store: SettingsStore<{ sidebarOrder: string[] }>) {
  const save = useSaveSettings();
  const { mutate } = save;
  return useCallback((next: string[]) => {
    store.value.sidebarOrder = next;
    store.save();
    mutate({ sidebarOrder: next }, { onError: () => {} });
  }, [store, mutate]);
}

/** 把 `key` 挪到 `target` 前面或后面；挪不了（同一项、任一项不在顺序里）时是 null。 */
export function moveSidebarKey(order: readonly string[], key: string, target: string, after: boolean): string[] | null {
  if (key === target) return null;
  const next = [...order];
  const from = next.indexOf(key);
  if (from < 0) return null;
  next.splice(from, 1);
  const at = next.indexOf(target);
  if (at < 0) return null;
  next.splice(at + (after ? 1 : 0), 0, key);
  return next;
}
