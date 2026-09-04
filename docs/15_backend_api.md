# 15. Backend REST API Specification

## 1. 목적

본 문서는 TalentScope 1차 MVP의 FastAPI Backend REST API 계약을 FIX한다.

기준 문서:

- [12. Database ERD & Core Tables](12_database_erd.md)
- [13. Application Architecture & Backend Stack](13_application_architecture.md)
- [14. PostgreSQL DDL Baseline](14_database_ddl.md)

API는 다음 핵심 흐름을 지원한다.

```text
로그인
  → 신규 인력 문서 업로드
  → 최소 식별정보 분석 / 중복 확인
  → 신규 Person 생성 또는 기존 Person 연결
  → 상세 AI 분석
  → Diff 검토 / 확정
  → Confirmed Profile 반영
  → Search Index 비동기 갱신
  → 인력 목록 / 상세 조회
  → 자연어 조건해석 / Hybrid Search
  → Evidence / 원본문서 Drill-down
```

---

## 2. 공통 API 규칙

### Base Path

```text
/api/v1
```

Traefik에서는 동일 Origin으로 다음과 같이 라우팅한다.

```text
https://${TALENTSCOPE_HOST}/        → Frontend
https://${TALENTSCOPE_HOST}/api/*  → FastAPI
```

Frontend와 Backend는 동일 Origin 사용을 기본으로 하며 CORS는 기본적으로 비활성화한다.

### 데이터 형식

- 일반 Request/Response: `application/json`
- 파일 업로드: `multipart/form-data`
- UUID: 문자열 UUID
- 날짜: `YYYY-MM-DD`
- 일시: ISO 8601 + timezone
- DB 내부 Enum 값은 영문 대문자 코드를 사용한다.

### API Response

단일 Resource 예:

```json
{
  "data": {
    "id": "5d9d...",
    "name": "홍길동"
  }
}
```

목록 예:

```json
{
  "data": [],
  "meta": {
    "page": 1,
    "page_size": 20,
    "total": 127,
    "total_pages": 7
  }
}
```

### 오류 응답

오류는 `application/problem+json` 스타일로 통일한다.

```json
{
  "type": "https://talentscope/errors/profile-version-conflict",
  "title": "Profile version conflict",
  "status": 409,
  "code": "PROFILE_VERSION_CONFLICT",
  "detail": "다른 사용자가 프로필을 먼저 수정했습니다.",
  "instance": "/api/v1/analyses/abc/confirm"
}
```

`code`는 Frontend가 분기처리할 수 있는 안정적인 Application Error Code다.

### 공통 HTTP Status

| Status | 의미 |
|---|---|
| `200` | 조회/수정/업무처리 성공 |
| `201` | Resource 생성 성공 |
| `202` | 비동기 작업 접수 성공 |
| `204` | Response Body 없는 삭제/로그아웃 성공 |
| `400` | 업무규칙 위반 또는 잘못된 요청 |
| `401` | 로그인 필요 |
| `403` | 권한 없음 |
| `404` | Resource 없음 |
| `409` | Version/상태/중복 충돌 |
| `413` | 업로드 용량 초과 |
| `415` | 지원하지 않는 파일형식 |
| `422` | Pydantic Validation 실패 |
| `500` | 내부 오류 |
| `503` | 일시적 서비스/AI Runtime 장애 |

---

## 3. 인증 및 세션 FIX

MVP Browser 인증은 **서버 세션 + HttpOnly Cookie** 방식으로 FIX한다.

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
- `HttpOnly=true`
- `Secure=true` (운영 HTTPS)
- `SameSite=Lax`
- Session ID만 Cookie에 저장하고 사용자 권한정보 자체를 신뢰 가능한 Client Token에 넣지 않는다.
- Session 만료시간은 환경변수로 설정한다.
- Role 변경/사용자 비활성화 시 기존 Session을 무효화할 수 있어야 한다.
- 상태 변경 API는 CSRF 방어를 적용한다. 예: `ts_csrf` + `X-CSRF-Token` Double Submit 또는 동등한 방식.
- 비밀번호는 Backend에서 안전한 Password Hash로 검증한다.

### Auth API

| Method | Endpoint | 권한 | 설명 |
|---|---|---|---|
| `POST` | `/auth/login` | Public | 로그인 |
| `POST` | `/auth/logout` | Login | 현재 세션 종료 |
| `GET` | `/auth/me` | Login | 로그인 사용자 조회 |

#### `POST /auth/login`

Request:

```json
{
  "login_id": "admin",
  "password": "********"
}
```

