# 05. Document Management

## 1. 목적

인력의 이력서·경력기술서·프로필 등 원본 문서를 웹 애플리케이션에서 등록하고, 원본과 메타정보를 통합 관리한다. 등록된 문서는 이후 AI 분석, 프로필 구조화, 임베딩, 검색근거의 기준 자료로 사용한다.

웹하드와 직접 연동하지 않고 TalentScope 자체가 문서 저장소 역할을 담당한다.

## 2. 사용자 흐름

### 신규 인력

```text
신규 인력 등록
  → 문서 업로드
  → 파일 저장 및 검증
  → 문서종류 지정
  → AI 기본 식별정보 추출
  → 기존 인력 중복 확인
  → 신규 인력 생성
  → 상세 AI 분석
  → 사용자 검토·확정
```

### 기존 인력

```text
인력 상세
  → 문서 탭
  → 문서 추가
  → 새 버전/별도 문서 선택
  → AI 분석
  → 기존 Profile 비교
```

## 3. 지원 파일형식

| 형식 | 업로드 | AI 분석 | 미리보기 |
|---|---:|---:|---:|
| PDF | O | O | O |
| DOCX | O | O | O |
| DOC | O | O* | O* |
| HWPX | O | O | O* |
| HWP | O | O* | O* |
| PPTX | O | O | O* |
| PPT | O | O* | O* |
| XLSX/XLS | O | 선택 | O* |
| JPG/JPEG | O | O | O |
| PNG | O | O | O |

`*`는 서버 변환 또는 별도 Parser가 필요한 형식이다.

MVP 핵심 지원 포맷은 `HWP/HWPX`, `DOC/DOCX`, `PDF`, `PPT/PPTX`, `JPG/PNG`로 한다.

## 4. 문서종류 코드

- `DOC-RESUME`: 이력서
- `DOC-PROFILE`: 인력 프로필
- `DOC-CAREER`: 경력기술서
- `DOC-KOSA`: KOSA 등 경력증명
- `DOC-CERT`: 자격증/인증서
- `DOC-PORTFOLIO`: 포트폴리오
- `DOC-EDU`: 학력/교육 증빙
- `DOC-OTHER`: 기타

신분증, 통장사본, 주민등록 관련 문서 등 민감문서는 1차 MVP 관리대상에서 제외한다.

## 5. 다중 업로드

한 인력에 여러 파일을 동시에 등록할 수 있다.

```text
홍길동_이력서.hwp          → 이력서
홍길동_경력기술서.pptx     → 경력기술서
홍길동_KOSA.pdf             → 경력증명
정보처리기사.jpg            → 자격증
```

파일명/내용을 이용하여 문서종류를 자동 제안할 수 있으나 사용자가 변경 가능해야 한다.

## 6. 저장 방식

권장 구조:

```text
Web Application
   → Backend
      → File Storage (원본/Preview)
      → PostgreSQL (메타정보)
```

File Storage는 서버 파일시스템/NAS/MinIO(S3-Compatible) 중 구현단계에서 결정한다. 장기 확장성을 고려하면 MinIO/S3 계열을 우선 검토한다.

원본 파일은 수정하지 않는다.

```text
사용자 파일명: 홍길동_이력서_20260904.hwp
내부 저장경로: /person/{person_id}/document/{document_id}/original.hwp
```

사용자가 다운로드할 때는 원래 파일명으로 제공한다.

## 7. 문서 메타데이터

최소 관리 항목:

- `document_id`
- `person_id`
- `document_group_id`
- `document_type`
- `title`
- `original_filename`
- `extension`
- `mime_type`
- `file_size`
- `storage_path`
- `version`
- `is_latest`
- `document_date`
- `uploaded_at`
- `uploaded_by`
- `analysis_status`
- `embedding_status`
- `checksum`
- `deleted_at` 또는 삭제상태

## 8. 미리보기

- PDF / 이미지: 브라우저에서 직접 미리보기
- HWP/DOCX/PPTX 등: 서버에서 Preview PDF를 생성하여 Web Viewer로 표시
- 원본은 항상 별도로 보존

MVP 미리보기 기능:
- 페이지 이동
- 확대/축소
- 전체화면
- 원본 다운로드

문서 직접 편집 기능은 제공하지 않는다.

## 9. 버전관리

동일 인력의 새 문서가 등록되어도 기존 파일을 덮어쓰지 않는다.

```text
문서그룹: 이력서
  ├ v1 2025-03
  ├ v2 2026-01
  └ v3 2026-09 (최신)
```

권장 구조:

```text
Document Group
  → Document Version
     → Physical File
```

동일 유형 문서가 존재할 때 사용자가 `새 버전으로 등록 / 별도 문서로 등록 / 취소`를 선택할 수 있게 한다.

## 10. 최신버전 정책

기본 문서조회와 재분석에서는 최신버전을 우선하지만, 과거 문서에만 존재하는 프로젝트/기술을 자동 삭제하지 않는다.

`최신 원본문서`와 `누적 Confirmed Profile`은 별개 개념으로 관리한다.

## 11. 중복검사

파일 Hash(checksum)를 이용하여 동일 파일을 확인한다. 동일 파일이 있을 경우 기존 문서를 안내하고 등록 지속 여부를 선택하게 한다.

## 12. 상태관리

예시 상태:

- `UPLOADED`
- `PROCESSING`
- `ANALYZED`
- `EMBEDDED`
- `FAILED`

실패 문서는 원본을 보존하고 관리자가 재분석할 수 있어야 한다.

## 13. 삭제

문서 삭제는 기본적으로 Soft Delete를 사용한다. 관리자만 삭제/복원할 수 있고, 물리삭제 정책은 운영단계에서 별도로 정한다.

## 14. 검색결과 연결

검색결과나 추천근거에서 관련 원본문서를 바로 확인할 수 있어야 한다.

```text
RAG 경험 근거
  → 프로젝트
  → 경력기술서 v3 / p.8
  → [원문 보기] [다운로드]
```
