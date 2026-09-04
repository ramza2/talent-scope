# 08. Search & Recommendation

## 1. 목적

사용자가 구조화된 조건을 직접 선택하거나 자연어로 원하는 인력조건을 입력하면, 구조화 DB와 원본문서/프로젝트 임베딩을 결합하여 관련 인력을 검색하고 적합도와 근거를 제공한다.

## 2. 검색 방식

### 조건검색
정확한 조건을 DB에서 검색한다.

예:
- 직무
- 기술
- 등급
- 총 경력
- 전문분야
- 사업분야
- 고객유형
- 소속
- 자격증

### 자연어검색
Qwen3-14B가 자연어 요청을 검색조건 JSON으로 변환한다.

예:

```text
"AI 개발 경험 있고 RAG 해본 특급 인력 찾아줘"
  → 직무: AI개발자
  → 등급: 특급
  → 전문분야: RAG
  → 의미검색: LLM/RAG 기반 AI 시스템 구축 경험
```

### Hybrid Search

```text
Structured DB Search
+ Keyword / Full Text Search
+ Vector Search
→ Person 기준 병합
→ Hard Filter
→ Ranking
→ Evidence 생성
```

## 3. 검색 UI 원칙

조건검색과 자연어검색을 별도 기능으로 경쟁시키지 않고 하나의 통합 화면에서 연결한다.

자연어를 입력하면 AI가 해석한 조건을 선택식 검색 UI에 자동 채우고, 사용자가 수정 후 재검색할 수 있게 한다.

## 4. 검색조건

주요 조건:
- 이름
- JOB 직무
- TECH 기술
- 기술등급
- 총 경력 범위
- EXP 전문분야
- BIZ 사업분야
- CUSTOMER_TYPE 고객유형
- 프로젝트명/키워드
- 자격증
- 소속
- 프로필 최종 갱신일

기본 화면에는 직무, 등급, 경력, 기술, 전문분야를 우선 노출하고 나머지는 상세조건으로 제공한다.

## 5. 복수조건 규칙

기본 규칙:
- 서로 다른 필드 간: AND
- 동일 필드 내 복수값: OR

예:

```text
직무 = DBA
AND
기술 = Oracle OR Tibero
AND
등급 = 고급 이상
```

필요 시 `모두 포함` 옵션을 추가할 수 있다.

## 6. 계층 및 Alias 검색

- 상위 JOB 검색 시 하위 JOB을 포함한다.
- Alias Dictionary를 표준코드로 정규화한다.
- Keyword 검색에도 Alias를 활용할 수 있다.

예:

```text
AI Engineer / AI Developer / 인공지능개발
→ JOB-AI-DEV
```

## 7. Search Query JSON

자연어 검색의 내부 계약 예시:

```json
{
  "query_version": "1.0",
  "required": {
    "jobs": [],
    "skills": [],
    "expertise": [],
    "business_domains": [],
    "customer_types": [],
    "grade": null,
    "career": null
  },
  "preferred": {
    "jobs": [],
    "skills": [],
    "expertise": [],
    "business_domains": [],
    "customer_types": [],
    "recent_experience": null
  },
  "semantic_query": null,
  "keyword_query": null,
  "ranking_focus": [],
  "sort": "relevance"
}
```

## 8. Required / Preferred

자연어에서 필수조건과 선호조건을 구분한다.

예:

```text
"특급 DBA 중 Oracle 경험 있고 금융 경험 있으면 좋겠어"
```

- Required: DBA, 특급, Oracle
- Preferred: 금융 경험

AI가 해석한 결과는 사용자에게 보여주고 수정 가능해야 한다.

## 9. 모호한 질의

`AI 잘하는 사람`처럼 모호한 질의는 지나치게 좁은 조건을 임의 생성하지 않고 AI 관련 직무/전문분야를 넓게 검색한다. 결과화면에서 적용한 검색기준을 설명한다.

## 10. Structured DB Search

다음과 같은 정량/정확 조건은 DB가 판단한다.

