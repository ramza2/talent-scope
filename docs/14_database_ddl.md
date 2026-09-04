# 14. PostgreSQL DDL Baseline

## 1. 목적

본 문서는 [12. Database ERD & Core Tables](12_database_erd.md)와 [13. Application Architecture & Backend Stack](13_application_architecture.md)을 실제 PostgreSQL Schema 수준으로 구체화한다.

실행 가능한 기준 DDL은 다음 파일에 둔다.

- [`db/schema.sql`](../db/schema.sql)

이 DDL은 **1차 MVP 설계 Baseline**이다. 실제 Backend 구현을 시작한 이후에는 SQLAlchemy Model과 Alembic Migration을 이용하여 Schema 변경이력을 관리하고, 운영환경에서 `schema.sql`을 반복 실행하여 Schema를 변경하는 방식은 사용하지 않는다.

---

## 2. DB Runtime 기준

FIX 기준:

| 항목 | 기준 |
|---|---|
| RDBMS | PostgreSQL 16+ |
| ORM | SQLAlchemy 2.x |
| Migration | Alembic |
| Driver | psycopg 3 |
| Vector | pgvector |
| Fuzzy Search | pg_trgm |
| UUID | pgcrypto `gen_random_uuid()` |
| Embedding Dimension | 1024 (BGE-M3 기준) |

필수 Extension:

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

운영 Docker Image는 `pgvector` Extension을 사용할 수 있는 PostgreSQL Image를 사용해야 한다.

---

## 3. 테이블 구성

### 사용자 / 코드

```text
app_user
code_master
code_alias
```

`code_master`는 `JOB / TECH / EXP / BIZ / CUSTOMER_TYPE / DOC_TYPE`을 공통 관리한다.

`code_alias`는 문서 및 자연어에서 발견한 표현을 표준코드에 Mapping한다.

예:

```text
AI Engineer
AI Developer
인공지능 개발자
        ↓
JOB-AI-DEV
```

공통 Code Master 방식이므로 `person_skill.tech_code`가 반드시 TECH인지와 같은 **Code Type 검증은 Service Layer에서 필수 수행**한다. 필요 시 고도화 단계에서 DB Trigger 또는 타입별 View/Constraint를 추가할 수 있다.

---

## 4. 인력 등록

```text
upload_session
  └ upload_temp_file
          ↓
      Person 결정
          ↓
person
  └ person_profile
```

### `upload_session`

신규 등록 Wizard에서 아직 신규인력/기존인력 여부가 결정되지 않은 상태를 관리한다.

상태:

```text
UPLOADING
→ IDENTIFYING
→ IDENTIFIED
→ RESOLVED
```

취소/만료:

```text
CANCELLED
EXPIRED
```

기본 식별정보인 이름, 소속, 연락처, 이메일과 중복 인력 후보 결과를 보관한다.

### `upload_temp_file`

정식 Person/Document에 연결되기 전 임시파일 Metadata를 관리한다.

- 원본 파일명
- 임시 Object Storage Key
- 문서종류
- MIME / 확장자 / 크기
- SHA-256
- 유효성 검사 상태

실제 File Binary는 PostgreSQL에 저장하지 않는다.

---

## 5. Confirmed Profile

### `person`

사람 자체의 Identity와 상태만 관리한다.

```text
ACTIVE
INACTIVE
ARCHIVED
DELETED
```

### `person_profile`

사용자가 최종 확정한 현재 Profile 대표값이다.

주요 값:

- 이름
- 연락처 / 이메일 / 거주지역
- 소속 / 부서 / 직위
- 기술등급
- 경력 시작일
- 계산 경력 / 문서표기 경력 / 확정 경력
- 확정 Profile Summary
- Profile Version

AI Candidate 값은 이 Table에 직접 저장하지 않는다.

---

## 6. 직무·기술·전문분야

```text
person_job
person_skill
person_expertise
```

### `person_job`

직무 유형:

```text
PRIMARY
SECONDARY
EXPERIENCE
```

`UNIQUE(person_id, job_code, job_type)`으로 중복 직무 연결을 방지한다.

