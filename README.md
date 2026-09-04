# TalentScope

AI 기반 인력 프로필 관리·검색 시스템입니다.

TalentScope는 사내에서 보유한 이력서, 경력기술서, 인력 프로필 등의 문서를 시스템에 등록하고, 기 구축된 LLM/VLM Runtime을 활용해 인력 정보를 구조화한 뒤 조건검색·자연어검색·임베딩 기반 의미검색으로 적합한 인력을 찾는 것을 목표로 합니다.

## 1차 MVP 목표

1차 MVP는 다음 흐름까지를 범위로 합니다.

```text
문서 업로드
  → 원본 파일 저장
  → Parser/VLM 문서 인식
  → LLM 프로필 구조화
  → 기존 인력/프로필 비교
  → 사용자 검토·확정
  → Profile DB 반영
  → 임베딩 생성
  → 조건검색 / 자연어검색 / Hybrid 검색
  → 관련 인력 및 근거 조회
```

### MVP 포함

- 이력서·프로필·경력기술서 등 문서 업로드 및 원본 관리
- HWP/HWPX, DOC/DOCX, PDF, PPT/PPTX, JPG/PNG 등 주요 형식 지원
- Parser + VLM + LLM 기반 문서 분석 및 Profile JSON 구조화
- JOB / TECH / EXP / BIZ / CUSTOMER_TYPE 표준 코드 및 Alias 정규화
- AI 분석 결과와 기존 DB 비교 및 사용자 확정
- 인력 상세 프로필, 프로젝트 경력, 학력, 자격, 첨부문서 관리
- 조건 기반 검색
- 자연어 기반 검색조건 생성
- DB + Keyword + Vector 기반 Hybrid Search
- 후보 Ranking, 추천 근거 및 원본문서 연결
- 일반사용자/관리자 2단계 권한

### MVP 제외 / 고도화 범위

- 사업별 인력요청 및 후보 Pipeline
- 고객 제안, 확정, 실제 투입 관리
- 소싱업체·외부인력 Workflow
- 계약·정산
- 개인정보 마스킹 및 세부 필드 ACL
- 부서/프로젝트별 세분화 권한

## AI Runtime

기 구축된 ALZI Runtime을 공유하여 사용합니다.

### LLM

- Runtime Artifact: `Qwen/Qwen3-14B-AWQ`
- API Model Name: `Qwen3-14B`
- Backend Container: `http://vllm-qwen3:8000`
- External HTTPS: `https://alzi-llm.openlink.kr`

주요 역할: 자연어 검색조건 분석, 코드 정규화 보조, Profile 구조화, 검색 결과 설명 생성.

### VLM

- Runtime Artifact: `Qwen/Qwen2.5-VL-7B-Instruct-AWQ`
- API Model Name: `Qwen2.5-VL-7B-Instruct`
- Backend Container: `http://vllm-qwen25-vl:8000`
- External HTTPS: `https://alzi-vlm.openlink.kr`

주요 역할: 이미지형/스캔 문서, 표 구조, 이미지 자격증 등 시각 정보 인식.

> Frontend는 LLM/VLM Runtime을 직접 호출하지 않고 TalentScope Backend API를 통해 사용합니다.

## 설계 문서

- [01. Overview](docs/01_overview.md)
- [02. MVP Scope](docs/02_mvp_scope.md)
- [03. Functional Requirements](docs/03_functional_requirements.md)
- [04. Code Taxonomy](docs/04_code_taxonomy.md)
- [05. Document Management](docs/05_document_management.md)
- [06. AI Profile Analysis](docs/06_ai_profile_analysis.md)
- [07. Profile Management](docs/07_profile_management.md)
- [08. Search & Recommendation](docs/08_search_recommendation.md)
- [09. Security & Permission](docs/09_security_permission.md)
- [10. Information Architecture](docs/10_ia.md)
- [11. Core Screen Wireframes](docs/11_wireframes.md)
- [12. Database ERD & Core Tables](docs/12_database_erd.md)
- [13. Application Architecture & Backend Stack](docs/13_application_architecture.md)
- [14. PostgreSQL DDL Baseline](docs/14_database_ddl.md)
- [15. Backend REST API Specification](docs/15_backend_api.md)

실행 가능한 1차 MVP 기준 PostgreSQL Schema 초안은 [`db/schema.sql`](db/schema.sql)에 정리합니다. 구현 착수 이후 Schema 변경은 SQLAlchemy + Alembic Migration으로 관리합니다.

`15_backend_api.md`에서는 인증·사용자·코드·인력·업로드·문서·AI 분석·프로젝트·근거·통합검색·운영상태 API의 Endpoint, 권한, Request/Response, 비동기 처리와 상태코드를 FIX합니다.

향후 배포 설계는 **Docker Compose + Traefik Label 기반 라우팅**을 전제로 하며, Frontend/Backend/Worker/PostgreSQL(pgvector)/Redis/MinIO의 서비스·네트워크·Persistent Volume 구성을 별도 문서에서 구체화할 예정입니다.
