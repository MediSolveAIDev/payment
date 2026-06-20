# 구독·결제 도메인 로직 검증 리포트

- **일자**: 2026-06-11
- **범위**: 비즈니스 로직의 **논리적 정합성**만 (보안·성능·구조는 [기존 감사](2026-06-11-code-audit.md)에서 완료)
- **방법**: 영역별 독립 분석(결제 흐름 / 시간·금액 경계 / 상태머신·EXTENDED 정합성) + 핵심 발견은 코드 직접 대조로 재검증. 추측 배제, file:line + 재현 시나리오 기반
- **기준선**: 전체 테스트 **512 passed** (EXTENDED 작업분 포함 워킹트리, 단독 실행)
- **참고**: 검증 중 발생한 테스트 실패(16→66건)는 코드 문제가 아니라 **두 세션이 같은 테스트 DB(payment_test)를 동시에 사용**해 서로 스키마를 drop/create한 충돌이었음 — 하단 '운영 참고' 참조

---

## 종합 평가

결제 3원칙(PENDING 선커밋 → 타임아웃≠실패 → 멱등 order_id)이 단건·첫결제·갱신 전 경로에 일관 적용되어 있고, 상태머신·경계 부등호·정수 금액 처리 모두 견고합니다. 다만 **수동 결제 경로가 멱등 체계 밖에 있어** 생기는 이중 청구 계열 문제(H-1, H-2)와 **재시도 중 금액 재계산**(H-3), **0원 갱신 청구 가드 부재**(H-4)는 실제 돈 사고로 이어질 수 있어 수정을 권합니다.

| 심각도 | 건수 | 핵심 |
|---|---|---|
| High | 4 | 수동결제 이중청구, 스윕 영구 방치, 금액 불일치, 0원 청구 |
| Medium | 7 | 복구 누락, 알림 부재, 집계 기준 불일치, 정책 미확정 2건 |
| Low | 5 | 토스 대사 불일치, 경계 한정, 테스트 공백 등 |
| EXTENDED WIP | 3 | 진행 중 작업의 미완성 지점 |

---

## High — 돈이 잘못될 수 있는 문제

### H-1. 수동 결제와 갱신 배치의 동시 실행 → 동일 구독 이중 청구

- **위치**: `app/services/subscriptions.py` `_perform_manual_charge`, `app/services/renewals.py` `_renew_one` 3단계 재검증
- **원인**: 수동 결제는 **Redis 락(`lock:renew:{sub_id}`)도 FOR UPDATE도 없이** 매 호출 새 order_id(`m{uuid}`)+새 멱등키로 토스를 호출. 갱신 배치의 3단계 재검증은 `sub.status in DUE_STATUSES`만 보는데 **ACTIVE도 DUE_STATUSES에 포함**되어 있음.
- **시나리오 A**: PAST_DUE 구독을 배치가 결제 중(토스 응답 대기, 최대 65초) → 그 사이 관리자가 "재결제" 클릭 → 수동 결제 즉시 승인·ACTIVE 복귀 → 배치 토스 호출도 승인 → 3단계에서 ACTIVE ∈ DUE_STATUSES라 통과 → **같은 기간 2건 결제 + 기간 2회 전진**.
- **시나리오 B**: 수동 결제 버튼 동시 2회 클릭 → 각자 새 order_id/멱등키 → 둘 다 승인. 어떤 계층도 막지 못함.
- **권고**: ① 수동 결제도 `lock:renew:{sub_id}` 획득, ② `_renew_one` 3단계 재검증에 "여전히 결제 예정인지"(`next_billing_at <= now` 또는 기간 미전진) 확인 추가.

### H-2. 정산 스윕의 skip 조건이 수동 결제 PENDING을 영구 방치 — 승인된 돈이 영원히 미기록

