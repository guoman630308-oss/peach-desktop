/* 标签页的三块：筛选浮层、正文（字母表或标签云）、所选标签的操作条。
 *
 * 两套词表共用这三块，区别在颜色和去处：本地是 ledger 里的中文标签，按 ledger 的九类着色，
 * 点开回目录按它筛选，选择模式下拼成多标签筛选；在线是关注来源上的 booru 标签，按上游
 * tag_type 着色，点开去「关注 · 这个标签」，并且不进多选——多选拼的是目录筛选，拿在线标签
 * 去筛目录必然一条不中。 */
import { Radio, RadioGroup } from 'react-aria-components';

import { Button } from '@/components/base/buttons/button';
import { Checkbox } from '@/components/base/checkbox/checkbox';

import { TabCount } from '../components/board-tabs';
import { cardClass } from '../components/card';
import { FilterGlassRows, FilterPill } from '../components/filter-glass';
import { SEGMENTED_GLASS_TRACK, SEGMENT_GLASS } from '../components/segmented';
import { SelectionDock } from '../components/selection-dock';
import { spriteGlyph } from '../components/sprite-glyph';
import { useHorizontalScroller } from '../components/use-horizontal-scroller';
import { FavoriteButton } from '../library-shortcuts/shortcut-controls';
import { tagColorKey, type IndexTag, type TagView } from './index-data';

export interface TagEntry extends IndexTag {
  label: string;
}

/** 一枚标签的交互：选择模式下切换选中，否则直接打开。 */
export interface TagActions {
  online: boolean;
  picked: ReadonlySet<string>;
  press(tag: string): void;
}

const Dot = () => <span aria-hidden data-tag-dot="" className="size-1.75 flex-none rounded-full" />;

/* 一行一枚标签：36px 高、8px 圆角，取的是 Board 排名行那一档身量。nowrap 是省略号的前提，
   少了它长标签改成折行，同一行里的其它标签跟着被拉高，几列各行高度也对不齐。 */
function AlphaTag({ tag, actions }: { tag: TagEntry; actions: TagActions }) {
  const pressed = actions.picked.has(tag.k);
  return (
    <div className="flex min-w-0 items-center gap-1">
    <button type="button" data-alpha-tag="" data-k={tag.k} data-tag-cat={tagColorKey(actions.online, tag.cat)}
      aria-pressed={pressed} title={tag.label} onClick={() => actions.press(tag.k)}
      className="flex h-9 min-w-0 flex-1 cursor-pointer items-center gap-2.25 rounded-lg px-2.5 text-left text-body-regular text-text-secondary outline-none hover:text-text-primary focus-visible:ring-2 focus-visible:ring-border-focus-ring">
      <Dot />
      <span className="min-w-0 flex-1 truncate">{tag.label}</span>
      <span className="flex-none text-caption-1-regular text-text-secondary">{tag.n.toLocaleString()}</span>
    </button>
    {!actions.online ? <FavoriteButton item={{ kind: 'tag', name: tag.k, entity_id: tag.entity_id, favorite: tag.favorite }} /> : null}
    </div>
  );
}

/** 字母表：每个首字一张卡，字头是卡的标题，右边挂这一组有几个。`data-alpha-group` 是浮层
 *  下排那排跳转键的落点，卡顶给吸顶的浮层让出位置。 */
export function TagAlphabet({ groups, actions }: { groups: [string, TagEntry[]][]; actions: TagActions }) {
  return (
    <div data-alphabet="" className="flex flex-col gap-4">
      {groups.map(([letter, tags], at) => (
        <section key={letter} data-alpha-group={at}
          className={cardClass({ radius: 'plain', padding: 'none', className: 'px-5 pt-4 pb-3' })}>
          <h3 className="mb-2 flex items-center text-title-2-medium text-text-primary">
            {letter}<TabCount value={tags.length} />
          </h3>
          <div className="index-tag-list gap-x-4 gap-y-0.5">
            {tags.map((tag) => <AlphaTag key={tag.k} tag={tag} actions={actions} />)}
          </div>
        </section>
      ))}
    </div>
  );
}

const NBSP = String.fromCharCode(0xa0);

/** 标签云：这一页它们是主体，不是附注，所以比卡片上的标签大一档。名字与计数之间隔一个
 *  不断行空格，宽度是名字那一档字号下的一个空格（13px 时约 3.7px）；flex 会吞掉普通空格，
 *  所以用不断行的那一个。 */
