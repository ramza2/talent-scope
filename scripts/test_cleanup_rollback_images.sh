#!/usr/bin/env bash
# Mock-only tests for cleanup-rollback-images.sh (no real Docker mutations).
# Mock docker emits real CLI-shaped output (ps -aq, inspect, images --no-trunc).
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="${ROOT_DIR}/scripts/cleanup-rollback-images.sh"
PASS=0
FAIL=0

# Full 64-hex ids (canonical sha256 bodies; hex only).
ID_A='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
ID_B='bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
ID_C='cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc'
ID_D='dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd'
ID_W1='1111111111111111111111111111111111111111111111111111111111111111'
ID_W2='2222222222222222222222222222222222222222222222222222222222222222'
ID_W3='3333333333333333333333333333333333333333333333333333333333333333'
ID_WOLD='4444444444444444444444444444444444444444444444444444444444444444'
ID_F1='5151515151515151515151515151515151515151515151515151515151515151'
ID_F2='5252525252525252525252525252525252525252525252525252525252525252'
ID_F3='5353535353535353535353535353535353535353535353535353535353535353'
ID_FOLD='5454545454545454545454545454545454545454545454545454545454545454'
# Duplicate-tag shared id (two rollback tags → one ID).
ID_DUP='eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
# Unused image id used only for mid-flight tag retarget regression.
ID_RETARGET='ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    PASS=$((PASS + 1)); printf 'PASS %s\n' "$label"
  else
    FAIL=$((FAIL + 1))
    printf 'FAIL %s\n expected=%s\n actual=%s\n' "$label" "$expected" "$actual"
  fi
}

assert_contains() {
  local label="$1" needle="$2" haystack="$3"
  if [[ "$haystack" == *"$needle"* ]]; then
    PASS=$((PASS + 1)); printf 'PASS %s\n' "$label"
  else
    FAIL=$((FAIL + 1))
    printf 'FAIL %s\n missing=%s\n' "$label" "$needle"
  fi
}

