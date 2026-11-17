#!/usr/bin/env bash
# Mock-only tests for cleanup-rollback-images.sh (no real Docker mutations).
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="${ROOT_DIR}/scripts/cleanup-rollback-images.sh"
PASS=0
FAIL=0

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    PASS=$((PASS + 1))
    printf 'PASS %s\n' "$label"
  else
    FAIL=$((FAIL + 1))
    printf 'FAIL %s\n expected=%s\n actual=%s\n' "$label" "$expected" "$actual"
  fi
}

assert_contains() {
  local label="$1" needle="$2" haystack="$3"
  if [[ "$haystack" == *"$needle"* ]]; then
    PASS=$((PASS + 1))
    printf 'PASS %s\n' "$label"
  else
    FAIL=$((FAIL + 1))
    printf 'FAIL %s\n missing=%s\n in=%s\n' "$label" "$needle" "$haystack"
  fi
}

assert_not_contains() {
  local label="$1" needle="$2" haystack="$3"
  if [[ "$haystack" != *"$needle"* ]]; then
    PASS=$((PASS + 1))
    printf 'PASS %s\n' "$label"
  else
    FAIL=$((FAIL + 1))
    printf 'FAIL %s\n unexpectedly found=%s\n' "$label" "$needle"
  fi
}

make_mock_docker() {
  local bin_dir="$1"
  mkdir -p "$bin_dir"
  cat >"${bin_dir}/docker" <<'MOCK'
#!/usr/bin/env bash
set -Eeuo pipefail
STATE_DIR="${MOCK_DOCKER_STATE:?}"
cmd="${1:-}"
shift || true

case "$cmd" in
  images)
    # Support: images --no-trunc --format '...'
    if [[ "${1:-}" == "--no-trunc" ]]; then
      shift
    fi
    if [[ "${1:-}" == "--format" ]]; then
      # Print stored image lines (already formatted).
      cat "${STATE_DIR}/images.tsv"
      exit 0
    fi
    echo "unexpected docker images invocation: $*" >&2
    exit 2
    ;;
  ps)
    cat "${STATE_DIR}/containers.tsv"
    exit 0
    ;;
  image)
    sub="${1:-}"
    shift || true
    if [[ "$sub" == "inspect" ]]; then
      # docker image inspect --format '{{.Id}}' ref  OR inspect ref
      local_format=""
      if [[ "${1:-}" == "--format" ]]; then
        local_format="$2"
        shift 2
      fi
      ref="${1:-}"
      while IFS=$'\t' read -r repo tag id created size; do
        [[ "${repo}:${tag}" == "$ref" || "$id" == "$ref" || "sha256:${id}" == "$ref" ]] || continue
        if [[ "$local_format" == '{{.Id}}' ]]; then
          printf 'sha256:%s\n' "$id"
        else
          printf '%s\n' "$id"
        fi
        exit 0
      done <"${STATE_DIR}/images.tsv"
      # Also allow bare id presence file for reclaim checks.
      if [[ -f "${STATE_DIR}/ids/${ref}" || -f "${STATE_DIR}/ids/${ref#sha256:}" ]]; then
        exit 0
      fi
      exit 1
    fi
    echo "unexpected docker image invocation: $sub $*" >&2
    exit 2
    ;;
  rmi)
    ref="${1:-}"
    [[ -n "$ref" ]] || exit 2
    # Refuse force-style args if ever passed.
    for a in "$@"; do
      if [[ "$a" == "-f" || "$a" == "--force" || "$a" == "prune" ]]; then
        echo "force/prune forbidden in mock" >&2
        exit 3
      fi
    done
    tmp="$(mktemp)"
    removed=0
    removed_id=""
    while IFS=$'\t' read -r repo tag id created size; do
      if [[ "${repo}:${tag}" == "$ref" ]]; then
        removed=1
        removed_id="$id"
        printf '%s\n' "$id" >>"${STATE_DIR}/deleted_ids.txt"
        printf '%s\n' "$ref" >>"${STATE_DIR}/deleted_tags.txt"
        continue
      fi
      printf '%s\t%s\t%s\t%s\t%s\n' "$repo" "$tag" "$id" "$created" "$size" >>"$tmp"
    done <"${STATE_DIR}/images.tsv"
    mv "$tmp" "${STATE_DIR}/images.tsv"
    [[ "$removed" -eq 1 ]] || exit 1
    # If no tags remain for that id, drop the id marker (simulates reclaimable image).
    if [[ -n "$removed_id" ]] && ! awk -F '\t' -v id="$removed_id" '$3==id { found=1 } END { exit !found }' "${STATE_DIR}/images.tsv"; then
      rm -f "${STATE_DIR}/ids/${removed_id}"
    fi
    exit 0
    ;;
  *)
    echo "unexpected docker command: $cmd $*" >&2
    exit 2
    ;;
esac
MOCK
  chmod +x "${bin_dir}/docker"
}