### `person_skill`

관리값:

- TECH Code
- 최근 사용년도
- 사용자 확정 경험개월(있는 경우)
- 대표기술 여부

AI가 임의 생성한 숙련도 상/중/하 필드는 MVP Schema에 두지 않는다.

### `person_expertise`

전문분야는 `EXPLICIT / INFERRED` 근거유형을 보존한다.

---

## 7. 프로젝트 및 경력

핵심 Entity:

```text
project
 ├ project_job
 ├ project_skill
 ├ project_expertise
 ├ project_business_domain
 └ project_customer_type
```

프로젝트는 Person별 Entity로 관리하며 검색과 추천 근거의 중심 데이터가 된다.

`project`는 Soft Delete(`deleted_at`)를 사용한다.

인력 수준의 BIZ/CUSTOMER_TYPE은 별도 Source Table로 중복 저장하지 않고 프로젝트에서 집계한다.

DDL에 다음 View를 포함한다.

```text
vw_person_business_domain
vw_person_customer_type
```

기타 Profile Entity:

```text
employment_history
education
certification
```

---

## 8. 문서 및 버전관리

```text
document_group
  └ document
      ├ document_page
      └ document_chunk
```

### `document_group`

한 논리 문서를 표현한다.

예:

```text
홍길동 이력서
```

### `document`

각 실제 Version을 표현한다.

```text
홍길동 이력서
  ├ v1
  ├ v2
  └ v3 (is_latest=true)
```

주요 제약:

```sql
UNIQUE (document_group_id, version_no)
```

또한 Partial Unique Index를 사용하여 삭제되지 않은 하나의 Document Group에 `is_latest=true`인 Version이 하나만 존재하도록 한다.

원본/Preview는 MinIO 등 Object Storage에 저장하며 DB에는 Storage Key만 저장한다.

### `document_page`

원문 Evidence를 페이지 단위로 연결하기 위한 Data다.

- 페이지 번호
- 추출 Text
- Layout JSON
- 추출방식

### `document_chunk`

문서 Keyword/Vector Search용 Chunk다.

- Chunk Text
- Page Range
- Token Count
- Chunk Hash
- Metadata JSON

Embedding Vector 자체는 `document_chunk`가 아니라 공통 검색 파생 Table인 `search_index_item`에 저장한다.

---

## 9. AI Analysis / Review

```text
analysis_run
  ├ analysis_run_document
  └ analysis_diff_item
          └ analysis_diff_evidence
```

### `analysis_run`

한 번의 Profile AI 분석 실행이다.

상태:

```text
QUEUED
→ PROCESSING
→ REVIEWING
→ CONFIRMED
```

실패/취소:

```text
FAILED
CANCELLED
```

AI Candidate 전체 결과는 다음에 저장한다.

```text
candidate_json JSONB
```

MVP에서는 Candidate의 `candidate_project`, `candidate_skill` 등을 별도 Staging Table로 완전 정규화하지 않는다.

### `analysis_diff_item`

Confirmed DB와 Candidate를 비교하여 검토화면의 하나의 판단 단위를 만든다.

변경상태:

```text
SAME
NEW
UPDATE
CONFLICT
REVIEW
```

검토상태:

```text
PENDING
ACCEPTED
REJECTED
MODIFIED
MERGED
```

프로젝트 병합도 별도 Workflow Table 대신 `existing_target_id + MERGED`로 관리한다.

---

## 10. Evidence

```text
evidence
 ├ analysis_diff_evidence
 └ evidence_link
```

### `evidence`

실제 근거가 되는 문서 위치를 저장한다.

```text
Document
Page
Quote Text
BBox / Character Position
Extraction Method
Confidence
```

### `analysis_diff_evidence`

AI 검토항목과 원문 Evidence를 연결한다.

### `evidence_link`

확정 이후 Profile/Project와 Evidence를 연결한다.

`target_type + target_id` Polymorphic Link를 사용한다.

예:

```text
PERSON_SKILL / <uuid>
PROJECT / <uuid>
CERTIFICATION / <uuid>
```

이를 통해 다음 경로를 유지한다.