- 특급
- 15년 이상
- DBA
- 특정 TECH 코드
- 특정 자격

Semantic Score가 필수조건을 뒤집을 수 없다.

## 11. Keyword / Full Text Search

정확한 프로젝트명, 고객명, 제품명, 기술어 검색에 사용한다.

예:
- DEMIS
- Tibero
- 국가정보자원관리원

## 12. Vector Search

표현이 다르지만 의미가 비슷한 경험을 찾는 데 사용한다.

예:

```text
질의: 병원 의료데이터를 이용한 AI 경험
문서: EMR 진료기록 기반 진단보조 모델 개발
```

Vector 대상:
- Profile Vector
- Project Vector
- Document Chunk Vector

검색 신뢰도는 `Confirmed Profile/Project > Original Document Chunk > Inferred` 순으로 가중할 수 있다.

## 13. Candidate Merge

각 검색채널 결과는 `person_id` 기준으로 병합한다.

```text
홍길동
  ├ Structured Match
  ├ Profile Vector Match
  ├ Project Vector Match x 2
  └ Document Match x 4
```

## 14. Hard Filter

명시적 필수조건 미충족 후보는 기본 검색결과에서 제외한다.

예: `특급만` 요청 시 고급 후보를 Semantic 유사도가 높다는 이유로 포함하지 않는다.

## 15. Ranking

점수는 LLM이 임의로 정하지 않고 Backend 규칙으로 계산한다.

초기 기본 가중치 예:

| 평가요소 | 기본 가중치 |
|---|---:|
| 직무 일치 | 20 |
| 기술 일치 | 20 |
| 전문분야 일치 | 15 |
| 관련 프로젝트 경험 | 20 |
| 사업분야/고객유형 | 10 |
| 경력/등급 | 5 |
| Semantic 유사도 | 5 |
| 최근성 | 5 |

실제 가중치는 테스트를 통해 조정한다.

LLM은 질의의 `ranking_focus`를 해석할 수 있으나 실제 점수계산은 Backend가 수행한다.

## 16. 프로젝트 경험 반영

단순 키워드 등장 여부뿐 아니라 다음을 반영한다.
- 관련 프로젝트 건수
- 수행기간
- 수행역할
- 최근 수행시점
- EXPLICIT / INFERRED 여부

최근성은 보정값으로 사용하며 오래된 경험을 과도하게 감점하지 않는다.

## 17. 결과 표현

검색결과 예:

```text
적합도 94점
홍길동
특급 / AI개발자 / 18년

Python · FastAPI · Qwen · RAG

조건일치
✓ AI개발자
✓ 특급
✓ RAG 프로젝트 4건

주요근거
○○기관 AI 지식검색 플랫폼 구축
[상세 프로필] [검색근거 보기]
```

`94%` 같은 확률 표현보다 `적합도 94점`을 사용한다.

## 18. 추천 설명

Qwen3-14B는 검색 Evidence만 입력받아 사람이 읽기 쉬운 설명을 생성한다. DB나 원문에 없는 경력을 생성하지 않도록 한다.

## 19. 근거 Drill-down

조건 충족 근거에서 프로젝트와 원본문서로 이동할 수 있어야 한다.

```text
RAG 경험 4건
  → ○○기관 AI 플랫폼 구축
  → 경력기술서 v3 / p.8
  → [원문 보기]
```

## 20. 0건 처리

필수조건을 자동 완화하지 않는다. 대신 완화했을 때의 후보 가능성을 제안한다.

예:

```text
모든 조건을 만족하는 인력이 없습니다.
- 지역 조건 제외 → 3명
- 등급을 고급 이상으로 변경 → 7명
```

사용자가 선택할 때만 조건을 변경한다.

## 21. 정렬/재검색

결과 정렬 후보:
- 적합도순
- 총 경력순
- 최근 갱신순
- 최근 관련 프로젝트순
- 이름순

상세 화면을 보고 돌아왔을 때 기존 검색조건을 유지한다.
