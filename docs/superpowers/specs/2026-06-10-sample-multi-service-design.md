# 설계 — 샘플 서비스 다중 서비스 테스트 (서비스 목록 선택 + 키 저장)

날짜: 2026-06-10
상태: 승인됨
배경: sample_service가 .env의 단일 SERVICE_API_KEY로만 테스트 → 전체 서비스를 선택해 테스트하도록 변경.

## 결정 사항
- 결제 서버에 **무인증 `GET /api/v1/services`** 추가 — `[{id, name, status}]`만 반환(키/시크릿·구독정보 미포함).
- 키/시크릿은 서버가 일괄 노출하지 않음. 운영자가 서비스별로 입력하면 샘플 DB에 저장(다시 묻지 않음).
- 인증 실패(401) 시 "키 변경됨" 경고 배너 + 키 재입력 폼.
- 목적: 전체 서비스 대상 테스트(활성 서비스 전환).

## A. 결제 서버 (payment_system, main)
### 엔드포인트
- `app/api/v1/services.py`(신규): `GET /services` — 의존성 없음(무인증). `select(Service)` 전체를 id·name·status로 직렬화해 `{"services": [...]}` 반환. 키/해시/암호문·구독 등 민감정보 미포함. 정렬: name.
- `app/api/v1/__init__.py`에 `router.include_router(services.router, tags=["services"])` 추가.
- 주의: 이 엔드포인트는 서비스 식별을 위한 목록만 제공(테스트 도구 편의). 인증/HMAC 흐름·다른 엔드포인트는 불변.

### 테스트
- e2e: 인증 헤더 없이 `GET /api/v1/services` 200 + 등록된 서비스 id/name 포함, **응답에 api_key/hmac_secret/secret 류 키 미포함** 단언.

## B. 샘플 서비스 (sample_service, 별도 repo)
### 모델
- `ServiceCredential`(shop): `service_id`(CharField unique — 서버 UUID 문자열), `name`, `api_key`, `hmac_secret`, `created_at`. 마이그레이션 0003.

### payment_client
- `_request`가 활성 서비스 자격증명을 사용하도록 변경. 방식: 모듈 함수에 `creds: tuple[str,str] | None = None`(api_key, hmac_secret) 인자 추가, `_request`가 creds 없으면 `settings.SERVICE_*` 폴백.
  - 공개 함수(get_plans/create_subscription/.../get_payments/cancel_one_off_payment)에 `creds=None` 전달 인자 추가.
- 신규 `list_services() -> list[dict]`: `GET /api/v1/services`(무인증, creds 불필요) → `["services"]`.

### 뷰/세션/흐름
- 활성 서비스: `request.session["service_id"]`(서버 UUID). 헬퍼 `_active_cred(request) -> ServiceCredential | None`(session의 service_id로 DB 조회).
- **서비스 선택 페이지** `/services`(`services_view`): `list_services()`로 서버 목록 + 각 서비스의 저장된 ServiceCredential 매칭. 화면: 서비스별 [선택](저장키 있음) 또는 [키 입력 폼](api_key+hmac_secret). 서버 연결 실패는 메시지.
  - `POST /services/select`(`service_select_view`): service_id 받아 session 설정 → /plans.
  - `POST /services/save-key`(`service_save_key_view`): service_id+name+api_key+hmac_secret 받아 ServiceCredential upsert(get_or_create/update) → session 활성화 → /plans. 한번 저장하면 다음부터 [선택]만으로 활성화(다시 묻지 않음).
- 로그인 후 활성 서비스 없으면 `/services`로 유도(요금제/구독/결제 뷰 진입 시 가드). 로그인 자체는 기존 이메일 흐름 유지.
- 모든 payment_client 호출에 활성 서비스 creds 전달. 활성 서비스가 ServiceCredential 없으면(목록 선택만 하고 키 미입력) /services로 보냄.
- **인증 실패 처리**: payment_client 호출이 `PaymentAPIError(status==401)`이면 → "이 서비스(name)의 key가 변경되었습니다. 다시 입력하세요" 경고 + 키 재입력 폼이 있는 화면으로(예: /services?reauth=<service_id> 또는 전용 reauth 화면). 폼 제출 시 save-key로 갱신.
  - 공통 처리: 뷰의 except에서 status==401이면 reauth 유도. 헬퍼로 일관 처리.
- 내비(base.html): 활성 서비스명 표시 + "서비스 변경"(/services) 링크.
- `.env` 단일 키: 폴백으로 유지(활성 서비스 미설정 시 기존 단일 서비스 동작 — 하위호환).

### 화면
- `shop/templates/shop/services.html`(신규): 서버 서비스 목록 + 저장상태 + 선택/키입력 폼 + (reauth 시) 경고 배너·해당 서비스 키 재입력 강조. 매뉴얼 안내(이 화면이 GET /api/v1/services 호출).

### 테스트(shop/tests.py)
- list_services 클라이언트 경로.
- /services 렌더(list_services mock), 키 저장(ServiceCredential 생성/갱신), 선택 시 session 설정.
- 활성 서비스 creds로 payment_client 호출(서명에 해당 키 사용) — _request creds 우선.
- 401 → reauth 유도(경고+폼).

## 변경하지 않는 것
- 서버 HMAC 인증·구독/결제/요금제 로직. 서버는 목록 엔드포인트만 추가.
- 토스 카드등록(authKey) 흐름.
