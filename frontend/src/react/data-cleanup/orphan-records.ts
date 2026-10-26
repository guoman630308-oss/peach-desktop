/* 孤儿记录的数据契约：文件已不在盘上、带着个人记录的作品（ADR-0087）。
 *
 * 一个读端点、一个写端点。列表带每条在六张表里的记录数、行上的评分与次数和在库候选；
 * 接回把记录搬到选中的文件、删掉旧行，记一个可整批撤回的批次。彻底删除走批量接口的 `delete`。 */
import { apiGet, apiSend } from '../../api';
import { BATCH_URL } from '../duplicates/duplicates';

export const ORPHAN_RECORDS_URL = '/api/orphan-records';
export const ORPHAN_ATTACH_URL = '/api/orphan-records/attach';
export const ORPHAN_RECORDS_KEY = ['orphan-records'] as const;

/** 在库里可能是它新版本的那个文件。 */
export interface OrphanCandidate {
  id: number;
  location: string;
  name: string;
  duration: number | null;
  size: number | null;
}

export interface OrphanRecord {
  id: number;
  name: string;
  code: string;
  location: string;
  duration: number | null;
  size: number | null;
  vanished_at: number | null;
  has_thumb: boolean;
  /** 六张表各有几条，加上行上的评分、高潮与播放次数；`last_played` 记过就是 1。没有的不出现。 */
  records: Partial<Record<RecordKey, number>>;
  candidates: OrphanCandidate[];
}

export interface OrphanRecordsData { total: number; items: OrphanRecord[] }

export type RecordTable = 'asset_preference' | 'watch_queue' | 'playlist_item'
  | 'asset_quality_goal' | 'activity_event' | 'asset_tag_preference';

/** 行上自己的四列个人记录。 */
export type RowRecord = 'rating' | 'o_count' | 'play_count' | 'last_played';

export type RecordKey = RecordTable | RowRecord;

/** 六张表在这一块里的名字，顺序就是显示顺序。 */
export const RECORD_LABELS: ReadonlyArray<readonly [RecordTable, string]> = [
  ['asset_preference', '喜欢与理由'],
  ['watch_queue', '稍后看'],
  ['playlist_item', '播放列表'],
  ['asset_quality_goal', '寻找更好版本'],
  ['activity_event', '观看历史'],
  ['asset_tag_preference', '隐藏标签'],
];

export const fetchOrphanRecords = (signal?: AbortSignal) =>
  apiGet<OrphanRecordsData>(ORPHAN_RECORDS_URL, signal);

/** 把一条孤儿记录接到在库的某个文件上。真实 ledger 写入，旧行随之删除。 */
export const attachOrphan = (id: number, target: number) =>
  apiSend<{ ok: boolean; batch: string }>(ORPHAN_ATTACH_URL, { id, target });

/** 彻底删除这一条和它带的记录。不可撤销，调用方必须先过危险档的确认弹层。
 *  文件又回到盘上的那一条落在 `blocked` 里、不删，下一轮扫描会把它接回在库。 */
export const purgeOrphan = (id: number | number[]) =>
  apiSend<{ ok: boolean; purged: number; blocked: unknown[] }>(BATCH_URL, { ids: Array.isArray(id) ? id : [id], operation: 'delete' });

/** 行上四列的写法：分值与次数照写；只记过最近播放、没有播放次数时写「播放过」。 */
const rowRecordsText = (records: OrphanRecord['records']) => [
  records.rating ? `评分 ${records.rating}` : '',
  records.play_count ? `播放 ${Number(records.play_count).toLocaleString()} 次`
    : records.last_played ? '播放过' : '',
  records.o_count ? `高潮 ${Number(records.o_count).toLocaleString()} 次` : '',
];

/** 一条带的记录排成一句：「喜欢与理由 1 · 观看历史 3 · 评分 4 · 播放 2 次」。 */
export const recordsText = (records: OrphanRecord['records']) => [
  ...RECORD_LABELS
    .filter(([table]) => records[table])
    .map(([table, label]) => `${label} ${Number(records[table]).toLocaleString()}`),
  ...rowRecordsText(records),
].filter(Boolean).join(' · ');

const minutes = (seconds: number | null) => (seconds ? `${Math.round(seconds / 60)} 分钟` : '');

/** 下拉框里一个候选文件那一行。 */
export const candidateText = (item: OrphanCandidate) =>
  [item.name, minutes(item.duration)].filter(Boolean).join(' · ');
