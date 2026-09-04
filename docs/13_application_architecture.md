# 13. Application Architecture & Backend Technology Stack

## 1. 목적

본 문서는 TalentScope 1차 MVP의 **Backend 기술스택과 전체 애플리케이션 아키텍처를 FIX**한다.

목표는 다음과 같다.

- 1차 MVP를 과도한 분산구조 없이 빠르게 개발한다.
- 문서 변환·AI 분석·임베딩 등 장시간 작업을 API 요청과 분리한다.
- 기존 ALZI LLM/VLM Runtime을 재사용한다.
- PostgreSQL + pgvector 하나로 구조화 검색, Full Text Search, Vector Search를 처리한다.
- 원본문서는 S3 호환 Object Storage에 저장한다.
- 향후 서버 배포는 **Docker Compose + Traefik Label 기반**으로 구성한다.

---

## 2. 최종 기술스택

| 영역 | FIX 기술 | 비고 |
|---|---|---|
| Frontend | React + TypeScript + Vite | SPA |
| UI | Ant Design | 사내 업무화면, Table/Form 중심 |
| Client Data | TanStack Query | API 캐시/비동기 상태 |
| Routing | React Router | 화면 Routing |
| Backend API | FastAPI / Python 3.12 | REST API + OpenAPI |
| Validation | Pydantic v2 | API 및 AI JSON Schema 검증 |
| ORM | SQLAlchemy 2.x | PostgreSQL ORM |
| Migration | Alembic | DB Schema Version 관리 |
| DB Driver | psycopg 3 | PostgreSQL Driver |
| RDB / Search | PostgreSQL + pgvector | 구조화 DB + FTS + Vector |
| Fuzzy Search | pg_trgm | 이름/회사/프로젝트 유사검색 |
| Async Job | Celery | 문서처리/AI분석/Embedding |
| Broker / Session / Cache | Redis | Celery Broker, 서버 세션 저장, 단기 Cache |
| Object Storage | MinIO | S3 호환 원본/Preview 저장 |
| HTTP Client | httpx | LLM/VLM 등 외부/내부 API 호출 |
| PDF | PyMuPDF | PDF Text/Page 처리 |
| DOCX | python-docx | 문서 Text 추출 |
| PPTX | python-pptx | Slide Text 추출 |
| HWP/HWPX | Format Adapter 방식 | HWP/HWPX 전용 Parser + 변환 fallback |
| Office Preview | LibreOffice Headless | Preview PDF 변환 |
| Reverse Proxy | Traefik | Docker Label 기반 Router/TLS |
| Deployment | Docker Compose | 1차 MVP 운영 배포 |
| Test | pytest / Vitest | Backend/Frontend Test |

### 선택하지 않는 구성

1차 MVP에서는 다음을 사용하지 않는다.

- Kubernetes
- Elasticsearch / OpenSearch
- 별도 Vector DB(Milvus/Qdrant 등)
- Kafka
- Microservice 분리
- Frontend → LLM/VLM 직접 호출

---

## 3. Architecture Style

1차 MVP는 **Modular Monolith + Async Worker** 구조로 FIX한다.

```text
사용자 Browser
     │
     ▼
  Traefik
     │
     ├──────────────► Frontend
     │                  React / Vite
     │
     └──────────────► Backend API
                        FastAPI
                           │
         ┌─────────────────┼───────────────────┐
         │                 │                   │
         ▼                 ▼                   ▼
   PostgreSQL          Redis              MinIO
   + pgvector           │              Original/Preview
         ▲              │
         │              ▼
         │           Celery Worker
         │              │
         │      ┌───────┼────────────┐
         │      │       │            │
         │      ▼       ▼            ▼
         │   Parser   LLM/VLM     Embedding
         │              │
         └──────────────┘
```

Backend 코드는 하나의 애플리케이션으로 유지하되 기능영역을 Module로 분리한다.

---

## 4. Docker 서비스 구성

초기 운영 Compose는 다음 서비스를 기준으로 한다.

```text
traefik              # 기존 서버 공용 Traefik 사용 가능

TalentScope Stack
├ frontend
├ api
├ worker
├ beat
├ postgres
├ redis
└ minio
```