Response `200`:

```json
{
  "data": {
    "id": "...",
    "login_id": "admin",
    "name": "관리자",
    "role": "ADMIN"
  }
}
```

로그인 실패는 상세 원인을 노출하지 않고 `401 INVALID_CREDENTIALS`로 통일한다.

---

## 4. 권한 모델

Role은 두 개만 사용한다.

```text
USER
ADMIN
```

### USER

- 인력 목록/상세 조회
- 연락처/이메일 조회
- 문서 미리보기/다운로드
- 조건검색/자연어검색
- 검색근거 조회

### ADMIN

USER 권한 전체 +

- 인력 생성/수정/상태변경
- 문서 업로드/삭제/복원
- AI 분석/재분석
- AI Diff 검토/확정
- 프로젝트 직접 관리
- 코드 관리
- 사용자 관리

권한검사는 Router 표시 여부와 별개로 Backend Service/API에서 반드시 수행한다.

---

## 5. 사용자 관리 API

모두 ADMIN 전용이다.

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/users` | 사용자 목록 |
| `POST` | `/users` | 사용자 생성 |
| `GET` | `/users/{user_id}` | 사용자 상세 |
| `PATCH` | `/users/{user_id}` | 이름/Role/상태 수정 |
| `POST` | `/users/{user_id}/reset-password` | 비밀번호 초기화 |

`GET /users` Query 예:

```text
?q=홍길동&role=ADMIN&status=ACTIVE&page=1&page_size=20
```

사용자를 비활성화하면 신규 로그인뿐 아니라 기존 활성 Session도 무효화한다.

---

## 6. 코드 관리 API

지원 Code Type:

```text
JOB
TECH
EXP
BIZ
CUSTOMER_TYPE
DOC_TYPE
```

| Method | Endpoint | 권한 | 설명 |
|---|---|---|---|
| `GET` | `/codes` | USER | 코드 검색/조회 |
| `GET` | `/codes/{code}` | USER | 코드 상세 |
| `POST` | `/codes` | ADMIN | 코드 생성 |
| `PATCH` | `/codes/{code}` | ADMIN | 코드/표준명/상위/활성상태 수정 |
| `PUT` | `/codes/{code}/aliases` | ADMIN | Alias 전체 교체 |

`GET /codes` Query:

```text
?type=TECH&q=python&parent_code=TECH-LANG&active=true
```

Response 예:

```json
{
  "data": [
    {
      "code": "JOB-AI-DEV",
      "type": "JOB",
      "name": "AI개발자",
      "parent_code": "JOB-AI",
      "aliases": ["AI Engineer", "AI Developer"],
      "is_active": true
    }
  ]
}
```

---

## 7. 인력 목록/상세 API

### Endpoint

| Method | Endpoint | 권한 | 설명 |
|---|---|---|---|
| `GET` | `/people` | USER | 관리용 인력 목록 |
| `POST` | `/people` | ADMIN | 문서 없는 인력 직접 생성(보조 기능) |
| `GET` | `/people/{person_id}` | USER | 인력 상세 Summary |
| `PATCH` | `/people/{person_id}` | ADMIN | 상태 변경 |
| `PATCH` | `/people/{person_id}/profile` | ADMIN | 기본 Profile 수정 |
| `PUT` | `/people/{person_id}/jobs` | ADMIN | 직무 Set 저장 |
| `PUT` | `/people/{person_id}/skills` | ADMIN | 기술 Set 저장 |
| `PUT` | `/people/{person_id}/expertise` | ADMIN | 전문분야 Set 저장 |
| `GET` | `/people/{person_id}/revisions` | ADMIN | Profile Revision 조회 |

### `GET /people`

인력 목록 화면 전용 Query다. 복잡한 AI 추천은 `/search/people`을 사용한다.

Query 예:

```text
?q=Oracle
&job_codes=JOB-DATA-DBA
&grade=SPECIAL
&tech_codes=TECH-DB-ORACLE
&exp_codes=EXP-DATA-DB-TUNING
&affiliation=ABC
&freshness=STALE
&analysis_status=REVIEWING
&sort=updated_desc
&page=1
&page_size=20
```

기본 AND/OR:

- 서로 다른 Filter Category: AND
- 같은 Category의 복수값: OR

### `GET /people/{person_id}`

상세 화면 Header/프로필 탭에 필요한 Aggregate를 한 번에 반환한다.

```json
{
  "data": {
    "id": "...",
    "status": "ACTIVE",
    "profile_version": 7,
    "profile": {
      "name": "홍길동",
      "phone": "010-1234-5678",
      "email": "hong@example.com",
      "address_region": "서울",
      "affiliation_company": "ABC테크",
      "technical_grade": "SPECIAL",
      "career_confirmed_months": 223,
      "profile_summary": "...",
      "profile_updated_at": "2026-09-04T13:00:00+09:00"
    },
    "jobs": [],
    "skills": [],
    "expertise": [],
    "business_domains": [],
    "customer_types": [],
    "recent_projects": [],
    "document_summary": {
      "count": 4,
      "latest_document_at": "2026-09-04T12:00:00+09:00"
    },
    "pending_analysis": null
  }
}
```

### Optimistic Lock

직접 Profile 수정 시 `expected_profile_version`을 필수로 전달한다.

```json
{
  "expected_profile_version": 7,
  "technical_grade": "SPECIAL",
  "career_confirmed_months": 223
}
```

현재 DB Version이 다르면:

```text
409 PROFILE_VERSION_CONFLICT
```

으로 처리한다.

---

## 8. 신규 등록용 Upload Session API

신규 인력 등록 Wizard는 `UPLOAD_SESSION`을 중심으로 동작한다.

### Flow

```text
POST upload-session
  → POST files
  → 문서종류 수정
  → POST identify
  → GET session polling
  → 중복 후보 확인
  → POST resolve
  → 정식 Person/Document 생성
  → POST analysis
