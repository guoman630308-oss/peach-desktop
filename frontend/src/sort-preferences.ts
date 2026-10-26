/* 目录排序的词表：有哪几列、每一列的方向怎么说、旧键怎么换成新键。壳的地址解析、筛选条的排序钮与
 * 启动时的设置归一化（`appearance/settings.ts`）读的都是这一份。 */

/** `[键, 显示名]`。随机排第一：它是出厂默认，也没有方向。 */
export const SORTS: readonly (readonly [key: string, label: string])[] = [
  ['seed', '随机'], ['rating', '评分'], ['o', '高潮计数'], ['plays', '观看次数'], ['dur', '时长'],
  ['size', '体积'], ['new', '入库时间'], ['played', '观看时间'],
];
/** 只在 JAV 语境里出现的那一列。 */
export const JAV_RELEASE_SORT: readonly [key: string, label: string] = ['release', '发行时间'];
export const SORT_KEYS: readonly string[] = [...SORTS, JAV_RELEASE_SORT].map(([key]) => key);

/** `[desc, asc]` 两种方向的说法。 */
export type SortDirWords = Readonly<Record<string, readonly [desc: string, asc: string]>>;

/* 方向词按列各自定义：同一个 desc 在时间列上是「从新到旧」，在时长上是「从长到短」，
   写成通用的「降序」等于让界面解释 SQL。数组是 [desc,asc]，在表里就等于这一列可翻转。 */
export const SORT_DIR_WORDS: SortDirWords = {
  rating: ['从高到低', '从低到高'], o: ['从多到少', '从少到多'],
  plays: ['从多到少', '从少到多'], dur: ['从长到短', '从短到长'], size: ['从大到小', '从小到大'],
  new: ['从新到旧', '从旧到新'], played: ['从近到远', '从远到近'], release: ['从新到旧', '从旧到新'],
};

/* 旧键沿用：地址栏、书签和设置里存着把方向写进键名的值。方向现在单独由 `dir` 表达，
   两个时长键收敛成一个 dur；认不出旧键的后果不是报错，是静默换成另一种排序。 */
export const SORT_ALIASES: Readonly<Record<string, readonly [sort: string, dir: 'asc' | 'desc']>> = {
  big: ['size', 'desc'], short: ['dur', 'asc'], long: ['dur', 'desc'],
};

/* 词表可换：关注页排的是在线更新，列不一样（热度那一列只有它有），但「点未选中项换列、
   点选中项翻方向」和箭头怎么画两页完全相同。传表进来，这三枚函数就不必各写一份。 */
export const sortDirWord = (key: string, dir: string, words: SortDirWords = SORT_DIR_WORDS): string =>
  (words[key] || [])[dir === 'asc' ? 1 : 0] || '';

/** 一列的默认方向：可翻转的列从 desc 起，随机这类没有方向的列是空串。 */
export const defaultSortDir = (key: string, words: SortDirWords = SORT_DIR_WORDS): string => (words[key] ? 'desc' : '');

/* 点未选中项＝换列并用该列的默认方向；点选中项＝翻方向。随机没有方向，重复点它
   什么都不做——换一批是它旁边那枚按钮的事。 */
export function nextSortState(
  key: string, current: string, dir: string, words: SortDirWords = SORT_DIR_WORDS,
): { sort: string; dir: string } | null {
  if (key !== current) return { sort: key, dir: defaultSortDir(key, words) };
  if (!words[key]) return null;
  return { sort: key, dir: dir === 'asc' ? 'desc' : 'asc' };
}

/** 首页未指定方向时使用浏览偏好；显式 URL 始终优先。 */
export function preferredDirection(sort: string, preferredSort: string, direction: unknown): string {
  return sort === 'seed' ? '' : sort === preferredSort && direction === 'asc' ? 'asc' : 'desc';
}

/** 地址上的 `sort` 与 `dir` 一次解成列和方向。旧键自带方向；`dir` 显式写了就听它的；写了列、没写方向是那一列的
 *  默认方向（目录地址省掉的正是它）；两样都没写按浏览偏好。随机没有方向。认不出的列换成 `fallback`。 */
export function sortFromAddress(
  rawSort: string | null, rawDir: string | null, preferredSort: string, direction: unknown, fallback = preferredSort,
): { sort: string; dir: string } {
  const alias = rawSort ? SORT_ALIASES[rawSort] : undefined;
  const key = alias ? alias[0] : rawSort;
  const sort = key && SORT_KEYS.includes(key) ? key : fallback;
  if (!SORT_DIR_WORDS[sort]) return { sort, dir: '' };
  const dir = rawDir === 'asc' || rawDir === 'desc' ? rawDir
    : alias ? alias[1] : rawSort ? 'desc' : preferredDirection(sort, preferredSort, direction);
  return { sort, dir };
}