- **위치**: `app/services/reconciliation.py` skip 조건(`payment_type != FIRST AND sub.status in DUE_STATUSES`)
- **원인**: "갱신 풀의 RENEWAL/RETRY는 `_renew_one`이 같은 order_id로 수렴 처리"라는 가정은 **배치의 결정적 order_id(`r…`)에만 성립**. 수동 결제(`m{uuid}`)는 `_renew_one`이 절대 다시 다루지 않음.
- **시나리오**: PAST_DUE에서 수동 결제 → 타임아웃(실제 승인됨, PENDING 유지) → 다음 배치가 **다른** order_id로 갱신 성공 → ACTIVE. 이후 모든 스윕에서 ACTIVE ∈ DUE_STATUSES라 `m…` PENDING 영구 skip → **사용자 2회 청구, DB엔 DONE 1건 + 영구 PENDING 1건, 환불 검토 메일 없음**.
- **EXTENDED와의 결합**: 갱신 타임아웃 PENDING이 있는 구독을 연장하면 `current_period_end` 변경으로 결정적 order_id가 달라져 같은 방식으로 옛 PENDING이 고아가 됨.
- **권고**: skip 조건을 "구독의 **현재** 결정적 order_id(`_renewal_order_id(sub)`)와 일치하는 PENDING"으로 한정 — 그 외 PENDING은 전부 스윕이 확정.

### H-3. 갱신 재시도 중 요금제 가격 변경 → 청구액과 DB 기록액 불일치

- **위치**: `app/services/renewals.py` — 기존 PENDING 재사용 시 `amount = plan_recurring_amount(plan)`을 매 배치 재계산하지만 `payment.amount`는 갱신하지 않음
- **시나리오**: 10,000원 PENDING 커밋 → 토스 미도달 타임아웃 → 운영자가 상시할인 변경(11,000원) → 다음 배치가 같은 order_id/멱등키로 11,000원 청구·승인 → `payment.amount`는 10,000원 그대로 → **정산·대시보드 매출 1,000원 누락**.
- **권고**: PENDING 재사용 시 `payment.amount`를 청구액의 단일 진실로 사용(재계산 금지)하거나, DONE 확정 시 토스 응답 금액으로 동기화.

### H-4. 100% 상시할인 요금제 → 갱신 배치가 0원을 토스에 청구 → 구독 자동 파괴

- **위치**: `app/services/plans.py` `_validate_recurring_discount`(percent 100 허용, 정액은 price 대비 상한 없음), `app/services/renewals.py`·`_perform_manual_charge`(amount=0 가드 없음)
- **시나리오**: price 10,000 + 상시할인 100%(또는 정액 ≥ price) → 정기 결제액 0원. 첫 결제는 `if amount > 0`으로 건너뛰지만 **갱신·수동결제는 가드 없이 0원 청구** → 토스 거절 → FAILED → PAST_DUE → 재시도 소진 → SUSPENDED → EXPIRED. "100% 할인" 요금제가 구독자를 자동 정지·만료시킴. (1~99원 등 토스 최소금액 미만도 동일 — L-1)
- **권고**: 요금제 검증에서 percent<100·정액<price 강제, 또는 갱신/수동결제에 amount==0 분기(결제 생략·기간 전진) 추가 — 정책 결정 필요.

---

## Medium — 운영·정합성 문제