### `frontend`

- React Build 결과를 제공한다.
- 외부 접근 가능.
- Traefik Router를 통해 서비스한다.

### `api`

- FastAPI Backend.
- 인증, 인력 CRUD, 문서 Metadata, AI Review, 검색 API 담당.
- 외부 접근은 Traefik을 통해서만 허용한다.

### `worker`

- API와 동일한 Backend Image를 사용한다.
- Celery Worker Process만 실행한다.
- 문서 변환, Text 추출, VLM/LLM 분석, Chunk/Embedding 작업 담당.

### `beat`

- Celery Beat.
- 임시 Upload 정리, 실패 Job 재시도 등 주기작업 담당.
- 기능이 단순하면 초기 개발 시 Worker와 함께 구성할 수 있으나 운영 Compose에서는 별도 Process를 권장한다.

### `postgres`

- PostgreSQL + pgvector.
- 외부 Host Port를 기본 공개하지 않는다.

### `redis`

- Celery Broker, Browser 서버 세션 저장소 및 짧은 TTL Cache로 사용한다.
- 업무 데이터 Source of Truth로 사용하지 않는다.
- Session 만료/무효화는 Auth Service가 관리하고 사용자 권한정보의 Source of Truth는 PostgreSQL의 `app_user`다.

### `minio`

- 원본파일, Preview PDF, 임시 Upload 저장.
- Browser가 MinIO에 직접 무제한 접근하지 않는다.
- Backend 권한검사 후 단기 Presigned URL 또는 Proxy Download 방식으로 제공한다.

---

## 5. Traefik 배포 원칙

운영 서버는 **Traefik Docker Provider + Label 기반 Routing**을 전제로 한다.

권장 Routing은 동일 Host에서 Frontend와 API를 Path로 구분한다.

```text
https://${TALENTSCOPE_HOST}/        → frontend
https://${TALENTSCOPE_HOST}/api/*  → api
```

장점:

- Frontend와 API가 동일 Origin을 사용한다.
- CORS 설정을 최소화할 수 있다.
- 인증 Cookie 처리가 단순해진다.

Compose에서는 개념적으로 다음 Label 구조를 사용한다.

```yaml
labels:
  - traefik.enable=true
  - traefik.http.routers.talentscope.rule=Host(`${TALENTSCOPE_HOST}`)
  - traefik.http.routers.talentscope.entrypoints=websecure
  - traefik.http.routers.talentscope.tls=true
```

API Router는 `PathPrefix(`/api`)` 조건과 Priority를 별도로 둔다.

실제 Domain, EntryPoint, Certificate Resolver 명칭은 서버의 기존 Traefik 설정에 맞춰 배포문서에서 확정한다.

### Docker Network

최소 두 Network를 사용한다.

```text
proxy                 # Traefik과 frontend/api가 연결되는 external network

talentscope-internal  # api/worker/postgres/redis/minio 내부통신
```

`postgres`, `redis`, `minio`, `worker`는 원칙적으로 `proxy` Network에 연결하지 않는다.

---

## 6. Backend Module Structure

Backend는 기능별 Module로 분리한다.

```text
backend/
└ app/
   ├ main.py
   ├ core/
   │  ├ config.py
   │  ├ security.py
   │  ├ logging.py
   │  └ exceptions.py
   │
   ├ db/
   │  ├ session.py
   │  ├ base.py
   │  └ models/
   │
   ├ modules/
   │  ├ auth/
   │  ├ users/
   │  ├ people/
   │  ├ codes/
   │  ├ documents/
   │  ├ analysis/
   │  ├ projects/
   │  ├ evidence/
   │  └ search/
   │
   ├ ai/
   │  ├ providers/
   │  │  ├ llm.py
   │  │  ├ vlm.py
   │  │  └ embedding.py
   │  ├ prompts/
   │  ├ schemas/
   │  └ pipeline/
   │
   ├ documents/
   │  ├ adapters/
   │  │  ├ pdf.py
   │  │  ├ docx.py
   │  │  ├ pptx.py
   │  │  ├ hwp.py
   │  │  └ hwpx.py
   │  ├ preview.py
   │  └ chunker.py
   │
   ├ tasks/
   │  ├ celery_app.py
   │  ├ document_tasks.py
   │  ├ analysis_tasks.py
   │  └ index_tasks.py
   │
   └ api/
      └ v1/
```

