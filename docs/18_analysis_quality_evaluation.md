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

## Golden Set 정확도 평가

`analysis_quality_report.py`가 구조·근거 보유 여부를 측정한다면,
`analysis_golden_set_eval.py`는 사람이 확정한 정답셋과 기존
`AnalysisRun.candidate_json`을 비교한다. DB는 읽기 전용이며 LLM/VLM 호출,
재분석, Confirm, 운영 데이터 수정은 하지 않는다.

Golden Set schema version은 `talentscope-analysis-golden-v1`이다.
예시는 `docs/examples/analysis_golden_set.example.json`을 참고한다.

평가 범위(v1):

- root `jobs` / `skills` / `expertise` code precision / recall / F1
- project 존재 precision / recall / F1
- 매칭된 project의 `jobs` / `skills` / `expertise` / `business_domains` / `customer_types`
- Golden Set에 기대 항목으로 명시된 entity의 `source_refs` 보유율

Golden Set은 **부분 정답셋**을 허용한다. 필드를 생략하면 그 영역은 평가하지 않고,
빈 배열 `[]`을 명시하면 해당 영역의 기대값이 0개라는 뜻이다.
Project는 `project_name`이 필수이며 `customer_name`, `start_date`,
`end_date`는 선택 제약이다. 이름/고객사는 공백 차이를 무시하고,
Golden Set 날짜가 `YYYY.MM`이면 실제 day precision 값도 같은 월이면 매칭한다.

`analysis_run_id`가 있으면 해당 run을 평가한다. 없으면 해당 인력의 최신
`REVIEWING`/`CONFIRMED` run을 사용한다. 실제 기준셋은 재현성을 위해 가능하면
`analysis_run_id`를 명시한다.

서버 실행 예:

```bash
docker compose \
  --env-file .env.server \
  -f docker-compose.server.yml \
  run --rm --no-deps \
  -v "$PWD/backend:/workspace/backend:ro" \
  -v "$PWD/golden:/workspace/golden:ro" \
  --entrypoint python api \
  /workspace/backend/scripts/analysis_golden_set_eval.py \
  --golden-set /workspace/golden/analysis_golden_set.json
```

JSON 결과:

```bash
python backend/scripts/analysis_golden_set_eval.py \
  --golden-set golden/analysis_golden_set.json \
  --format json \
  --output /tmp/golden-eval.json
```

### 실제 Golden Set 구축 순서

1. 문서 유형과 경력 형태가 겹치지 않도록 실제 인력 약 10명을 선정한다.
2. 각 인력의 프로젝트, JOB, TECH, EXP와 필요한 project relation을 사람이 원문 기준으로 확정한다.
3. 정답 JSON에는 개인식별정보 원문/연락처를 넣지 않고 `person_id`,
   `analysis_run_id`, 표준 code와 project identity만 저장한다.
4. 동일 Golden Set으로 prompt/normalizer 변경 전후 결과를 반복 비교한다.
5. 10명 기준셋이 안정화된 뒤에만 precision/recall/F1을 품질 gate 후보로 사용한다.

기존 구조/근거 리포트 JSON 계약은 유지한다. Golden Set evaluator는 별도 도구이며
기존 출력 key를 변경하지 않는다.

## 다음 단계

실제 인력 약 10명을 선정해 Golden Set JSON을 채우고 첫 baseline을 기록한다.
