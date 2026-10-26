/* 队列里的一张卡：问的是什么、凭什么判、以及三个结论。
 *
 * 主体动作在最右——一行里从左到右是「拒绝、跳过、通过」，读到最后一枚才是这张卡真正要人
 * 做的判断。Geist 的弹层与 Fieldset 操作条都是这个方向。 */
import { useRef } from 'react';

import { avatarInner } from '@peach/card-art';

import { Button } from '@/components/base/buttons/button';
import { Checkbox } from '@/components/base/checkbox/checkbox';
import { Chip } from '@/components/base/badges/chip';
import { cardClass } from '../components/card';
import { EmptyState } from '../components/empty-state';
import { useOverlayScrollbar } from '../components/overlay-scrollbar';
import { RiImageLine } from '@remixicon/react';
import { busyProps } from '../settings/use-action';
import { CandidateChoices, PendingGenres } from './candidate-form';
import { AssetPicker, EntityHead, IdentityEvidence, OriginAsset, SourceImage } from './review-evidence';
import type { DecisionStatus, ReviewCategory, ReviewRow, RowChoice } from './review';
import {
  canApprove, ENTITY_REVIEW_KINDS, rowEvidence, rowFieldName, rowSubject, rowTags, rowTitle,
} from './review';

/* 队列里的卡是一圈细描边围着一层浅底：判定状态靠卡内的东西表达，整卡不换色，所以卡面
   本身要淡——一屏二十张同样的框并排，卡面越安静，眼睛越容易落到内容上。横向内边距 20px
   到边，判定条自己铺满卡底那一格。这一页所有卡只读这一个常量，免得出现两种卡。 */
const CARD = cardClass({
  variant: 'outlined', padding: 'none', className: 'flex flex-col gap-4 px-5 pt-4',
});

/* 卡头那一行的行高：字段名是一枚 caption Chip（12/16 加上下各 4），24px 由它定。标题、
   勾选框和判定徽章各自在这 24px 里居中，谁的字号变了也还在同一条中线上。 */
const HEADING_LINE = 'flex min-h-6 min-w-0 items-center';

/** 实体类卡头最多露几部对照作品，同服务端给的条数。 */
const COMPARISON_SHOWN = 2;

/* 一排卡等高，长出来的那部分在卡内滚。高度随内容走的话，一行里几张卡参差不齐，
   眼睛要在每张卡上重新找「判定」在哪；身份回配那一类要放下来源图和样本列表，高一档。 */
const CARD_HEIGHT = (category: ReviewCategory) =>
  (category === 'western_identity' ? 'h-review-card-tall' : 'h-review-card');

export interface CardHandlers {
  openItem(id: number): void;
  openEntity(kind: string, name: string): void;
  onReveal(id: number): void;
  revealing: number;
  revealNote: string;
  toast(message: string): void;
}

export interface ReviewCardProps {
  category: ReviewCategory;
  row: ReviewRow;
  choice: RowChoice;
  onChoice(next: RowChoice): void;
  selected: boolean;
  onSelect(range: boolean, checked: boolean): void;
  locked: boolean;
  busy: boolean;
  problem: string;
  genreTags: string[];
  onDecide(status: DecisionStatus): void;
  handlers: CardHandlers;
}