```text
검색결과
 → RAG 경험
 → 관련 프로젝트
 → 경력기술서 p.8
```

---

## 11. Profile Revision / Audit

### `profile_revision`

AI 확정 또는 사용자 직접수정 시 Person의 확정 Profile Snapshot을 남긴다.

```text
Person
 ├ revision 1
 ├ revision 2
 └ revision 3
```

`UNIQUE(person_id, revision_no)`를 적용한다.

### `audit_log`

다음과 같은 주요 행위를 기록한다.

```text
PERSON_CREATE
PROFILE_UPDATE
ANALYSIS_CONFIRM
DOCUMENT_UPLOAD
DOCUMENT_DOWNLOAD
PROJECT_UPDATE
```

Target이 여러 Entity가 될 수 있으므로 `target_type + target_id` 구조를 사용한다.

---

## 12. Search / Embedding

### `search_index_item`

검색용 파생 데이터다.

```text
PROFILE
PROJECT
DOCUMENT_CHUNK
```

세 타입을 하나의 Table에서 관리한다.

주요 컬럼:

```text
person_id
object_type
object_id
search_text
search_tsv
embedding VECTOR(1024)
source_weight
metadata_json
embedding_model
embedding_version
```

`search_tsv`는 PostgreSQL Generated Column으로 만든다.

```sql
to_tsvector('simple', search_text)
```

한국어 형태소 분석을 별도 검색엔진으로 도입하지 않는 MVP에서는 다음을 조합한다.

```text
Structured SQL
+ PostgreSQL FTS(simple)
+ pg_trgm
+ pgvector
```

### Vector Index

BGE-M3의 1024 Dimension을 기준으로 한다.

```sql
embedding VECTOR(1024)
```

Cosine Distance용 HNSW Index를 사용한다.

```sql
USING HNSW (embedding vector_cosine_ops)
```

향후 Embedding Model을 변경하여 Dimension 자체가 바뀌는 경우에는 단순 Model Name 변경만으로 처리하지 않고 **Schema Migration 또는 별도 Vector Column/Table 전략을 다시 검토**한다.

---

## 13. Search Index Job

`search_index_job`은 Profile 확정 Transaction과 실제 Embedding 생성을 분리한다.

```text
Profile 확정
   ↓
DB COMMIT
   ↓
search_index_job = PENDING
   ↓
Celery index Queue
   ↓
Embedding Provider
   ↓
search_index_item UPSERT
```

상태:

```text
PENDING
PROCESSING
COMPLETED
FAILED
CANCELLED
```

`idempotency_key`를 두어 동일 Index 작업의 중복 처리를 제어할 수 있도록 한다.

Celery/Redis 상태가 아닌 PostgreSQL Job 상태를 업무상 Source of Truth로 사용한다.

---

## 14. 주요 Index

DDL에는 다음 검색경로를 고려한 Index를 포함한다.

| 대상 | Index |
|---|---|
| Person 이름 | `pg_trgm` GIN |
| 소속회사 | `pg_trgm` GIN |
| Email | Lower-case B-tree |
| 직무 | `(job_code, person_id)` |
| 기술 | `(tech_code, person_id)` |
| 전문분야 | `(exp_code, person_id)` |
| 프로젝트명 | `pg_trgm` GIN |
| 고객명 | `pg_trgm` GIN |
| 문서 Hash | B-tree |
| Analysis 상태 | `(status, created_at)` |
| Diff 검토상태 | `(analysis_run_id, review_status)` |
| 검색 Text | GIN `tsvector` |
| 검색 유사문자 | GIN `gin_trgm_ops` |
| Vector | HNSW cosine |

---

## 15. Soft Delete 정책

MVP에서 이력 보존이 중요한 Entity는 Soft Delete를 사용한다.

```text
person
document_group
document
project
```

`deleted_at`이 설정된 데이터는 일반 조회 및 검색 Index에서 제외한다.

직무/기술 같은 연결 Table은 Profile Revision과 Audit Log가 과거 상태를 보존하므로 확정 Profile 갱신 시 실제 Row를 제거/재생성할 수 있다.