Controller/API Layer에서 DB와 AI Client를 직접 복잡하게 조작하지 않고 Service Layer에서 업무 로직을 처리한다.

---

## 7. API Request와 Async Job 분리

다음 작업은 HTTP Request 안에서 완료될 때까지 기다리지 않는다.

- Office/HWP Preview PDF 변환
- VLM 분석
- LLM Profile 구조화
- 다수 문서 Chunk 생성
- Embedding 생성
- 대규모 Search Index 재생성

예:

```text
POST /api/v1/analyses
        │
        ├─ analysis_run 생성
        ├─ Celery Task 발행
        └─ 202 Accepted + analysis_run_id

Worker
  → Parsing
  → VLM
  → LLM
  → Candidate JSON
  → Diff 생성
  → status = REVIEWING
```

Frontend는 상태 API를 Polling한다.

```text
GET /api/v1/analyses/{id}
```

MVP에서는 WebSocket/SSE를 필수로 하지 않는다.

---

## 8. Celery Queue 구성

논리 Queue는 다음 세 종류로 나눈다.

```text
document
analysis
index
```

### `document`

- 파일 검증
- Preview 변환
- Text/Page 추출
- Chunk 생성

### `analysis`

- VLM 호출
- LLM 구조화
- 코드 정규화
- 기존 Profile Diff 생성

### `index`

- Profile Search Text 생성
- Project Search Text 생성
- Document Chunk Embedding
- `search_index_item` UPSERT/DELETE

초기에는 Worker Container 하나가 세 Queue를 모두 소비할 수 있다.

운영 중 부하가 증가하면 동일 Image로 다음처럼 분리한다.

```text
worker-document
worker-analysis
worker-index
```

애플리케이션 구조를 변경하지 않고 Scale-out 가능하도록 한다.

---

## 9. Job Source of Truth

Celery/Redis 자체의 Result Backend를 업무상태의 Source of Truth로 사용하지 않는다.

DB 테이블을 기준으로 한다.

```text
analysis_run
search_index_job
document.processing_status
```

Redis/Celery 장애가 발생해도 현재 처리상태와 재처리 대상은 DB에서 복구할 수 있어야 한다.

### Idempotency

Worker는 동일 Task가 중복 실행되어도 데이터가 중복 생성되지 않도록 설계한다.

예:

- `(document_group_id, version_no)` Unique
- `(analysis_run_id, document_id)` Unique
- `(person_id, job_code, job_type)` Unique
- `(project_id, tech_code)` Unique
- Search Index UPSERT

---

## 10. Document Processing Architecture

문서처리는 Format Adapter 방식으로 FIX한다.

```text
Document
   │
   ▼
Format Detector
   │
   ├ PDF  ─────► PdfAdapter
   ├ DOCX ─────► DocxAdapter
   ├ PPTX ─────► PptxAdapter
   ├ HWP  ─────► HwpAdapter
   ├ HWPX ─────► HwpxAdapter
   └ Image ────► ImageAdapter / VLM
```

각 Adapter의 공통 Interface는 개념적으로 다음 기능을 제공한다.

```python
extract_text()
extract_pages()
create_preview()
extract_metadata()
```

### Preview

- PDF/Image: 원본 또는 직접 Preview
- DOC/DOCX/PPT/PPTX/HWP/HWPX: 가능한 경우 LibreOffice Headless 등으로 PDF 변환
- 원본은 항상 별도로 보존한다.

### HWP/HWPX

HWP/HWPX는 특정 Library에 시스템 전체가 강하게 결합되지 않도록 Adapter 내부로 격리한다.

- HWPX: ZIP/XML 기반 직접 추출 우선
- HWP: 전용 Parser 우선
- 실패 시 Preview 변환/VLM을 fallback으로 사용

Parser 교체가 필요해도 `documents/adapters` 영역만 변경하도록 한다.

---

## 11. AI Runtime Integration

기존 ALZI Runtime을 사용한다.

