# 06. AI Profile Analysis

## 1. 목적

업로드된 이력서·프로필·경력기술서에서 인력 정보를 자동 추출하고, 표준 코드로 정규화하여 Candidate Profile을 생성한다. 기존 Confirmed Profile과 비교한 뒤 사용자가 최종 확정한 값만 운영 DB에 반영한다.

## 2. 분석 파이프라인

```text
문서 업로드
  → 형식 판별
  → Text Parser
  → 필요한 경우 VLM
  → Raw Extraction
  → Qwen3-14B 구조화/정규화
  → Candidate Profile
  → 기존 Confirmed Profile 비교
  → 사용자 검토/확정
  → Profile DB 반영
  → 검색용 Summary/Embedding 생성
```

VLM은 문서를 읽는 역할, LLM은 추출 내용을 시스템 데이터로 구조화·정규화하는 역할로 분리한다.

## 3. 분석 대상 문서

우선순위가 높은 문서:
- 이력서
- 인력 프로필
- 경력기술서
- KOSA 등 경력증명
- 자격증
- 포트폴리오

## 4. 추출 데이터 계층

각 주요 값은 다음 세 계층을 구분한다.

1. `raw_value`: 문서에 나타난 원문 값
2. `normalized_value`: 표준화된 값/코드
3. `confirmed_value`: 사용자가 최종 확정한 운영 값

예:

```json
{
  "raw_value": "Project Manager",
  "normalized_value": "PM",
  "code": "JOB-MGT-PM"
}
```

## 5. 기본정보 추출 필드

- `name`
- `birth_year` (필요 시)
- `phone`
- `email`
- `address_region`
- `affiliation`
- `employment_type`
- `current_title`
- `career_start_date`
- `total_career_months`
- `technical_grade`
- `profile_summary`

주민등록번호, 계좌번호, 신분증번호 등 민감정보는 Profile JSON 출력 대상에서 제외한다.

## 6. 직무 추출

한 사람에 복수 직무를 허용한다.

```json
{
  "code": "JOB-AI-DEV",
  "name": "AI개발자",
  "type": "PRIMARY",
  "source_value": "AI Engineer",
  "confidence": 0.96
}
```

직무 유형:
- `PRIMARY`: 현재/주 직무
- `SECONDARY`: 부 직무
- `EXPERIENCE`: 과거 수행 경험

## 7. 기술 추출

기술별 관리 후보:
- 표준코드/표준명
- 원문값
- 최근 사용연도
- 경험기간(문서 명시/계산/사용자 확정 구분)
- 근거 프로젝트
- Confidence

AI가 숙련도를 임의로 확정하지 않는다. 숙련도는 문서에 명시되거나 사용자가 확정한 경우에만 저장한다.

## 8. 전문분야 추출

전문분야는 `EXP` 코드로 정규화하고 근거유형을 구분한다.

- `EXPLICIT`: 문서에 직접 표현됨
- `INFERRED`: 프로젝트 설명 등을 바탕으로 의미상 추론됨

예:

```json
{
  "code": "EXP-AI-RAG",
  "name": "RAG",
  "evidence_type": "INFERRED",
  "confidence": 0.82
}
```

## 9. 프로젝트 경력 추출

프로젝트는 검색 품질의 핵심 데이터이므로 개별 Entity로 구조화한다.

필드:
- `project_name`
- `customer_name`
- `customer_type`
- `business_domains`
- `start_date`
- `end_date`
- `duration_months`
- `roles`
- `technologies`
- `expertise`
- `responsibilities`
- `project_summary`
- `source_document_id`
- `source_page` 또는 원문 위치
- `confidence`

## 10. 기타 추출 필드

### 근무경력
- 회사명
- 입사/퇴사일
- 부서
- 직위
- 담당업무

### 학력
- 학교
- 전공
- 학위
- 입학/졸업 시점
- 상태

### 자격
- 자격명
- 발급기관
- 취득일
- 만료일(있는 경우)
- 증빙문서

## 11. Profile JSON Schema 예시

