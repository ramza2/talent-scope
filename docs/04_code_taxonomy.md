# 04. Code Taxonomy

## 1. 분류 원칙

TalentScope의 검색용 코드체계는 다음 다섯 축으로 분리한다.

- `JOB`: 이 사람이 어떤 역할을 하는가
- `TECH`: 이 사람이 어떤 기술을 사용할 수 있는가
- `EXP`: 어떤 업무/문제에 전문성이 있는가
- `BIZ`: 어떤 산업/사업 도메인의 경험이 있는가
- `CUSTOMER_TYPE`: 어떤 유형의 고객/기관을 대상으로 수행했는가

각 분류는 상하위 계층을 지원하고, 한 인력에 복수 코드를 연결할 수 있다.

## 2. JOB - 직무

### 사업관리 `JOB-MGT`
- `JOB-MGT-PM` PM
  - Alias: Project Manager, 프로젝트관리자, 사업관리
- `JOB-MGT-PL` PL
  - Alias: Project Leader, 프로젝트리더, 개발PL
- `JOB-MGT-PMO` PMO

### 아키텍처 `JOB-ARC`
- `JOB-ARC-SA` SA / Solution Architect / System Architect
- `JOB-ARC-AA` AA / Application Architect
- `JOB-ARC-TA` TA / Technical Architect

### 데이터/DB `JOB-DATA`
- `JOB-DATA-DA` DA / Data Architect
- `JOB-DATA-DBA` DBA / Database Administrator / DB관리자 / DB운영

### 개발 `JOB-DEV`
- `JOB-DEV-GEN` 개발자 / Software Engineer / Programmer
- `JOB-DEV-BE` 백엔드개발자 / Backend Developer / 서버개발자
- `JOB-DEV-FE` 프론트엔드개발자 / Frontend Developer
- `JOB-DEV-FS` 풀스택개발자 / Full Stack Developer
- `JOB-DEV-MOB` 모바일개발자 / Android / iOS / App 개발자
- `JOB-DEV-INT` 인터페이스개발자 / API / EAI / 연계개발

### AI `JOB-AI`
- `JOB-AI-DEV` AI개발자 / AI Engineer / AI Developer
- `JOB-AI-ML` ML Engineer / Machine Learning Engineer
- `JOB-AI-LLM` LLM Engineer / 생성형AI개발자
- `JOB-AI-VISION` Vision AI Engineer / CV Engineer / Computer Vision Engineer
- `JOB-AI-PLATFORM` AI Platform Engineer

### 시스템 `JOB-SYS`
- `JOB-SYS-SE` 시스템SE / System Engineer / 서버SE
- `JOB-SYS-OS` Linux/Unix SE
- `JOB-SYS-CLOUD` Cloud Engineer
- `JOB-SYS-MW` Middleware Engineer / WAS Engineer

### 기타
- `JOB-NET-ENG` 네트워크엔지니어
- `JOB-SEC-ENG` 보안엔지니어
- `JOB-OPS-SYS` 시스템운영
- `JOB-OPS-APP` 애플리케이션운영
- `JOB-QA-ENG` QA / Tester / 테스트엔지니어

상위코드 검색 시 하위코드를 자동 포함한다. 예: `JOB-DEV` 검색은 백엔드/프론트엔드/풀스택/모바일/인터페이스 등을 포함한다.

## 3. TECH - 기술

### Language `TECH-LANG`
Java, Python, JavaScript, TypeScript, C, C++, C#, Kotlin, Swift, Go, Rust, PHP, SQL, PL/SQL, Shell Script 등

대표 Alias:
- JS → JavaScript
- TS → TypeScript
- Bash / Shell → Shell Script

### Backend `TECH-BE`
Spring, Spring Boot, Spring MVC, FastAPI, Django, Flask, Node.js, NestJS, Express, .NET, ASP.NET

### Frontend `TECH-FE`
React, Vue.js, Angular, Next.js, HTML, CSS, JavaScript, TypeScript

### Database `TECH-DB`
- RDBMS: Oracle, PostgreSQL, MySQL, MariaDB, MS SQL Server, Tibero, Altibase, DB2
- NoSQL/Search: MongoDB, Redis, Elasticsearch, OpenSearch
- Vector: pgvector, Milvus, FAISS, Qdrant, Pinecone

대표 Alias:
- Postgres → PostgreSQL
- MSSQL / SQL Server → MS SQL Server
- 오라클 / Oracle DB → Oracle

### AI/ML `TECH-AI`
- Framework: PyTorch, TensorFlow, Hugging Face Transformers, ONNX
- Runtime: vLLM, Ollama, llama.cpp, TensorRT-LLM, TGI
- Model Family: Qwen, Llama, Gemma, Mistral, DeepSeek, GPT, BERT, CLIP, YOLO
- Application: LangChain, LangGraph, LlamaIndex, MCP
- Embedding: BGE, BGE-M3, E5, Sentence Transformers

