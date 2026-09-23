/** Display-only profile freshness derived from `profile_updated_at`. */

export type ProfileFreshness = 'CURRENT' | 'STALE' | 'UNKNOWN'

export const PROFILE_FRESHNESS_LABELS: Record<ProfileFreshness, string> = {
  CURRENT: '최신',
  STALE: '오래됨',
  UNKNOWN: '미확인',
}

const MS_PER_DAY = 24 * 60 * 60 * 1000
const FRESHNESS_CALENDAR_DAYS = 365

/** Local calendar date as UTC-midnight ms — ignores time-of-day and DST hour shifts. */
function localCalendarDayUtcMs(d: Date): number {
  return Date.UTC(d.getFullYear(), d.getMonth(), d.getDate())
}

/**
 * CURRENT: updated on or after (today − 365 calendar days), including exactly 365 days ago.
 * STALE: updated before that calendar day.
 * UNKNOWN: missing or unparseable timestamp.
 * Future timestamps count as CURRENT.
 */
export function getProfileFreshness(
  profileUpdatedAt: string | null | undefined,
  now: Date = new Date(),
): ProfileFreshness {
  if (profileUpdatedAt == null || profileUpdatedAt === '') {
    return 'UNKNOWN'
  }
  const updated = new Date(profileUpdatedAt)
  if (Number.isNaN(updated.getTime())) {
    return 'UNKNOWN'
  }
  const dayDiff =
    (localCalendarDayUtcMs(now) - localCalendarDayUtcMs(updated)) / MS_PER_DAY
  if (dayDiff <= FRESHNESS_CALENDAR_DAYS) {
    return 'CURRENT'
  }
  return 'STALE'
}

export function profileFreshnessTagColor(
  freshness: ProfileFreshness,
): 'success' | 'warning' | 'default' {
  if (freshness === 'CURRENT') return 'success'
  if (freshness === 'STALE') return 'warning'
  return 'default'
}
