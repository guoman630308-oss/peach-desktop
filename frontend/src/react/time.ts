/* 数据库里一律存 UTC（ISO 带 Z），界面要按看的人所在时区显示。第二个页面要用就搬进
 * 这里，不各写一份：两处各自解释「没有时区标记的算哪一边」的话，同一个时间会在两页上
 * 差出八小时。 */

/** 本机时间。没有时区标记的按 UTC 解释——存进去的时候就是 UTC。 */
export function localTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const text = /[Zz]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`;
  const when = new Date(text);
  if (Number.isNaN(when.getTime())) return String(iso).replace('T', ' ').slice(0, 16);
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())} `
    + `${pad(when.getHours())}:${pad(when.getMinutes())}`;
}
