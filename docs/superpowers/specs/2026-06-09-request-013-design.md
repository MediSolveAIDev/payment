# 요청 013 설계 — 어드민 전체설정 + 서비스 취소정책 UI + 요금제 자동결제안함·추가정보

날짜: 2026-06-09
상태: 승인됨
요청: docs/requests/013.md

## 결정 사항
- 전역 설정은 **DB 단일행 `GlobalSettings`** 에 저장(런타임 변경). config 환경변수는 최초 시드 기본값으로만 사용.
- 어드민 IP 제한: 빈 목록=제한 없음, 저장 시 현재 접속 IP 미포함이면 거부(lockout 방지). 외부 API(서비스별 allowed_ips)는 무관.
- 킬스위치: `server_disabled=True`면 외부 API(authenticate_service)가 503 + `{code:"SERVER_DISABLED", reason}`. 어드민은 영향 없음. 전환 시 SYSTEM_ADMIN 비밀번호 재확인 + 사유.
- 요금제 "자동결제 안함"(auto_renew=False): 첫 주기 결제 후 `next_billing_at=None` → 주기 종료 시 자동 EXPIRED. trial과 배타.
- 요금제 추가정보: `extra_info` JSONB, 폼 textarea(`key: value` 줄별) → JSON. 외부 PlanResponse에 노출.

## A. GlobalSettings (모델 + 마이그레이션 + 접근 헬퍼)
### 모델 `app/models/global_settings.py`
- 단일 행 강제: 고정 PK(`id` Integer PK = 1, 또는 `Boolean unique` 싱글톤). 본 설계는 `id: int PK`(항상 1) 사용.
- 컬럼:
  - `retry_limit: int`(default config.retry_limit), `retry_interval_hours: int`, `suspended_grace_days: int` — 자동결제 재시도
  - `admin_allowed_ips: list`(JSONB, default list) — 어드민 접속 허용 IP(빈=제한없음)
  - `server_disabled: bool`(default False), `disabled_reason: str|None`, `disabled_at: datetime|None`, `disabled_by: uuid|None`(User) — 킬스위치
  - TimestampMixin
- `app/models/__init__.py`에 export.

### 마이그레이션 `e5f6a7b8c9d0_global_settings`(down=d4e5f6a7b8c9)
- `global_settings` 테이블 생성. (시드 행은 런타임 get_or_create로 처리 — 마이그레이션에서 직접 insert하지 않음.)

### 접근 헬퍼 `app/services/app_settings.py`
- `async def get_global_settings(db) -> GlobalSettings`: id=1 행 조회, 없으면 config 기본값으로 생성 후 commit(get_or_create).
- `async def update_retry_settings(db, *, retry_limit, retry_interval_hours, suspended_grace_days, actor_user_id)`: 검증(retry_limit>=0, interval>=1, grace>=0) + 감사 `settings.retry_updated`.
- `async def update_admin_ips(db, *, ips: list[str], current_ip: str, actor_user_id)`: IPv4 검증(registry._validate_ips 패턴 재사용), 비어있지 않은데 current_ip 미포함이면 InputValidationError("현재 접속 IP를 포함해야 합니다"). 감사 `settings.admin_ips_updated`.
- `async def set_server_disabled(db, *, disabled: bool, reason: str|None, actor_user: User, password: str)`: verify_password(actor_user.password_hash, password) 실패 시 AuthenticationError. disabled=True면 reason 필수(InputValidationError). 필드 설정 + disabled_at/by. 감사 `server.disabled`/`server.enabled`.
- 감사 라벨 추가(audit_labels): settings.retry_updated/admin_ips_updated, server.disabled, server.enabled.