```

### Endpoint

| Method | Endpoint | 권한 | 설명 |
|---|---|---|---|
| `POST` | `/upload-sessions` | ADMIN | 임시 등록 세션 생성 |
| `GET` | `/upload-sessions/{id}` | ADMIN | 세션/식별상태/중복후보 조회 |
| `POST` | `/upload-sessions/{id}/files` | ADMIN | 다중 파일 업로드 |
| `PATCH` | `/upload-sessions/{id}/files/{file_id}` | ADMIN | 문서종류 수정 |
| `DELETE` | `/upload-sessions/{id}/files/{file_id}` | ADMIN | 임시 파일 제거 |
| `POST` | `/upload-sessions/{id}/identify` | ADMIN | 최소 식별정보 AI 분석 시작 |
| `POST` | `/upload-sessions/{id}/resolve` | ADMIN | 신규/기존 Person 결정 및 Document 확정 |
| `DELETE` | `/upload-sessions/{id}` | ADMIN | 등록 취소 |

### `POST /upload-sessions`

Request:

```json
{
  "target_person_id": null
}
```

`target_person_id`가 있으면 기존 인력의 `+ 문서 추가` Flow로 사용할 수 있다.

### 파일 업로드

```text
POST /upload-sessions/{id}/files
Content-Type: multipart/form-data
```

다중 `files`를 허용한다.

Response `201`:

```json
{
  "data": [
    {
      "temp_file_id": "...",
      "original_filename": "홍길동_이력서.hwp",
      "file_size": 1840000,
      "document_type_code": "DOC-RESUME",
      "document_type_suggested": true,
      "validation_status": "VALID",
      "duplicate": null
    }
  ]
}
```

파일 Size/개수 제한은 환경설정으로 관리하고 초과 시 `413`을 반환한다.

### `POST /upload-sessions/{id}/identify`

AI 호출이 포함되므로 비동기로 처리한다.

Response `202`:

```json
{
  "data": {
    "upload_session_id": "...",
    "status": "IDENTIFYING"
  }
}
```

Frontend는 `GET /upload-sessions/{id}`를 Polling한다.

완료 예:

```json
{
  "data": {
    "id": "...",
    "status": "IDENTIFIED",
    "identity": {
      "name": "홍길동",
      "company": "ABC테크",
      "phone": "010-1234-5678",
      "email": "hong@example.com"
    },
    "duplicate_candidates": [
      {
        "person_id": "...",
        "name": "홍길동",
        "company": "ABC테크",
        "match_reasons": ["EMAIL_EXACT", "PHONE_EXACT"],
        "score": 1.0
      }
    ]
  }
}
```

중복 점수는 자동 병합 근거가 아니며 사용자가 최종 결정한다.

### `POST /upload-sessions/{id}/resolve`

신규 인력 생성:

```json
{
  "mode": "CREATE_NEW",
  "identity": {
    "name": "홍길동",
    "company": "ABC테크",
    "phone": "010-1234-5678",
    "email": "hong@example.com"
  },
  "document_resolution": [
    {
      "temp_file_id": "...",
      "mode": "NEW_GROUP"
    }
  ]
}
```

기존 인력 연결:

```json
{
  "mode": "LINK_EXISTING",
  "person_id": "...",
  "document_resolution": [
    {
      "temp_file_id": "...",
      "mode": "NEW_VERSION",
      "document_group_id": "..."
    }
  ]
}
```

Response `201`:

```json
{
  "data": {
    "person_id": "...",
    "document_ids": ["...", "..."],
    "profile_version": 1
  }
}
```

`resolve`는 Person/Document 승격을 하나의 DB Transaction으로 수행하고 실패 시 전체 Rollback한다.

---

## 9. 문서 API

| Method | Endpoint | 권한 | 설명 |
|---|---|---|---|
| `GET` | `/people/{person_id}/documents` | USER | 인력 문서 목록 |
| `GET` | `/documents/{document_id}` | USER | Document Metadata |
| `GET` | `/documents/{document_id}/preview` | USER | Preview PDF/Image Inline 응답 |
| `GET` | `/documents/{document_id}/download` | USER | 원본 다운로드 |
| `GET` | `/document-groups/{group_id}/versions` | USER | 버전 목록 |
| `DELETE` | `/documents/{document_id}` | ADMIN | Soft Delete |
| `POST` | `/documents/{document_id}/restore` | ADMIN | 복원 |

### Preview / Download

MVP는 **Backend 권한검사 후 Stream/Proxy 응답**을 기본으로 FIX한다.

- `/preview`: `Content-Disposition: inline`
- `/download`: `Content-Disposition: attachment`
- 다운로드는 `audit_log`에 기록한다.
- PDF Viewer를 위해 HTTP Range Request를 지원하는 구현을 권장한다.
- Object Storage를 Browser에 직접 공개하지 않는다.

운영 부하가 커지면 추후 짧은 TTL의 Presigned URL 방식으로 변경 가능하다.

---

## 10. AI 분석 API

### Endpoint

| Method | Endpoint | 권한 | 설명 |
|---|---|---|---|
| `GET` | `/analyses` | ADMIN | AI 검토 Queue/상태 목록 |
| `POST` | `/analyses` | ADMIN | 상세 Profile 분석 시작 |
| `GET` | `/analyses/{analysis_id}` | ADMIN | 분석상태/Summary 조회 |
| `GET` | `/analyses/{analysis_id}/diffs` | ADMIN | Diff 목록 |
| `PATCH` | `/analyses/{analysis_id}/diffs/{diff_id}` | ADMIN | 단일 검토 결정 |
| `POST` | `/analyses/{analysis_id}/diffs/bulk` | ADMIN | 선택 Diff 일괄 결정 |
| `POST` | `/analyses/{analysis_id}/confirm` | ADMIN | Profile 최종 반영 |
| `POST` | `/analyses/{analysis_id}/retry` | ADMIN | 실패 Run 재처리 |

### `POST /analyses`

```json
{
  "person_id": "...",
  "document_ids": ["...", "...", "..."],
  "analysis_type": "PROFILE"
}
```

Response `202`:

```json
{
  "data": {
    "analysis_id": "...",
    "status": "QUEUED"
  }
}
```

AI 처리 단계는 Worker가 수행한다.

```text
QUEUED
 → PROCESSING
 → REVIEWING
 → CONFIRMED

