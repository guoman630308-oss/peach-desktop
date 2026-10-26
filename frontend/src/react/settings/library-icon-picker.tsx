/* 媒体库图标：触发键显示当前字形，点开是一块七列网格的单选面板，按「应用」才提交。
 *
 * 注册表里没有网格单选的弹出面板，用 React Aria 的 `Popover`、`Dialog`、`RadioGroup` 组合，
 * 面板外观取 BoardUI `menu-styles.ts`。取值是雪碧图里的字形名，服务端按
 * `peach.media_libraries.LIBRARY_ICONS` 校验。 */
import { useRef, useState } from 'react';
import { Dialog, Heading, Popover, Radio, RadioGroup } from 'react-aria-components';
import { MEDIA_SOURCE_ICONS } from '@peach/legacy/ui';

import { Button } from '@/components/base/buttons/button';
import { MENU_POPOVER_SURFACE } from '@/components/base/dropdown/menu-styles';

import { SourceMark } from './section';

/* 一行七枚、六行排完。第一枚是「不另选」：网盘库自动用网盘标识，本地路径没有可识别的
   来源，就是默认那枚磁盘。其余按题材成组：身体与情欲、身份与装扮、场景与情境、媒介与归档。 */
export const LIBRARY_ICON_CHOICES = [
  ['', '自动识别'], ['hard-drive', '磁盘'], ['database', '资料库'], ['heart', '心动'], ['heart-hand', '亲密'], ['flame', '热情'], ['cherry', '樱桃'],
  ['lollipop', '甜心'], ['candy', '糖果'], ['banana', '香蕉'], ['droplets', '湿润'], ['venus', '女性'], ['mars', '男性'], ['venus-and-mars', '情侣'],
  ['gem', '精选'], ['crown', '女王'], ['ribbon', '丝带'], ['shirt', '制服'], ['graduation-cap', '学生'], ['stethoscope', '护士'], ['glasses', '眼镜'],
  ['footprints', '足迹'], ['hand', '手部'], ['flower', '花朵'], ['venetian-mask', '角色扮演'], ['rabbit', '兔女郎'], ['paw-print', '兽耳'], ['dumbbell', '健身'],
  ['bed-double', '卧室'], ['bath', '浴室'], ['key-round', '私密'], ['wine', '微醺'], ['cigarette', '烟'], ['moon', '夜色'], ['sparkles', '幻想'],
  ['camera', '写真'], ['video', '影片'], ['film', '电影'], ['image', '图集'], ['gamepad-2', '游戏'], ['star', '收藏'], ['tags', '主题'],
] as const;

/** 不另选时的字形与说法：网盘库跟着来源走（115、PikPak 的站标），本地路径没有什么可识别，就是默认磁盘。 */
export function libraryAutoChoice(kind: string): [string, string] {
  const mark = kind !== 'local' ? MEDIA_SOURCE_ICONS[kind] : '';
  return mark ? [mark, '自动识别'] : ['hard-drive', '默认'];
}

/** 「不另选」在单选组里的取值。存下来是空串，而空串做单选值时 React Aria 当作没有选中。 */
const AUTO = 'auto';

export function LibraryIconPicker({ value, label, kind = 'local', onChange }: {
  value: string; label: string; kind?: string; onChange(value: string): void;
}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(value);
  const [autoMark, autoLabel] = libraryAutoChoice(kind);
  const nameOf = (key: string) => (key && LIBRARY_ICON_CHOICES.find(([choice]) => choice === key)?.[1]) || autoLabel;

  return (
    <>
      <Button ref={trigger} variant="secondary" className="self-start" aria-label={label} aria-haspopup="dialog"
        aria-expanded={open} onClick={() => { setDraft(value); setOpen(true); }}>
        <span data-icon-choice className="flex items-center gap-2"><SourceMark mark={value || autoMark} />{nameOf(value)}</span>
      </Button>
      <Popover triggerRef={trigger} isOpen={open} onOpenChange={setOpen} placement="bottom start" offset={4}
        className={MENU_POPOVER_SURFACE}>
        <Dialog aria-label={`${label}可选图标`} className="flex w-72 flex-col gap-3 outline-none">
          <Heading slot="title" className="px-1 text-body-medium text-text-primary">选择媒体库图标</Heading>
          {/* 格子里只放图标，名字走 aria-label：带着字的话 42 个候选要排十几行。 */}
          <RadioGroup aria-label="可选图标" value={draft || AUTO} onChange={(next) => setDraft(next === AUTO ? '' : next)}
            className="inline-grid grid-cols-7 gap-1">
            {LIBRARY_ICON_CHOICES.map(([key, name]) => (
              <Radio key={key || AUTO} value={key || AUTO} aria-label={key ? name : autoLabel}
                className="flex h-10 cursor-pointer items-center justify-center rounded-lg text-foreground-icon-secondary outline-none hover:bg-dropdown-item-hover-background focus-visible:ring-2 focus-visible:ring-border-focus-ring data-selected:bg-dropdown-item-hover-background data-selected:text-text-primary">
                <SourceMark mark={key || autoMark} />
              </Radio>
            ))}
          </RadioGroup>
          <div className="flex justify-end gap-2 border-t border-separator-border pt-2.5">
            <Button variant="secondary" onClick={() => setOpen(false)}>取消</Button>
            <Button disabled={draft === value} onClick={() => { onChange(draft); setOpen(false); }}>应用</Button>
          </div>
        </Dialog>
      </Popover>
    </>
  );
}