assert_not_contains() {
  local label="$1" needle="$2" haystack="$3"
  if [[ "$haystack" != *"$needle"* ]]; then
    PASS=$((PASS + 1)); printf 'PASS %s\n' "$label"
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

fail_inspect_image() {
  [[ "${MOCK_FAIL_IMAGE_INSPECT:-0}" == "1" ]]
}

fail_ps() {
  [[ "${MOCK_FAIL_PS:-0}" == "1" ]]
}

case "$cmd" in
  images)
    # Real shape: docker images --no-trunc --format '...'
    if [[ -f "${STATE_DIR}/images_hook.sh" ]]; then
      # shellcheck disable=SC1091
      source "${STATE_DIR}/images_hook.sh"
    fi
    cat "${STATE_DIR}/images_format.tsv"
    exit 0
    ;;
  ps)
    if fail_ps; then
      echo "Cannot connect to the Docker daemon" >&2
      exit 1
    fi
    # Only support safe form: ps -aq  (no .ImageID format)
    if [[ "${1:-}" == "-aq" || ( "${1:-}" == "-a" && "${2:-}" == "-q" ) ]]; then
      if [[ -f "${STATE_DIR}/ps_hook.sh" ]]; then
        # shellcheck disable=SC1091
        source "${STATE_DIR}/ps_hook.sh"
      fi
      awk '{print $1}' "${STATE_DIR}/containers.tsv"
      exit 0
    fi
    echo "unexpected docker ps invocation (ImageID format forbidden): $*" >&2
    exit 2
    ;;
  inspect)
    # docker inspect --format '{{.Image}}' CID
    # docker inspect --format '{{.Config.Image}}' CID
    fmt=""
    if [[ "${1:-}" == "--format" || "${1:-}" == "-f" ]]; then
      fmt="$2"
      shift 2
    fi
    target="${1:-}"
    [[ -n "$target" ]] || exit 2
    # Container inspect
    while IFS=$'\t' read -r cid image_ref image_id status; do
      [[ "$cid" == "$target" || "$cid" == "$target"* ]] || continue
      case "$fmt" in
        '{{.Image}}')
          printf 'sha256:%s\n' "$image_id"; exit 0 ;;
        '{{.Config.Image}}')
          printf '%s\n' "$image_ref"; exit 0 ;;
        *)
          printf '%s\n' "$image_id"; exit 0 ;;
      esac
    done <"${STATE_DIR}/containers.tsv"
    echo "No such object: $target" >&2
    exit 1
    ;;
  image)
    sub="${1:-}"; shift || true
    if [[ "$sub" != "inspect" ]]; then
      echo "unexpected docker image $sub" >&2
      exit 2
    fi
    if fail_inspect_image; then
      echo "Error: No such image" >&2
      exit 1
    fi
    fmt=""
    if [[ "${1:-}" == "--format" || "${1:-}" == "-f" ]]; then
      fmt="$2"
      shift 2
    fi
    ref="${1:-}"
    while IFS=$'\t' read -r repo tag id created size; do
      full="sha256:${id}"
      if [[ "${repo}:${tag}" == "$ref" || "$full" == "$ref" || "$id" == "$ref" ]]; then
        case "$fmt" in
          '{{.Id}}') printf '%s\n' "$full"; exit 0 ;;
          '{{.Created}}')
            # RFC3339-like inspect output derived from CreatedAt list field.
            # created column: 2026-11-17 10:00:00 +0000 UTC
            d="${created%% *}"
            t="$(printf '%s' "$created" | awk '{print $2}')"
            printf '%sT%s.000000000Z\n' "$d" "$t"
            exit 0
            ;;
          *) printf '%s\n' "$full"; exit 0 ;;
        esac
      fi
    done <"${STATE_DIR}/images_format.tsv"
    # Presence-only reclaim check by id file
    bare="${ref#sha256:}"
    if [[ -f "${STATE_DIR}/ids/${bare}" ]]; then
      exit 0
    fi
    exit 1
    ;;
  rmi)
    ref="${1:-}"
    [[ -n "$ref" ]] || exit 2
    for a in "$@"; do
      if [[ "$a" == "-f" || "$a" == "--force" || "$a" == "prune" ]]; then
        echo "force/prune forbidden" >&2
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
        printf '%s\n' "$ref" >>"${STATE_DIR}/deleted_tags.txt"
        continue
      fi
      printf '%s\t%s\t%s\t%s\t%s\n' "$repo" "$tag" "$id" "$created" "$size" >>"$tmp"
    done <"${STATE_DIR}/images_format.tsv"
    mv "$tmp" "${STATE_DIR}/images_format.tsv"
    [[ "$removed" -eq 1 ]] || exit 1
    if [[ -n "$removed_id" ]] \
      && ! awk -F '\t' -v id="$removed_id" '$3==id {f=1} END{exit !f}' \
        "${STATE_DIR}/images_format.tsv"; then
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
  : >"${state}/deleted_tags.txt"
  # docker images --no-trunc --format '{{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.CreatedAt}}\t{{.Size}}'
  cat >"${state}/images_format.tsv" <<EOF
talent-scope-api	rollback-aaa	${ID_A}	2026-11-17 10:00:00 +0000 UTC	100MB
talent-scope-api	rollback-bbb	${ID_B}	2026-11-16 10:00:00 +0000 UTC	100MB
talent-scope-api	rollback-ccc	${ID_C}	2026-11-15 10:00:00 +0000 UTC	100MB
talent-scope-api	rollback-ddd	${ID_D}	2026-11-14 10:00:00 +0000 UTC	100MB
talent-scope-api	rollback-dup-one	${ID_DUP}	2026-11-13 10:00:00 +0000 UTC	100MB
talent-scope-api	rollback-dup-two	${ID_DUP}	2026-11-13 10:00:00 +0000 UTC	100MB
talent-scope-worker	rollback-aaa	${ID_W1}	2026-11-17 10:00:00 +0000 UTC	90MB
talent-scope-worker	rollback-bbb	${ID_W2}	2026-11-16 10:00:00 +0000 UTC	90MB
talent-scope-worker	rollback-ccc	${ID_W3}	2026-11-15 10:00:00 +0000 UTC	90MB
talent-scope-worker	rollback-old	${ID_WOLD}	2026-11-10 10:00:00 +0000 UTC	90MB
talent-scope-frontend	rollback-aaa	${ID_F1}	2026-11-17 10:00:00 +0000 UTC	40MB
talent-scope-frontend	rollback-bbb	${ID_F2}	2026-11-16 10:00:00 +0000 UTC	40MB
talent-scope-frontend	rollback-ccc	${ID_F3}	2026-11-15 10:00:00 +0000 UTC	40MB
talent-scope-frontend	rollback-old	${ID_FOLD}	2026-11-10 10:00:00 +0000 UTC	40MB
postgres	rollback-should-ignore	pppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppp	2026-11-01 10:00:00 +0000 UTC	200MB
talent-scope-api	latest	${ID_A}	2026-11-17 10:00:00 +0000 UTC	100MB
EOF
  # containers.tsv: cid\tConfig.Image\tImage(sha body)\tstatus
  # Includes a stopped container referencing worker-old.
  cat >"${state}/containers.tsv" <<EOF