실패 시
 → FAILED
```

### `GET /analyses`

Query 예:

```text
?status=REVIEWING&page=1&page_size=20&sort=completed_desc
```

Queue Response에는 다음 Summary를 포함한다.

```json
{
  "analysis_id": "...",
  "person": {"id": "...", "name": "홍길동"},
  "documents": ["경력기술서 v3"],
  "status": "REVIEWING",
  "counts": {
    "new": 7,
    "update": 3,
    "conflict": 1,
    "review": 2,
    "same": 24,
    "pending": 6
  },
  "completed_at": "2026-09-04T14:00:00+09:00"
}
```

### Diff 조회

```text
GET /analyses/{id}/diffs?change_types=NEW,UPDATE,CONFLICT,REVIEW&review_status=PENDING&entity_type=PROJECT
```

Diff Response는 Evidence Summary를 포함한다.

```json
{
  "id": "...",
  "entity_type": "PROFILE",
  "field_name": "technical_grade",
  "change_type": "CONFLICT",
  "old_value": "ADVANCED",
  "new_value": "SPECIAL",
  "confidence": 0.94,
  "evidence_type": "EXPLICIT",
  "review_status": "PENDING",
  "evidence": [
    {
      "id": "...",
      "document_id": "...",
      "page_no": 1,
      "quote_text": "기술등급: 특급"
    }
  ]
}
```

### 단일 Diff 결정

```json
{
  "review_status": "ACCEPTED"
}
```

직접 수정:

```json
{
  "review_status": "MODIFIED",
  "decided_value": "SPECIAL"
}
```

프로젝트 병합:

```json
{
  "review_status": "MERGED",
  "existing_target_id": "existing-project-uuid",
  "decided_value": {
    "roles": ["JOB-MGT-PL", "JOB-AI-DEV"],
    "skills": ["TECH-LANG-PYTHON", "TECH-AI-RAG"]
  }
}
```

### 일괄 결정

자동 Criteria를 Backend가 몰래 적용하지 않고 UI가 선택한 Diff ID를 명시한다.

```json
{
  "diff_ids": ["...", "...", "..."],
  "review_status": "ACCEPTED"
}
```

Backend는 `CONFLICT` 또는 일괄처리가 부적합한 Diff의 Bulk Accept를 거부할 수 있다.

### `POST /analyses/{id}/confirm`

Request:

```json
{
  "expected_profile_version": 7
}
```

확정 조건:

- Analysis 상태가 `REVIEWING`
- 필수 `CONFLICT / REVIEW`가 미처리 상태가 아님
- 현재 `person_profile.profile_version == expected_profile_version`

성공 Response `200`:

```json
{
  "data": {
    "analysis_id": "...",
    "person_id": "...",
    "profile_version": 8,
    "status": "CONFIRMED",
    "search_index_status": "PENDING"
  }
}
```

동일 Confirm 요청이 재전송될 경우 중복 Project/Profile Update가 발생하지 않도록 Idempotent하게 처리한다.

확정 Transaction에서는 외부 LLM/Embedding API를 호출하지 않는다. Search Index 작업은 `search_index_job` 생성 후 Celery `index` Queue에서 수행한다.

---

## 11. 프로젝트 API

| Method | Endpoint | 권한 | 설명 |
|---|---|---|---|
| `GET` | `/people/{person_id}/projects` | USER | 프로젝트 목록 |
| `POST` | `/people/{person_id}/projects` | ADMIN | 직접 프로젝트 추가 |
| `GET` | `/projects/{project_id}` | USER | 프로젝트 상세 |
| `PATCH` | `/projects/{project_id}` | ADMIN | 프로젝트 수정 |
| `DELETE` | `/projects/{project_id}` | ADMIN | Soft Delete |

목록 Query 예:

```text
?job_codes=JOB-MGT-PL&tech_codes=TECH-LANG-PYTHON&biz_codes=BIZ-PUBLIC&from=2024-01&to=2026-12
```

프로젝트 수정 시 역할/기술/전문분야/BIZ/CUSTOMER_TYPE 관계를 한 Transaction에서 갱신한다.

직접 생성/수정 데이터는 `source_type=USER`로 기록하며 변경이력과 Search Index Job을 생성한다.

---

## 12. 학력·자격·근무경력 API

상세 프로필 Tab용으로 다음 Resource를 제공한다.

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/people/{id}/education` | 학력 조회 |
| `POST` | `/people/{id}/education` | 학력 추가 |
| `PATCH` | `/education/{id}` | 학력 수정 |
| `DELETE` | `/education/{id}` | 학력 삭제 |
| `GET` | `/people/{id}/certifications` | 자격 조회 |
| `POST` | `/people/{id}/certifications` | 자격 추가 |
| `PATCH` | `/certifications/{id}` | 자격 수정 |
| `DELETE` | `/certifications/{id}` | 자격 삭제 |
| `GET` | `/people/{id}/employment-history` | 근무경력 조회 |
| `POST` | `/people/{id}/employment-history` | 근무경력 추가 |
| `PATCH` | `/employment-history/{id}` | 근무경력 수정 |
| `DELETE` | `/employment-history/{id}` | 근무경력 삭제 |

