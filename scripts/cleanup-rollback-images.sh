#!/usr/bin/env bash
# TalentScope rollback-image retention cleaner.
#
# Default: dry-run (report only). Deletes only with explicit --apply.
# Scope: talent-scope-{api,worker,frontend} images tagged rollback-*.
# Never runs prune/force/volume deletion.
set -Eeuo pipefail

KEEP_PREVIOUS=2
APPLY=0
DOCKER_BIN="${DOCKER_BIN:-docker}"
LOOKUP_FAILED=0
LOOKUP_FAIL_REASON=""

REPO_SUFFIXES=(api worker frontend)
REPO_REGEX='^(.*\/)?talent-scope-(api|worker|frontend)$'
TAG_REGEX='^rollback-.+'
FULL_ID_REGEX='^[a-f0-9]{64}$'

usage() {
  cat <<'EOF'
TalentScope rollback image cleanup

Usage:
  ./scripts/cleanup-rollback-images.sh [--keep N] [--apply]

Options:
  --keep N   Keep N distinct past rollback image IDs per repository
             (default: 2), in addition to current/container-referenced IDs.
  --apply    Delete candidate tags after re-verification. Default is dry-run.
  -h, --help Show this help

Safety:
  - Only talent-scope api/worker/frontend rollback-* tags are candidates.
  - Protects running and stopped container image IDs (via ps -aq + inspect),
    current ops images, --keep distinct past image IDs, and shared IDs.
  - Uses full sha256 image IDs. Uncertain timestamps or lookup failures
    forbid deletion (fail closed).
  - Never uses docker image prune, rmi --force, or volume delete.
  - Tag removal and actual disk reclaim are reported separately.
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

mark_lookup_failed() {
  LOOKUP_FAILED=1
  LOOKUP_FAIL_REASON="$1"
  printf 'WARNING: %s (deletion disabled)\n' "$1" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep)
      [[ $# -ge 2 ]] || die "--keep requires an integer"
      KEEP_PREVIOUS="$2"
      shift 2
      ;;
    --apply)
      APPLY=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

[[ "$KEEP_PREVIOUS" =~ ^[0-9]+$ ]] || die "--keep must be a non-negative integer"
require_cmd "$DOCKER_BIN"
require_cmd awk
require_cmd sort
require_cmd grep

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/ts-rollback-clean.XXXXXX")"
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

IMAGES_RAW="${WORKDIR}/images.tsv"
PROTECTED_IDS="${WORKDIR}/protected_ids.txt"
UNCERTAIN_IDS="${WORKDIR}/uncertain_ids.txt"
KEEP_TAGS="${WORKDIR}/keep_tags.txt"
CANDIDATE_TAGS="${WORKDIR}/candidate_tags.txt"
CANDIDATE_IDS="${WORKDIR}/candidate_ids.txt"
PLAN="${WORKDIR}/plan.tsv"
SNAPSHOT_CANDIDATES="${WORKDIR}/snapshot_candidates.txt"
SNAPSHOT_PROTECTED="${WORKDIR}/snapshot_protected.txt"
# First-selection tag → full sha256 map (never rewritten after initial save).
INITIAL_CANDIDATE_MAP="${WORKDIR}/initial_candidate_map.tsv"

: >"$PROTECTED_IDS"
: >"$UNCERTAIN_IDS"
: >"$KEEP_TAGS"
: >"$CANDIDATE_TAGS"
: >"$CANDIDATE_IDS"
: >"$PLAN"
: >"$INITIAL_CANDIDATE_MAP"

is_target_repo() {
  [[ "$1" =~ $REPO_REGEX ]]
}

is_rollback_tag() {
  [[ "$1" =~ $TAG_REGEX ]]
}

# Canonical full image id: sha256: + 64 lowercase hex. Returns non-zero if short/unknown.
canonical_id() {
  local raw="${1:-}"
  raw="${raw#sha256:}"
  raw="$(printf '%s' "$raw" | tr 'A-F' 'a-f')"
  if [[ "$raw" =~ $FULL_ID_REGEX ]]; then
    printf 'sha256:%s\n' "$raw"
    return 0
  fi
  return 1
}

ids_equal() {
  local a b
  a="$(canonical_id "$1")" || return 1
  b="$(canonical_id "$2")" || return 1
  [[ "$a" == "$b" ]]
}

parse_created_epoch() {
  # Accept Docker CreatedAt / inspect .Created. Print epoch or return non-zero.
  local created="${1:-}"
  local norm epoch
  [[ -n "$created" ]] || return 1
  norm="${created% UTC}"
  norm="${norm% utc}"
  # inspect often returns 2026-11-17T10:00:00.123456789Z
  if [[ "$norm" == *T* ]]; then
    norm="${norm%%.*}Z"
    norm="${norm/Z/}"
    if epoch="$(date -u -d "${norm}Z" +%s 2>/dev/null)"; then
      printf '%s\n' "$epoch"
      return 0
    fi
    if epoch="$(date -u -j -f '%Y-%m-%dT%H:%M:%SZ' "${norm}Z" +%s 2>/dev/null)"; then
      printf '%s\n' "$epoch"
      return 0
    fi
  fi
  if epoch="$(date -u -d "$norm" +%s 2>/dev/null)"; then
    printf '%s\n' "$epoch"
    return 0
  fi
  if epoch="$(date -u -j -f '%Y-%m-%d %H:%M:%S %z' "$norm" +%s 2>/dev/null)"; then
    printf '%s\n' "$epoch"
    return 0
  fi
  return 1
}

image_created_epoch() {
  local id="$1"
  local created
  created="$("$DOCKER_BIN" image inspect --format '{{.Created}}' "$id" 2>/dev/null)" \
    || return 1
  parse_created_epoch "$created"
}

collect_images() {
  # Output rows: repo\ttag\tfull_id\tlist_epoch_or_empty\tsize
  if ! "$DOCKER_BIN" images --no-trunc --format \
    '{{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.CreatedAt}}\t{{.Size}}' \
    >"$IMAGES_RAW.raw"; then
    mark_lookup_failed "docker images failed"
    : >"$IMAGES_RAW"
    return 0
  fi

  : >"$IMAGES_RAW"
  local repo tag id created size full list_epoch inspect_epoch
  while IFS=$'\t' read -r repo tag id created size; do
    [[ -n "${repo:-}" ]] || continue
    [[ "$repo" == "<none>" ]] && continue
    [[ "$tag" == "<none>" ]] && continue
    is_target_repo "$repo" || continue
    is_rollback_tag "$tag" || continue
    if ! full="$(canonical_id "$id")"; then
      mark_lookup_failed "non-full image id for ${repo}:${tag} ($id)"
      continue
    fi
    list_epoch=""
    if list_epoch="$(parse_created_epoch "$created")"; then
      :
    else
      list_epoch=""
    fi
    # Prefer inspect .Created; if list and inspect disagree, mark uncertain.
    if inspect_epoch="$(image_created_epoch "$full")"; then
      if [[ -n "$list_epoch" && "$list_epoch" != "$inspect_epoch" ]]; then
        printf '%s\n' "$full" >>"$UNCERTAIN_IDS"
        mark_lookup_failed \
          "image vs tag/list created time mismatch for $full (${repo}:${tag})"
      fi
      printf '%s\t%s\t%s\t%s\t%s\n' \
        "$repo" "$tag" "$full" "$inspect_epoch" "$size" >>"$IMAGES_RAW"
    elif [[ -n "$list_epoch" ]]; then
      # Inspect failed → uncertain; keep row for reporting but protect.
      printf '%s\n' "$full" >>"$UNCERTAIN_IDS"
      mark_lookup_failed "image inspect Created failed for $full (${repo}:${tag})"
      printf '%s\t%s\t%s\t%s\t%s\n' \
        "$repo" "$tag" "$full" "$list_epoch" "$size" >>"$IMAGES_RAW"
    else
      printf '%s\n' "$full" >>"$UNCERTAIN_IDS"
      mark_lookup_failed "cannot determine created time for $full (${repo}:${tag})"
      printf '%s\t%s\t%s\t%s\t%s\n' \
        "$repo" "$tag" "$full" "0" "$size" >>"$IMAGES_RAW"
    fi
  done <"$IMAGES_RAW.raw"

  if [[ -s "$UNCERTAIN_IDS" ]]; then
    sort -u "$UNCERTAIN_IDS" -o "$UNCERTAIN_IDS"
  fi
}

collect_container_refs() {
  # Collect image IDs for every running and stopped container via ps -aq + inspect.
  # Never uses docker ps --format .ImageID (unsupported on some CLI versions).
  local cid_file="${WORKDIR}/container_cids.txt"
  local cid img full repo_guess image_name

  if ! "$DOCKER_BIN" ps -aq >"$cid_file"; then
    mark_lookup_failed "docker ps -aq failed"
    return 0
  fi

  while IFS= read -r cid; do
    [[ -n "${cid:-}" ]] || continue
    if ! img="$("$DOCKER_BIN" inspect --format '{{.Image}}' "$cid" 2>/dev/null)"; then
      mark_lookup_failed "docker inspect Image failed for container $cid"
      continue
    fi
    if ! full="$(canonical_id "$img")"; then
      mark_lookup_failed "container $cid returned non-full image id ($img)"
      continue
    fi
    printf '%s\n' "$full" >>"$PROTECTED_IDS"

    image_name="$("$DOCKER_BIN" inspect --format '{{.Config.Image}}' "$cid" 2>/dev/null || true)"
    repo_guess="${image_name%%:*}"
    if [[ -n "$image_name" ]] && is_target_repo "$repo_guess"; then
      printf '%s\n' "$full" >>"$PROTECTED_IDS"
      if [[ "$image_name" == *:* && "$image_name" != *sha256:* ]]; then
        printf '%s\n' "$image_name" >>"$KEEP_TAGS"
      fi
    fi
  done <"$cid_file"

  sort -u "$PROTECTED_IDS" -o "$PROTECTED_IDS"
  if [[ -s "$KEEP_TAGS" ]]; then
    sort -u "$KEEP_TAGS" -o "$KEEP_TAGS"
  fi
}

id_is_protected() {
  grep -qxF "$1" "$PROTECTED_IDS"
}

id_is_uncertain() {
  [[ -s "$UNCERTAIN_IDS" ]] && grep -qxF "$1" "$UNCERTAIN_IDS"
}

select_keep_and_candidates() {
  local suffix
  for suffix in "${REPO_SUFFIXES[@]}"; do
    awk -F '\t' -v suffix="$suffix" '
      BEGIN { re = "^(.*\\/)?talent-scope-" suffix "$" }
      $1 ~ re { print }
    ' "$IMAGES_RAW" >"${WORKDIR}/repo_${suffix}_tags.tsv"

    # Distinct image IDs with max epoch among their tags (for ordering).
    : >"${WORKDIR}/repo_${suffix}_ids.tsv"
    if [[ -s "${WORKDIR}/repo_${suffix}_tags.tsv" ]]; then
      awk -F '\t' '
        {
          id=$3; epoch=$4+0
          if (!(id in max) || epoch > max[id]) max[id]=epoch
          # Track if any row for id has epoch 0 / missing certainty flag via file later
          count[id]++
        }
        END {
          for (id in max) printf "%s\t%s\n", max[id], id
        }
      ' "${WORKDIR}/repo_${suffix}_tags.tsv" \
        | sort -t $'\t' -k1,1nr -k2,2 >"${WORKDIR}/repo_${suffix}_ids.tsv"
    fi

    local kept_ids=0
    local epoch id
    : >"${WORKDIR}/repo_${suffix}_keep_ids.txt"
    while IFS=$'\t' read -r epoch id; do
      [[ -n "${id:-}" ]] || continue
      if id_is_protected "$id" || id_is_uncertain "$id" || [[ "$epoch" -eq 0 ]]; then
        printf '%s\n' "$id" >>"${WORKDIR}/repo_${suffix}_keep_ids.txt"
        if id_is_uncertain "$id" || [[ "$epoch" -eq 0 ]]; then
          printf '%s\n' "$id" >>"$PROTECTED_IDS"
        fi
        continue
      fi
      if [[ "$kept_ids" -lt "$KEEP_PREVIOUS" ]]; then
        printf '%s\n' "$id" >>"${WORKDIR}/repo_${suffix}_keep_ids.txt"
        printf '%s\n' "$id" >>"$PROTECTED_IDS"
        kept_ids=$((kept_ids + 1))
        continue
      fi
      # Past ID beyond keep window → candidate (unless later protected globally).
      printf '%s\n' "$id" >>"${WORKDIR}/repo_${suffix}_candidate_ids.txt"
    done <"${WORKDIR}/repo_${suffix}_ids.tsv"

    sort -u "${WORKDIR}/repo_${suffix}_keep_ids.txt" -o "${WORKDIR}/repo_${suffix}_keep_ids.txt" 2>/dev/null || true
    : >>"${WORKDIR}/repo_${suffix}_candidate_ids.txt"
    sort -u "${WORKDIR}/repo_${suffix}_candidate_ids.txt" -o "${WORKDIR}/repo_${suffix}_candidate_ids.txt" 2>/dev/null || true

    # Map tags → protect or candidate by ID membership.
    local r tag full size reason
    while IFS=$'\t' read -r r tag full epoch size; do
      [[ -n "${r:-}" ]] || continue
      local ref="${r}:${tag}"
      if id_is_uncertain "$full" || [[ "$epoch" -eq 0 ]]; then
        printf '%s\n' "$ref" >>"$KEEP_TAGS"
        printf 'protect\t%s\t%s\tuncertain-created-time\t%s\t%s\n' \
          "$ref" "$full" "$epoch" "$size" >>"$PLAN"
        continue
      fi
      if grep -qxF "$full" "${WORKDIR}/repo_${suffix}_keep_ids.txt"; then
        printf '%s\n' "$ref" >>"$KEEP_TAGS"
        if id_is_protected "$full" && ! grep -qxF "$full" "${WORKDIR}/repo_${suffix}_candidate_ids.txt" 2>/dev/null; then
          # Distinguish container vs keep-previous in plan when possible.
          reason="protected-or-keep-id"
          printf 'protect\t%s\t%s\t%s\t%s\t%s\n' \
            "$ref" "$full" "$reason" "$epoch" "$size" >>"$PLAN"
        else
          printf 'protect\t%s\t%s\tkeep-previous-id\t%s\t%s\n' \
            "$ref" "$full" "$epoch" "$size" >>"$PLAN"
        fi
        continue
      fi
      # Candidate only when lookup is healthy and ID is beyond keep window.
      if [[ "$LOOKUP_FAILED" -ne 0 ]]; then
        printf '%s\n' "$ref" >>"$KEEP_TAGS"
        printf 'protect\t%s\t%s\tlookup-failed-no-delete\t%s\t%s\n' \
          "$ref" "$full" "$epoch" "$size" >>"$PLAN"
        continue
      fi
      if id_is_protected "$full"; then
        printf '%s\n' "$ref" >>"$KEEP_TAGS"
        printf 'protect\t%s\t%s\tcontainer-referenced\t%s\t%s\n' \
          "$ref" "$full" "$epoch" "$size" >>"$PLAN"
        continue
      fi
      printf '%s\n' "$ref" >>"$CANDIDATE_TAGS"
      printf '%s\n' "$full" >>"$CANDIDATE_IDS"
      printf 'candidate\t%s\t%s\tolder-than-keep-ids\t%s\t%s\n' \
        "$ref" "$full" "$epoch" "$size" >>"$PLAN"
    done <"${WORKDIR}/repo_${suffix}_tags.tsv"
  done

  sort -u "$PROTECTED_IDS" -o "$PROTECTED_IDS"
  sort -u "$KEEP_TAGS" -o "$KEEP_TAGS" 2>/dev/null || :
  sort -u "$CANDIDATE_TAGS" -o "$CANDIDATE_TAGS" 2>/dev/null || :
  sort -u "$CANDIDATE_IDS" -o "$CANDIDATE_IDS" 2>/dev/null || :

  # Fail closed: never allow candidates when lookup failed.
  if [[ "$LOOKUP_FAILED" -ne 0 && -s "$CANDIDATE_TAGS" ]]; then
    cat "$CANDIDATE_TAGS" >>"$KEEP_TAGS"
    : >"$CANDIDATE_TAGS"
    : >"$CANDIDATE_IDS"
    sort -u "$KEEP_TAGS" -o "$KEEP_TAGS"
  fi
}

save_initial_candidate_map() {
  # Persist first-pass candidate tag → full sha256 mapping only once.
  if [[ -s "$INITIAL_CANDIDATE_MAP" ]]; then
    return 0
  fi
  : >"$INITIAL_CANDIDATE_MAP"
  if [[ ! -s "$PLAN" ]]; then
    return 0
  fi
  awk -F '\t' '$1 == "candidate" && $2 != "" && $3 != "" { print $2 "\t" $3 }' \
    "$PLAN" | sort -u >"$INITIAL_CANDIDATE_MAP"
}

initial_id_for_ref() {
  local ref="$1"
  local id
  id="$(awk -F '\t' -v ref="$ref" '$1 == ref { print $2; found=1; exit } END { exit !found }' \
    "$INITIAL_CANDIDATE_MAP")" || return 1
  canonical_id "$id"
}

assert_matches_initial_id() {
  local phase="$1"
  local ref="$2"
  local current="$3"
  local expected
  expected="$(initial_id_for_ref "$ref")" \
    || die "${phase}: missing initial tag→id mapping for $ref"
  current="$(canonical_id "$current")" \
    || die "${phase}: non-full current id for $ref"
  if ! ids_equal "$current" "$expected"; then
    die "${phase}: id changed for $ref (initial $expected now $current)"
  fi
}

snapshot_state() {
  cp "$CANDIDATE_TAGS" "$SNAPSHOT_CANDIDATES"
  cp "$PROTECTED_IDS" "$SNAPSHOT_PROTECTED"
  : >>"$SNAPSHOT_CANDIDATES"
  : >>"$SNAPSHOT_PROTECTED"
  sort -u "$SNAPSHOT_CANDIDATES" -o "$SNAPSHOT_CANDIDATES"
  sort -u "$SNAPSHOT_PROTECTED" -o "$SNAPSHOT_PROTECTED"
}

state_unchanged_or_die() {
  local label="$1"
  local now_cand="${WORKDIR}/now_candidates.txt"
  local now_prot="${WORKDIR}/now_protected.txt"
  cp "$CANDIDATE_TAGS" "$now_cand"
  cp "$PROTECTED_IDS" "$now_prot"
  sort -u "$now_cand" -o "$now_cand"
  sort -u "$now_prot" -o "$now_prot"
  if ! cmp -s "$SNAPSHOT_CANDIDATES" "$now_cand"; then
    die "re-verification failed (${label}): candidate set changed"
  fi
  if ! cmp -s "$SNAPSHOT_PROTECTED" "$now_prot"; then
    die "re-verification failed (${label}): protected id set changed"
  fi
}

rebuild_plan() {
  LOOKUP_FAILED=0
  LOOKUP_FAIL_REASON=""
  : >"$PROTECTED_IDS"
  : >"$UNCERTAIN_IDS"
  : >"$KEEP_TAGS"
  : >"$CANDIDATE_TAGS"
  : >"$CANDIDATE_IDS"
  : >"$PLAN"
  for suffix in "${REPO_SUFFIXES[@]}"; do
    : >"${WORKDIR}/repo_${suffix}_candidate_ids.txt"
  done
  collect_images
  collect_container_refs
  select_keep_and_candidates
}

remaining_tags_for_id() {
  local target_id="$1"
  shift
  local -a delete_refs=("$@")
  local count=0 repo tag id full ref skip d
  target_id="$(canonical_id "$target_id")" || { printf '0\n'; return 0; }
  while IFS=$'\t' read -r repo tag id _; do
    [[ -n "${repo:-}" ]] || continue
    [[ "$repo" == "<none>" && "$tag" == "<none>" ]] && continue
    full="$(canonical_id "$id")" || continue
    [[ "$full" == "$target_id" ]] || continue
    ref="${repo}:${tag}"
    skip=0
    for d in "${delete_refs[@]:-}"; do
      if [[ "$d" == "$ref" ]]; then
        skip=1
        break
      fi
    done
    [[ "$skip" -eq 1 ]] && continue
    count=$((count + 1))
  done < <("$DOCKER_BIN" images --no-trunc --format '{{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.CreatedAt}}')
  printf '%s\n' "$count"
}

print_report() {
  local mode="$1"
  echo "=== TalentScope rollback image cleanup (${mode}) ==="
  echo "Keep previous distinct rollback image IDs per repo: ${KEEP_PREVIOUS}"
  if [[ "$LOOKUP_FAILED" -ne 0 ]]; then
    echo "Lookup/safety failure: ${LOOKUP_FAIL_REASON:-unknown} (deletion disabled)"
  fi
  echo
  echo "-- Protection / plan --"
  if [[ ! -s "$PLAN" ]]; then
    echo "(no talent-scope api/worker/frontend rollback-* tags found)"
  else
    printf '%s\n' "action  ref  id  reason  created_epoch  size"
    column -t -s $'\t' "$PLAN" 2>/dev/null || cat "$PLAN"
  fi
  echo
  echo "-- Candidate tags for deletion --"
  if [[ ! -s "$CANDIDATE_TAGS" ]]; then
    echo "(none)"
  else
    cat "$CANDIDATE_TAGS"
  fi
}

reverify_or_die() {
  local old_candidates old_protected
  old_candidates="$(cat "$CANDIDATE_TAGS")"
  old_protected="$(cat "$PROTECTED_IDS")"
  snapshot_state

  rebuild_plan
  [[ "$LOOKUP_FAILED" -eq 0 ]] \
    || die "re-verification failed: lookup failure: ${LOOKUP_FAIL_REASON}"

  # Candidate/protected membership must be identical (full sha256 lines).
  local ref
  while IFS= read -r ref; do
    [[ -n "$ref" ]] || continue
    grep -qxF "$ref" "$CANDIDATE_TAGS" \
      || die "re-verification failed: candidate no longer deletable: $ref"
    grep -qxF "$ref" "$KEEP_TAGS" \
      && die "re-verification failed: candidate is now protected: $ref"
    local repo tag
    repo="${ref%:*}"
    tag="${ref##*:}"
    is_target_repo "$repo" || die "re-verification failed: out-of-scope repo: $ref"
    is_rollback_tag "$tag" || die "re-verification failed: non-rollback tag: $ref"
    local full
    full="$("$DOCKER_BIN" image inspect --format '{{.Id}}' "$ref" 2>/dev/null)" \
      || die "re-verification failed: missing image tag: $ref"
    full="$(canonical_id "$full")" \
      || die "re-verification failed: non-full id for $ref"
    assert_matches_initial_id "re-verification failed" "$ref" "$full"
    id_is_protected "$full" \
      && die "re-verification failed: $ref id is protected: $full"
  done <<<"$old_candidates"

  # Any newly protected IDs vs snapshot → abort.
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    grep -qxF "$pid" <<<"$old_protected" \
      || die "re-verification failed: new protected id appeared: $pid"
  done <"$PROTECTED_IDS"

  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    grep -qxF "$pid" "$PROTECTED_IDS" \
      || die "re-verification failed: protected id disappeared: $pid"
  done <<<"$old_protected"

  state_unchanged_or_die "post-rebuild"
}

verify_ref_still_deletable() {
  local ref="$1"
  local full expected
  expected="$(initial_id_for_ref "$ref")" \
    || die "pre-delete aborted: missing initial tag→id mapping for $ref"

  [[ "$LOOKUP_FAILED" -eq 0 ]] || die "pre-delete aborted: lookup failure"
  grep -qxF "$ref" "$CANDIDATE_TAGS" \
    || die "pre-delete aborted: $ref no longer a candidate"
  full="$("$DOCKER_BIN" image inspect --format '{{.Id}}' "$ref" 2>/dev/null)" \
    || die "pre-delete aborted: cannot inspect $ref"
  full="$(canonical_id "$full")" \
    || die "pre-delete aborted: non-full id for $ref"
  assert_matches_initial_id "pre-delete aborted" "$ref" "$full"
  id_is_protected "$full" \
    && die "pre-delete aborted: $ref resolves to protected id $full"

  # Refresh container protection just before delete.
  local cid_file="${WORKDIR}/pre_delete_cids.txt" cid img
  "$DOCKER_BIN" ps -aq >"$cid_file" \
    || die "pre-delete aborted: docker ps -aq failed"
  while IFS= read -r cid; do
    [[ -n "$cid" ]] || continue
    img="$("$DOCKER_BIN" inspect --format '{{.Image}}' "$cid" 2>/dev/null)" \
      || die "pre-delete aborted: inspect failed for container $cid"
    img="$(canonical_id "$img")" \
      || die "pre-delete aborted: non-full container image id"
    if ids_equal "$img" "$full"; then
      die "pre-delete aborted: $ref is referenced by container $cid"
    fi
  done <"$cid_file"
}

apply_deletions() {
  local ref
  local -a deleted_refs=()
  local -a deleted_ids=()
  local failed=0

  [[ "$LOOKUP_FAILED" -eq 0 ]] || die "deletion forbidden: ${LOOKUP_FAIL_REASON}"

  mapfile -t candidates <"$CANDIDATE_TAGS"
  if [[ "${#candidates[@]}" -eq 0 || -z "${candidates[0]:-}" ]]; then
    echo "Nothing to delete."
    return 0
  fi

  echo "-- Applying tag deletions (no --force, no prune, no volumes) --"
  for ref in "${candidates[@]}"; do
    [[ -n "$ref" ]] || continue
    local id
    id="$(initial_id_for_ref "$ref")" || {
      echo "ABORT: missing initial tag→id mapping for $ref"
      failed=1
      break
    }
    if ! verify_ref_still_deletable "$ref"; then
      failed=1
      break
    fi
    # Final ID check immediately before rmi (tag may have been retargeted).
    local live
    live="$("$DOCKER_BIN" image inspect --format '{{.Id}}' "$ref" 2>/dev/null || true)"
    live="$(canonical_id "${live:-}")" || {
      echo "ABORT: cannot inspect $ref immediately before rmi"
      failed=1
      break
    }
    if ! ids_equal "$live" "$id"; then
      echo "ABORT: id changed for $ref immediately before rmi (initial $id now $live)"
      failed=1
      break
    fi
    if ! "$DOCKER_BIN" rmi "$ref"; then
      echo "ABORT: docker rmi failed for $ref (deletion stopped)"
      failed=1
      break
    fi
    deleted_refs+=("$ref")
    deleted_ids+=("$id")
    echo "deleted_tag: $ref (id=$id)"
  done

  echo
  echo "-- Disk reclaim assessment --"
  local i dref did remaining
  for i in "${!deleted_refs[@]}"; do
    dref="${deleted_refs[$i]}"
    did="${deleted_ids[$i]}"
    remaining="$(remaining_tags_for_id "$did" "${deleted_refs[@]}")"
    if [[ "$remaining" -gt 0 ]]; then
      echo "tag_deleted_not_reclaimed: $dref id=$did remaining_tags=$remaining (shared image retained)"
    elif "$DOCKER_BIN" image inspect "$did" >/dev/null 2>&1; then
      echo "tag_deleted_image_still_present: $dref id=$did (not pruned; no force reclaim)"
    else
      echo "tag_deleted_and_reclaimed: $dref id=$did"
    fi
  done

  [[ "$failed" -eq 0 ]] || die "deletion aborted after partial failure; inspect report above"
}

# --- main ---
rebuild_plan
save_initial_candidate_map
snapshot_state

if [[ "$APPLY" -eq 0 ]]; then
  print_report "dry-run"
  echo
  if [[ "$LOOKUP_FAILED" -ne 0 ]]; then
    echo "Dry-run only; deletion disabled due to lookup/safety failure."
  else
    echo "Dry-run only. Re-run with --apply to delete candidate tags."
  fi
  exit 0
fi

[[ "$LOOKUP_FAILED" -eq 0 ]] || die "refusing --apply: ${LOOKUP_FAIL_REASON}"
[[ -s "$INITIAL_CANDIDATE_MAP" || ! -s "$CANDIDATE_TAGS" ]] \
  || die "refusing --apply: initial candidate tag→id map was not recorded"

echo "Re-verifying candidates before --apply..."
reverify_or_die
print_report "apply"
echo
apply_deletions
echo
echo "Done."
