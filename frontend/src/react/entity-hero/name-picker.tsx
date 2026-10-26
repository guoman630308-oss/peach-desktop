/* 名字右边那枚下拉：在这条实体已有的写法里挑一个当统称，或者添一个新的。
 *
 * 统称由用户自己定。同一个人在库里常有中文、日文、罗马字几种写法，哪一个该顶在标题上是他的
 * 偏好，数据库里没有能推出答案的字段。菜单只列这条实体名下已有的写法：换统称是换显示的那一个，
 * 不是改名。这枚下拉恒在，只有一个名字时也在——「她还叫过别的」要有入口，而恰恰是只剩一个
 * 名字的人最需要补。添别名是另一件事，隔一条线摆在末尾。
 *
 * 换统称写的是 `entity.canonical_name` 这个真相字段：确认、写回、回执与重进这一页都在壳里
 * （`actions.chooseName`），勾只在服务端换完、页面重画之后才挪。
 *
 * 面板从按钮左缘往右开：往左开的话 260px 的菜单越过名字压在头像上（用户报过）。面板那身
 * 盒子取 BoardUI 的菜单面（`MENU_POPOVER_SURFACE`，同旧锚定菜单 `.popmenu` 的 16px 圆角、
 * 10px 内边距与投影），菜单项的几何写在 `./entity-hero.css`。 */
import { useEffect, useRef, useState } from 'react';
import { Button, Menu, MenuItem, MenuSection, MenuTrigger, Popover, Separator } from 'react-aria-components';

import { MENU_POPOVER_SURFACE } from '@/components/base/dropdown/menu-styles';

import { Glyph } from './glyph';

export function NamePicker({ current, choices, onChoose, onAddAlias }: {
  current: string;
  choices: string[];
  onChoose(name: string): void;
  onAddAlias(): void;
}) {
  const [open, setOpen] = useState(false);
  const toggle = useRef<HTMLButtonElement>(null);
  /* title 给壳的提示条用（board-controls 只认 [title]），和旧资料卡那枚一样悬停就出字。
     react-aria 的 Button 不收 title，挂载后直接写到节点上；提示条收起时会把它放回来。 */
  useEffect(() => { toggle.current?.setAttribute('title', '名字与别名') }, []);
  return (
    <MenuTrigger isOpen={open} onOpenChange={setOpen}>
      <Button ref={toggle} data-namepick-toggle="" aria-label="名字与别名">
        <Glyph name="chevron-down" />
      </Button>
      {/* 离按钮 8px：旧锚定菜单 wireAnchoredMenu 就是 anchor.bottom + 8，另两枚弹层也是 8。 */}
      <Popover placement="bottom start" offset={8} containerPadding={8} className={MENU_POPOVER_SURFACE}
        data-namepick-menu="">
        <Menu aria-label="名字与别名" data-namepick-list="">
          <MenuSection selectionMode="single" disallowEmptySelection selectedKeys={[current]}
            onSelectionChange={(keys) => {
              const chosen = [...(keys as Set<string>)][0];
              if (chosen && chosen !== current) onChoose(chosen);
            }}>
            {choices.map((option) => (
              <MenuItem key={option} id={option} textValue={option} data-namepick-name={option}>
                <Glyph name="check" /><span>{option}</span>
              </MenuItem>
            ))}
          </MenuSection>
          <Separator data-namepick-rule="" />
          <MenuSection>
            <MenuItem id="alias" textValue="添加别名" data-namepick-alias="" onAction={onAddAlias}>
              <Glyph name="plus" /><span>添加别名…</span>
            </MenuItem>
          </MenuSection>
        </Menu>
      </Popover>
    </MenuTrigger>
  );
}