cidapi001	talent-scope-api:latest	${ID_A}	running
cidworker1	talent-scope-worker:rollback-old	${ID_WOLD}	exited
cidfront01	talent-scope-frontend:rollback-aaa	${ID_F1}	running
EOF
  for id in "$ID_A" "$ID_B" "$ID_C" "$ID_D" "$ID_DUP" \
            "$ID_W1" "$ID_W2" "$ID_W3" "$ID_WOLD" \
            "$ID_F1" "$ID_F2" "$ID_F3" "$ID_FOLD"; do
    : >"${state}/ids/${id}"
  done
}

# Globals set by prepare_mock / last_run helpers (avoid subshell state loss).
MOCK_STATE_DIR=""
MOCK_TMP_DIR=""
LAST_OUT=""
LAST_EC=0

prepare_mock() {
  MOCK_TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ts-rb-test.XXXXXX")"
  make_mock_docker "${MOCK_TMP_DIR}/bin"
  MOCK_STATE_DIR="${MOCK_TMP_DIR}/state"
  seed_state "$MOCK_STATE_DIR"
  export MOCK_DOCKER_STATE="$MOCK_STATE_DIR"
  export DOCKER_BIN="${MOCK_TMP_DIR}/bin/docker"
  unset MOCK_FAIL_PS MOCK_FAIL_IMAGE_INSPECT || true
}

run_script() {
  set +e
  LAST_OUT="$("$SCRIPT" "$@" 2>&1)"
  LAST_EC=$?
  set -e
}

cleanup_mock() {
  [[ -n "${MOCK_TMP_DIR:-}" ]] && rm -rf "$MOCK_TMP_DIR"
  MOCK_TMP_DIR=""
  MOCK_STATE_DIR=""
}

[[ -x "$SCRIPT" ]] || chmod +x "$SCRIPT"

echo "== dry-run: keep 2 distinct past IDs + stopped container protection =="
prepare_mock
run_script
printf '%s\n' "$LAST_OUT" | sed -n '1,40p'
assert_eq "dry:exit" "0" "$LAST_EC"
assert_contains "dry:mode" "dry-run" "$LAST_OUT"
# api: A protected (container), B+C kept as 2 past IDs, D + DUP candidates
assert_contains "dry:cand-api-d" "talent-scope-api:rollback-ddd" "$LAST_OUT"
assert_contains "dry:cand-dup-one" "talent-scope-api:rollback-dup-one" "$LAST_OUT"
assert_contains "dry:cand-dup-two" "talent-scope-api:rollback-dup-two" "$LAST_OUT"
assert_not_contains "dry:api-c-not-cand-list" "talent-scope-api:rollback-ccc" "$(printf '%s\n' "$LAST_OUT" | awk '/Candidate tags for deletion/,0')"
# worker stopped container protects WOLD; W1+W2 kept; W3 candidate
assert_contains "dry:cand-worker-w3" "talent-scope-worker:rollback-ccc" "$LAST_OUT"
assert_not_contains "dry:worker-old-not-cand" "talent-scope-worker:rollback-old" "$(printf '%s\n' "$LAST_OUT" | awk '/Candidate tags for deletion/,0')"
assert_contains "dry:cand-fe-old" "talent-scope-frontend:rollback-old" "$LAST_OUT"
assert_not_contains "dry:no-postgres" "postgres:rollback" "$LAST_OUT"
assert_eq "dry:no-delete" "0" "$(wc -l <"${MOCK_STATE_DIR}/deleted_tags.txt" | tr -d ' ')"
cleanup_mock

echo "== apply: deletes candidates only; duplicate tags share reclaim =="
prepare_mock
run_script --apply
assert_eq "apply:exit" "0" "$LAST_EC"
assert_contains "apply:del-d" "deleted_tag: talent-scope-api:rollback-ddd" "$LAST_OUT"
assert_contains "apply:del-dup1" "deleted_tag: talent-scope-api:rollback-dup-one" "$LAST_OUT"
assert_contains "apply:del-dup2" "deleted_tag: talent-scope-api:rollback-dup-two" "$LAST_OUT"
assert_contains "apply:del-w3" "deleted_tag: talent-scope-worker:rollback-ccc" "$LAST_OUT"
assert_contains "apply:del-fe-old" "deleted_tag: talent-scope-frontend:rollback-old" "$LAST_OUT"
assert_not_contains "apply:no-worker-old" "deleted_tag: talent-scope-worker:rollback-old" "$LAST_OUT"
assert_not_contains "apply:no-rmi-force" "rmi --force" "$LAST_OUT"
assert_contains "apply:reclaim" "tag_deleted" "$LAST_OUT"
assert_contains "apply:dup-reclaim-or-shared" "sha256:${ID_DUP}" "$LAST_OUT"
cleanup_mock

