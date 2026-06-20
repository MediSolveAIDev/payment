# 요청 007 — 서비스 담당자 계정 선택 + 감사로그 개선 설계

날짜: 2026-06-07
상태: 승인됨
요청: docs/requests/007.md

## 목표

1. 서비스 등록 시 담당자를 계정 리스트에서 멀티 선택 (+대표 계정 지정)
2. 서비스 상세 개요에서 담당자 표시 제거 — 담당자 계정 섹션으로 일원화 (대표 지정/수정 가능)
3. 감사로그: 외부서비스 행위자에 서비스명 표시+상세 링크, 필터(행위자/활동 select)와
   like 검색(행위자/대상/상세) 개선

## 1. 서비스 등록 — 담당자 계정 선택 (요청 1.1, 1.2)

### 폼 (`app/admin/templates/services/new.html`)
- "담당자 이메일" 입력 제거.
- **담당자 계정**: SERVICE_MANAGER 역할 계정 전체를 체크박스 리스트로(멀티 선택,
  `manager_ids`). 이메일 표시.
- **대표 계정**: `primary_user_id` select (필수) — 알림 메일 수신처임을 도움말로 표시.
  JS 없이 동작: 서버가 "대표 계정이 체크 목록에 없으면 자동 포함" 처리.
- SERVICE_MANAGER 계정이 0명이면 폼 대신 안내 + `/admin/users/new` 링크.

### 서비스 계층 (`app/services/registry.py`)
- `register_service(db, cipher, email_sender, *, name, allowed_ips,
  manager_user_ids: list[uuid.UUID], primary_user_id: uuid.UUID, actor_user_id)` 로 변경
  (`manager_email`, `base_url` 파라미터 제거).
- 검증: primary가 manager_user_ids에 없으면 자동 포함. 목록이 비면
  `InputValidationError("담당자를 1명 이상 선택해야 합니다")`. 각 계정은 존재하고
  SERVICE_MANAGER 역할이어야 함(아니면 검증 에러).
- `Service.manager_email` = **대표 계정 이메일** 저장 (renewals/webhooks 알림 수신처 —
  해당 코드 무변경).
- 선택 계정들에 서비스 할당: 계정에 주 서비스(`User.service_id`)가 없으면 주로 설정,
  있으면 `UserService` junction 추가 (기존 `accounts.assign_service`와 동일 규칙 —
  가능하면 해당 함수 재사용, 단 커밋 단위는 register_service가 묶음).
- **신규 계정 자동 생성 + 설정 메일 발송 제거** — `email_sender` 파라미터와
  `IssuedCredentials.setup_token`/`email_sent` 필드 제거(또는 항상 None — 사용처 정리).
  keys.html의 메일 발송 flash도 제거.
- 감사 로그 `service.register`의 detail에 `manager_ids` 수 포함(기존 name 유지).

### 라우트 (`app/admin/routes/services.py` services_new/services_create)
- new: SERVICE_MANAGER 계정 목록을 폼에 전달.
- create: `manager_ids`(getlist) + `primary_user_id` 파싱 → register_service 호출.
  검증 에러 시 폼 재렌더(계정 목록 포함).

## 2. 서비스 상세 — 담당자 일원화 (요청 2)

- 개요 카드의 `담당자: {{ service.manager_email }}` kv 제거.
- "담당자 계정" 섹션:
  - 대표 계정 행에 **"대표" 배지** (manager_email과 이메일 일치 기준).
  - 비대표 행에 **"대표 지정"** 버튼 → `POST /admin/services/{id}/primary-manager`
    (body: user_id, CSRF) → `registry.set_primary_manager(db, service_id, user_id)`:
    해당 계정이 이 서비스 담당자인지 검증 후 `manager_email` 갱신 + 감사 로그
    `service.set_primary_manager` (라벨: "대표 담당자 지정") → 상세로 리다이렉트.
  - **대표 계정 행은 삭제(담당 해제) 버튼 숨김** — 다른 계정을 대표로 지정한 뒤 해제.
    (서버측도 `unassign_service` 호출 전 라우트에서 대표 여부 검사해 에러 메시지.)