| # | 내용 | 위치/시나리오 | 권고 |
|---|---|---|---|
| M-1 | **수동결제 타임아웃 → 스윕이 DONE 확정해도 구독은 SUSPENDED 그대로** — 돈 받고 서비스 미제공, orphaned 메일 대상(CANCELED/EXPIRED)에도 빠짐. grace 경과 시 그대로 EXPIRED | `reconciliation.py` DONE 확정부 | DONE 확정 시 sub가 SUSPENDED/PAST_DUE면 복구 또는 최소 review 메일 |
| M-2 | `_renew_one`의 "갱신 풀 이탈 후 결제 성공" 경로는 **감사 로그만** 남김 — reconciliation 동일 정책은 이메일 발송. 환불 실행 수단도 없음(구독 결제 취소 API 부재) | `renewals.py` requires_review 분기 | 이메일 통일 + 구독 결제 환불 절차 정의 |
| M-3 | 외부(토스 콘솔) 취소 웹훅: 구독 결제가 취소돼도 **구독 기간은 유지**(환불+이용 지속), 알림·감사 없음, cancel_fee 미기록 | `webhooks.py` CANCELED 동기화 | 구독 결제 외부 취소 시 운영자 알림 + 정책 결정 |
| M-4 | **환불 집계 기준 불일치**: 정산=`approved_at`(원결제 월 — 마감 정산이 소급 변동), 대시보드=`requested_at`, `canceled_at` 컬럼은 미사용. "이번 달 환불" 지표가 실제 취소 시점과 무관 | `settlement.py` vs `dashboard.py` | 환불은 `canceled_at` 기준 월 귀속(회계 일반) 또는 현행 명시 |
| M-5 | **월말 앵커 드리프트**: 1/31 가입 → 2/28(클램프) → 이후 영원히 28일 — 가입일 복귀 없음. 연간 ~3일 청구 주기 단축(고객 불리). 정책 미문서화·다단계 테스트 없음 | `billing_math.py` + `_advance_period` | anchor-day 보존 또는 정책 문서화 |
| M-6 | **체험만 쓰고 나간 사용자가 첫구독 할인 영구 상실**: 체험 구독은 Payment row가 없어 `_is_first_subscription`의 "결제 시도 없는 구독=혜택 소진"에 걸림. 체험→전환 첫 결제도 상시할인가만 적용 | `subscriptions.py` `_is_first_subscription` | 의도(악용 방지)면 문서화, 아니면 체험 구독 예외 처리 |
| M-7 | **집계는 UTC 경계, 표시는 KST**: KST 00:00~09:00 결제는 화면 날짜와 집계 월이 어긋남(5/31 23:30 UTC 결제 = 화면 "06-01", 집계 5월). 내부적으론 일관되어 이중집계는 없음 | 대시보드·정산·날짜 필터 전반 | KST 경계로 통일 또는 매뉴얼에 "집계는 UTC" 명시 |

## Low

- **L-1**: 토스 최소 결제금액 미만(1~99원대) 할인 결과액 가드 없음 — H-4의 부분 케이스
- **L-2**: 취소 수수료 100%면 토스 미호출 + 로컬만 CANCELED — 토스 대사 시 상태 불일치로 보임(의도라면 주석화)
- **L-3**: FIRST PENDING NOT_FOUND 만료 처리가 `ACTIVE`만 대상 — 첫 결제 타임아웃 후 취소(CANCELED)하면 결제 0원으로 한 주기 혜택 유지
- **L-4**: 구독 연장일이 UTC 자정으로 저장 — "7/1까지" 입력 시 실제 만료 KST 7/1 09:00 (M-7과 함께 정책 통일 대상)
- **L-5**: 단위 테스트 공백 — 월말 클램프 다단계, 100%/0원 할인, `compute_cancel_fee` 불변식(fee+refund==amount), DAY 대형 주기

---

## EXTENDED(연장처리) 작업분 — 정합성 점검 결과 (WIP)

진행 중 작업이지만 **완성도가 높음**: enum·ACCESS/OPEN 집합·부분 유니크 인덱스·DUE_STATUSES(배치 수거)·transitions 테이블·대시보드 색/라벨·admin 라벨·감사 라벨·force_cancel 허용 상태까지 일관되게 반영됨. 테스트 512개 통과. 남은 지점:

