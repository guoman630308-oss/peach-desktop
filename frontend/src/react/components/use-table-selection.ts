import { useCallback, useEffect, useId, useRef } from 'react';

const tables = new Map<string, () => void>();

/** 同一页面只让当前表格保留选择，批量操作栏因此只有一份。 */
export function useTableSelection(clear: () => void) {
  const id = useId();
  const latest = useRef(clear);
  latest.current = clear;
  useEffect(() => {
    tables.set(id, () => latest.current());
    return () => { tables.delete(id); };
  }, [id]);
  return useCallback(() => {
    for (const [other, reset] of tables) if (other !== id) reset();
  }, [id]);
}
