/* 播放列表页：保存下来的 Mix 与手动建的列表，按最近改动排。
 *
 * 一份播放列表就是一叠视频，和首页那张 Mix 卡是同一件东西，所以穿同一身
 * （`components/mix-card.tsx`）：封面悬停时逐张翻过列表里的画面，下面一行是列表里出镜最多
 * 的那几位的头像，标题就是列表名。改名和删除收进名字右边那个点点点菜单——它们是这张卡的
 * 次要动作，不该和「打开」并排占着一整行。
 *
 * 新建、改名、删除都写 ledger，只由点击触发；删除先过确认弹层。三者成功后都给一颗撤销键，
 * 写完让列表键重取，不重挂整页。壳在这一页上要求重读时把 `revision` 加一，页面同样只重取。 */
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Heading, Popover } from 'react-aria-components';
import { MenuDialog as Dialog } from '../components/menu-dialog';
import { confirmModal } from '@peach/legacy/ui';

import { Button } from '@/components/base/buttons/button';
import {
  MENU_ITEM, MENU_ITEM_INTERACTIVE, MENU_POPOVER_SURFACE,
} from '@/components/base/dropdown/menu-styles';
import { Input } from '@/components/base/input/input';
import { cx } from '@/utils/cx';

import { errorMessage } from '../../api';
import type { PlaylistsProps } from '../bundle';
import { EmptyState } from '../components/empty-state';
import { RetryNote } from '../components/grid-reveal';
import { MixCard } from '../components/mix-card';
import { ModalFrame } from '../components/modal-frame';
import { spriteGlyph } from '../components/sprite-glyph';
import { busyProps } from '../settings/use-action';
import {
  createPlaylist, deletePlaylist, fetchPlaylists, keepPlaylist, PLAYLISTS_KEY, posterUrl, recreatePlaylist,
  refreshPlaylists, renamePlaylist, resumeAssetId, type PlaylistRow,
} from './playlists';

const ELLIPSIS = spriteGlyph('ellipsis');
const PENCIL = spriteGlyph('pencil');
const TRASH = spriteGlyph('trash');
const PLAYLIST = spriteGlyph('playlist');

const MENU_ROW = cx(MENU_ITEM, MENU_ITEM_INTERACTIVE, 'text-body-2-medium');
const EMPTY_NAME = '播放列表名称不能为空';

type Toast = PlaylistsProps['toast'];

/** 撤销那一步：做完无论成败都重读列表；抛出的错交给回执，它自己换成「撤销失败」。 */
const undoing = (task: () => Promise<unknown>) => async () => {
  try {
    await task();
  } finally {
    void refreshPlaylists();
  }
};