- 서비스 리스트(`services/_table.html`)의 담당자 컬럼은 manager_email(대표) 표시 유지.

## 3. 감사로그 (요청 3)

### 데이터
- `AuditLog.actor_service_id: Mapped[uuid.UUID | None]` 컬럼 추가(nullable) + Alembic
  마이그레이션 1건.
- `record_audit(..., actor_service_id: uuid.UUID | None = None)` 파라미터 추가.
- SERVICE 행위 기록 지점(`app/services/subscriptions.py`의 actor_type="SERVICE" 6곳)에
  `actor_service_id=service.id` 전달. (이외 SERVICE 행위 지점이 있으면 동일 적용 —
  구현 시 grep으로 전수 확인.)

### 표시 (`app/admin/routes/audit.py` + `audit/_table.html`)
- `_resolve_names`에 actor_service_id 배치 resolve(Service.name) 추가.
- row에 `actor_service_id`/`actor_service_name` 추가. 행위자 셀:
  - USER: 기존(이메일)
  - SERVICE + 서비스명 있음: `외부 서비스 (<a href="/admin/services/{id}">서비스명</a>)`
  - SERVICE + 없음(과거 로그): "외부 서비스" (기존)
  - 행 자체 클릭 동작은 없음(감사 테이블은 링크만).

### 필터/검색
- 필터 select 2개: 행위자(actor_type — 기존) + **활동(action)** — `ACTION_LABELS` 전체를
  (액션키, 한글라벨) 옵션으로. `filter_keys=("actor_type", "action")`,
  `AuditLog.action == 값` 필터.
- 검색(q) like 대상 변경: 기존 `action ilike | target_id ilike` →
  - 행위자: `actor_user_id IN (SELECT id FROM users WHERE email ILIKE %q%)`
    OR `actor_service_id IN (SELECT id FROM services WHERE name ILIKE %q%)`
  - 대상: `target_id ILIKE %q%`
  - 상세: `CAST(detail AS TEXT) ILIKE %q%` (JSONB 텍스트 캐스팅)
  - 플레이스홀더: "행위자·대상·상세 검색"

## 에러 처리

- 등록: 담당자 0명/존재하지 않는 계정/SERVICE_MANAGER 아님 → 폼 에러 표시.
- 대표 지정: 담당자가 아닌 계정 → `?error=` 리다이렉트. 대표 해제 시도 → 에러 메시지.

## 테스트

- registry 단위/통합: 멀티 할당(주/추가 규칙), manager_email=대표 이메일, 계정 미생성
  (User 수 불변), 검증 에러 3종.
- e2e: 등록 폼(체크박스/select 렌더, 계정 0명 안내), 등록 → 상세 담당자 목록 반영,
  대표 배지/대표 지정/대표 해제 차단, 개요 담당자 kv 부재.
- 감사: SERVICE 로그에 actor_service_id 기록(구독 생성 통합 테스트), 목록에 서비스명
  링크 렌더, 활동 필터, q 검색(이메일/서비스명/detail 각 1건).
- 기존 테스트 갱신: 서비스 등록 e2e 전반(`manager_email` POST → `manager_ids`+
  `primary_user_id`), registry 테스트, 이메일 flash 테스트(서비스 등록 건 제거),
  factories.create_service는 모델 직접 생성이라 영향 적음(확인).

## 변경하지 않는 것

- 알림 발송 로직(renewals/webhooks — `service.manager_email` 사용) 무변경.
- 외부 API(/api/v1), 인증, sample_service 무변경.
- 모델 변경은 `AuditLog.actor_service_id` 추가뿐 (Service 모델 무변경 —
  manager_email 의미만 "대표 계정 이메일"로 재정의).