export function TagCloud({ tags, actions }: { tags: TagEntry[]; actions: TagActions }) {
  return (
    <div data-tag-cloud="" className="flex flex-wrap gap-1.75">
      {tags.map((tag) => (
        <span key={tag.k} className="inline-flex min-w-0 items-center gap-1">
        <button type="button" data-tag-chip="" data-k={tag.k}
          data-tag-cat={tagColorKey(actions.online, tag.cat)} aria-pressed={actions.picked.has(tag.k)}
          onClick={() => actions.press(tag.k)}
          className="inline-flex cursor-pointer items-center rounded-lg border px-3 py-1.25 text-body-2-regular text-text-secondary outline-none focus-visible:ring-2 focus-visible:ring-border-focus-ring">
          {tag.label + NBSP}
          <span data-tag-n="" className="text-caption-1-regular opacity-60">{tag.n.toLocaleString()}</span>
        </button>
        {!actions.online ? <FavoriteButton item={{ kind: 'tag', name: tag.k, entity_id: tag.entity_id, favorite: tag.favorite }} /> : null}
        </span>
      ))}
    </div>
  );
}

const VIEWS: readonly (readonly [TagView, string, string])[] = [
  ['cloud', '标签云', 'tags'], ['alphabet', '字母表', 'text-aa'],
];

/** 筛选浮层：上排类型药丸，下排读数、按首字跳转与视图切换。只摆数得到的那几类。 */
export function TagFilters(
  { categories, category, online, readout, letters, view, onCategory, onJump, onView }: {
    categories: readonly (readonly [string, string])[];
    category: string;
    online: boolean;
    readout: string;
    letters: string[];
    view: TagView;
    onCategory(key: string): void;
    onJump(at: number): void;
    onView(view: TagView): void;
  },
) {
  const alphaScroll = useHorizontalScroller<HTMLElement>();
  return (
    <FilterGlassRows label="标签筛选" topLabel="标签类型"
      top={categories.map(([key, label]) => (
        <FilterPill key={key} data-tag-pill="" data-tag-cat={key === 'all' ? 'all' : tagColorKey(online, key)}
          pressed={category === key} onPress={() => onCategory(key)}>
          <Dot />{label}
        </FilterPill>
      ))}
      bottom={<>
        <span data-index-readout="" className="flex-none text-caption-1-regular leading-5 whitespace-nowrap text-text-secondary tabular-nums">
          {readout}
        </span>
        {letters.length > 1 ? (
          <nav ref={alphaScroll} aria-label="按首字跳转" data-alpha-jump=""
            className="flex min-w-0 gap-0.5 overflow-x-auto overscroll-x-contain">
            {letters.map((letter, at) => (
              <button key={letter} type="button" onClick={() => onJump(at)}
                className="h-7.5 min-w-7.5 flex-none cursor-pointer rounded-glass-key px-1.5 text-caption-1-regular leading-5 text-(--glass-text)/60 outline-none hover:text-(--glass-text) focus-visible:ring-2 focus-visible:ring-border-focus-ring">
                {letter}
              </button>
            ))}
          </nav>
        ) : null}
        <RadioGroup aria-label="标签视图" orientation="horizontal" value={view}
          onChange={(next) => onView(next as TagView)} className={`ml-auto ${SEGMENTED_GLASS_TRACK}`}>
          {VIEWS.map(([value, label, symbol]) => {
            const Glyph = spriteGlyph(symbol);
            return (
              <Radio key={value} value={value} aria-label={label} data-glass-segment="" className={SEGMENT_GLASS}>
                <span title={label} className="contents"><Glyph className="size-4" /></span>
              </Radio>
            );
          })}
        </RadioGroup>
      </>}
    />
  );
}

/** 所选标签的操作条。三颗键都只动这一屏：「广泛匹配」切的是回目录时按任一还是全部匹配，
 *  「清空」只清本地所选，「显示结果」回目录按所选标签筛选——没有一颗写数据库。 */
export function TagDock(
  { count, match, onMatch, onClear, onApply }:
  { count: number; match: 'any' | 'all'; onMatch(match: 'any' | 'all'): void; onClear(): void; onApply(): void },
) {
  return (
    <SelectionDock visible={count > 0} label="所选标签操作" count={`已选 ${count.toLocaleString()} 个标签`}>
      <Checkbox isSelected={match === 'any'} onChange={(on) => onMatch(on ? 'any' : 'all')}>
        <span className="flex flex-col">
          <span className="text-body-medium">广泛匹配</span>
          <span className="text-caption-1-regular text-text-secondary">开启后匹配任一所选标签；关闭后必须同时包含全部标签。</span>
        </span>
      </Checkbox>
      <Button variant="secondary" size="small" onClick={onClear}>清空</Button>
      <Button variant="primary" size="small" disabled={!count} onClick={onApply}>显示结果</Button>
    </SelectionDock>
  );
}