```json
{
  "schema_version": "1.0",
  "document": {
    "document_id": "DOC-001",
    "document_type": "RESUME",
    "document_date": "2026-09-01",
    "filename": "홍길동_이력서.docx"
  },
  "person": {
    "name": "홍길동",
    "private_contact": {
      "phone": "010-1234-5678",
      "email": "hong@example.com",
      "address_region": "서울"
    },
    "affiliation": {
      "company": "ABC테크",
      "title": "부장",
      "employment_type": "외부"
    },
    "career": {
      "career_start_date": "2008-03",
      "total_career_months": 222,
      "technical_grade": "특급"
    },
    "jobs": [],
    "skills": [],
    "expertise": [],
    "business_domains": [],
    "customer_types": [],
    "employment_history": [],
    "education": [],
    "certifications": [],
    "projects": [],
    "summary": {
      "career_summary": "...",
      "search_text": "..."
    }
  },
  "analysis": {
    "llm_model": "Qwen3-14B",
    "vlm_model": "Qwen2.5-VL-7B-Instruct",
    "prompt_version": "resume-extract-v1",
    "overall_confidence": 0.93
  }
}
```

AI 응답 Schema와 실제 관계형 DB Schema는 동일하게 만들 필요가 없다. AI 결과는 Candidate JSON으로 받고, 최종 DB는 정규화된 관계형 구조로 저장한다.

## 12. 원문 근거

중요한 추출값에는 가능한 한 Evidence를 연결한다.

```json
{
  "document_id": "DOC-123",
  "page": 5,
  "text": "Oracle 기반 DB 운영 및 튜닝 수행"
}
```

검색/추천 단계에서 실제 문서 페이지로 Drill-down하는 근거로 사용한다.

## 13. Confidence

내부적으로 Confidence를 저장하되 사용자 화면에서는 `높음 / 확인 권장 / 확인 필요`처럼 표현할 수 있다. 초기 기준은 실증을 통해 조정한다.

## 14. 기존 DB 비교

Candidate Profile과 Confirmed Profile 비교 상태:

- `SAME`: 동일
- `NEW`: 기존에 없는 신규 값
- `UPDATE`: 최신 문서로 갱신 가능성이 높은 값
- `CONFLICT`: 기존 확정값과 상충
- `REMOVE_CANDIDATE`: 최신 문서에 없지만 기존에는 있는 값

`REMOVE_CANDIDATE`는 자동 삭제하지 않는다.

## 15. 충돌 처리 원칙

- 사용자 확정값이 최우선이다.
- 신규 문서는 기존 값을 자동으로 덮어쓰지 않는다.
- 날짜 기반 값은 문서 기준일을 고려한다.
- 과거 기술/프로젝트가 최신 문서에 없다는 이유로 삭제하지 않는다.
- 공식 경력증명/KOSA는 충돌 검토 시 높은 신뢰도의 근거로 활용할 수 있다.

## 16. 프로젝트 중복/병합

프로젝트명, 고객사, 기간, 역할 등을 비교하여 동일/유사 프로젝트를 판단한다. 동일 가능성이 높으면 사용자에게 `병합 / 별도 등록` 선택을 제공한다.

## 17. 사용자 확정 상태

Candidate 값 상태:
- `PENDING`
- `ACCEPTED`
- `REJECTED`
- `MODIFIED`

확정 전 Candidate 데이터는 일반 검색의 운영 프로필로 사용하지 않는다.

## 18. 확정 후 처리

```text
Candidate 확정
  → Profile DB 반영
  → 프로젝트/기술/직무 관계 반영
  → 검색용 Summary 생성
  → Profile Embedding 생성
  → Project Embedding 생성
  → Document Chunk Embedding 생성
  → Search Index 갱신
```

## 19. 재분석 및 분석이력

재분석 시 기존 Confirmed Profile은 유지하고 새 Candidate 결과와 다시 비교한다.

문서별 기록:
- 분석일
- LLM/VLM 모델
- Prompt Version
- Schema Version
- 분석 결과
- 확정 결과
- 확정자/확정일