### LLM

- Runtime Artifact: `Qwen/Qwen3-14B-AWQ`
- API Model: `Qwen3-14B`
- OpenAI-compatible Chat Completions

### VLM

- Runtime Artifact: `Qwen/Qwen2.5-VL-7B-Instruct-AWQ`
- API Model: `Qwen2.5-VL-7B-Instruct`
- OpenAI-compatible multimodal endpoint

구성값은 코드에 직접 넣지 않고 Environment로 관리한다.

```text
LLM_BASE_URL
LLM_MODEL
LLM_API_KEY

VLM_BASE_URL
VLM_MODEL
VLM_API_KEY
```

같은 Docker Network에서 접근 가능하다면 내부 Endpoint를 사용하고, 별도 서버라면 HTTPS Endpoint를 사용한다.

### Provider Layer

```text
Backend Service
     │
     ▼
LLMProvider / VLMProvider
     │
     ▼
OpenAI-compatible Runtime
```

업무 코드가 특정 Model Endpoint에 직접 의존하지 않도록 한다.

### AI Response Validation

```text
LLM Response
   ↓
Pydantic Schema Validation
   ↓
Success ─────────► Candidate JSON
   │
   └ Failure
       ↓
   Repair/Retry
       ↓
   FAILED + error_message
```

Prompt와 Schema 버전을 `analysis_run`에 저장한다.

---

## 12. Embedding Architecture

Embedding Provider도 별도 Interface로 둔다.

```text
EmbeddingProvider
  ├ OpenAI-compatible Embedding API
  └ Local/Internal Embedding Runtime
```

TalentScope의 기본 Embedding Model은 기존 ALZI에서 사용 중인 **BGE-M3 계열 재사용을 우선**한다.

실제 Endpoint는 배포환경에서 설정하고 Backend 코드에는 고정하지 않는다.

Embedding 대상:

- Confirmed Person Profile
- Confirmed Project
- Document Chunk

Embedding과 FTS 데이터는 `search_index_item`에 저장한다.

---

## 13. Search Architecture

MVP 검색엔진은 PostgreSQL 안에서 구성한다.

```text
Natural Language Query
       │
       ▼
Qwen3 Query Parser
       │
       ├ required condition
       ├ preferred condition
       ├ keyword_query
       └ semantic_query
       │
       ▼
┌──────────────────────────────┐
│ PostgreSQL                   │
│                              │
│ Structured Filter           │
│ + tsvector Full Text Search │
│ + pg_trgm                   │
│ + pgvector                  │
└───────────────┬──────────────┘
                ▼
           Candidate Merge
                ▼
            Hard Filter
                ▼
        Deterministic Ranking
                ▼
             Evidence
```

### 원칙

- 등급, 경력, 직무 등 Hard Condition은 SQL 조건으로 판정한다.
- Keyword는 PostgreSQL FTS와 필요 시 `pg_trgm`을 사용한다.
- 의미 유사성은 pgvector를 사용한다.
- 여러 Project/Chunk 결과를 `person_id` 기준으로 병합한다.
- LLM이 인력 전체를 읽고 임의 점수를 부여하지 않는다.
- 최종 적합도는 Backend Rule로 계산한다.

별도 Elasticsearch/OpenSearch/Vector DB는 검색 규모가 PostgreSQL 단독 운영한계를 넘을 때 검토한다.

---

## 14. Object Storage

MinIO Bucket은 하나 또는 소수 Bucket + Prefix 구조를 사용한다.

예:

```text
talent-scope/
├ originals/{person_id}/{document_id}/...
├ previews/{document_id}/preview.pdf
└ temp/{upload_session_id}/...
```

DB에는 Binary를 저장하지 않고 `storage_key`를 저장한다.

### 원본 불변 원칙

- 원본파일은 수정하지 않는다.
- 새 문서는 새로운 `document` Version으로 저장한다.
- Preview는 언제든 다시 생성 가능한 파생 파일로 본다.

---

## 15. 인증과 API 보안

MVP Role은 기존 정의대로 유지한다.

```text
USER
ADMIN
```

MVP Browser 인증은 **ID/Password + Redis 서버 세션 + HttpOnly Cookie** 방식으로 FIX한다.