seed_state() {
  local state="$1"
  mkdir -p "${state}/ids"
  : >"${state}/deleted_ids.txt"
  : >"${state}/deleted_tags.txt"
  # Format matches docker images --format used by the script after our parser:
  # We store CreatedAt as a parseable date string; script converts with date -d.
  # For mock simplicity, write the FINAL tsv the script expects after parsing? 
  # Actually mock returns what `docker images --format` returns; script parses CreatedAt.
  cat >"${state}/images.tsv" <<'EOF'
talent-scope-api	rollback-aaa	aaaaaaaaaaaa	2026-11-17 10:00:00 +0000 UTC	100MB
talent-scope-api	rollback-bbb	bbbbbbbbbbbb	2026-11-16 10:00:00 +0000 UTC	100MB
talent-scope-api	rollback-ccc	cccccccccccc	2026-11-15 10:00:00 +0000 UTC	100MB
talent-scope-api	rollback-ddd	dddddddddddd	2026-11-14 10:00:00 +0000 UTC	100MB
talent-scope-worker	rollback-aaa	waaaaaaaaaaa	2026-11-17 10:00:00 +0000 UTC	90MB
talent-scope-worker	rollback-bbb	wbbbbbbbbbbb	2026-11-16 10:00:00 +0000 UTC	90MB
talent-scope-worker	rollback-ccc	wccccccccccc	2026-11-15 10:00:00 +0000 UTC	90MB
talent-scope-worker	rollback-old	woldoldoldol	2026-11-10 10:00:00 +0000 UTC	90MB
talent-scope-frontend	rollback-aaa	faaaaaaaaaaa	2026-11-17 10:00:00 +0000 UTC	40MB
talent-scope-frontend	rollback-bbb	fbbbbbbbbbbb	2026-11-16 10:00:00 +0000 UTC	40MB
talent-scope-frontend	rollback-ccc	fccccccccccc	2026-11-15 10:00:00 +0000 UTC	40MB
talent-scope-frontend	rollback-old	foldfoldfold	2026-11-10 10:00:00 +0000 UTC	40MB
postgres	rollback-should-ignore	pppppppppppp	2026-11-01 10:00:00 +0000 UTC	200MB
talent-scope-api	latest	aaaaaaaaaaaa	2026-11-17 10:00:00 +0000 UTC	100MB
EOF
  # Running/stopped containers: current api uses aaa, stopped worker still on old.
  cat >"${state}/containers.tsv" <<'EOF'
talent-scope-api-1	talent-scope-api:latest	sha256:aaaaaaaaaaaa	Up 2 hours
talent-scope-worker-1	talent-scope-worker:rollback-old	sha256:woldoldoldol	Exited (0) 3 days ago
talent-scope-frontend-1	talent-scope-frontend:rollback-aaa	sha256:faaaaaaaaaaa	Up 2 hours
EOF
  # Track reclaimable ids initially present.
  for id in aaaaaaaaaaaa bbbbbbbbbbbb cccccccccccc dddddddddddd \
            waaaaaaaaaaa wbbbbbbbbbbb wccccccccccc woldoldoldol \
            faaaaaaaaaaa fbbbbbbbbbbb fccccccccccc foldfoldfold; do
    : >"${state}/ids/${id}"
  done
}

run_case() {
  local name="$1"
  shift
  local tmp
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/ts-rb-test.XXXXXX")"
  local bin="${tmp}/bin"
  local state="${tmp}/state"
  make_mock_docker "$bin"
  seed_state "$state"
  export MOCK_DOCKER_STATE="$state"
  export DOCKER_BIN="${bin}/docker"
  export PATH="${bin}:$PATH"

  local out ec
  set +e
  out="$("$SCRIPT" "$@" 2>&1)"
  ec=$?
  set -e
  printf '\n== %s (exit=%s) ==\n' "$name" "$ec"
  printf '%s\n' "$out" | sed -n '1,80p'

  case "$name" in
    dry-run-keeps-two-plus-protected)
      assert_eq "${name}:exit" "0" "$ec"
      assert_contains "${name}:dry" "dry-run" "$out"
      # api: aaa protected, bbb/ccc kept, ddd candidate
      assert_contains "${name}:candidate-api-ddd" "talent-scope-api:rollback-ddd" "$out"
      assert_not_contains "${name}:api-ccc-not-candidate-line" "candidate	talent-scope-api:rollback-ccc" "$out"
      assert_not_contains "${name}:keep-aaa" "candidate	talent-scope-api:rollback-aaa" "$out"
      # worker: old protected by stopped container; aaa/bbb kept; ccc candidate
      assert_contains "${name}:candidate-worker-ccc" "talent-scope-worker:rollback-ccc" "$out"
      cand_block="$(printf '%s\n' "$out" | awk '/Candidate tags for deletion/,0')"
      assert_not_contains "${name}:worker-old-not-candidate" "talent-scope-worker:rollback-old" "$cand_block"
      # frontend: aaa protected, bbb/ccc kept, old candidate
      assert_contains "${name}:candidate-fe-old" "talent-scope-frontend:rollback-old" "$out"
      # postgres out of scope
      assert_not_contains "${name}:no-postgres" "postgres:rollback" "$out"
      assert_eq "${name}:no-delete" "0" "$(wc -l <"${state}/deleted_tags.txt" | tr -d ' ')"
      ;;
    apply-deletes-candidates-only)
      assert_eq "${name}:exit" "0" "$ec"
      assert_contains "${name}:deleted-ddd" "deleted_tag: talent-scope-api:rollback-ddd" "$out"
      assert_contains "${name}:deleted-worker-ccc" "deleted_tag: talent-scope-worker:rollback-ccc" "$out"
      assert_contains "${name}:deleted-fe-old" "deleted_tag: talent-scope-frontend:rollback-old" "$out"
      assert_not_contains "${name}:no-worker-old" "deleted_tag: talent-scope-worker:rollback-old" "$out"
      assert_not_contains "${name}:no-rmi-force" "rmi --force" "$out"
      assert_not_contains "${name}:no-image-prune" "image prune" "$out"
      assert_not_contains "${name}:no-volume-rm" "volume rm" "$out"
      assert_contains "${name}:reclaim-report" "tag_deleted" "$out"
      ;;
  esac

  rm -rf "$tmp"
}

