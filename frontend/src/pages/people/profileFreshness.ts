/** Display-only profile freshness derived from `profile_updated_at`. */

export type ProfileFreshness = 'CURRENT' | 'STALE' | 'UNKNOWN'

export const PROFILE_FRESHNESS_LABELS: Record<ProfileFreshness, string> = {
  CURRENT: '최신',
  STALE: '오래됨',
  UNKNOWN: '미확인',
}

const MS_PER_DAY = 24 * 60 * 60 * 1000
const FRESHNESS_WINDOW_MS = 365 * MS_PER_DAY

/**
 * CURRENT: updated within the last 365 days (inclusive of exactly 365d).
 * STALE: older than 365 days.
 * UNKNOWN: missing or unparseable timestamp.
 */
export function getProfileFreshness(
  profileUpdatedAt: string | null | undefined,
  now: Date = new Date(),
): ProfileFreshness {
  if (profileUpdatedAt == null || profileUpdatedAt === '') {
    return 'UNKNOWN'
  }
  const updated = new Date(profileUpdatedAt)
  const updatedMs = updated.getTime()
  if (Number.isNaN(updatedMs)) {
    return 'UNKNOWN'
  }
  const ageMs = now.getTime() - updatedMs
  if (ageMs <= FRESHNESS_WINDOW_MS) {
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
