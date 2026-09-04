# TalentScope Agent Rules

이 문서는 TalentScope 저장소에서 작업하는 Cursor/AI Coding Agent의 작업 규칙이다.
기존 설계문서를 Source of Truth로 사용하고, 아키텍처나 기능범위를 임의로 변경하지 않는다.

## 1. Source of Truth

구현 작업 전 관련 설계문서를 반드시 확인한다.

우선순위가 높은 문서:

- README.md
- docs/02_mvp_scope.md
- docs/03_functional_requirements.md
- docs/04_code_taxonomy.md
- docs/10_ia.md
- docs/11_wireframes.md
- docs/12_database_erd.md
- docs/13_application_architecture.md
- docs/14_database_ddl.md
- docs/15_backend_api.md
- db/schema.sql

문서 간 충돌이 있는 경우:

- 최신 설계문서를 우선 참고한다.
- DB 구조는 `docs/14_database_ddl.md`와 `db/schema.sql`을 기준으로 한다.
- API 구조는 `docs/15_backend_api.md`를 기준으로 한다.
- 아키텍처는 `docs/13_application_architecture.md`를 기준으로 한다.

설계를 코드에서 임의로 수정하지 않는다.

설계 변경이 필요하다고 판단하면:

1. 구현하지 않는다.
2. 문제점과 제안사항을 TODO 또는 작업결과에 보고한다.
3. 사용자의 승인을 받은 후 변경한다.

## 2. Fixed Architecture

1차 MVP 아키텍처는 아래 기준을 유지한다.

Backend:

- Python 3.12
- FastAPI
- Pydantic v2
- SQLAlchemy 2.x
- Alembic
- psycopg 3

Frontend:

- React
- TypeScript
- Vite
- Ant Design
- TanStack Query
- React Router

Data:

- PostgreSQL 16+
- pgvector
- pg_trgm
- pgcrypto

Async:

- Celery
- Redis

Object Storage:

- MinIO / S3 Compatible

Deployment:

- Docker Compose
- Traefik Docker Provider
- Traefik Label 기반 Routing

Architecture Style:

- Modular Monolith + Async Worker

임의로 Microservice 구조로 분리하지 않는다.

## 3. Explicitly Out of MVP

다음 기능/기술을 임의로 추가하지 않는다.

기능:

- 사업별 인력요청 및 후보 Pipeline
- 인력 투입/배치 Workflow
- 고객 제안/확정 Workflow
- 소싱업체 관리
- 계약/정산
- 세부 개인정보 ACL
- 부서/프로젝트별 권한체계

기술:

- Kubernetes
- Kafka
- Elasticsearch / OpenSearch
- Milvus / Qdrant 등 별도 Vector DB
- 불필요한 Microservice
- 별도 API Gateway 제품

필요성이 있다고 판단하면 구현하지 말고 제안사항으로만 보고한다.

## 4. AI Data Rules

AI Candidate와 Confirmed Profile은 반드시 분리한다.

기본 Flow:

Document
→ AI Candidate
→ Diff
→ User Review
→ Confirmed Profile

AI 분석결과가 Confirmed Profile을 자동으로 덮어쓰면 안 된다.

AI Candidate는 기본적으로:

- `analysis_run.candidate_json`
- `analysis_diff_item`

을 기준으로 관리한다.

확정 후에만:

- person_profile
- person_job
- person_skill
- person_expertise
- project
- education
- certification

등 운영 테이블에 반영한다.

## 5. Taxonomy Rules

아래 분류체계를 혼동하지 않는다.

- JOB: 직무/역할
- TECH: 실제 기술/제품/프레임워크/언어
- EXP: 전문분야/업무역량
- BIZ: 사업/산업 도메인
- CUSTOMER_TYPE: 고객/기관 유형

중요 예시:

RAG는 TECH가 아니라 EXP이다.
LLM, RAG, AI Agent는 EXP로 관리한다.

Python, Java, FastAPI, Qwen, PostgreSQL, Oracle 등은 TECH이다.

AI개발자, PL, DBA 등은 JOB이다.

## 6. Database Rules

- SQLAlchemy 2.x Declarative Model을 사용한다.
- Schema 변경이력은 Alembic으로 관리한다.
- PostgreSQL을 업무 데이터 Source of Truth로 사용한다.
- Redis/Celery Result Backend를 업무상태 Source of Truth로 사용하지 않는다.
- 원본문서를 PostgreSQL BLOB으로 저장하지 않는다.
- Original File은 MinIO/S3에 저장하고 DB에는 storage_key만 저장한다.
- Search Index와 Embedding은 재생성 가능한 파생 데이터로 취급한다.
- Soft Delete가 정의된 주요 Entity는 물리삭제를 기본으로 하지 않는다.
- 기존 Unique Constraint와 Idempotency 원칙을 깨지 않는다.