export function ReviewCard(props: ReviewCardProps) {
  const {
    category, row, choice, onChoice, selected, onSelect, locked, busy, problem, genreTags,
    onDecide, handlers,
  } = props;
  /* Checkbox 的 `onChange` 只给新状态，Shift 要从触发它的那一下事件里取：按下先于变更，
     键盘的空格也一样。 */
  const range = useRef(false);
  /* 卡里会自己滚的两块：中段的证据，和贴在卡底那一句当前信息。 */
  const body = useOverlayScrollbar<HTMLDivElement>();
  const current = useOverlayScrollbar<HTMLParagraphElement>();
  const metadata = category === 'metadata_fields';
  const subjectKind = ENTITY_REVIEW_KINDS[category] || '';
  const subjectName = String(row.creator || '').trim();
  const asEntity = !!subjectKind && !!subjectName;
  const title = rowTitle(category, row);
  const fieldName = rowFieldName(category, row);
  const subject = rowSubject(category, row);
  const evidence = rowEvidence(row);
  const tags = rowTags(row);
  const approvable = canApprove(category, row);
  const comparison = row.comparison_assets || [];
  const assets = row.preview_assets || [];
  const decided = String(row.decision || 'pending') !== 'pending';

  /* 对照作品排在不缩的卡头里：服务端给两条，再多也只露前两条，其余报一个数，
     否则卡头会把动作条挤出卡外。 */
  const origin = comparison.length > 1
    ? <div className="flex flex-col gap-3">
        {comparison.slice(0, COMPARISON_SHOWN).map((asset) => (
          <OriginAsset key={asset.id} asset={asset} openItem={handlers.openItem} />
        ))}
        {comparison.length > COMPARISON_SHOWN
          ? <p className="text-body-2-regular text-text-secondary">
              {`另有 ${(comparison.length - COMPARISON_SHOWN).toLocaleString()} 部对照作品`}
            </p>
          : null}
      </div>
    : asEntity
      ? <EntityHead kind={subjectKind} name={subjectName}
          works={Number(row.video_count || row.videos || 0)}
          openEntity={handlers.openEntity}
          avatar={avatarInner(subjectName,
            row.entity_id
              ? { id: row.entity_id, has_image: !!row.has_image, image_version: row.image_version, avatar_focus: row.avatar_focus }
              : null,
            null, subjectKind)} />
      : row.asset_id
        ? <OriginAsset openItem={handlers.openItem}
            asset={{ id: row.asset_id, name: row.asset_name, preview_url: row.asset_preview_url }} />
        : null;

  /* 预览和判断依据是同一件事的两半：看这几帧，然后读这一句。候选表单和身份证据那两类
     每一项自己就是一个框，不再套一层。 */
  const preview = metadata
    ? <CandidateChoices row={row} fieldName={fieldName} value={choice.candidateKey}
        onChange={(next) => onChoice({ ...choice, candidateKey: next })} locked={locked} />
    : category === 'creator_tags'
      ? (assets.length
        ? <AssetPicker assets={assets} picked={choice.assets} locked={locked}
            onPicked={(next) => onChoice({ ...choice, assets: next })} />
        /* 空白一片会被当成界面坏了。真实原因是这些作品还没抽帧，说清楚比留白好。 */
        : <EmptyState shell="inset" icon={RiImageLine} title="这批作品尚未抽帧">
            {`${row.video_count || ''} 条作品还没有可用预览；批准后仍会按所选资料写入标签。`}
          </EmptyState>)
      : category === 'fc2_similarity'
        ? null
        : category === 'western_identity'
          ? <IdentityEvidence row={row} openItem={handlers.openItem} openEntity={handlers.openEntity}
              onReveal={handlers.onReveal} revealing={handlers.revealing} note={handlers.revealNote} />
          : <SourceImage url={row.preview_url} />;

  const heading = asEntity
    ? origin
    : fieldName
      /* 字段名是这张卡在问的问题（「这个作品的创作者填什么」），作品标识只是它问的对象。
         写在标题末尾的话，一条无番号视频的文件名会先把它挤出省略号。 */
      ? <h4 className={`${HEADING_LINE} flex-wrap gap-2`}>
          {/* Chip 自己不截断：外面套一层封顶的裁切框，超长的字段名不撑出卡外，全文在 title 里。 */}
          <span title={fieldName} className="flex max-w-full min-w-0 overflow-hidden rounded-md"><Chip variant="caption" color="blue">{fieldName}</Chip></span>
          <span title={subject} className="min-w-0 truncate text-body-medium text-text-primary">{subject}</span>
        </h4>
      : <h4 className={HEADING_LINE}>
          <span title={title} className="min-w-0 truncate text-body-medium text-text-primary">{title}</span>
        </h4>;

  return (
    <section aria-label={title} data-review-key={row.item_key}
      className={`${CARD} ${CARD_HEIGHT(category)} overflow-hidden`}>
      <header className="flex min-w-0 shrink-0 items-start gap-2">
        {locked ? null : (
          /* 勾选框只有 16px，标题那行 24px。两个都按 24px 各自居中，中线才落在一条上——
             顶对顶排的话，读的人看到的是勾选框浮在标题上面一截。 */
          <span className={`${HEADING_LINE} shrink-0`}
            onPointerDownCapture={(event) => { range.current = event.shiftKey }}
            onKeyDownCapture={(event) => { range.current = event.shiftKey }}>
            <Checkbox isSelected={selected} aria-label={`选择 ${title}`}
              onChange={(on) => onSelect(range.current, on)} />
          </span>
        )}
        <div className="min-w-0 flex-1">{heading}</div>
        {decided ? <Chip variant="caption" color="neutral">{row.decision}</Chip> : null}
      </header>

      {/* 中段只长不缩：内容短时它吃掉剩下的高度，内容长时卡不变形，由这一段自己滚。
          外面这一层只为放那条覆盖式滚动条的轨道：它按 `absolute` 铺，得有一个只裹着
          滚动块本身的定位祖先，否则会跑到整页的右边缘去。 */}
      <div className="relative flex min-w-0 min-h-0 flex-1 flex-col">
        <div ref={body} className="flex min-w-0 min-h-0 flex-1 flex-col gap-3 overflow-y-auto">
          {/* 数据库规范名当标题，抓取来源给的写法（多为罗马音）留作副标题。 */}
          {row.source_name
            ? <p className="text-body-2-regular text-text-secondary">{`来源写法：${row.source_name}`}</p>
            : null}
          {/* 实体类卡片的作品数已经写在创作者入口里，这里再写一遍就是同一个数字两处。 */}
          {!asEntity && (row.board || row.assets)
            ? <p className="text-body-2-regular text-text-secondary">
                {`对照文件：${Number(row.video_count || row.assets || 0).toLocaleString()}`}
              </p>
            : null}
          {!asEntity && origin ? origin : null}
          {tags.length || category === 'creator_tags' ? (
            <div className="flex flex-wrap gap-1">
              {tags.length
                ? tags.map((tag) => <Chip key={tag} variant="caption" color="soft">{tag}</Chip>)
                : <small className="text-body-2-regular text-text-secondary">暂无待确认标签</small>}
            </div>
          ) : null}
          {preview}
          <PendingGenres row={row} tags={genreTags} locked={locked} toast={handlers.toast} />
          {!metadata && evidence
            ? <p className="text-body-2-regular text-text-secondary wrap-anywhere">{evidence}</p>
            : null}
        </div>
      </div>

      {/* 候选表单的当前信息另有去处：它贴在卡底，那一句读的是「现在是什么」，不是这一屏
          证据的一部分。外面一层同样只为那条轨道，负边距由它出：轨道要贴着卡的内缘，
          而这一句自己得铺满整条。 */}
      {metadata && evidence
        ? <div className="relative -mx-5 -mb-4 shrink-0">
            <p ref={current} role="region" aria-label="当前信息"
              className="max-h-22 overflow-y-auto border-t border-separator-border px-5 py-2.5 text-body-2-regular text-text-secondary wrap-anywhere">
              {evidence}
            </p>
          </div>
        : null}

      {/* 动作条占满卡底那一格：沉一档底色、上面一条线把「看证据」和「下判断」分开，
          下面两角跟着卡的圆角收口。 */}
      <footer className="-mx-5 flex min-h-14 shrink-0 flex-wrap items-center justify-end gap-2 rounded-b-surface border-t border-separator-border bg-background-secondary-default py-3 pr-3 pl-5">
        <Button variant="danger" size="small" disabled={locked} {...busyProps(busy)}
          onClick={() => onDecide('rejected')}>拒绝</Button>
        <Button variant="secondary" size="small" disabled={locked} {...busyProps(busy)}
          onClick={() => onDecide('skipped')}>跳过</Button>
        {/* 没有可采纳的候选时这颗键灰着，字面说它为什么点不了，和旁边的「跳过」分得开。 */}
        <Button variant="primary" size="small" disabled={locked || !approvable} {...busyProps(busy)}
          title={approvable ? undefined : '没有可采用的资料，只能拒绝或跳过'}
          onClick={() => onDecide('approved')}>
          {approvable ? '通过' : '不可通过'}
        </Button>
        {problem
          ? <p role="status" className="w-full text-body-2-regular text-text-error-primary">{problem}</p>
          : null}
      </footer>
    </section>
  );
}
