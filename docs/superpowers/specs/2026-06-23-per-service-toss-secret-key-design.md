# 서비스별 toss_secret_key — 설계

- 날짜: 2026-06-23
- 작성자: seungjinhan (oasis@medisolveai.com)
- 상태: 승인 대기

## 목적

토스 시크릿 키가 **서비스마다 다르다**. 따라서 서비스 등록/수정 시 그 서비스의 `toss_secret_key`를 등록하고, 결제·승인·취소·갱신 등 서버가 토스 API를 호출하는 모든 지점에서 **해당 서비스의 키**로 호출하도록 전환한다. client_key는 각 서비스 프론트가 자체적으로 사용하므로 서버에는 저장하지 않는다. 전환 완료 후 전역 `TOSS_SECRET_KEY`(.env)를 제거한다.

## 확정 결정

1. **저장 키**: `toss_secret_key`만(서버측). client_key는 저장 안 함.
2. **암호화 저장**: `services.toss_secret_key_encrypted`(AES-GCM, 기존 `hmac_secret_encrypted`/`api_key_encrypted`와 동일). 평문은 DB·API응답·감사로그 어디에도 남기지 않는다.
3. **완전 전환(전역 폴백 없음)**: 서비스에 키가 없으면 결제/승인/갱신이 명확한 에러(`TOSS_KEY_NOT_CONFIGURED`)로 실패. 전역 `TOSS_SECRET_KEY`는 제거.
4. **등록 시 선택**: 등록 폼에서 키는 선택 입력(미설정 허용). 미설정이면 결제 시점에 위 에러로 강제. 수정에서 설정/교체 가능.
5. **감사로그 상세**: 키 설정/교체 액션을 감사로그에 기록하되 **시크릿 값은 절대 기록하지 않는다**(service_id·서비스명·actor·set/changed 구분·시각).
6. **아키텍처**: Provider + 호출지점 해석(아래 Approach A).

## 비범위 (YAGNI)

- client_key 저장/노출.
- 서비스별 `toss_api_base_url`/타임아웃(토스 엔드포인트는 공통 — 전역 유지).
- 기존 서비스 키 자동 백필(운영자가 어드민에서 등록). 단, 마이그레이션은 nullable 컬럼만 추가.

## 아키텍처 — Approach A: Provider + 호출지점 해석

현재 `app.state.toss`(전역 `HttpTossClient`)가 모든 결제 경로에 주입된다. 스케줄러 `process_due`는 여러 서비스 구독을 한 번에 처리하므로 전역 1개로는 서비스별 키를 못 쓴다.

- **`TossClientProvider`**(신규, `app/toss/provider.py`):
  - `for_service(service: Service) -> TossClient`: `service.toss_secret_key_encrypted`가 없으면 `TossKeyNotConfiguredError` 발생; 있으면 cipher로 복호화한 시크릿으로 `HttpTossClient` 생성·반환. **시크릿별 캐시**(dict[secret]→client)로 httpx 연결 재사용. 전역 `toss_api_base_url`/타임아웃 사용.
  - `aclose()`: 캐시된 모든 HttpTossClient 정리(앱 종료 시).
  - 생성자에 client 팩토리를 주입 가능하게 해 테스트에서 FakeTossClient를 반환하도록 한다.
- **`app.state.toss_provider`** = `TossClientProvider(cipher, base_url, timeouts, factory=HttpTossClient)`. 기존 `app.state.toss`(전역 단일) 제거.
- **서비스 계층 함수 시그니처는 유지**(`toss: TossClient`). 해석은 호출 지점에서:
  - **API 라우트**: `authenticate_service`가 이미 `service`(ApiContext)를 준다 → 새 의존성 `get_toss_for_service(ctx, provider)`가 `provider.for_service(ctx.service)` 반환. 기존 `get_toss`(전역) 대체.
  - **어드민 라우트**(수동결제·취소): 대상 구독/서비스 로드 후 `provider.for_service(service)`로 해석해 서비스 함수에 전달.
  - **스케줄러 `_renew_one` 등**: 이미 로드하는 `service`로 `provider.for_service(service)` 해석. 한 구독의 키 미설정/토스 오류가 전체 스윕을 멈추지 않도록 기존 per-sub 예외 격리 패턴 유지 + 실패 audit/로그.
- **새 에러** `TossKeyNotConfiguredError`(`app/toss/errors.py` 또는 core errors) → API/어드민에서 `TOSS_KEY_NOT_CONFIGURED` 응답, 스케줄러에서 해당 구독 결제 실패 처리.

## 변경 대상 (touchpoints)

### 1. 모델 + 마이그레이션
- `app/models/service.py`: `toss_secret_key_encrypted: Mapped[str | None]`(String(512), nullable). 주석: 서비스별 토스 시크릿(AES, 미설정 시 결제 거부).
- alembic: `services.toss_secret_key_encrypted` nullable add / downgrade drop.