| # | 미완성/검토 지점 | 내용 |
|---|---|---|
| W-1 | **auto_renew=False 요금제 연장 시 자동결제 예약** | `extend_subscription`이 무조건 `next_billing_at=new_end` 설정 → "자동결제 안함" 요금제도 연장 후 새 만료일에 **자동 청구**됨. 연장=이용기간만 연장인지, 연장 후 자동결제 재개인지 정책 확정 필요(자동결제 안함이면 `next_billing_at=None` 유지 + 만료 스윕 대상에 EXTENDED 추가 필요) |
| W-2 | **외부 API 문서/스키마 미반영** | `SubscriptionResponse.status` 설명이 "TRIAL\|ACTIVE\|…\|EXPIRED"로 EXTENDED 누락 — 외부 서비스가 status 문자열로 분기하면 새 값에 놀랄 수 있음. `access_allowed` 설명도 동일. dev_manual·sample_service(상태 배지 `st-EXTENDED` 없음 → 회색 기본 배지)도 미반영 |
| W-3 | **H-2와의 결합** | 갱신 PENDING이 있는 구독을 연장하면 order_id 연결이 끊겨 PENDING 고아화 — H-2 수정 시 함께 해소됨. 연장 전에 "미확정 PENDING 존재" 경고를 띄우는 것도 방법 |

---

## 잘 설계된 부분 (검증 완료)

- **결제 3원칙 일관 적용**: PENDING 선커밋 → 타임아웃 시 절대 FAILED 금지 → 멱등 order_id — 단건·첫결제·갱신 모두 동일. "2xx인데 JSON 파싱 실패 → 타임아웃 취급"(client.py) 같은 방어가 특히 훌륭
- **갱신 결정적 order_id** + 타임아웃 시 retry_count 미증가 — 크래시 재실행이 토스 멱등 재생으로 수렴(이중결제 차단의 핵심)
- **`_renew_one` 3단계 재검증**: `payment.status != PENDING`이면 skip — 웹훅/스윕 선확정과의 중복 적용 정확히 차단
- **금액 전부 정수(KRW)**: float 유입 경로 없음. `compute_cancel_fee`는 fee+refund==amount 불변식 항상 성립, 실행·조회·화면이 단일 함수 공유
- **due 카테고리 상호배타**: 상태·NULL 조합으로 한 구독이 두 스윕에 동시 진입 불가
- **경계 부등호 일관**: 만료(`<= now`)와 재개 거부(`<= now`)가 같은 방향 — 경계 시각 모순 없음
- **시간대 규약**: 전 컬럼 timezone-aware UTC, naive datetime 미사용
- **웹훅**: transmission-id 멱등 + 페이로드 불신·토스 재조회 확정

---

## 권장 수정 순서

1. **H-1 + H-2** (한 묶음): 수동 결제에 구독 락 적용 + 스윕 skip 조건을 현재 결정적 order_id로 한정 — 이중 청구 발생과 은폐를 동시에 차단
2. **H-4 + L-1**: 요금제 할인 상한 검증(또는 0원 갱신 분기) — 마이그레이션 불필요, 검증 함수 수정만
3. **H-3**: PENDING 재사용 시 amount 재계산 금지
4. **M-1·M-2**: 스윕 DONE 확정 시 구독 복구/알림 통일
5. **EXTENDED 마무리 시**: W-1(비자동갱신 정책)·W-2(API 문서) 반영
6. **정책 결정 후 일괄**: M-4(환불 월 귀속)·M-5(앵커)·M-6(체험 혜택)·M-7(KST 경계) — 코드보다 정책 확정이 먼저

## 운영 참고 — 테스트 DB 동시 사용 충돌

두 세션이 동시에 `pytest`를 실행하면 둘 다 같은 `payment_test` DB에 세션 시작 시 `drop_all/create_all`을 수행해 **서로의 테이블을 파괴**합니다(이번 검증 중 16→66건 가짜 실패로 재현). 동시 작업이 잦다면 `TEST_DATABASE_URL`을 세션별로 분리(예: `payment_test_a`/`payment_test_b`)하는 것을 권장합니다.
