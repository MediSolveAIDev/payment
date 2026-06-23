# 워크로그: 서비스별 toss_secret_key 구현 (T1~T9)

**날짜**: 2026-06-23  
**작업자**: seungjinhan  
**작업 유형**: 기능 개발 (보안 강화 — 결제 키 서비스 격리)

---

## 목적

기존에는 서버 전역 환경변수 `TOSS_SECRET_KEY` 하나로 모든 서비스의 토스 결제 호출을 처리했다.
이 방식은 키가 노출될 경우 모든 서비스가 영향을 받는 보안 취약점이 있다.

이번 작업은 **서비스별 개별 Toss 시크릿 키**를 어드민에서 등록하고 AES 암호화로 저장하여,
키를 서비스 단위로 격리하고 전역 키를 완전히 제거하는 것을 목적으로 한다.

---

## 주요 결정 사항

| 결정 | 세부 내용 |
|------|-----------|
| 서비스별 개별 키 저장 | `services.toss_secret_key_encrypted` 컬럼에 AES-256-GCM 암호화 저장 |
| 전역 키 제거 | `TOSS_SECRET_KEY` 환경변수·config 항목 완전 제거 |
| 키 미설정 시 결제 거부 | `TossKeyNotConfiguredError` (HTTP 422, 코드 `TOSS_KEY_NOT_CONFIGURED`) |
| 등록은 선택(즉시 강제 안 함) | 서비스 등록 직후 즉시 결제 없이도 운영 가능하도록 nullable 컬럼 |
| 감사 로그 값 미기록 | set/changed 사실만 기록, 키 평문 값은 감사 detail에 절대 미기록 |
| client_key(토스 위젯 공개키) 미저장 | 프론트 자체 사용 공개키이므로 서버 저장 불필요 |
| 평문 미노출 | 저장 후 화면·로그 어디에도 평문 노출 없음 |

---

## 변경 요약 (T1~T9)

### T1: 모델 + 마이그레이션
- `app/models/service.py` — `toss_secret_key_encrypted: Mapped[str | None]` 컬럼 추가
- `alembic/versions/` — 마이그레이션 파일 생성 (컬럼 ADD, nullable, no default)

### T2: TossClientProvider + TossKeyNotConfiguredError
- `app/toss/provider.py` — `TossClientProvider` 클래스 신규 작성
  - `for_service(service) -> TossClient`: 서비스의 `toss_secret_key_encrypted` 복호화 후 클라이언트 생성
  - `toss_secret_key_encrypted` 없으면 `TossKeyNotConfiguredError` 발생
  - `aclose()`: 앱 종료 시 클라이언트 정리
- `app/core/errors.py` — `TossKeyNotConfiguredError` 클래스 추가 (`code="TOSS_KEY_NOT_CONFIGURED"`, `http_status=422`)

### T3: registry 등록 + set_toss_secret_key
- `app/services/registry.py`
  - `register_service()` — `toss_secret_key` 선택 파라미터 추가 (설정 시 AES 암호화 저장 + 감사 로그)
  - `set_toss_secret_key(db, cipher, *, service_id, toss_secret_key, actor_user_id)` 신규 추가
    - 기존 키 존재 여부로 `service.toss_secret_key.set` / `service.toss_secret_key.changed` 구분
    - 감사 detail에 키 평문 미기록

### T4: 앱 배선 (lifespan)
- `app/main.py` — `TossClientProvider` 인스턴스 생성·lifespan 연결
- `app/api/deps.py` — `get_toss_provider()` 의존성 추가 (기존 `get_toss_client()` 대체 준비)

### T5: API·어드민 라우트 전환
- `app/api/v1/subscriptions.py` — `TossClientProvider.for_service(service)` 로 전환
- `app/api/v1/payments.py` — 동일 전환
- 어드민 결제 관련 라우트 — 동일 전환

### T6: 스케줄러 전환
- `app/services/renewals.py` — 자동 갱신 배치에서 `provider.for_service(service)` 사용
  - 키 미설정 서비스: `TossKeyNotConfiguredError` 발생 → 갱신 실패 처리(SUSPENDED 전환 등)

### T7: 전역 키 제거 + 에러 매핑
- `app/core/config.py` — `toss_secret_key` Settings 필드 제거
- `.env.example` — `TOSS_SECRET_KEY` 항목 제거
- `app/api/errors.py` — `TossKeyNotConfiguredError` → `422` 응답 매핑 추가

### T8: 어드민 UI + 감사 라벨
- `app/admin/routes/services.py` — `POST /admin/services/{id}/toss-secret-key` 라우트 추가
  - 폼 필드 `toss_secret_key`: 빈 값이면 변경 없음(기존 키 유지)
- `app/admin/templates/services/detail.html` — Toss 시크릿 키 카드 추가
- 감사 로그 한글 라벨: `service.toss_secret_key.set`, `service.toss_secret_key.changed`

### T9: 문서 + 워크로그 (이번 태스크)
- `docs/manual/dev_manual/09-services-registry.md` — 섹션 11 "서비스별 Toss 시크릿 키 관리" 추가, 컬럼 표 갱신, 섹션 번호 재정리
- `docs/manual/dev_manual/03-auth-and-security.md` — 5-1절 AES 암호화 저장 대상 목록에 `toss_secret_key_encrypted` 추가 + 변경 배경 주석
- `docs/manual/dev_manual/admin/03-services.md` — 서비스 등록 흐름에 Toss 키 등록 단계 추가, 라우트 표 갱신, 주의사항 섹션 추가
- `docs/user_manual/10-install-deploy.md` — `TOSS_SECRET_KEY` 항목 제거됨 표기, 토스 키 등록 안내 추가
- 빌드: `uv run --with markdown python docs/user_manual/build.py` (19개 문서 → HTML)
- 빌드: `uv run --with markdown python docs/manual/dev_manual/build_html.py` (30개 문서 → HTML)

---

## 배포 순서 주의

> 서비스별 키 체계로 전환 시 다음 순서를 **반드시** 지킬 것.

```
1. alembic upgrade head
   — services 테이블에 toss_secret_key_encrypted 컬럼 추가

2. 어드민 콘솔 → 각 서비스 상세 → [Toss 시크릿 키] 카드
   — 서비스마다 개별 토스 시크릿 키 입력 후 저장

3. .env / .env.prod 에서 TOSS_SECRET_KEY 항목 제거
```

**주의**: 2번(키 등록) 전에 3번(전역 키 제거)을 먼저 하면
키가 없는 서비스의 모든 결제가 422로 거부된다.

---

## 검증

- `uv run pytest` 전체 테스트: **623 passed** (실패 없음)
- 빌드 출력:
  - `docs/user_manual/`: 19개 문서 HTML 재생성 완료
  - `docs/manual/dev_manual/`: 30개 문서 HTML 재생성 완료

---

## 관련 문서

- 설계 문서: `docs/superpowers/specs/2026-06-23-per-service-toss-secret-key-design.md`
- 구현 계획: `docs/superpowers/plans/2026-06-23-per-service-toss-secret-key.md`
- 핵심 구현 파일:
  - `app/models/service.py` (toss_secret_key_encrypted 컬럼)
  - `app/toss/provider.py` (TossClientProvider)
  - `app/core/errors.py` (TossKeyNotConfiguredError)
  - `app/services/registry.py` (set_toss_secret_key, register_service 수정)
  - `app/admin/routes/services.py` (toss-secret-key 라우트)
  - `app/services/renewals.py` (갱신 배치 전환)
  - `app/services/reconciliation.py` (정산 전환)
  - `app/services/webhooks.py` (웹훅 전환)
