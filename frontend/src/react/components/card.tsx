/* Board 的卡面。只有这一份出处。
 *
 * 旧样式表里的卡分两族，按页各有各的基线，不能一律画成同一种：
 *
 * - **填充**：`background:var(--ground);border:0`，圆角 16px（图表卡 20px），底下压一层
 *   1px 接触阴影。读数卡 `.metricstrip>button`、口味读数 `.tastesummary`、面板
 *   `.insightpanel`、分区 `.followmanage .fmain>.fsec`、图表卡 `.board-radial-card` 都是这一族。
 *   `--ground` 接的就是 `--color-background-secondary-default`（`web/board.css` 的 `:root`
 *   映射），阴影取上游同一用途的 `--shadow-card`（「1px contact shadow used on white cards
 *   sitting on the secondary background」）。
 * - **描边**：`border:1px solid var(--line-soft);background:var(--color-background-primary-default)`，
 *   复核页的 `body[data-surface="/review"] .reviewitem` 是这一族——卡浮在页面底上，靠一条
 *   发丝线收边，不靠换底色。
 *
 * 另有一批是「填充再加一条描边」：活动页 `.cleanupfieldset` 是 `--ground` 底加 `--line`，
 * 高清版 `.qualityitem` 是 `--ground` 底加更淡的 `--line-soft`。所以描边是填充之上的一档
 * 可选项（`bordered`），不是填充的对立面。
 *
 * 迁移时每页各拼一串 `rounded-2xl border border-separator-border`，同一种卡片在十个文件里
 * 长出二十种描边空心壳，页面整体掉了一层底色——卡面收在这里就是为了不再发生这件事。 */

/** 圆角：读数卡、面板、分区都是 16px；图表卡大一档 20px，和旧 `.board-radial-card` 一致；
 *  描边卡是 Board 那档不浮起的表面 `--surface-radius`（14px），`outlined` 默认取它。 */
export type CardRadius = 'default' | 'chart' | 'surface' | 'plain';

/** 内边距：`none` 交给卡内自己的头、体、脚各自排；`default` 是旧图表卡与面板体的 20px。 */
export type CardPadding = 'none' | 'default';

/** 卡面本身。哪一档由那一页的基线 CSS 说了算，不由「看起来更好」说了算。
 *
 *  - `filled`：`--ground` 填充、无描边。Board 管理区的主力卡。
 *  - `outlined`：`--color-background-primary-default` 底加一条 `--line-soft`。复核页的条目卡。
 *  - `raised`：primary 底、无描边。卡上再浮一层（关注管理里按作者分组的那张卡）。 */
export type CardVariant = 'filled' | 'outlined' | 'raised';

export type CardOptions = {
  variant?: CardVariant;
  radius?: CardRadius;
  padding?: CardPadding;
  /** 卡片本身可点（读数卡兼页签、来源卡）：补悬停填充、焦点环与光标。 */
  interactive?: boolean;
  /** 卡片带互斥选中态：选中时换成浮层那一档面、收掉接触阴影，最上层压一圈 2px 的焦点环色描边。 */
  selectable?: boolean;
  /** 填充之上另加的那条描边：活动页的 Fieldset 用 `--line`，高清版的条目用 `--line-soft`。
   *  `outlined` 自带描边，不必再给。 */
  bordered?: 'line' | 'soft';
  className?: string;
};

const FACE = 'min-w-0';

const VARIANT: Record<CardVariant, string> = {
  filled: 'bg-background-secondary-default',
  outlined: 'border border-separator-border bg-background-primary-default',
  raised: 'bg-background-primary-default',
};

/* 圆角与阴影是一件事：旧样式表里有阴影的只有读数卡那一档（`0 1px 2px #00000008`），
 * 图表卡 `.board-radial-card` / `.board-heat-card` / `.board-sankey-card` 都是净面。 */
const RADIUS: Record<CardRadius, string> = {
  default: 'rounded-2xl shadow-card',
  chart: 'rounded-2-5xl',
  surface: 'rounded-surface',
  /* 数据管理那一排净面读数卡（旧 `.board-plain-stat`）：同样 16px，但不压接触阴影。 */
  plain: 'rounded-2xl',
};

const BORDER = {
  line: 'border border-border-button-default',
  soft: 'border border-separator-border',
};

const PADDING: Record<CardPadding, string> = {
  none: '',
  default: 'p-5',
};

/* 悬停只抬填充，选中时不抬：旧规则里选中态那条排在悬停后面，压着它。工具类的先后由
 * Tailwind 自己排，不是写的顺序，所以把「没选中」写进变体里，不指望顺序。 */
const INTERACTIVE = 'cursor-pointer outline-none not-data-selected:hover:bg-card-hover'
  + ' focus-visible:ring-2 focus-visible:ring-border-focus-ring focus-visible:ring-offset-3';

/* 选中的那一圈画在 `::after` 上、压在最上层，不用 inset 的 box-shadow：内阴影画在子元素底下，
 * 读数卡的脚注带一铺底色，环的下半截就被盖掉，只剩上面三边。旧
 * `.metricstrip>button[aria-selected=true]::after` 就是一层 2px 描边的覆盖层，圆角跟卡走。 */
const SELECTABLE = 'relative data-selected:bg-background-primary-default data-selected:shadow-none'
  + ' data-selected:after:pointer-events-none data-selected:after:absolute data-selected:after:inset-0'
  + ' data-selected:after:z-1 data-selected:after:rounded-[inherit]'
  + ' data-selected:after:border-2 data-selected:after:border-border-focus-ring';

/** 卡面的类名。宿主可能是 `section`、`li`、React Aria 的 `Tab` 或 `Modal`，所以只给类名。 */
export function cardClass(options: CardOptions = {}) {
  const {
    variant = 'filled', padding = 'default', interactive, selectable, bordered, className,
  } = options;
  /* 圆角跟着变体走：填充那一族是 16px 加一层接触阴影，描边那一族是 14px 的净面。
     旧样式表里这两件事本来就绑在一起，拆开写只会让调用方每次都要记住配哪一个。 */
  const radius = options.radius ?? (variant === 'outlined' ? 'surface' : 'default');
  return [
    FACE,
    VARIANT[variant],
    RADIUS[radius],
    PADDING[padding],
    bordered ? BORDER[bordered] : '',
    interactive ? INTERACTIVE : '',
    selectable ? SELECTABLE : '',
    className ?? '',
  ].filter(Boolean).join(' ');
}