# Fix apply-aborts case: the nested make_mock path is fragile. Use simpler approach:
# run a dedicated case function instead.

test_dry_run() {
  run_case dry-run-keeps-two-plus-protected
}

test_apply() {
  run_case apply-deletes-candidates-only --apply
}

test_reverify_abort() {
  local tmp bin state
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/ts-rb-test.XXXXXX")"
  bin="${tmp}/bin"
  state="${tmp}/state"
  make_mock_docker "$bin"
  seed_state "$state"
  # Wrap docker: on second `ps` call during reverify, protect frontend old.
  cat >"${bin}/docker.inner" <<'INNER'
#!/usr/bin/env bash
set -Eeuo pipefail
STATE_DIR="${MOCK_DOCKER_STATE:?}"
cmd="${1:-}"
shift || true
case "$cmd" in
  images)
    cat "${STATE_DIR}/images.tsv"; exit 0 ;;
  ps)
    count_file="${STATE_DIR}/ps_count"
    n=0
    [[ -f "$count_file" ]] && n="$(cat "$count_file")"
    n=$((n + 1))
    echo "$n" >"$count_file"
    if [[ "$n" -ge 2 ]]; then
      cat >"${STATE_DIR}/containers.tsv" <<'EOF'
talent-scope-api-1	talent-scope-api:latest	sha256:aaaaaaaaaaaa	Up 2 hours
talent-scope-worker-1	talent-scope-worker:rollback-old	sha256:woldoldoldol	Exited (0) 3 days ago
talent-scope-frontend-1	talent-scope-frontend:rollback-aaa	sha256:faaaaaaaaaaa	Up 2 hours
talent-scope-frontend-old	talent-scope-frontend:rollback-old	sha256:foldfoldfold	Exited (0) 1 hours ago
EOF
    fi
    cat "${STATE_DIR}/containers.tsv"; exit 0 ;;
  image)
    sub="${1:-}"; shift || true
    if [[ "$sub" == "inspect" ]]; then
      fmt=""; [[ "${1:-}" == "--format" ]] && { fmt="$2"; shift 2; }
      ref="${1:-}"
      while IFS=$'\t' read -r repo tag id created size; do
        [[ "${repo}:${tag}" == "$ref" || "$id" == "$ref" ]] || continue
        [[ "$fmt" == '{{.Id}}' ]] && printf 'sha256:%s\n' "$id" || printf '%s\n' "$id"
        exit 0
      done <"${STATE_DIR}/images.tsv"
      exit 1
    fi
    exit 2 ;;
  rmi)
    echo "rmi should not run on abort" >&2
    exit 9 ;;
  *) exit 2 ;;
esac
INNER
  chmod +x "${bin}/docker.inner"
  ln -sf docker.inner "${bin}/docker"
  export MOCK_DOCKER_STATE="$state"
  export DOCKER_BIN="${bin}/docker"

  local out ec
  set +e
  out="$("$SCRIPT" --apply 2>&1)"
  ec=$?
  set -e
  printf '\n== apply-aborts-when-candidate-becomes-protected (exit=%s) ==\n' "$ec"
  printf '%s\n' "$out" | sed -n '1,60p'
  assert_eq "reverify-abort:exit" "1" "$ec"
  assert_contains "reverify-abort:msg" "re-verification failed" "$out"
  assert_not_contains "reverify-abort:no-rmi" "deleted_tag:" "$out"
  rm -rf "$tmp"
}

[[ -x "$SCRIPT" ]] || chmod +x "$SCRIPT"

test_dry_run
test_apply
test_reverify_abort

echo
echo "Passed=${PASS} Failed=${FAIL}"
[[ "$FAIL" -eq 0 ]]