## B. 자동결제 재시도 설정 적용
- `app/services/renewals.py` `_Cfg`: 현재 settings(config)에서 읽음 → **GlobalSettings(DB)에서 읽도록**. `_Cfg.__init__(self, gs: GlobalSettings | None)`로 변경하거나, `_Cfg.from_global(gs)` 추가. 값 없으면 기존 DEFAULT 상수 fallback 유지(테스트 편의).
- `process_due(session_factory, ...)`: 시작 시 한 세션으로 `gs = await get_global_settings(db)` 로드 → `_Cfg(gs)` 구성. (현재 `now`/settings 주입 방식과 호환.)
- `scheduler/runner.py` `run_renewals`: 변경 최소 — process_due 내부에서 DB 로드하므로 별도 전달 불필요. (process_due가 settings 인자를 받던 경우 GlobalSettings 로드로 대체.)
- 적용 효과: 시도횟수(retry_limit)·주기(retry_interval_hours)가 `_handle_charge_failure`의 SUSPENDED 전환·다음 재시도 스케줄에 반영. suspended_grace_days는 `_expire_suspended` 판정에 반영. **다음 배치부터 적용**.

## C. 어드민 접속 IP 제한
- 적용 지점: `app/admin/deps.py` `require_user`(모든 어드민 라우트 진입점). 세션 검증 후 `gs.admin_allowed_ips`가 비어있지 않으면 `get_client_ip(request)`가 목록에 있어야 함, 아니면 403(PermissionDeniedError 또는 admin 전용 403 렌더). 로그인 페이지(`/admin/login`)·정적자원은 제외(require_user를 거치지 않음 — 확인).
- `get_client_ip`는 `app/api/deps.py`에 있음 → 공용 위치로 import(또는 deps에서 재사용). trust_proxy 설정 동일 적용.
- 주의: IP 차단은 로그인 여부와 독립적으로 보안 강화 — 단, 로그인 화면 자체는 접근 가능해야 운영자가 상황 파악(차단은 인증 후 컨텍스트에서; 로그인 후 IP 불일치면 403). MVP: require_user 통과(세션 유효) 후 IP 검사.

## D. 킬스위치 (서비스 API 게이트 + 전환)
- 신규 예외 `app/core/errors.py` `ServerDisabledError(DomainError)`: http_status=503, code="SERVER_DISABLED". 메시지=사유.
- 게이트: `app/api/deps.py` `authenticate_service` **진입 직후**(API키 검증 전에) `gs = await get_global_settings(db); if gs.server_disabled: raise ServerDisabledError(gs.disabled_reason or "서비스 점검 중입니다")`. 모든 외부 API(구독/결제/취소/조회)가 authenticate_service 의존 → 일괄 차단. 응답 본문에 reason 포함(errors 핸들러가 message로 노출).
- 어드민은 authenticate_service 미사용 → 영향 없음(킬스위치 해제 가능).
- 전환 UI는 E의 설정 화면.

## E. 어드민 전체설정 화면
- 신규 라우트 파일 `app/admin/routes/settings.py`, 라우터 등록(app/admin/__init__ 또는 main의 admin 라우터 묶음).
  - `GET /admin/settings`(require_admin): 현재 GlobalSettings 렌더(재시도/어드민IP/킬스위치 폼). 템플릿 `settings/index.html`.
  - `POST /admin/settings/retry`(require_admin, csrf): update_retry_settings → redirect.
  - `POST /admin/settings/admin-ips`(require_admin, csrf): update_admin_ips(current_ip=get_client_ip) → redirect/에러.
  - `POST /admin/settings/server-toggle`(require_admin, csrf): set_server_disabled(actor_user, password, disabled, reason) → redirect/에러.
- 좌측 네비(base 템플릿)에 "전체 설정" 링크(SYSTEM_ADMIN만 노출).
- require_admin은 SYSTEM_ADMIN 전용(기존 require_role(SYSTEM_ADMIN)).

## F. 서비스 상세 취소정책 UI
- `app/admin/templates/services/detail.html`: 허용 IP 카드 안의 취소정책 폼을 **별도 `<div class="card">`** 로 분리. 한 줄 배치: `(체크박스) 일반결제 취소 허용` + `취소 수수료 [__]%`(허용 시) — 한 form, 한 row(flex/inline). 라우트·로직 변경 없음(POST /services/{id}/cancel-policy 그대로).