echo "== lookup failure (ps -aq): deletion forbidden =="
prepare_mock
export MOCK_FAIL_PS=1
run_script --apply
assert_eq "ps-fail:exit" "1" "$LAST_EC"
assert_contains "ps-fail:msg" "refusing --apply" "$LAST_OUT"
assert_eq "ps-fail:no-delete" "0" "$(wc -l <"${MOCK_STATE_DIR}/deleted_tags.txt" | tr -d ' ')"
unset MOCK_FAIL_PS
cleanup_mock

echo "== lookup failure (image inspect Created): dry-run disables candidates =="
prepare_mock
export MOCK_FAIL_IMAGE_INSPECT=1
run_script
assert_eq "inspect-fail-dry:exit" "0" "$LAST_EC"
assert_contains "inspect-fail-dry:disabled" "deletion disabled" "$LAST_OUT"
assert_contains "inspect-fail-dry:none" "(none)" "$(printf '%s\n' "$LAST_OUT" | awk '/Candidate tags for deletion/,0')"
unset MOCK_FAIL_IMAGE_INSPECT
cleanup_mock

echo "== protection change during reverify aborts before delete =="
prepare_mock
cat >"${MOCK_STATE_DIR}/ps_hook.sh" <<EOF
n=0
[[ -f "\${STATE_DIR}/ps_count" ]] && n=\$(cat "\${STATE_DIR}/ps_count")
n=\$((n + 1))
echo "\$n" >"\${STATE_DIR}/ps_count"
if [[ "\$n" -ge 2 ]]; then
  cat >"\${STATE_DIR}/containers.tsv" <<CONTS
cidapi001	talent-scope-api:latest	${ID_A}	running
cidworker1	talent-scope-worker:rollback-old	${ID_WOLD}	exited
cidfront01	talent-scope-frontend:rollback-aaa	${ID_F1}	running
cidfrontold	talent-scope-frontend:rollback-old	${ID_FOLD}	exited
CONTS
fi
EOF
run_script --apply
assert_eq "reverify-abort:exit" "1" "$LAST_EC"
assert_contains "reverify-abort:msg" "re-verification failed" "$LAST_OUT"
assert_not_contains "reverify-abort:no-delete" "deleted_tag:" "$LAST_OUT"
cleanup_mock

echo "== candidate tag retargeted to different unused image id aborts =="
prepare_mock
: >"${MOCK_STATE_DIR}/ids/${ID_RETARGET}"
# After initial selection (1st images call), retarget rollback-ddd to an unused id.
cat >"${MOCK_STATE_DIR}/images_hook.sh" <<EOF
n=0
[[ -f "\${STATE_DIR}/images_count" ]] && n=\$(cat "\${STATE_DIR}/images_count")
n=\$((n + 1))
echo "\$n" >"\${STATE_DIR}/images_count"
if [[ "\$n" -ge 2 ]]; then
  awk -F '\t' -v OFS='\t' -v nid='${ID_RETARGET}' '
    \$1=="talent-scope-api" && \$2=="rollback-ddd" { \$3=nid }
    { print }
  ' "\${STATE_DIR}/images_format.tsv" >"\${STATE_DIR}/images_format.tsv.tmp"
  mv "\${STATE_DIR}/images_format.tsv.tmp" "\${STATE_DIR}/images_format.tsv"
fi
EOF
run_script --apply
assert_eq "retarget-abort:exit" "1" "$LAST_EC"
assert_contains "retarget-abort:msg" "id changed for talent-scope-api:rollback-ddd" "$LAST_OUT"
assert_contains "retarget-abort:initial" "sha256:${ID_D}" "$LAST_OUT"
assert_contains "retarget-abort:now" "sha256:${ID_RETARGET}" "$LAST_OUT"
assert_not_contains "retarget-abort:no-delete" "deleted_tag:" "$LAST_OUT"
cleanup_mock

echo
echo "Passed=${PASS} Failed=${FAIL}"
[[ "$FAIL" -eq 0 ]]
