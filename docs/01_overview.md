# 01. Overview

## 1. 프로젝트 개요

TalentScope는 사내에서 보유한 이력서, 인력 프로필, 경력기술서 등의 문서를 AI로 분석하여 인력 정보를 구조화하고, 조건검색·자연어검색·의미검색을 통해 적합한 인력을 빠르게 찾기 위한 사내 인력 프로필 관리·검색 시스템이다.

기존 방식은 사람별 웹하드 폴더와 Excel 인력관리대장에 의존하여 문서를 직접 열어보거나 여러 시트를 확인해야 했다. TalentScope는 원본 문서와 구조화된 인력 DB를 하나의 시스템에서 연결하고, 검색 결과에서 실제 근거 문서까지 확인할 수 있도록 한다.

## 2. 핵심 목표

- 인력 관련 원본 문서를 웹 애플리케이션에서 직접 등록·보관한다.
- Parser/VLM/LLM을 이용하여 이력서·프로필의 정보를 자동 추출한다.
- 추출된 정보를 표준 코드체계로 정규화하여 구조화 DB에 저장한다.
- AI 추출 결과는 사용자가 검토·확정한 후 운영 프로필에 반영한다.
- 조건검색과 자연어검색을 함께 제공한다.
- 구조화 DB, Keyword, Vector 검색을 결합하여 관련 인력을 찾는다.
- 추천 결과는 실제 프로젝트 경력과 원본문서를 근거로 설명한다.

## 3. 기본 원칙

### 문서와 DB의 역할 분리

- 원본 이력서/프로필은 File Storage에 보존한다.
- DB에는 인력 프로필, 프로젝트, 기술, 문서 메타정보 등을 구조화하여 저장한다.
- 파일 자체를 DB BLOB으로 저장하는 방식은 우선 채택하지 않는다.

### AI 결과의 Human-in-the-Loop

AI 결과가 기존 확정 데이터를 자동으로 덮어쓰지 않는다.

```text
Document
  → AI Candidate Profile
  → 기존 Confirmed Profile 비교
  → 사용자 검토
  → Confirmed Profile
```

### 검색 원칙

정확한 조건은 DB가 판단하고, 의미검색은 숨은 경험을 찾는 용도로 사용한다.

```text
Structured DB Search
+ Keyword / Full Text Search
+ Vector Search
→ Candidate Merge
→ Hard Filter
→ Ranking
→ Evidence
```

## 4. AI Runtime

기 구축된 ALZI Runtime을 공유하여 사용한다.

### LLM

- Runtime Artifact: `Qwen/Qwen3-14B-AWQ`
- API Model Name: `Qwen3-14B`
- Backend Container: `http://vllm-qwen3:8000`
- Server Localhost: `http://localhost:8002`
- External HTTPS: `https://alzi-llm.openlink.kr`
- API: OpenAI-compatible `/v1/chat/completions`

주요 역할:
- Profile JSON 구조화
- 표준코드 정규화 보조
- 자연어 검색조건 분석
- 필수/선호조건 구분
- 검색용 의미 Query 생성
- 검색결과 설명 생성

### VLM

- Runtime Artifact: `Qwen/Qwen2.5-VL-7B-Instruct-AWQ`
- API Model Name: `Qwen2.5-VL-7B-Instruct`
- Backend Container: `http://vllm-qwen25-vl:8000`
- Server Localhost: `http://localhost:8003`
- External HTTPS: `https://alzi-vlm.openlink.kr`
- API: OpenAI-compatible Multimodal API

주요 역할:
- 스캔/이미지형 문서 인식
- 표 구조 인식
- 이미지 자격증 등 시각 정보 추출

Frontend는 Runtime을 직접 호출하지 않고 TalentScope Backend를 통해 사용한다.

## 5. 향후 설계 흐름

1. MVP 기능 및 데이터 항목 확정
2. 메뉴/화면 IA 확정
3. Wireframe 작성
4. DB ERD 및 API 설계
5. 기술스택 및 저장소 구조 확정
6. 구현