function CreateForm({ toast }: { toast: Toast }) {
  const [name, setName] = useState('');
  const [problem, setProblem] = useState('');
  const create = useMutation({
    mutationFn: (value: string) => createPlaylist(value),
    onSuccess: (result) => {
      setName('');
      setProblem('');
      void refreshPlaylists();
      toast('已新建播放列表', { undo: undoing(() => deletePlaylist(result.playlist.id)) });
    },
    onError: (cause) => setProblem(errorMessage(cause) || '新建失败'),
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (create.isPending) return;
    if (!name.trim()) { setProblem(EMPTY_NAME); return }
    create.mutate(name);
  };
  return (
    <form data-playlist-create="" aria-label="新建播放列表" onSubmit={submit}
      className="flex flex-wrap items-end gap-2">
      <div className="min-w-50 max-w-80 flex-1 basis-50 max-board-narrow:min-w-0 max-board-narrow:max-w-none">
        <Input label="新播放列表" placeholder="输入名称" maxLength={80} value={name}
          onChange={(value) => { setName(value); setProblem('') }}
          validationBehavior="aria" isInvalid={Boolean(problem)} hint={problem || undefined} />
      </div>
      <Button type="submit" {...busyProps(create.isPending)}>新建</Button>
    </form>
  );
}

function RenameDialog({ target, close, toast }: { target: PlaylistRow | null; close(): void; toast: Toast }) {
  const [name, setName] = useState('');
  const [problem, setProblem] = useState('');
  const input = useRef<HTMLInputElement>(null);
  /* 打开时把原名整段选中：多数时候是整个换掉，少数是在原名上补一截。 */
  useEffect(() => {
    if (!target) return undefined;
    setName(target.name);
    setProblem('');
    const frame = requestAnimationFrame(() => { input.current?.focus(); input.current?.select() });
    return () => cancelAnimationFrame(frame);
  }, [target]);
  const rename = useMutation({
    mutationFn: ({ row, value }: { row: PlaylistRow; value: string }) => renamePlaylist(row.id, value),
    onSuccess: (_result, { row }) => {
      close();
      void refreshPlaylists();
      toast('已重命名播放列表', { undo: undoing(() => renamePlaylist(row.id, row.name)) });
    },
    onError: (cause) => setProblem(errorMessage(cause)),
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!target || rename.isPending) return;
    const value = name.trim();
    if (!value) { setProblem(EMPTY_NAME); return }
    rename.mutate({ row: target, value });
  };
  return (
    <ModalFrame isOpen={Boolean(target)} onOpenChange={(open) => { if (!open) close() }} width="form">
      <form data-playlist-rename="" onSubmit={submit} className="flex min-h-0 flex-col">
        <div className="flex flex-col gap-4 p-5">
          <Heading slot="title" className="text-title-2-semibold text-text-primary">编辑名称</Heading>
          <Input ref={input} label="名称" placeholder="输入名称" maxLength={80} value={name}
            onChange={(value) => { setName(value); setProblem('') }}
            validationBehavior="aria" isInvalid={Boolean(problem)} hint={problem || undefined} />
        </div>
        <div className="flex justify-between gap-4 p-3">
          <Button variant="secondary" onClick={close}>取消</Button>
          <Button type="submit" {...busyProps(rename.isPending)}>保存名称</Button>
        </div>
      </form>
    </ModalFrame>
  );
}

/** 卡片右下那枚点点点：编辑名称、删除播放列表。
 *
 *  触发键静止时是透明的一枚字形，指上或展开才铺一块面，和裁剪封面那枚同一副
 *  （`../cover-crop/cover-crop-page.tsx`）。BoardUI 的图标钮静止态都有面：`ghost` 是一块
 *  浅蓝，`IconButton` 是白底描边，每张卡都顶着一块，署名行就不再只是头像与标题。 */
function PlaylistMenu({ name, onRename, onDelete }: { name: string; onRename(): void; onDelete(): void }) {
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const run = (action: () => void) => { setOpen(false); action() };
  return (
    <>
      <button ref={trigger} type="button" title="更多操作" aria-label={`播放列表操作：${name}`}
        aria-haspopup="dialog" aria-expanded={open} data-playlist-menu="" onClick={() => setOpen(true)}
        className="flex size-7.5 flex-none cursor-pointer items-center justify-center rounded-2lg text-foreground-icon-secondary outline-none transition-colors hover:bg-background-primary-hover hover:text-foreground-icon-primary aria-expanded:bg-background-primary-hover aria-expanded:text-foreground-icon-primary focus-visible:ring-2 focus-visible:ring-border-focus-ring">
        <ELLIPSIS aria-hidden className="size-4.5" />
      </button>
      <Popover triggerRef={trigger} isOpen={open} onOpenChange={setOpen}
        placement="bottom end" offset={4} className={MENU_POPOVER_SURFACE}>
        {/* 面板外宽 172px，同旧卡片菜单：外框再加 10px 内边距与 1px 描边。 */}
        <Dialog aria-label={`播放列表操作：${name}`} className="flex w-37.5 flex-col gap-0.5 outline-none">
          <button type="button" className={MENU_ROW} onClick={() => run(onRename)}>
            <PENCIL aria-hidden className="size-4 shrink-0" />编辑名称
          </button>
          <button type="button" className={MENU_ROW} onClick={() => run(onDelete)}>
            <TRASH aria-hidden className="size-4 shrink-0" />删除播放列表
          </button>
        </Dialog>
      </Popover>
    </>
  );
}

export function PlaylistsPage({ openPlaylist, openEntity, canFlip, toast, revision }: PlaylistsProps) {
  const playlists = useQuery({ queryKey: PLAYLISTS_KEY, queryFn: ({ signal }) => fetchPlaylists(signal) });
  const [renaming, setRenaming] = useState<PlaylistRow | null>(null);
  /* 壳要求重读：首帧那一代已经由 `prefetch` 取过，之后每加一次重取一次。 */
  const seen = useRef(revision);
  useEffect(() => {
    if (seen.current === revision) return;
    seen.current = revision;
    void refreshPlaylists();
  }, [revision]);

  const data = playlists.data;
  const items = data?.items ?? [];

  const remove = (row: PlaylistRow) => {
    void confirmModal({
      title: '删除播放列表', body: '这个播放列表将被删除，视频文件保留。', confirmLabel: '删除播放列表', danger: true,
      onConfirm: async () => {
        const kept = await keepPlaylist(row.id);
        await deletePlaylist(row.id);
        void refreshPlaylists();
        toast('已删除播放列表', kept ? { undo: undoing(() => recreatePlaylist(kept)) } : undefined);
      },
    });
  };

  return (
    <div className="w-full pt-1 pb-10.5">
      <header className="mb-4 flex items-start justify-between gap-4.5 max-board-narrow:flex-col max-board-narrow:items-stretch">
        <div>
          {/* 遗留样式表那条 `.stats h2` 不在层里，把二级标题定成 24px（窄屏 20px），工具类压不过，
              字形带 `!`；它给的 4px 底距与这里的 `mb-1` 相同。 */}
          <h2 className="mb-1 text-display-4-medium! text-text-primary">播放列表</h2>
          <p className="text-body-2-regular text-text-secondary">保存 Mix，按自己的顺序继续播放。</p>
        </div>
        <CreateForm toast={toast} />
      </header>
      <div className="mx-auto w-full max-w-board">
      {/* 读取失败时页头与新建框照常在，提示与重试键落在列表的位置上。 */}
      {!data
        ? (playlists.isError
          ? <RetryNote message={errorMessage(playlists.error) || '播放列表读取失败'} onRetry={() => void playlists.refetch()} />
          : null)
        : items.length
        ? (
          <div data-playlist-grid="" className="card-grid-cover gap-4.5">
            {items.map((row) => {
              const resume = resumeAssetId(row);
              const name = row.name.trim() || '未命名播放列表';
              return (
                <MixCard key={row.id} data-playlist-card={String(row.id)} name={name}
                  caption={row.source_kind === 'mix' ? '由 Mix 保存' : '手动播放列表'}
                  count={row.item_count}
                  poster={row.preview_asset_id ? posterUrl(row.preview_asset_id) : null}
                  flipImages={async () => (row.preview_ids || []).map(posterUrl)}
                  canFlip={canFlip} faces={row.faces || []} onOpenEntity={openEntity}
                  {...(resume ? { onOpen: () => openPlaylist(row.id, resume) } : {})}
                  openLabel={`打开播放列表 ${name}`}
                  menu={<PlaylistMenu name={name} onRename={() => setRenaming(row)} onDelete={() => remove(row)} />} />
              );
            })}
          </div>
        )
        : (
          <EmptyState icon={PLAYLIST} title="还没有播放列表">
            保存 Mix 或新建列表后，会在这里按自己的顺序继续播放。
          </EmptyState>
        )}
      </div>
      <RenameDialog target={renaming} close={() => setRenaming(null)} toast={toast} />
    </div>
  );
}