### 2. registry(서비스 등록/수정 서비스 계층)
- `register_service(..., toss_secret_key: str | None = None)`: 전달 시 `cipher.encrypt`하여 저장. 설정된 경우 audit `service.toss_secret_key.set`(값 미기록).
- 신규 `set_toss_secret_key(db, cipher, service_id, toss_secret_key, actor_user_id)`: 신규 설정/교체. 기존에 값이 있었는지로 `set`/`changed` 구분해 audit. 빈 값 거부(`InputValidationError`). 커밋.
- (조회용) 서비스 상세/표시에 `toss_secret_key_configured: bool`만 노출(평문 미반환).

### 3. 어드민 UI (htmx)
- 서비스 등록 폼(`app/admin/templates/services/...` + `app/admin/routes/services.py`): `toss_secret_key` 입력(쓰기 전용). 등록 시 전달.
- 서비스 수정/상세: "토스 시크릿 키: 설정됨/미설정" 표시 + 설정/교체 입력(제출 시 `set_toss_secret_key`). 저장된 값은 다시 표시하지 않음(hmac/빌링키 정책과 동일).
- 모든 키 설정/교체 액션 → 감사로그(상세, 값 제외). 감사 라벨(`app/admin/audit_labels.py`)에 신규 액션 한글 라벨 추가.

### 4. Toss 클라이언트 해석 배선
- `app/toss/provider.py` 신규(위 Approach A).
- `app/main.py`: `app.state.toss` 생성 제거 → `app.state.toss_provider` 생성/`aclose`. 테스트 주입 경로(현재 `toss_client` 파라미터)도 provider 주입으로 대체(팩토리가 Fake 반환).
- `app/core/deps.py`: `get_toss`(전역) 제거 또는 provider 반환으로 대체 + `get_toss_for_service` 추가.
- `app/api/deps.py`/`app/api/v1/*`, `app/admin/routes/{payments,subscriptions}.py`, `app/scheduler/runner.py`/`app/services/renewals.py`: 해석 지점 수정.
- `TossKeyNotConfiguredError` + 에러 라벨/응답 매핑.

### 5. 전역 키 제거
- `app/core/config.py`: `toss_secret_key` 필드 제거(또는 미사용화). `toss_api_base_url`/타임아웃 유지.
- `.env`, `.env.dev`, `.env.prod`, `.env.example`에서 `TOSS_SECRET_KEY` 제거.
- 문서/주석의 "toss_secret_key는 .env에 보관" 서술 갱신.

### 6. 테스트
- 모델/마이그레이션 적용.
- registry: 등록 시 키 암호화 저장, `set_toss_secret_key` set/changed audit(시크릿 값이 audit·응답에 없음 검증), 빈 값 거부.
- provider: 키 있는 서비스→클라이언트 반환(+캐시 동일 인스턴스), 키 없는 서비스→`TossKeyNotConfiguredError`.
- 결제/승인/취소/갱신이 **해당 서비스 키로** 동작(FakeToss로 검증), 키 없는 서비스 결제 거부(API/어드민/스케줄러 각각).
- 회귀: 기존 결제·갱신 테스트가 provider 주입(Fake)로 통과.

### 7. 문서
- dev_manual(서비스 레지스트리/보안) + user_manual(서비스 등록 화면)에 toss_secret_key 등록·암호화·미설정 시 결제 거부 설명 추가, 재빌드.
- `docs/audit/` 워크로그.

## 데이터 흐름 (변경 후)

서비스 등록(어드민) → toss_secret_key 입력 → `cipher.encrypt` 저장 + audit(set) → 결제 요청 시 인증된 service로 `provider.for_service(service)` → 그 서비스 키의 `HttpTossClient`로 토스 승인/취소 호출. 키 미설정 서비스는 `TOSS_KEY_NOT_CONFIGURED`로 거부. 스케줄러 갱신도 구독별 service 키로 호출. 전역 키는 더 이상 존재하지 않음.

## 리스크/주의

- **운영 전환**: 배포 후 모든 활성 서비스에 키를 등록해야 결제가 동작(미등록 시 거부). 배포 순서: 마이그레이션 → 키 등록(어드민) → .env 키 제거.
- **시크릿 비노출**: 폼 재표시·API 응답·감사로그·로그 어디에도 평문 금지.
- **캐시 무효화**: 키 교체(`set_toss_secret_key`) 시 provider 캐시가 옛 시크릿 클라이언트를 들고 있을 수 있음 → 캐시 키를 시크릿 값으로 두면 새 시크릿은 자동으로 새 엔트리(옛 엔트리는 유휴) → 단순/안전. 교체 직후에도 정상 동작.