---

## 16. AI 확정 Transaction

관리자가 `[확정 및 Profile 반영]`을 수행하면 Backend Service에서 하나의 DB Transaction으로 처리한다.

```text
BEGIN

1. analysis_run 잠금 및 상태검증
2. PENDING CONFLICT/REVIEW 존재여부 확인
3. ACCEPTED/MODIFIED/MERGED 결정 반영
4. person_profile 갱신
5. person_job / person_skill / person_expertise 반영
6. project 및 프로젝트 코드관계 반영
7. employment/education/certification 반영
8. evidence_link 생성
9. profile_revision Snapshot 생성
10. analysis_run = CONFIRMED
11. audit_log 생성
12. search_index_job 생성

COMMIT
```

LLM/Embedding Runtime 호출은 이 Transaction 내부에서 수행하지 않는다.

즉 Profile 확정은 외부 AI Runtime 장애와 분리한다.

---

## 17. Alembic 적용 전략

구현 시작 시 다음 방향으로 전환한다.

```text
SQLAlchemy Model
      ↓
Alembic Revision
      ↓
PostgreSQL Schema
```

권장 구조:

```text
backend/
├ app/
│  └ db/
│     └ models/
│
└ alembic/
   └ versions/
```

첫 Migration은 `db/schema.sql`의 구조를 SQLAlchemy Model로 옮긴 뒤 생성한다.

단, 다음 항목은 Alembic Migration에서 직접 SQL을 사용할 가능성이 높다.

- `CREATE EXTENSION vector`
- `CREATE EXTENSION pg_trgm`
- Generated `tsvector`
- HNSW Vector Index
- Partial Index
- `vw_person_business_domain`
- `vw_person_customer_type`

---

## 18. Docker / Traefik 배포 시 DB 원칙

배포 구조는 [13. Application Architecture](13_application_architecture.md)의 원칙을 따른다.

```text
Traefik
 ├ Frontend
 └ API
      │
      └ talentscope-internal
          ├ postgres + pgvector
          ├ redis
          ├ minio
          └ worker
```

PostgreSQL은 기본적으로 Host Port를 외부 공개하지 않는다.

Persistent Volume 대상:

```text
PostgreSQL Data
MinIO Data
```

Redis는 업무 Data의 Source of Truth가 아니므로 영속화 정책은 운영 필요에 따라 정하되, PostgreSQL 데이터와 동일한 중요도로 취급하지 않는다.

운영 배포 시 DB Migration은 API/Worker가 동시에 자동실행하게 하지 않고 별도의 명시적 Migration Step으로 수행한다.

예:

```text
docker compose run --rm api alembic upgrade head
```

실제 명령과 Compose Profile은 배포 설계에서 최종 확정한다.

---

## 19. 구현 시 추가 검증할 사항

DDL Baseline은 현재 MVP 요구를 기준으로 FIX하되 구현 과정에서 다음은 Test Data로 검증한다.

- 동일 인물 중복판정에 필요한 Phone/Email 정규화 방식
- 동일 프로젝트 병합 Key와 유사도 판단
- 기술등급 Code 표현 및 기존 Excel 데이터 Mapping
- HWP/PPTX 페이지 번호와 Preview PDF 페이지의 일치성
- Evidence BBox 좌표체계
- 문서 Chunk Size / Overlap
- `pg_trgm + FTS + pgvector`의 실제 한국어 검색 품질
- BGE-M3 Embedding Search Threshold
- HNSW Parameter 및 인력/프로젝트 데이터 규모별 성능

---

## 20. 다음 단계

이 Schema를 기준으로 다음 단계에서 **Backend REST API 명세**를 정의한다.

우선 API Group은 다음을 기준으로 한다.

```text
/api/v1/auth
/api/v1/users
/api/v1/codes
/api/v1/people
/api/v1/uploads
/api/v1/documents
/api/v1/analyses
/api/v1/projects
/api/v1/search
```

각 API에 Request/Response Schema, 권한, 동기/비동기 여부, 관련 DB Transaction을 연결하여 정의한다.
