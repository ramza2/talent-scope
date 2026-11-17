/**
 * Confirm-policy helpers for AnalysisDetailPage (pure, unit-testable).
 */

export type ReviewDecisionCounts = {
  accepted: number
  modified: number
  merged: number
  rejected: number
  pendingActionable: number
  same: number
}

export type DiffDecisionLike = {
  review_status?: string | null
  change_type?: string | null
}

/** Source-First family: v30/v31/v32 and future profile-source-first-* prompts. */
export function isSourceFirstPromptVersion(
  promptVersion: string | null | undefined,
): boolean {
  const version = (promptVersion ?? '').trim()
  return version.startsWith('profile-source-first-')
}

/**
 * Frontend confirm request flag.
 * Source-First → false (fail whole confirm on store errors).
 * Empty/unknown/legacy (e.g. profile-extract-v25) → true (prior non-v30 default).
 */
export function shouldSkipInvalidValuesOnConfirm(
  promptVersion: string | null | undefined,
): boolean {
  return !isSourceFirstPromptVersion(promptVersion)
}

export function isPendingActionableDiff(diff: DiffDecisionLike): boolean {
  return (
    (diff.review_status || '').toUpperCase() === 'PENDING' &&
    (diff.change_type || '').toUpperCase() !== 'SAME' &&
    ['NEW', 'UPDATE', 'CONFLICT', 'REVIEW'].includes(
      (diff.change_type || '').toUpperCase(),
    )
  )
}

/** Decision summary for the confirm modal (not claimed equal to apply success). */
export function summarizeReviewDecisions(
  diffs: DiffDecisionLike[] | null | undefined,
): ReviewDecisionCounts {
  const counts: ReviewDecisionCounts = {
    accepted: 0,
    modified: 0,
    merged: 0,
    rejected: 0,
    pendingActionable: 0,
    same: 0,
  }
  for (const diff of diffs ?? []) {
    const status = (diff.review_status || '').toUpperCase()
    const change = (diff.change_type || '').toUpperCase()
    if (change === 'SAME') {
      counts.same += 1
      continue
    }
    if (isPendingActionableDiff(diff)) {
      counts.pendingActionable += 1
      continue
    }
    if (status === 'ACCEPTED') counts.accepted += 1
    else if (status === 'MODIFIED') counts.modified += 1
    else if (status === 'MERGED') counts.merged += 1
    else if (status === 'REJECTED') counts.rejected += 1
  }
  return counts
}
