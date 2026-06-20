# 감사로그 엑셀 다운로드 + 과거 데이터 삭제 설계

날짜: 2026-06-07
상태: 승인됨
요청: 감사로그에 엑셀 다운로드 버튼, 과거 데이터 삭제 기능 추가

## 결정 사항

- 다운로드 범위: **현재 필터/검색이 적용된 결과 전체** (페이지네이션 무시)
- 파일 형식: **xlsx** (openpyxl 의존성 추가)
- 삭제 방식: **기준일 이전 수동 삭제** (관리자가 날짜 선택 → 확인 다이얼로그 → 일괄 삭제)
- 권한: `/admin/audit`은 이미 `require_admin`(SYSTEM_ADMIN 전용) — 추가 권한 제어 불필요
- 삭제 행위 자체를 감사로그에 기록 (기준일 + 삭제 건수)
- 시간 기준: **UTC 자정** — 저장/화면 표시 모두 UTC이므로 화면에 보이는 값과 일관

## 1. 엑셀 다운로드

### 공유 헬퍼 추출 (`app/admin/routes/audit.py`)
- `audit_list`의 쿼리 구성(검색 4경로: 행위자 이메일/서비스명/대상/상세 + 필터 2종: actor_type/action)을
  `_build_audit_query(pp) -> Select` 헬퍼로 추출.
- 행 구성(`_resolve_names` 호출 + actor/action/target/detail/ip 라벨링 dict 생성)을
  `_build_rows(db, logs) -> list[dict]` 헬퍼로 추출.
- `audit_list`와 다운로드 라우트가 두 헬퍼를 공유 — 화면과 파일 내용이 항상 일치.

### 다운로드 라우트
- `GET /admin/audit/export.xlsx` (require_admin).
- 동일 PageParams 파싱(q/필터/정렬), 페이지네이션 없이 전체 행 조회.
- openpyxl `Workbook(write_only=True)`로 생성, BytesIO → `Response`
  (media_type: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`,
  `Content-Disposition: attachment; filename="audit-log-YYYYMMDD-HHMM.xlsx"`).
- 컬럼(한글 헤더, 화면과 동일): 시각, 행위자, 활동, 대상, 상세, IP.
  - 행위자는 화면과 동일 규칙: USER=이메일, SERVICE=`외부 서비스 (서비스명)`(링크 없음 텍스트), 그 외 한글 라벨.
- 행 수 제한 없음(사내 도구 규모 — write_only 스트리밍으로 메모리 안전).

### UI
- 감사로그 툴바 옆 "엑셀 다운로드" 버튼(`<a>` 링크) — 현재 쿼리스트링(q, actor_type, action, sort, dir)을
  그대로 export URL에 전달.

## 2. 과거 데이터 삭제

### UI
- 감사로그 테이블 카드 하단에 날짜 input(`type=date`) + "이전 로그 삭제" 버튼(btn-danger).
  (엑셀 다운로드 버튼은 상단 툴바 우측 — 조회용과 파괴적 작업을 시각적으로 분리)
- 기존 `data-confirm` 다이얼로그 패턴: "기준일 이전 로그가 영구 삭제됩니다." 확인 후 제출.

### 삭제 라우트
- `POST /admin/audit/purge` (require_admin + CSRF).
- body: `before`(YYYY-MM-DD). 파싱 실패/미입력 → `?error=` 리다이렉트.
- `DELETE FROM audit_logs WHERE created_at < 기준일 00:00 UTC` 실행, 삭제 건수 확보.
- 삭제 직후 감사 기록: `record_audit(actor_type="USER", action="audit.purge",
  detail={"before": "YYYY-MM-DD", "deleted_count": n})` — 라벨 "감사로그 삭제".
  (purge 기록 자체는 현재 시각이므로 삭제 대상에 포함되지 않음)
- 응답: 303 → `/admin/audit?flash=N건 삭제됨` (기존 flash 토스트 패턴).

## 3. 의존성

- `openpyxl` 추가 (`pyproject.toml` dependencies).

## 4. 테스트

- 다운로드 e2e: 200 + xlsx 컨텐츠 타입/시그니처(PK zip magic), 필터 적용 시 해당 행만 포함,
  한글 헤더 존재(openpyxl로 다시 읽어 검증).
- 삭제 e2e: 기준일 이전만 삭제(이후 행 보존), `audit.purge` 감사 기록(detail 검증),
  잘못된 날짜 → error 리다이렉트, CSRF 필수.
- 권한: 기존 require_admin 미들웨어 커버 — SERVICE_MANAGER 접근 차단은 기존 테스트 패턴 준용.

## 변경하지 않는 것

- 감사로그 목록 화면의 기존 필터/검색/정렬/페이지네이션 동작.
- AuditLog 모델/스키마 (마이그레이션 없음).
- 외부 API, 알림, 스케줄러.
