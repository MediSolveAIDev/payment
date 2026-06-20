# Phase 4 수정 작업 내역 (유지보수성 — 구조 개선)

- **일자**: 2026-06-11
- **근거**: [2026-06-11-code-audit.md](2026-06-11-code-audit.md) — 3장(구조) / 4장 Phase 4
- **선행**: [Phase 1](2026-06-11-phase1-worklog.md) · [Phase 2](2026-06-11-phase2-worklog.md) · [Phase 3](2026-06-11-phase3-worklog.md)
- **검증**: 전체 테스트 `505 passed` (Phase 3 종료 시 497 → 신규 8, 실패 0)
- **성격**: 기능 변화 없는 구조 개선 — 외부 동작·URL·템플릿 출력 동일(기존 테스트가 회귀망)

---

## 작업 1. 구독 상태 전이 중앙화 (S1) ✅ — 가장 가치 큰 항목

**문제**: `sub.status = SubscriptionStatus.X` 직접 대입이 11곳(subscriptions/renewals/reconciliation)에 분산. "EXPIRED는 종단" 같은 규칙이 호출부 if문과 주석에만 존재했고, 잘못된 전이를 어디서도 막지 못했으며, `next_billing_at`·`suspended_at`·`retry_count` 동기화를 전이마다 수기로 반복.

**변경**: **신규 모듈 [`app/services/transitions.py`](../../app/services/transitions.py)**
- `ALLOWED_TRANSITIONS` — 상태별 허용 전이 테이블(상태 머신을 코드로 명문화). EXPIRED는 빈 집합(종단).
- `transition(sub, new_status, *, now)` — 허용 검증(위반 시 `InvalidStateTransition`) + **보편 불변식** 일괄 적용:
  - EXPIRED/CANCELED 진입 → `next_billing_at=None`
  - SUSPENDED 진입 → `suspended_at=now` 기록 + `next_billing_at=None`
  - ACTIVE 진입 → `retry_count=0`, `suspended_at=None`
- 전이별 고유 필드(기간 전진, 재시도 스케줄)는 호출측 소관으로 남김 — 정책과 메커니즘 분리.
- 11곳 전부 `transition()` 호출로 교체. 직접 대입은 transitions.py 내부 1곳뿐(grep으로 검증 가능: `grep -rn "\.status = SubscriptionStatus\." app/services/`).
- **신규 단위 테스트 8건** ([tests/unit/test_transitions.py](../../tests/unit/test_transitions.py)) — 종단성·거부 전이·불변식·자기 전이·전 상태 등재를 못박음. 기존 통합 테스트 232건이 실제 경로 회귀망.

## 작업 2. admin 쿼리 중복 제거 (S2) ✅

**문제**: 동일한 구독 필터 빌드가 3곳(구독 목록/엑셀, 서비스 상세 구독 탭, 탭 엑셀)에 복붙 + `services.py`가 `subscriptions.py`에서 상수를 import하는 라우트 간 수평 결합.

**변경**: [`app/admin/filters.py`](../../app/admin/filters.py)에 `subscription_query(pp, *, scope|service_id)` + `SUB_SORT` 통합(서비스 목록용 `services_query` + `SVC_SORT`도 함께 이동). 4개 사용처가 모두 같은 빌더 사용 — 검색 조건 추가 시 한 곳만 수정. 구독 탭 행이 (Subscription, Plan, Service) 3-튜플로 통일돼 템플릿 언패킹 1줄 수정.

## 작업 3. paginate 보일러플레이트 제거 (S3) ✅

**문제**: `select(func.count()).select_from(base.order_by(None).subquery())` 패턴이 admin 라우트 11곳에 반복 — `order_by(None)` 누락 실수 여지.

**변경**: `paginate(db, items_q, pp)` 신형 호출 — count 쿼리를 내부에서 생성(`count_of`). `flatten=True`로 단일 엔티티 Row 평탄화 후처리(`page.items = [r[0] ...]` 3곳)도 흡수. 레거시 4-인자 호출도 호환 유지(조인 없는 count 등 직접 제어용). 11곳 전부 신형으로 전환 + 미사용 `func` import 5개 파일 정리.

## 작업 4. "대표 담당자 해제 불가" 규칙을 서비스 레이어로 (S4) ✅

**변경**: 검사를 라우트에서 `accounts.unassign_service`로 이동 — `ConflictError`로 강제. 향후 API·CLI 등 어떤 진입점에서도 대표 담당자가 빠질 수 없음. 라우트는 기존 `DomainError → ?error=` 패턴으로 메시지 표시만(UX 동일).

## 작업 5. services.py 라우터 분리 (S6) ✅ — 571줄 → 3파일

