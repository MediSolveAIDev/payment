# 04. 코드 지도 — "이걸 고치려면 어디를 보나"

> 목표: 변경 요청을 받았을 때 30초 안에 열어야 할 파일을 안다.

## 1. 변경 유형별 진입 지점

| 바꾸고 싶은 것 | 여기를 고친다 | 주의 |
|---|---|---|
| 금액 계산(할인·수수료·기간) | `app/services/billing_math.py` | 화면·API·실행이 전부 이 함수를 공유 — 여기만 고치면 전체 일관 |
| 구독 상태/전이 규칙 | `app/models/enums.py` + `app/services/transitions.py` | 상태 추가는 08장 레시피 D의 체크리스트 필수 |
| 갱신·재시도·만료 정책 | `app/services/renewals.py` (+ 어드민 전체설정=GlobalSettings) | 재시도 횟수/간격/유예는 DB 설정 — 코드 수정 불필요 |
| 외부 API 추가/응답 필드 | `app/schemas/api.py` + `app/api/v1/…` + `app/services/…` | 레시피 B |
| 어드민 화면 | `app/admin/routes/…` + `templates/…` | 레시피 C — 라우터 등록 순서 주의 |
| 목록 검색/필터 | `app/admin/filters.py` | 목록·엑셀·탭이 공유 — 한 곳만 고친다 |
| 외부 API 인증 정책 | `app/api/deps.py` (authenticate_service) | 6단계 검증 순서에 이유가 있음 — 06장 |
| 공통 의존성(get_db 등) | `app/core/deps.py` | api/admin 양쪽이 사용 |
| 오류 메시지/HTTP 코드 | `app/core/errors.py` | DomainError 서브클래스가 HTTP 코드를 들고 있음 |
| 토스 API 연동 | `app/toss/client.py` (+ `fake.py`도 같이!) | Fake를 안 고치면 테스트가 현실과 어긋남 |
| 이메일 문구 | `app/services/renewals.py`·`reconciliation.py` 호출부 | 발송 구현은 `app/notifications/email.py` |
| 배치 주기/락 | `app/scheduler/runner.py` + Settings | heartbeat 로직 주의 (07장) |
| DB 컬럼/인덱스 | `app/models/…` + `alembic/versions/` 새 리비전 | 레시피 A |

## 2. services/ 모듈별 책임 한 줄 요약

| 모듈 | 책임 |
|---|---|
| `subscriptions.py` | 구독 생성·취소·재개·수동결제·카드변경·강제취소·연장 |
| `renewals.py` | 5분 배치 — 자동갱신·재시도·각종 만료 처리 |
| `reconciliation.py` | 결과불명(PENDING) 결제를 토스 재조회로 확정하는 정산 스윕 |
| `payments.py` | 단건(1회성) 결제 생성·취소 |
| `transitions.py` | 구독 상태 전이의 단일 출처(허용표+불변식) |
| `billing_math.py` | 모든 금액·기간 계산의 단일 출처 |
| `registry.py` | 서비스(테넌트) 등록·키 발급/회전/조회·취소정책 |
| `plans.py` | 요금제 CRUD·검증·보너스 일수 |
| `accounts.py` | 어드민 계정·담당 서비스 배정 |
| `auth.py` | 어드민 로그인·세션(Redis)·비밀번호 |
| `audit.py` | 감사 로그 기록/조회 — **모든 변경은 record_audit를 남긴다** |
| `settlement.py` / `dashboard.py` | 월별 정산 집계 / 대시보드 집계 |
| `app_settings.py` | 전역설정(재시도 정책·킬스위치·어드민 IP) |
| `locks.py` | Redis 분산 락 + 배치 공유 상수 |
| `webhooks.py` | 토스 웹훅 처리(멱등+페이로드 불신) |

## 3. 이 프로젝트의 코딩 규약 (지켜야 기존 코드와 결이 맞는다)

- **모든 변경 코드에 한국어 주석/docstring** — "왜"를 적는다. 기존 밀도를 따라갈 것.
- 상태 변경은 `transition()`, 금액은 `billing_math`, 감사는 `record_audit` — **우회 금지**.
- 외부(토스) 호출 중에는 DB 트랜잭션/FOR UPDATE를 쥐지 않는다 — 호출 전 commit,
  호출 후 재취득+재검증 (renewals `_renew_one`이 표준 패턴).
- 어드민 목록은 `PageParams + filters.py 빌더 + paginate` 3종 세트를 재사용.
- 기능 변경 후: dev_manual 해당 장 갱신 → `manual.html` 재빌드 → `docs/audit/` 워크로그.
