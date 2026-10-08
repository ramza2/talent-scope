# 18. AI Analysis Quality Evaluation

## 목적

`AnalysisRun` 결과를 **동일 기준**으로 반복 측정하기 위한 read-only 품질 리포트 기반이다.

이번 단계는 정답(Golden Set) 대비 정확도 평가가 아니다.  
현재 분석 결과의 **구조 / 근거(source_refs) / 소요시간 / Diff 요약**을 관찰해, 이후 프롬프트·정규화 개선의 전후 비교에 쓸 측정기를 제공한다.

## Metric 정의

| 영역 | Metric | 설명 |
|------|--------|------|
| Identity | analysis_run_id, person_id, status, documents, prompt/schema/model, timestamps | run 메타데이터 |
| Duration | `duration_seconds` | `started_at`과 `completed_at`이 모두 있을 때만 계산 |
| Entities | jobs / skills / expertise / projects / employment / education / certifications | `candidate_json` list item 수 (`employment` ← `employment_history`) |
| Evidence | eligible / with_source_refs / source_ref_count / coverage_pct | 근거 대상 entity 대비 source_refs 보유율 |
| Evidence refs | refs_with_document_id / quote_text / page_no | ref 필드 존재 카운트 |
| Evidence validity | valid_document_ref_count / invalid_document_ref_count | run에 연결된 document_id 일치 여부 |
| Diff | SAME/NEW/UPDATE/CONFLICT/REVIEW + PENDING | `analysis_diff_item` 집계 |

### Evidence denominator (근거 대상 entity)

profile-candidate-v1에서 `source_refs`를 갖도록 정의된 대상만 분모에 넣는다.

포함:

- 값이 채워진 profile scalar (`PROFILE_SCALAR_FIELDS`) — `profile.source_refs[field]`
- `jobs` / `skills` / `expertise` / `employment_history` / `education` / `certifications` / `projects`의 각 dict item
- project 하위 code-ref item (`jobs` / `skills` / `expertise` / `business_domains` / `customer_types`)

제외:

- `summary`, `analysis` 메타데이터
- 비어 있는 profile scalar
- malformed non-dict list item

`page_no`가 없는 ref는 정상일 수 있다. page_no coverage는 PASS/FAIL 기준으로 쓰지 않는다.

Aggregate coverage는 run별 pct 평균이 아니라 **분자·분모 합산 후** 계산한다.

## 이 리포트가 의미하지 않는 것

- 프로젝트/스킬/전문분야가 **정답인지** 여부
- 추출 누락·환각에 대한 정밀도/재현율
- 운영 데이터 변경 또는 재분석 트리거

측정만 하며 production row를 수정하지 않는다. LLM/VLM/Embedding을 호출하지 않는다.

## 실행 예

로컬 / Cloud Agent (host DATABASE_URL):

```bash
cd backend
python scripts/analysis_quality_report.py --latest 5
python scripts/analysis_quality_report.py --analysis-run-id <uuid>
python scripts/analysis_quality_report.py --person-id <uuid> --latest 3
python scripts/analysis_quality_report.py --latest 10 --format json --output /tmp/aqr.json
python scripts/analysis_quality_report.py --latest 10 --format csv --output /tmp/aqr.csv
```

서버 compose (UUID는 hard-code하지 말 것):

`api` service의 production `DATABASE_URL`로 실제 `AnalysisRun`을 조회한다.  
CLI 자체는 DB write/commit을 하지 않는 read-only 도구다.  
`test` service는 `POSTGRES_TEST_DB`용이며 tools-db-init가 test DB를 재생성하므로  
production quality report에 사용하지 않는다.

```bash
docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  run --rm --no-deps \
  -v "$PWD/backend:/workspace/backend:ro" \
  --entrypoint python api \
  /workspace/backend/scripts/analysis_quality_report.py \
  --latest 5

docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  run --rm --no-deps \
  -v "$PWD/backend:/workspace/backend:ro" \
  --entrypoint python api \
  /workspace/backend/scripts/analysis_quality_report.py \
  --analysis-run-id <uuid> --format json
```

Selector:

- `--analysis-run-id` (repeatable)
- `--person-id` (비삭제 인력만; 기본 최신 5건, `--latest`로 조정)
- `--latest N` (전역 최신 N건, 또는 person 범위)

DELETED person run은 기존 list visibility와 같이 제외한다.

## 다음 단계 (계획)

실제 인력 약 10명 내외를 Golden Set으로 선정하고,  
expected project / skill / expertise / evidence 등을 **별도 정답 기준**으로 평가하는 단계를 이어간다.  
본 리포트의 JSON 출력 key는 그 자동화의 입력으로 재사용할 수 있도록 안정적으로 유지한다.