| 파일 | 내용 |
|---|---|
| `services.py` (387줄) | 목록/상세/등록/키 관리/정책 — 핵심 흐름만 |
| `services_export.py` (신규) | 엑셀 다운로드 4종 |
| `services_managers.py` (신규) | 담당자 관리 3종 + `service_managers` 헬퍼 |

URL·템플릿 무변경. **주의(앞으로 라우터 추가 시)**: `app/admin/__init__.py`에서 `services_export.router`를 `services.router`보다 **먼저** 등록해야 한다 — `/services/export.xlsx`가 `/services/{service_id}`(UUID 경로)에 잡히면 422가 된다(주석으로 명시).

## 작업 6. 기계적 정리 (S5·S7·S8·S9·S13·S14) ✅

| # | 내용 |
|---|---|
| S5 | 문자열 리터럴 상태 비교 3곳 → `PaymentKind`/`PaymentStatus` enum (토스 API 응답 문자열 비교는 외부 값이므로 대상 아님) |
| S7 | `locks.py`의 `_acquire_lock/_release_lock/_DUE_STATUSES` → 밑줄 제거(모듈 경계를 넘는 공유 API) — 사용처 3파일 + 매뉴얼 참조 일괄 수정 |
| S8 | keys-modal 라우트의 직접 commit(트랜잭션 규약 유일한 예외) → `registry.reveal_keys`로 복호화+감사+commit 이동. 라우트는 렌더만 |
| S9 | 취소 정책 폼 파싱 2곳 중복 → `_parse_cancel_policy` 헬퍼 |
| S13 | `get_db/get_redis/get_cipher/get_toss/get_settings/get_email_sender/get_client_ip`를 **`app/core/deps.py`(신규)** 로 이동 — admin 13개 파일이 api 레이어 대신 core를 import(레이어 방향 정리). `app/api/deps.py`는 인증 전용 + 호환 재export |
| S14 | `api/deps.py` 인라인 매직 넘버 → `RATE_WINDOW_TTL=90`, `NONCE_TTL_SECONDS=600` 명명 상수 |

## 미적용 항목 (의도적 보류 — 감사 리포트의 권고와 일치)

| # | 항목 | 사유 |
|---|---|---|
| S10 | dashboard.py의 표시용 데이터(StatCard tint 등) 분리 | 감사 스스로 "현 상태 유지도 합리적" — 단일 소비자(admin 전용)라 분리 이득이 작음 |
| S11 | `registry.py` rename(`tenants.py` 등) | 감사 "시급하지 않음" — docstring이 역할을 충분히 설명. import 전면 수정 대비 이득 작음 |
| S12 | conftest.py의 HTML 리포트 생성기 분리 | 테스트 인프라 무관 변경 — 리포트 기능을 손볼 때 함께 처리 권장 |

## 테스트 결과

```
전체: 505 passed (Phase 3 종료 시 497 → +8 = tests/unit/test_transitions.py)
```
- e2e(어드민 화면·엑셀·htmx) 전부 통과 — 라우터 분리·쿼리 통합·paginate 전환이 화면 동작을 바꾸지 않음을 확인.
- 통합 232건 통과 — 상태 전이 중앙화가 갱신 상태머신을 보존함을 확인.

## 변경 파일 요약

| 영역 | 파일 |
|---|---|
| 신규 | `app/services/transitions.py`, `app/core/deps.py`, `app/admin/routes/services_export.py`, `app/admin/routes/services_managers.py`, `tests/unit/test_transitions.py` |
| 서비스 | `subscriptions.py`·`renewals.py`·`reconciliation.py`(전이 헬퍼), `accounts.py`(S4), `registry.py`(S8), `locks.py`(S7) |
| admin | `filters.py`(공유 빌더), `pagination.py`(S3), `__init__.py`(라우터 등록), routes 전반(import·paginate 전환), `_subs_table.html`(3-튜플) |
| api | `deps.py`(S13 재export + S14 상수) |
| docs | `05-subscription-renewal.md`(전이 중앙화·rename), `admin/05-subscriptions.md`(rename), `manual.html` 재빌드 |

---

## 감사 종결

Phase 1(운영 안정성) → 2(보안) → 3(성능) → 4(구조)로 감사 리포트의 모든 항목이
**적용 완료** 또는 **사유 명시 후 보류**로 종결됨. 테스트는 기준선 483 → 최종 505.
보류 항목(보안 L-2/L-4/L-6, 구조 S10/S11/S12)과 후속 권고(수동결제 Redis 락,
`_expire_subscription`의 락 중 빌링키 삭제 등)는 각 Phase 워크로그에 기록되어 있다.