GET은 USER, 변경 API는 ADMIN 권한이다.

---

## 13. Evidence API

| Method | Endpoint | 권한 | 설명 |
|---|---|---|---|
| `GET` | `/evidence/{evidence_id}` | USER | 근거 상세 |
| `GET` | `/evidence` | USER | 특정 운영 Entity의 근거 목록 |

Query 예:

```text
/evidence?target_type=PERSON_SKILL&target_id={id}&field_name=tech_code
```

Response:

```json
{
  "data": [
    {
      "id": "...",
      "document": {
        "id": "...",
        "title": "경력기술서 v3"
      },
      "page_no": 8,
      "quote_text": "RAG 기반 지식검색 플랫폼 설계 및 개발",
      "bbox": null,
      "relation_type": "SUPPORTS"
    }
  ]
}
```

Frontend는 `document.id + page_no`를 이용해 Document Preview를 해당 페이지로 이동한다.

---

## 14. 통합검색 API

검색은 두 단계 API로 FIX한다.

```text
1. 자연어 조건해석
POST /search/interpret

2. 실제 검색
POST /search/people
```

자연어를 사용하지 않을 때는 1단계를 생략하고 직접 `/search/people`을 호출한다.

### `POST /search/interpret`

Request:

```json
{
  "text": "AI 개발 경험 있고 RAG 프로젝트 해본 특급 인력 찾아줘",
  "previous_query": null
}
```