```text
app_user              Redis
   │                    │
   └ 로그인 검증        └ Session 저장 / TTL
             │
             ▼
      HttpOnly Session Cookie
```

원칙:

- Session Cookie 예: `ts_session`
- Cookie에는 Session ID만 저장한다.
- `HttpOnly=true`, 운영 HTTPS에서는 `Secure=true`, `SameSite=Lax`를 기본으로 한다.
- 사용자 Role/상태의 Source of Truth는 PostgreSQL `app_user`이며 Redis Session에 저장된 값만 신뢰하지 않는다.
- Role 변경 또는 사용자 비활성화 시 기존 Session을 무효화할 수 있어야 한다.
- 상태 변경 API에는 CSRF 방어를 적용한다.
- JWT Access/Refresh Token은 1차 MVP Browser 인증에 사용하지 않는다.
- 동일 Origin(`/`, `/api`) 구성을 기본으로 한다.

Frontend의 메뉴 숨김은 UX일 뿐 실제 권한은 Backend에서 검사한다.

### 외부 노출 서비스

Traefik을 통해 외부에 노출하는 것은 원칙적으로 다음 두 서비스뿐이다.

```text
frontend
api
```

PostgreSQL, Redis, MinIO 내부 API, Worker는 직접 공개하지 않는다.

---

## 16. Transaction Boundary

AI 분석 완료와 Profile 확정은 분리한다.

### AI 분석

```text
Worker
  → candidate_json 저장
  → analysis_diff_item 저장
  → analysis_run = REVIEWING
```

Confirmed Profile은 변경하지 않는다.

### 사용자 확정

```text
BEGIN
  Review 상태검사
  Profile 갱신
  Job/Skill/EXP 갱신
  Project 생성/병합
  Evidence Link 생성
  Profile Revision 생성
  Audit Log 생성
  Search Index Job 등록
  analysis_run = CONFIRMED
COMMIT
```

Embedding 생성은 Transaction 밖의 Worker에서 수행한다.

LLM/Embedding Runtime 장애 때문에 Profile 확정 Transaction이 Rollback되지 않도록 한다.

---

## 17. Configuration & Secret

Environment 기반으로 설정한다.

예:

```text
APP_ENV
APP_SECRET_KEY
DATABASE_URL
REDIS_URL

SESSION_COOKIE_NAME
SESSION_TTL_SECONDS
CSRF_COOKIE_NAME

S3_ENDPOINT
S3_ACCESS_KEY
S3_SECRET_KEY
S3_BUCKET

LLM_BASE_URL
LLM_API_KEY
LLM_MODEL

VLM_BASE_URL
VLM_API_KEY
VLM_MODEL

EMBEDDING_BASE_URL
EMBEDDING_API_KEY
EMBEDDING_MODEL

TALENTSCOPE_HOST
```

실제 `.env`와 Secret 값은 Git에 Commit하지 않는다.

운영 시 Docker Secret 또는 서버에서 관리하는 Environment File 방식으로 전환할 수 있게 한다.

---

## 18. Health Check

Backend는 `/api/v1` Base Path 기준으로 최소 두 Health Endpoint를 제공한다.

```text
GET /api/v1/health/live
GET /api/v1/health/ready
```

### live

Process가 정상적으로 실행 중인지 확인한다.

### ready

MVP Readiness의 필수 의존성은 다음 두 개다.

- PostgreSQL
- Redis

판정 규칙:

```text
PostgreSQL OK + Redis OK → ready / HTTP 200
PostgreSQL FAIL          → not_ready / HTTP 503
Redis FAIL               → not_ready / HTTP 503
```

MinIO와 LLM/VLM/Embedding Runtime은 기본 Readiness 판정에 포함하지 않고 기능상태/운영상태에서 별도로 관리한다. AI Runtime이 일시 장애여도 기존 인력 조회와 DB 검색은 계속 가능해야 한다.

---

## 19. Logging / Audit / Observability

MVP 기본:

- Backend Structured Log
- Worker Structured Log
- Request ID / Correlation ID
- `audit_log` DB 기록
- `analysis_run.error_message`
- `search_index_job.error_message`

