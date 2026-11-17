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

REPO_SUFFIXES=(api worker frontend)
REPO_REGEX='^(.*\/)?talent-scope-(api|worker|frontend)$'
TAG_REGEX='^rollback-.+'

usage() {
  cat <<'EOF'
TalentScope rollback image cleanup

Usage:
  ./scripts/cleanup-rollback-images.sh [--keep N] [--apply]

Options:
  --keep N   Keep the N newest rollback-* tags per repository (default: 2),
             in addition to current production / container-referenced images.
  --apply    Delete candidate tags after re-verification. Default is dry-run.
  -h, --help Show this help

Safety:
  - Only talent-scope api/worker/frontend rollback-* tags are candidates.
  - Protects running and stopped container image IDs, current ops images,
    the newest --keep rollback tags per repo, and shared image IDs.
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

# Temporary working files (cleaned on exit).
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/ts-rollback-clean.XXXXXX")"
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

IMAGES_RAW="${WORKDIR}/images.tsv"
CONTAINERS_RAW="${WORKDIR}/containers.tsv"
PROTECTED_IDS="${WORKDIR}/protected_ids.txt"
KEEP_TAGS="${WORKDIR}/keep_tags.txt"
CANDIDATE_TAGS="${WORKDIR}/candidate_tags.txt"
CANDIDATE_IDS="${WORKDIR}/candidate_ids.txt"
PLAN="${WORKDIR}/plan.tsv"

: >"$PROTECTED_IDS"
: >"$KEEP_TAGS"
: >"$CANDIDATE_TAGS"
: >"$CANDIDATE_IDS"
: >"$PLAN"

is_target_repo() {
  [[ "$1" =~ $REPO_REGEX ]]
}

is_rollback_tag() {
  [[ "$1" =~ $TAG_REGEX ]]
}

normalize_id() {
  # Prefer short 12-char id when docker returns sha256:...
  local id="$1"
  id="${id#sha256:}"
  printf '%s\n' "${id:0:12}"
}

collect_images() {
  # repo\ttag\tid\tcreated_epoch\tsize_bytes
  "$DOCKER_BIN" images --no-trunc --format \
    '{{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.CreatedAt}}\t{{.Size}}' \
    >"$IMAGES_RAW.raw" || die "docker images failed"

  : >"$IMAGES_RAW"
  local repo tag id created size epoch
  while IFS=$'\t' read -r repo tag id created size; do
    [[ -n "${repo:-}" ]] || continue
    [[ "$repo" == "<none>" ]] && continue
    [[ "$tag" == "<none>" ]] && continue
    is_target_repo "$repo" || continue
    is_rollback_tag "$tag" || continue
    id="$(normalize_id "$id")"
    # CreatedAt looks like "2026-11-17 03:17:00 +0000 UTC"; strip trailing zone name.
    created_norm="${created% UTC}"
    created_norm="${created_norm% utc}"
    if epoch="$(date -u -d "$created_norm" +%s 2>/dev/null)"; then
      :
    elif epoch="$(date -u -j -f '%Y-%m-%d %H:%M:%S %z' "$created_norm" +%s 2>/dev/null)"; then
      :
    else
      epoch=0
    fi
    # Size field is human; keep raw for display only. Disk reclaim uses inspect later.
    printf '%s\t%s\t%s\t%s\t%s\n' "$repo" "$tag" "$id" "$epoch" "$size" >>"$IMAGES_RAW"
  done <"$IMAGES_RAW.raw"
}

collect_container_refs() {
  # name\timage\tid\tstatus
  "$DOCKER_BIN" ps -a --no-trunc --format \
    '{{.Names}}\t{{.Image}}\t{{.ImageID}}\t{{.Status}}' \
    >"$CONTAINERS_RAW" || die "docker ps failed"

  local name image image_id status short
  while IFS=$'\t' read -r name image image_id status; do
    [[ -n "${image_id:-}" ]] || continue
    short="$(normalize_id "$image_id")"
    printf '%s\n' "$short" >>"$PROTECTED_IDS"

    # Current ops: containers whose image repo matches our three services.
    local repo_guess="${image%%:*}"
    if is_target_repo "$repo_guess"; then
      printf '%s\n' "$short" >>"$PROTECTED_IDS"
      # Also protect the exact repo:tag if present.
      if [[ "$image" == *:* ]]; then
        printf '%s\n' "$image" >>"$KEEP_TAGS"
      fi
    fi
  done <"$CONTAINERS_RAW"

  sort -u "$PROTECTED_IDS" -o "$PROTECTED_IDS"
  sort -u "$KEEP_TAGS" -o "$KEEP_TAGS"
}

select_keep_and_candidates() {
  local repo
  for suffix in "${REPO_SUFFIXES[@]}"; do
    repo="talent-scope-${suffix}"
    # Match exact or registry-prefixed repository ending with talent-scope-<svc>
    awk -F '\t' -v suffix="$suffix" '
      BEGIN { re = "^(.*\\/)?talent-scope-" suffix "$" }
      $1 ~ re { print }
    ' "$IMAGES_RAW" | sort -t $'\t' -k4,4nr -k2,2 >"${WORKDIR}/repo_${suffix}.tsv"

    local kept=0
    while IFS=$'\t' read -r r tag id epoch size; do
      [[ -n "${r:-}" ]] || continue
      local ref="${r}:${tag}"
      if grep -qxF "$id" "$PROTECTED_IDS"; then
        printf '%s\n' "$ref" >>"$KEEP_TAGS"
        printf 'protect\t%s\t%s\tcontainer-or-shared-id\t%s\t%s\n' \
          "$ref" "$id" "$epoch" "$size" >>"$PLAN"
        continue
      fi
      if [[ "$kept" -lt "$KEEP_PREVIOUS" ]]; then
        printf '%s\n' "$ref" >>"$KEEP_TAGS"
        printf 'protect\t%s\t%s\tkeep-previous-%s\t%s\t%s\n' \
          "$ref" "$id" "$((kept + 1))" "$epoch" "$size" >>"$PLAN"
        kept=$((kept + 1))
        continue
      fi
      printf '%s\n' "$ref" >>"$CANDIDATE_TAGS"
      printf '%s\n' "$id" >>"$CANDIDATE_IDS"
      printf 'candidate\t%s\t%s\tolder-than-keep\t%s\t%s\n' \
        "$ref" "$id" "$epoch" "$size" >>"$PLAN"
    done <"${WORKDIR}/repo_${suffix}.tsv"
  done

  sort -u "$KEEP_TAGS" -o "$KEEP_TAGS"
  sort -u "$CANDIDATE_TAGS" -o "$CANDIDATE_TAGS"
  sort -u "$CANDIDATE_IDS" -o "$CANDIDATE_IDS"
}

# Count remaining tags (any repo) pointing at an image id after planned deletions.
remaining_tags_for_id() {
  local target_id="$1"
  shift
  local -a delete_refs=("$@")
  local count=0
  local repo tag id
  # Re-query live images for accurate share detection.
  while IFS=$'\t' read -r repo tag id _; do
    [[ -n "${repo:-}" ]] || continue
    [[ "$repo" == "<none>" && "$tag" == "<none>" ]] && continue
    id="$(normalize_id "$id")"
    [[ "$id" == "$target_id" ]] || continue
    local ref="${repo}:${tag}"
    local skip=0
    local d
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
  echo "Keep previous rollback tags per repo: ${KEEP_PREVIOUS}"
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
  # Rebuild protection from live docker state and ensure every candidate is still
  # a deletable rollback tag that is not newly protected.
  local ref repo tag
  local old_candidates
  old_candidates="$(cat "$CANDIDATE_TAGS")"

  : >"$PROTECTED_IDS"
  : >"$KEEP_TAGS"
  : >"$CANDIDATE_TAGS"
  : >"$CANDIDATE_IDS"
  : >"$PLAN"
  collect_images
  collect_container_refs
  select_keep_and_candidates

  while IFS= read -r ref; do
    [[ -n "$ref" ]] || continue
    if ! grep -qxF "$ref" "$CANDIDATE_TAGS"; then
      die "re-verification failed: candidate no longer deletable: $ref"
    fi
    if grep -qxF "$ref" "$KEEP_TAGS"; then
      die "re-verification failed: candidate is now protected: $ref"
    fi
    repo="${ref%:*}"
    tag="${ref##*:}"
    is_target_repo "$repo" || die "re-verification failed: out-of-scope repo: $ref"
    is_rollback_tag "$tag" || die "re-verification failed: non-rollback tag: $ref"
    if ! "$DOCKER_BIN" image inspect "$ref" >/dev/null 2>&1; then
      die "re-verification failed: missing image tag: $ref"
    fi
  done <<<"$old_candidates"
}

apply_deletions() {
  local ref
  local -a deleted_refs=()
  local -a deleted_ids=()
  local failed=0

  mapfile -t candidates <"$CANDIDATE_TAGS"
  if [[ "${#candidates[@]}" -eq 0 || -z "${candidates[0]:-}" ]]; then
    echo "Nothing to delete."
    return 0
  fi

  echo "-- Applying tag deletions (no --force, no prune, no volumes) --"
  for ref in "${candidates[@]}"; do
    [[ -n "$ref" ]] || continue
    local id
    id="$("$DOCKER_BIN" image inspect --format '{{.Id}}' "$ref" 2>/dev/null || true)"
    id="$(normalize_id "${id:-}")"
    if [[ -z "$id" ]]; then
      echo "ABORT: cannot inspect $ref"
      failed=1
      break
    fi
    if grep -qxF "$id" "$PROTECTED_IDS"; then
      echo "ABORT: $ref resolves to protected id $id"
      failed=1
      break
    fi
    # Explicitly refuse force / prune style flags — plain rmi only.
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
  local i
  for i in "${!deleted_refs[@]}"; do
    local dref="${deleted_refs[$i]}"
    local did="${deleted_ids[$i]}"
    local remaining
    remaining="$(remaining_tags_for_id "$did" "${deleted_refs[@]}")"
    if [[ "$remaining" -gt 0 ]]; then
      echo "tag_deleted_not_reclaimed: $dref id=$did remaining_tags=$remaining (shared image retained)"
    else
      # Image may still exist as dangling <none> if containers or cache hold it;
      # we never prune. Report based on inspect.
      if "$DOCKER_BIN" image inspect "$did" >/dev/null 2>&1; then
        echo "tag_deleted_image_still_present: $dref id=$did (not pruned; no force reclaim)"
      else
        echo "tag_deleted_and_reclaimed: $dref id=$did"
      fi
    fi
  done

  [[ "$failed" -eq 0 ]] || die "deletion aborted after partial failure; inspect report above"
}

# --- main ---
collect_images
collect_container_refs
select_keep_and_candidates

if [[ "$APPLY" -eq 0 ]]; then
  print_report "dry-run"
  echo
  echo "Dry-run only. Re-run with --apply to delete candidate tags."
  exit 0
fi

echo "Re-verifying candidates before --apply..."
reverify_or_die
print_report "apply"
echo
apply_deletions
echo
echo "Done."
