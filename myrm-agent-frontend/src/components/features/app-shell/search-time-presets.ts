/**
 * [INPUT] 无外部依赖（仅标准 Date）
 * [OUTPUT] TimePreset: Cmd+K 时间筛选预设; computePresetRange: 预设 → 服务端 since / until（ISO 8601 UTC 字符串）
 * [POS] SearchDialog 的时间窗计算，纯函数、无 React 依赖。
 */

export type TimePreset = 'today' | 'week' | 'month' | 'quarter' | null;

/**
 * The same day of the month `months` calendar months earlier, or that month's last day when it is shorter
 * (Mar 31 minus one month is Feb 28 or 29). `Date#setMonth` alone would roll Mar 31 over to Mar 3.
 */
function subtractCalendarMonths(from: Date, months: number): Date {
  const lastDayOfTargetMonth = new Date(from.getFullYear(), from.getMonth() - months + 1, 0).getDate();
  return new Date(
    from.getFullYear(),
    from.getMonth() - months,
    Math.min(from.getDate(), lastDayOfTargetMonth),
    from.getHours(),
    from.getMinutes(),
    from.getSeconds(),
    from.getMilliseconds(),
  );
}

export function computePresetRange(preset: TimePreset, now: Date = new Date()): { since?: string; until?: string } {
  if (!preset) {
    return {};
  }
  const until = now.toISOString();
  let start = new Date(now);
  switch (preset) {
    case 'today':
      start.setHours(0, 0, 0, 0);
      break;
    case 'week':
      start.setDate(start.getDate() - 7);
      break;
    case 'month':
      start = subtractCalendarMonths(now, 1);
      break;
    case 'quarter':
      start = subtractCalendarMonths(now, 3);
      break;
  }
  return { since: start.toISOString(), until };
}