향후 고도화:

- Prometheus
- Grafana
- Loki
- Sentry 등 Error Tracking

관측도구는 MVP 필수조건으로 두지 않는다.

---

## 20. Repository 구조

최종 Repository 구조는 다음 방향으로 사용한다.

```text
talent-scope/
├ README.md
├ docs/
├ frontend/
├ backend/
├ infra/
│  ├ docker/
│  └ traefik/
├ scripts/
├ docker-compose.yml
├ docker-compose.dev.yml
└ .env.example
```

### `infra/`

추후 다음을 포함한다.

- Dockerfile
- Compose 설정
- Traefik Label 예제
- PostgreSQL Init/Extension 설정
- MinIO Bucket 초기화
- Backup/Restore Script

---

## 21. Development / Production 차이

### 개발환경

```text
Frontend Dev Server
FastAPI --reload
PostgreSQL
Redis
MinIO
Worker
```

LLM/VLM은 기존 ALZI HTTPS 또는 내부 Endpoint를 사용한다.

### 운영환경

```text
Traefik
 ├ frontend container
 └ api container

Internal
 ├ worker
 ├ beat
 ├ postgres + pgvector
 ├ redis
 └ minio
```

Frontend/API Container는 Immutable Image로 Build한다.

---

## 22. 향후 Scale-out 기준

MVP에서는 Microservice로 나누지 않지만 Container Scale-out이 가능한 구조로 만든다.

우선 확장 순서:

```text
1. worker-analysis 개수 증가
2. worker-document / analysis / index Queue 분리
3. API Replica 증가
4. PostgreSQL Tuning
5. 필요 시 Search Engine 분리
```

서비스별 코드 Repository를 분리하는 것은 이 시점 이후에 검토한다.

---

## 23. 최종 FIX 사항

1. Backend는 **FastAPI + Python 3.12**를 사용한다.
2. DB는 **PostgreSQL + pgvector** 하나를 Source of Truth 및 MVP Search Engine으로 사용한다.
3. ORM/Migration은 **SQLAlchemy 2.x + Alembic**을 사용한다.
4. 장시간 작업은 **Celery + Redis Worker**로 분리한다.
5. 원본문서/Preview는 **MinIO(S3 Compatible)** 에 저장한다.
6. Backend는 **Modular Monolith**로 시작하고 Microservice는 도입하지 않는다.
7. Frontend는 **React + TypeScript + Vite + Ant Design + TanStack Query**를 사용한다.
8. LLM/VLM은 Provider Layer를 통해 기존 **ALZI OpenAI-compatible Runtime**을 호출한다.
9. Embedding Provider를 분리하고 **BGE-M3 계열 재사용을 우선**한다.
10. 검색은 **SQL Hard Filter + PostgreSQL FTS + pg_trgm + pgvector + Backend Ranking**으로 구성한다.
11. LLM은 Query 해석/구조화/설명을 담당하며 최종 적합도 점수를 임의 계산하지 않는다.
12. Candidate AI 결과와 Confirmed 운영 DB는 분리한다.
13. Profile 확정 Transaction과 Embedding/Index 작업은 분리한다.
14. 운영 배포는 **Docker Compose + Traefik Label** 구조를 전제로 한다.
15. 외부에는 `frontend`와 `api`만 노출하고 DB/Redis/MinIO/Worker는 내부 Network에 둔다.
16. 동일 Host에서 `/` → Frontend, `/api` → Backend Routing을 기본안으로 한다.
17. Browser 인증은 **Redis Server Session + HttpOnly Cookie** 방식으로 사용하며 JWT Access/Refresh Token은 1차 MVP에 사용하지 않는다.
18. Health Endpoint는 `/api/v1/health/live`, `/api/v1/health/ready`로 통일하고 PostgreSQL/Redis를 필수 Readiness 의존성으로 둔다.
19. Kubernetes, Elasticsearch/OpenSearch, 별도 Vector DB, Kafka는 1차 MVP에서 도입하지 않는다.

이 Architecture를 기준으로 이후 PostgreSQL DDL, Backend REST API, Docker/Traefik 배포설계를 진행한다.