### Infra/DevOps `TECH-INFRA`
- OS: Linux, Unix, Windows Server, AIX, HP-UX
- Container: Docker, Kubernetes, OpenShift
- Cloud: AWS, Azure, GCP, Naver Cloud, NHN Cloud
- DevOps: Git, GitLab, GitHub, Jenkins, Argo CD, Ansible, Terraform
- Middleware: Tomcat, WebLogic, WebSphere, JEUS, Nginx, Apache HTTP Server

### Data `TECH-DATA`
Hadoop, Spark, Kafka, Airflow, NiFi, Flink, ETL, CDC, Data Warehouse, Data Lake

## 4. EXP - 전문분야

### AI `EXP-AI`
- 생성형AI
  - LLM
  - VLM
  - RAG
  - AI Agent
  - Prompt Engineering
  - Fine-tuning
- Machine Learning
- Deep Learning
- NLP
- Vision AI
- 음성AI / STT / TTS
- 추천시스템
- MLOps
- AI Serving
- AI 플랫폼 구축

대표 Alias:
- GenAI / Generative AI → 생성형AI
- 검색증강생성 / Retrieval Augmented Generation → RAG
- AI에이전트 / Agentic AI → AI Agent
- Computer Vision / CV → Vision AI

### SW `EXP-SW`
Web Application, Backend, Frontend, Mobile, API, Interface, Batch, Portal, Microservices, Legacy Modernization, 시스템통합

대표 Alias:
- SI / System Integration → 시스템통합
- MSA → Microservices

### Data/DB `EXP-DATA`
DB 설계, DB 구축, DB 운영, DB 튜닝, DB 마이그레이션, DB 이중화, 장애대응, 데이터 모델링, 데이터 아키텍처, 데이터 통합, 데이터 품질, ETL, DW, Big Data, 데이터 분석

### Infra `EXP-INFRA`
시스템 구축, 시스템 운영, 서버 구축, OS 운영, 가상화, Cloud 구축, Cloud Migration, Container, Kubernetes, Middleware, Network, Backup, DR, Monitoring

### Management `EXP-MGT`
대규모 프로젝트 관리, 공공 SI 관리, Agile 관리, 일정관리, 인력관리, 품질관리, 위험관리, 요구사항관리, 고객/발주처 대응, 컨소시엄 관리, 제안/기획

## 5. BIZ - 사업분야

- `BIZ-PUBLIC` 공공
  - 중앙정부
  - 지방자치단체
  - 공공기관
  - 공기업
- `BIZ-DEFENSE` 국방
  - 국방부
  - 육군/해군/공군
  - 군 의료
- `BIZ-HEALTHCARE` 의료/헬스케어
  - 병원
  - 의료정보
  - EMR
  - PACS
  - 디지털헬스
- `BIZ-FINANCE` 금융
  - 은행/카드/보험/증권/핀테크
- `BIZ-MANUFACTURING` 제조
- `BIZ-ENERGY` 에너지
- `BIZ-TELECOM` 통신
- `BIZ-RETAIL` 유통
- `BIZ-LOGISTICS` 물류
- `BIZ-EDUCATION` 교육
- `BIZ-TRANSPORT` 교통
- `BIZ-CONSTRUCTION` 건설
- `BIZ-MEDIA` 미디어
- `BIZ-ENTERPRISE` 일반기업

## 6. CUSTOMER_TYPE - 고객유형

사업분야와 고객유형을 분리한다. 예를 들어 한국가스안전공사 프로젝트는 `사업분야=에너지`, `고객유형=공공기관`으로 표현할 수 있다.

- 중앙정부
- 지방정부
- 공공기관
- 공기업
- 군
- 대학/학교
- 병원
- 금융기관
- 대기업
- 중견기업
- 중소기업

## 7. Alias Dictionary

동의어는 프로필 값에 직접 여러 개 저장하지 않고 별도 Alias Dictionary로 관리한다.

```text
canonical_code      canonical_name      alias
JOB-AI-DEV          AI개발자            AI Engineer
JOB-AI-DEV          AI개발자            AI Developer
JOB-SYS-SE          시스템SE            System Engineer
JOB-MGT-PM          PM                  Project Manager
TECH-DB-ORACLE      Oracle              오라클
EXP-AI-RAG          RAG                 검색증강생성
```

AI가 Alias를 추출해도 DB에는 표준 코드로 정규화한다.

## 8. 검색 적용 원칙

- 상위분류 검색 시 하위코드를 포함한다.
- 동일 항목의 복수값은 기본적으로 OR로 처리한다.
- 서로 다른 항목은 기본적으로 AND로 처리한다.
- 자연어 검색에서는 Alias를 표준 코드로 정규화한다.
- 원문에 명시된 값(`EXPLICIT`)과 AI가 의미상 추론한 값(`INFERRED`)을 구분한다.