Response:

```json
{
  "data": {
    "query_version": "1.0",
    "required": {
      "jobs": ["JOB-AI-DEV"],
      "skills": [],
      "expertise": [],
      "business_domains": [],
      "customer_types": [],
      "grade": {"values": ["SPECIAL"]},
      "career": null
    },
    "preferred": {
      "jobs": [],
      "skills": [],
      "expertise": ["EXP-AI-RAG"],
      "business_domains": [],
      "customer_types": []
    },
    "semantic_query": "LLM/RAG 기반 AI 시스템 개발 경험",
    "keyword_query": null,
    "sort": "RELEVANCE",
    "assumptions": [
      "RAG 경험을 선호조건으로 해석했습니다."
    ]
  }
}
```

AI는 Code Alias를 가능한 표준코드로 정규화하며 임의의 인력 ID나 적합도 점수를 생성하지 않는다.

### Search Query

`POST /search/people`

```json
{
  "required": {
    "jobs": ["JOB-AI-DEV"],
    "skills": [],
    "expertise": [],
    "business_domains": [],
    "customer_types": [],
    "grade": {"values": ["SPECIAL"]},
    "career": {"min_months": 120, "max_months": null},
    "affiliations": [],
    "certifications": [],
    "project_keywords": []
  },
  "preferred": {
    "expertise": ["EXP-AI-RAG"]
  },
  "skill_match_mode": "ANY",
  "semantic_query": "LLM/RAG 기반 AI 시스템 개발 경험",
  "keyword_query": null,
  "sort": "RELEVANCE",
  "page": 1,
  "page_size": 20,
  "suggest_relaxations": true
}
```

### Search 실행 원칙

```text
Structured DB Hard Filter
        +
Keyword / PostgreSQL FTS / pg_trgm
        +
pgvector Semantic Search
        ↓
Person ID 기준 병합
        ↓
필수조건 재검증
        ↓
Backend Ranking
        ↓
Evidence/Project 연결
```

필수조건은 Vector 유사도가 높아도 무시할 수 없다.

### 결과 Response

```json
{
  "data": [
    {
      "person_id": "...",
      "score": 94,
      "person": {
        "name": "홍길동",
        "technical_grade": "SPECIAL",
        "career_months": 223,
        "primary_jobs": ["AI개발자", "PL"],
        "skills": ["Python", "FastAPI", "RAG", "Qwen"]
      },
      "matches": [
        {
          "condition": "AI개발자",
          "type": "REQUIRED",
          "status": "MATCH",
          "evidence_count": 5
        },
        {
          "condition": "RAG",
          "type": "PREFERRED",
          "status": "MATCH",
          "evidence_count": 4
        }
      ],
      "top_projects": [
        {
          "project_id": "...",
          "project_name": "○○기관 AI 지식검색 플랫폼 구축",
          "period": "2025.01~2025.12",
          "roles": ["PL", "AI개발자"],
          "evidence_ids": ["..."]
        }
      ],
      "evidence": [
        {
          "evidence_id": "...",
          "source_level": "CONFIRMED_PROJECT",
          "document_id": "...",
          "page_no": 8,
          "snippet": "RAG 기반 지식검색 플랫폼 설계..."
        }
      ]
    }
  ],
  "meta": {
    "page": 1,
    "page_size": 20,
    "total": 12
  },
  "query": {
    "required": {},
    "preferred": {},
    "semantic_query": "..."
  },
  "relaxations": []
}
```

점수는 `94%`가 아니라 **적합도 94점**으로 표시한다.

### 0건 조건완화

`total=0`이고 `suggest_relaxations=true`이면 Backend가 실제 Count Query를 수행하여 제안한다.

