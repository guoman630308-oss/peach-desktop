/** 公开作品资料：发行期每天补齐，满一个月后的成功快照固定保存。 */
const DAY = 86_400_000;
const calendarDay = (time: number) => {
  const day = new Date(time);
  return Date.UTC(day.getFullYear(), day.getMonth(), day.getDate());
};

function stableAfter(released?: string | null) {
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:$|T)/.exec(released || '');
  if (!match) return undefined;
  const year = Number(match[1]), month = Number(match[2]) - 1, day = Number(match[3]);
  const issued = new Date(Date.UTC(year, month, day));
  if (issued.getUTCFullYear() !== year || issued.getUTCMonth() !== month || issued.getUTCDate() !== day) return undefined;
  const last = new Date(Date.UTC(year, month + 2, 0)).getUTCDate();
  return Date.UTC(year, month + 1, Math.min(day, last));
}

export function publicationLifetime(released?: string | null, now = Date.now()) {
  const cutoff = stableAfter(released);
  return cutoff !== undefined && calendarDay(now) > cutoff ? Infinity : DAY;
}

export function publicationFresh(saved: number, released?: string | null, now = Date.now()) {
  if (!Number.isFinite(saved) || saved <= 0 || saved > now) return false;
  return publicationLifetime(released, now) === Infinity
    ? calendarDay(saved) > stableAfter(released)!
    : now - saved < DAY;
}