## G. 요금제 자동결제안함 + 추가정보
### 모델 `app/models/plan.py`
- `auto_renew: Mapped[bool]`(default True, server_default "true") — False면 첫 주기 후 자동연장 안 함.
- `extra_info: Mapped[dict]`(JSONB, default dict, server_default "{}") — 서비스단 요금제 설명용 key/value.
- 마이그레이션 동일 리비전(e5f6a7b8c9d0) 또는 별도 — 본 설계는 같은 리비전에 plan 2컬럼 + global_settings 테이블 함께(또는 plan은 별도 리비전 f6a7...). **구현 단순화를 위해 별도 리비전 2개**: `e5f6...global_settings`, `f6a7...plan_autorenew_extra`. (체인: d4e5 → e5f6 → f6a7.)

### 구독 로직 `app/services/subscriptions.py`
- `create_subscription`: 요금제 auto_renew=False면 첫 결제 후 `next_billing_at=None`(자동갱신 비대상), 상태 ACTIVE 유지. trial과 배타는 plan 검증에서 차단(생성 시 trial 요청과 auto_renew=False 조합은 plan 단계에서 이미 배제).
- 만료 경로: `app/services/renewals.py` `process_due`에 **비자동갱신 만료** 추가 — `status==ACTIVE AND next_billing_at IS NULL AND current_period_end <= now` 구독을 EXPIRED 전환(`_expire_subscription` 재사용, reason="non_renewing_period_end"). 조회 쿼리(_due 후보)에 이 조건 추가하거나 별도 스윕.
- access_allowed: ACTIVE 동안 true, 만료 후 EXPIRED→false(기존 규칙 그대로).

### 요금제 검증·폼 `app/services/plans.py`, `app/admin/routes/plans.py`, `templates/plans/form.html`
- 검증: auto_renew=False와 trial_enabled=True 동시 → InputValidationError("자동결제 안함 요금제는 체험을 설정할 수 없습니다").
- 폼: "자동결제 안함" 체크박스(체크 시 trial 입력 비활성/무시) + "추가정보" textarea(placeholder `키: 값` 한 줄씩).
- `_form_plan_fields`/_form 파싱: auto_renew(체크박스), extra_info(textarea 파싱: 각 줄 `key: value`/`key=value`, 빈 줄 무시, key 중복 시 마지막, 형식 오류 줄은 InputValidationError). create_plan/update_plan 시그니처에 auto_renew/extra_info 추가(update는 _UNSET 패턴).

### 외부 API `app/schemas/api.py`, `app/api/v1/plans.py`
- `PlanResponse`에 `auto_renew: bool`, `extra_info: dict` 추가. from_model에서 채움. GET /plans 응답에 노출(서비스단 설명용).

## 테스트
- 통합: get_global_settings get_or_create, update_retry/admin_ips(lockout 거부)/server_toggle(비번 실패·성공), renewals가 DB 재시도값 사용(retry_limit 변경→SUSPENDED 전환 횟수 반영), 킬스위치 시 authenticate_service 503+reason, auto_renew=False 구독 첫결제 후 next_billing None·주기 종료 EXPIRED, trial 배타 거부, extra_info 파싱(정상/형식오류), PlanResponse 노출.
- e2e: /admin/settings 렌더·각 폼 저장, 어드민 IP 차단(불일치 403)·lockout 거부, 킬스위치 토글 후 외부 API 503, 서비스상세 취소정책 카드 분리, 요금제 폼 auto_renew/extra_info 저장.

## 매뉴얼
- 신규 `13-global-settings.md`(전체 설정: 재시도·어드민IP·킬스위치). 06(비자동갱신 만료 경로)·03(요금제 auto_renew/extra_info)·08(킬스위치 503)·01(취소정책 카드 UI)·00(GlobalSettings 데이터모델·엔드포인트) 갱신.

## 변경하지 않는 것
- 결제 3원칙, 구독 1건 인덱스, 서비스별 allowed_ips(외부 API IP검증), 첫결제/상시할인 계산.
