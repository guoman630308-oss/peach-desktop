/* 页面上印着全路径的那一行：路径本身，加一颗把它在资源管理器里打开的键。
 *
 * 凭据文件、问题日志、卸载会删的数据目录都印全路径，读到它的下一步就是去那儿看一眼；
 * 照着抄一遍再粘回资源管理器是白抄的。递给服务端的只是这一串，能不能开由它拿自己的
 * Peach 数据目录重判（`/api/reveal` 的 `path` 分支）。
 *
 * 窗口弹在**服务端那台机器**上：本机浏览时它自己就是回执，从别的设备浏览时这边屏幕
 * 什么都不会发生，所以成功仍要报一句。失败留在路径旁边，等人重试。 */
import { useEffect, useRef, useState } from 'react';

import { IconButton } from '@/components/base/buttons/icon-button';
import { cx } from '@/utils/cx';

import { ApiError, apiSend } from '../../api';
import { busyProps } from '../settings/use-action';

/** 全站「打开位置」那一枚。雪碧图里的 `folder-open`，Remix Icon 没有对应的一枚。 */
function FolderOpenIcon({ className }: { className?: string }) {
  return (
    <svg aria-hidden viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round" className={className}>
      <use href="#i-folder-open" />
    </svg>
  );
}

/** 请求没发出去（断网、页面换了口令）时的说法。服务端答得上来的原因由它自己写成中文。 */
const UNREACHED = '打开文件管理器失败，请重试';

export function PathLine(
  { path, prefix, className, onRevealed }:
  {
    path: string;
    /** 路径前面那几个字（「完整记录：」）。跟路径同一行，换行时不和路径拆开。 */
    prefix?: string;
    /** 这一行文字的字号与颜色由使用处定：路径是次要信息，各处的次要色不是同一个。 */
    className?: string;
    /** 定位成功后的回执。窗口不在这台机器上时，这句话是唯一的动静。 */
    onRevealed?(message: string): void;
  },
) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');
  /* 弹窗这一下由服务端去做，回程可能落在页面已经换掉之后；那时这一行早就不在了。 */
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false }, []);
  const reveal = async () => {
    if (busy) return;
    setBusy(true);
    setProblem('');
    try {
      await apiSend('/api/reveal', { path });
      onRevealed?.('已在资源管理器中显示');
    } catch (error) {
      if (alive.current) setProblem(error instanceof ApiError ? error.message : UNREACHED);
    } finally {
      if (alive.current) setBusy(false);
    }
  };
  return (
    <div className="flex flex-col gap-1">
      {/* 路径和那颗键排在同一条中线上：键比这行小字高一截，顶对齐时它自己看着往上飘。 */}
      <div className="flex items-center justify-between gap-2">
        <p className={cx('min-w-0 break-all', className)}>
          {prefix}{path}
        </p>
        <IconButton icon={FolderOpenIcon} size="small" className="shrink-0" aria-label="在资源管理器中显示"
          onClick={() => void reveal()} {...busyProps(busy)} />
      </div>
      {problem ? <p className="text-caption-1-regular text-text-error-primary">{problem}</p> : null}
    </div>
  );
}