```json
{
  "relaxations": [
    {
      "action": "REMOVE_CONDITION",
      "field": "business_domains",
      "value": "BIZ-HEALTHCARE",
      "estimated_count": 5,
      "label": "의료 경험 조건 제외"
    },
    {
      "action": "RELAX_GRADE",
      "from": "SPECIAL",
      "to": "ADVANCED_OR_HIGHER",
      "estimated_count": 7,
      "label": "고급 이상으로 변경"
    }
  ]
}
```

조건을 실제로 변경하는 것은 사용자가 제안을 선택했을 때만 수행한다.

---

## 15. 검색 결과 AI 설명 API

모든 검색 후보에 대해 LLM 설명을 선생성하면 느리고 비용/부하가 커진다. 따라서 **검색결과 자체는 Backend의 결정적 Ranking + Evidence만으로 즉시 반환**하고, 사용자가 `검색근거 보기`를 열었을 때 필요 시 한 사람에 대해 설명을 생성한다.

| Method | Endpoint | 권한 | 설명 |
|---|---|---|---|
| `POST` | `/search/explain` | USER | 특정 후보의 Evidence-bound 설명 생성 |

Request:

```json
{
  "person_id": "...",
  "search_query": {
    "required": {},
    "preferred": {},
    "semantic_query": "LLM/RAG 기반 AI 시스템 개발 경험"
  }
}
```

Response:

```json
{
  "data": {
    "person_id": "...",
    "explanation": "특급 AI개발자로 최근 RAG 기반 지식검색 프로젝트에서 PL 및 AI개발 역할을 수행했습니다.",
    "evidence_ids": ["...", "..."]
  }
}
```

LLM Prompt에는 검색 API가 반환한 Confirmed/Evidence 정보만 전달하며 새로운 경력을 추측하지 못하도록 제한한다.

---

## 16. Search Index 상태 API

Search Index 생성은 비동기이므로 관리자 운영화면에서 상태를 확인할 수 있어야 한다.

| Method | Endpoint | 권한 | 설명 |
|---|---|---|---|
| `GET` | `/search-index/jobs` | ADMIN | Index Job 목록 |
| `GET` | `/search-index/jobs/{job_id}` | ADMIN | Index Job 상세 |
| `POST` | `/search-index/jobs/{job_id}/retry` | ADMIN | 실패 Job 재시도 |
| `POST` | `/people/{person_id}/reindex` | ADMIN | 해당 인력 Search Index 재생성 |

사용자 검색 결과에는 Index 상태가 `PENDING`인 신규 변경사항이 아직 반영되지 않을 수 있음을 관리자 화면에서 확인 가능하게 한다.

---

## 17. Dashboard / 운영상태 API

### Dashboard

```text
GET /dashboard/summary
```

USER/ADMIN에 맞는 범위로 다음을 반환한다.

- 전체 인력 수
- 최근 등록 인력
- 관리자일 경우 검토대기/분석실패 건수

### AI 분석 현황

```text
GET /operations/analysis-summary
GET /operations/failed-jobs
```

ADMIN 전용.

---

## 18. Health Check

Traefik/Docker 운영을 위해 Application 업무 API와 별도 Health Endpoint를 둔다.

```text
GET /health/live
GET /health/ready
```

### Liveness

Process가 요청을 받을 수 있는지만 확인한다.

```json
{"status":"ok"}
```

### Readiness

최소 PostgreSQL 연결을 확인한다. Redis/MinIO/AI Runtime 장애는 서비스 특성에 따라 세부 Component 상태로 반환하되 AI Runtime 일시 장애 때문에 기본 Profile 조회 전체가 Unready가 되지는 않도록 한다.

예:

```json
{
  "status": "ready",
  "components": {
    "postgres": "ok",
    "redis": "ok",
    "minio": "ok",
    "llm": "degraded",
    "vlm": "ok"
  }
}
```

Traefik Health Check에는 `/health/ready` 또는 Docker Healthcheck를 연동한다.

---

## 19. 비동기 API 공통 패턴

비동기 작업 대상:

- 최소 식별정보 분석
- 문서 Preview 변환
- 상세 AI Profile 분석
- 재분석
- Embedding/Search Index 생성

Request는 긴 작업 완료를 기다리지 않는다.

```text
POST
  ↓
DB Job/Run 생성
  ↓
Celery 발행
  ↓
202 Accepted
```

Frontend는 2~5초 범위의 Polling부터 시작하고 Processing 시간이 길어질 경우 Polling 간격을 늘릴 수 있다.

MVP에서는 WebSocket/SSE를 필수로 하지 않는다.

업무 상태의 Source of Truth는 Redis/Celery Result가 아니라 PostgreSQL의 `analysis_run`, `search_index_job`, `document.processing_status`, `upload_session.status`다.

