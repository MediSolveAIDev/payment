# 02. 큰그림 — 이 시스템은 무엇이고 어떻게 생겼나

> 목표: "어떤 요청이 들어오면 어떤 파일을 지나 어디에 기록되는지"를 그림으로 그릴 수 있다.

## 1. 시스템의 역할

사내 여러 서비스(쇼핑몰, 진료 앱 등)가 **공통으로 쓰는 구독·결제 허브**다.
각 서비스는 회원/화면만 만들고, 구독 상태와 돈 문제는 전부 이 서버가 책임진다.

```
사내 서비스 A·B·C ──(HMAC 서명 API)──▶ ┌──────────────────────┐
                                       │  구독·결제 서버        │──▶ 토스페이먼츠
운영자(담당자/관리자) ──(어드민 htmx)──▶ │  FastAPI              │     (빌링키 발급·청구·취소)
                                       │  ├ PostgreSQL (원장)   │
토스 웹훅 ──(IP 검증)─────────────────▶ │  └ Redis (락·세션·캐시)│
                                       └─────────┬────────────┘
                                    APScheduler(5분) — 자동갱신·만료·정산 스윕
```

네 가지 진입점만 기억하면 된다:

| 진입점 | 누가 | 인증 | 코드 위치 |
|---|---|---|---|
| `/api/v1/*` | 사내 서비스 | API키+IP+HMAC 3중 | `app/api/v1/` |
| `/admin/*` | 운영자 | 세션 쿠키+CSRF | `app/admin/` |
| `/api/v1/webhooks/toss` | 토스 | 발신 IP 검증 | `app/api/v1/webhooks.py` |
| (HTTP 아님) 스케줄러 | 시스템 | — | `app/scheduler/runner.py` |

## 2. 디렉터리 지도

```
app/
├── main.py          앱 조립(create_app) — 의존성 주입·라우터 등록·보안헤더
├── core/            설정(config)·DB엔진(db)·암호화(crypto)·에러(errors)·공통 의존성(deps)
├── models/          SQLAlchemy 모델 + enums(상태 정의의 단일 출처)
├── schemas/api.py   외부 API 요청/응답 스키마 (ORM 비노출)
├── api/             외부 API — deps.py(3중 인증), v1/(엔드포인트)
├── admin/           어드민 — routes/(화면), templates/(htmx), filters·pagination(공용)
├── services/        ★ 비즈니스 로직 전부 — 구독·결제·갱신·정산·전이·감사
├── toss/            토스 클라이언트(client) + 테스트용 가짜(fake)
├── scheduler/       5분 배치 러너(전역 락+heartbeat)
└── notifications/   이메일 발송
tests/               unit / integration / e2e / security (09장)
alembic/versions/    DB 마이그레이션 (05장)
sample_service/      외부 서비스 연동 샘플 (Django)
```

## 3. 요청의 일생 ① — 외부 서비스가 구독을 만들 때

```
POST /api/v1/subscriptions
 1) app/api/deps.py        authenticate_service — 킬스위치 → API키 → IP → rate limit
                            → 타임스탬프 → HMAC 서명 → nonce (하나라도 실패 시 즉시 거부)
 2) app/api/v1/subscriptions.py   라우트 — 스키마 검증 후 서비스 함수 호출만
 3) app/services/subscriptions.py create_subscription
      검증 → 토스 빌링키 발급 → Subscription+PENDING Payment 생성 → 1차 commit
      → 토스 첫 결제 → 결과 확정 → 2차 commit + 감사 로그
 4) app/schemas/api.py     SubscriptionResponse.from_model — 민감정보 제외 응답
```

## 4. 요청의 일생 ② — 운영자가 어드민에서 목록을 볼 때

```
GET /admin/subscriptions
 1) app/admin/deps.py      세션 쿠키 → Redis 세션 → 역할/담당 서비스 스코프 결정
 2) app/admin/routes/subscriptions.py   PageParams(검색·정렬·필터 파싱)
 3) app/admin/filters.py   subscription_query — 목록·엑셀·탭이 공유하는 단일 쿼리 빌더
 4) app/admin/pagination.py paginate — count·페이징 자동 처리
 5) templates/…            전체 페이지 또는 htmx partial(_table.html)만 렌더
```

## 5. 레이어 규칙 — 어기면 안 되는 4가지

1. **라우트는 얇게**: 파싱·렌더만. 비즈니스 규칙은 전부 `app/services/`에.
2. **commit은 서비스 레이어가** 한다. 라우트에서 commit하지 않는다.
3. **오류는 `app/core/errors.py`의 DomainError 계열**을 던진다 — HTTP 변환은 핸들러가 자동으로.
4. **services는 api/admin을 import하지 않는다** (방향: 라우트 → 서비스 → 모델).

> 더 깊이: [dev_manual 01·02·03장](../dev_manual/manual.html)
