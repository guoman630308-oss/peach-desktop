/** 作品身份忽略号码中的分隔符和字母大小写。 */
export const workCodeKey = (value?: string | null): string => (value || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
export const sameWorkCode = (left?: string | null, right?: string | null): boolean => Boolean(workCodeKey(left)) && workCodeKey(left) === workCodeKey(right);