---

## 20. 동시성 / Idempotency

### Profile Version

Profile 변경과 Analysis 확정은 `profile_version`으로 Optimistic Lock을 수행한다.

### Analysis Confirm

`CONFIRMED`된 Analysis에 동일 Confirm Request가 다시 와도 동일 작업이 중복 생성되지 않아야 한다.

### File Hash

`sha256`으로 동일 파일을 확인하되 사용자의 명시적 결정 없이 자동 삭제/자동 병합하지 않는다.

### Search Index

동일 Object의 Index는 INSERT 누적보다 UPSERT를 기본으로 한다.

### API Idempotency-Key

네트워크 재시도가 중복 Resource 생성을 유발할 수 있는 중요 POST에는 추후 `Idempotency-Key` Header를 지원할 수 있다. MVP 필수 적용 대상은 Analysis Confirm 내부 Idempotency와 DB Unique/Transaction 보장으로 한정한다.

---

## 21. 핵심 업무 Flow와 API Mapping

### 신규 인력 등록

```text
POST /upload-sessions
POST /upload-sessions/{id}/files
POST /upload-sessions/{id}/identify
GET  /upload-sessions/{id}
POST /upload-sessions/{id}/resolve
POST /analyses
GET  /analyses/{id}
```

### AI 검토/확정

```text
GET   /analyses?status=REVIEWING
GET   /analyses/{id}/diffs
PATCH /analyses/{id}/diffs/{diff_id}
POST  /analyses/{id}/diffs/bulk
POST  /analyses/{id}/confirm
```

### 인력 상세

```text
GET /people/{id}
GET /people/{id}/projects
GET /people/{id}/documents
GET /people/{id}/education
GET /people/{id}/certifications
GET /evidence?... 
```

### 통합 인력검색

```text
POST /search/interpret      # 자연어 사용 시
POST /search/people
POST /search/explain       # 근거 Drawer를 열 때 선택적으로
GET  /evidence/{id}
GET  /documents/{id}/preview
```

---

## 22. FastAPI 구현 구조 Mapping

```text
app/modules/
├ auth/
│  ├ router.py
│  ├ schemas.py
│  └ service.py
├ users/
├ codes/
├ people/
├ documents/
├ analysis/
├ projects/
├ evidence/
└ search/
```

Router 역할:

- HTTP Parameter Parsing
- Pydantic Validation
- Authentication/Authorization Dependency
- Response Mapping

Service 역할:

- Transaction 경계
- 업무규칙
- DB Repository 호출
- Task 발행

Worker 역할:

- Parser/VLM/LLM
- Preview 변환
- Embedding/Index

API Router에서 Celery/LLM/MinIO/SQLAlchemy Query를 복잡하게 직접 조합하지 않는다.

---

## 23. OpenAPI

FastAPI의 자동 OpenAPI를 실제 계약의 기준으로 사용한다.

개발 단계에서:

```text
/docs
/openapi.json
```

을 제공하되 운영환경에서 Swagger UI 공개 여부는 환경설정으로 제어한다.

Pydantic Request/Response Schema와 Endpoint 구현이 본 문서의 계약을 따라야 하며 API 변경 시 문서와 OpenAPI를 함께 갱신한다.

---

## 24. MVP API FIX 요약

1차 MVP에서는 다음을 FIX한다.

- REST Base Path: `/api/v1`
- FastAPI + Pydantic v2
- Same-Origin + Server Session Cookie 인증
- USER / ADMIN 2개 Role
- 신규 등록은 `upload-session` 기반 Wizard API
- AI 분석은 `analysis_run` 기반 Async API
- Candidate Diff는 개별/일괄 검토 후 명시적 Confirm
- Profile 변경은 `profile_version` Optimistic Lock
- 문서는 Backend 권한검사 후 Preview/Download 제공
- Search는 `interpret → people` 2단계
- 검색결과 Ranking은 Backend가 결정하고 LLM은 설명 역할만 수행
- 검색 설명은 결과 전체가 아니라 필요 시 후보 단위 On-demand 생성
- Evidence에서 실제 문서/페이지까지 연결
- Search Index/Embedding은 비동기 Worker 처리
- PostgreSQL이 업무상태 Source of Truth
- Health Endpoint는 Traefik/Docker 운영을 고려하여 제공

다음 구현 단계에서는 이 API 계약을 기준으로 **FastAPI Project Skeleton, SQLAlchemy Model/Alembic 초기 Migration, Pydantic Schema, Router Stub**을 생성한다.