## 7. Backend Coding Rules

API Router에 복잡한 업무로직을 작성하지 않는다.

권장 Layer:

Router/API
→ Service
→ Repository/DB
→ Provider/External System

다음 원칙을 따른다.

- API Schema와 DB Model을 분리한다.
- Pydantic Schema Validation을 사용한다.
- Service Layer에서 Transaction Boundary를 관리한다.
- External AI Runtime 호출은 Provider Layer로 격리한다.
- LLM/VLM Endpoint를 업무 코드에 직접 Hard Coding하지 않는다.
- 환경변수로 설정한다.
- Secret/API Key를 Git에 Commit하지 않는다.

## 8. Async Job Rules

장시간 작업은 API Request와 분리한다.

비동기 대상 예:

- 문서 변환
- Text/Page 추출
- VLM 분석
- LLM Profile 구조화
- Chunk 생성
- Embedding 생성
- Search Index 재생성

Celery Queue는 기본적으로:

- document
- analysis
- index

구조를 따른다.

업무 상태는 다음 DB 값 기준으로 판단한다.

- document.processing_status
- analysis_run.status
- search_index_job.status

Worker는 중복 실행에 대해 Idempotent하게 작성한다.

## 9. Search Rules

검색은 다음 구조를 유지한다.

Structured DB Search
+ PostgreSQL FTS
+ pg_trgm
+ pgvector
→ Person 기준 병합
→ Hard Filter
→ Backend Ranking
→ Evidence

LLM이 임의로 Candidate 전체에 점수를 부여하지 않는다.

Hard Condition은 SQL/구조화 DB 기준으로 판단한다.

적합도는 Backend의 결정적 Ranking Rule로 계산한다.

LLM은:

- 자연어 검색조건 해석
- 조건 정규화
- 검색결과 설명

용도로만 사용한다.

## 10. Evidence Rules

인력정보와 프로젝트는 가능한 경우 원본문서 Evidence로 연결한다.

목표 Drill-down:

Search Result
→ Person
→ Skill / Expertise / Project
→ Evidence
→ Document
→ Page

Evidence 연결을 제거하거나 단순 문자열로만 축소하지 않는다.

## 11. Frontend Rules

화면구조는 아래 문서를 기준으로 한다.

- docs/10_ia.md
- docs/11_wireframes.md

Frontend에서 LLM/VLM Runtime을 직접 호출하지 않는다.

API 호출은 Backend `/api/v1`을 통해 수행한다.

Ant Design Component를 우선 사용한다.

Server State는 TanStack Query를 사용한다.

설계에 없는 복잡한 State Management Framework를 임의로 추가하지 않는다.

## 12. Security Rules

- Secret을 Source Code에 Hard Coding하지 않는다.
- `.env`를 Commit하지 않는다.
- Backend에서 실제 RBAC을 검사한다.
- Frontend 메뉴 숨김만으로 권한을 처리하지 않는다.
- 일반사용자/관리자 권한은 기존 설계를 따른다.
- 주민등록번호, 계좌번호, 신분증번호 등 불필요한 민감정보를 구조화/임베딩하지 않는다.

## 13. Deployment Rules

운영 배포는 향후 다음 구조를 전제로 한다.

Traefik
→ frontend
→ api

internal network:

- api
- worker
- postgres
- redis
- minio

PostgreSQL/Redis/MinIO/Worker는 기본적으로 외부에 직접 노출하지 않는다.

Docker Compose + Traefik Label 기반 구조를 방해하는 구현을 하지 않는다.

현재 Cloud Agent 개발환경과 운영 Docker 배포환경은 별개일 수 있으므로,
개발환경 편의를 위해 운영 아키텍처를 변경하지 않는다.

## 14. Scope Control

사용자가 요청한 작업범위만 구현한다.

예를 들어 Skeleton 구현 요청이면:

- Person CRUD
- Document Workflow
- AI Analysis
- Search

까지 임의로 구현하지 않는다.

다음 단계 구현은 사용자의 별도 요청 후 진행한다.

## 15. Work Completion Report

작업 완료 후 반드시 아래 항목을 보고한다.

- 변경/추가 파일 목록
- 구현 내용
- 실행 방법
- 테스트 결과
- 설계문서와 다른 점
- 발견한 문제
- TODO
- 다음 작업 후보

설계상 불확실성을 숨기지 않는다.
