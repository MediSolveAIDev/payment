# 설계 — 카드 등록 기반 결제 흐름 (Card Vault)

작성일: 2026-06-19
상태: 설계 승인 대기(사용자 리뷰)

## 1. 목표 / 배경

현재는 구독 생성·단건결제 시마다 외부 서비스가 토스 결제창에서 받은 `auth_key`(1회용)와
`customer_key`를 API로 전달하면, 서버가 **그 자리에서 빌링키를 발급**한다. "카드"라는 독립
개념이 없고, 빌링키는 구독 레코드에 붙어 있다.

이를 **"카드를 먼저 등록 → 등록된 카드로 구독·결제"** 흐름으로 **완전히 대체**한다.
즉 결제수단(카드)을 1급 엔티티(보관함, vault)로 도입하고, 구독·단건결제는 등록된 카드를
참조해 처리한다.

### 확정된 결정(사용자 확인)
1. **완전 대체** — 카드 등록이 선행 필수. 구독·단건결제는 `auth_key`를 직접 받지 않는다.
2. **사용자당 카드 1장** — `(service_id, external_user_id)`당 1장. 재등록 시 교체.
3. **운영 전(새로 시작)** — 보존할 실데이터 없음. 복잡한 이전 마이그레이션 불필요.
4. **접근법 A 채택** — 전용 `cards` 테이블 + 구독이 `card_id` FK로 참조.
5. 구독 상태명 **`CANCELED` 유지**(해지 예약=만료까지 유지, 만료 시 EXPIRED). "ACTIVE_SOON"은
   이 상태의 다른 호칭일 뿐 — 신규 상태값을 만들지 않는다.

## 2. 범위

**In scope**
- `cards` 테이블 신설(결제수단 보관함). 빌링키를 구독에서 카드로 이동.
- 카드 등록/교체/조회/삭제 외부 API.
- 구독 생성·단건결제 API를 카드 참조 방식으로 변경(`auth_key`/`customer_key` 제거).
- 구독 `change-card` 엔드포인트 제거(카드 재등록으로 통합).
- 자동 갱신·수동결제·단건취소가 카드 빌링키를 읽도록 정리.
- 어드민 카드 조회 표시, 샘플 서비스 데모 흐름 갱신.
- 마이그레이션, 테스트, 문서.

**Out of scope**
- 사용자당 다중 카드/기본카드 선택(카드 1장 규칙).
- 기존 운영 데이터 이전(운영 전).

## 3. 데이터 모델

### 3.1 신규 `cards` 테이블

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | UUID PK | |
| `service_id` | FK→services (ondelete RESTRICT), index | 카드가 속한 서비스 |
| `external_user_id` | String(255) | 외부 서비스 사용자 ID |
| `customer_key` | String(300) | 토스 customerKey(등록 시 SDK에 사용한 값) |
| `billing_key_encrypted` | String(1024) | 자동결제 빌링키(AES-GCM 암호문) |
| `billing_key_hash` | String(64), index | 빌링키 SHA-256(중복/조회용) |
| `card_info` | JSONB nullable | 카드 마스킹 정보(표시용) |
| `created_at` / `updated_at` | TimestampMixin | |

- **유니크 제약**: `(service_id, external_user_id)` — 사용자당 카드 1장 보장.
- 빌링키는 응답에 절대 노출하지 않음(마스킹 `card_info`만 외부 반환).

### 3.2 `subscriptions` 테이블 변경
- **제거**(카드로 이동): `customer_key`, `billing_key_encrypted`, `billing_key_hash`, `card_info`
- **추가**: `card_id` UUID FK→cards (NOT NULL, ondelete RESTRICT)

### 3.3 관계
- `cards` 1 ── 1 `subscriptions`(둘 다 `(service, user)`당 유니크 → 사실상 1:1).
- 결제/갱신 시 `subscription → card_id → card.billing_key`로 빌링키를 읽는다.

## 4. 외부 API

### 4.1 신규 — 카드
| 메서드·경로 | 동작 |
|---|---|
| `POST /api/v1/cards` | 등록/교체. body: `external_user_id`, `customer_key`, `auth_key`. 서버가 빌링키 발급 → `(service,user)` 카드 upsert(기존 있으면 옛 토스 빌링키 best-effort 삭제 후 교체). 반환: 마스킹 카드정보 |
| `GET /api/v1/cards/{external_user_id}` | 등록 카드 조회(마스킹, 빌링키 비노출) |
| `DELETE /api/v1/cards/{external_user_id}` | 카드 삭제(차단 규칙은 §6). 토스 빌링키 best-effort 삭제 |

### 4.2 변경 — 구독
| 경로 | 변경 |
|---|---|
| `POST /subscriptions` | body에서 `auth_key`·`customer_key` 제거. `external_user_id`+`plan_id`. 등록 카드 없으면 거부(`등록된 카드가 없습니다`). 첫 결제는 카드 빌링키 사용(무료·할인 정책 기존 유지) |
| `POST /subscriptions/{id}/change-card` | **삭제** — 카드 교체는 `POST /cards` 재등록으로 통합(구독이 card_id로 참조하므로 자동 반영) |
| `pay`(수동결제)·`cancel`·`resume`·`add-days`·`GET` | 경로 유지. 빌링키는 카드에서 읽음 |

### 4.3 변경 — 단건결제
| 경로 | 변경 |
|---|---|
| `POST /payments` | body에서 `auth_key`·`customer_key` 제거. `external_user_id`+`amount`+`order_name`. 등록 카드 필수. 카드 빌링키로 결제하고 **카드는 삭제하지 않음**(영속) |
| `GET /payments/{external_user_id}` | 유지 |
| `POST /payments/{order_id}/cancel` | **유지** — 단건 취소·환불. 취소 수수료는 서버가 서비스 취소정책(`cancellation_enabled`·`cancellation_fee_percent`)으로 계산(`compute_cancel_fee` 그대로). `toss_payment_key`로 처리하므로 카드와 무관. 환불 후 카드는 유지 |

**핵심**: `auth_key`는 이제 **오직 `POST /cards`** 에서만 사용.

## 5. 처리 흐름

1. **카드 등록/교체** `POST /cards`: 프론트(토스 SDK, customerKey)→authKey → 서버 `issue_billing_key` → (기존 카드 옛 빌링키 best-effort 삭제) → 카드 upsert(빌링키 암호화 저장).
2. **구독 생성** `POST /subscriptions`: 등록 카드 조회(없으면 거부) → 구독 생성(card_id 연결, PENDING 선커밋) → `card.billing_key`로 첫 결제(할인/무료 정책 기존).
3. **자동 갱신**(스케줄러): `subscription.card_id → card.billing_key`로 charge. 재시도·정지·만료 로직 기존 유지.
4. **단건결제** `POST /payments`: 등록 카드 필수 → Payment PENDING 선커밋 → `card.billing_key`로 charge → 카드 영속.
5. **카드 교체** = `POST /cards` 재등록: 새 빌링키로 카드행 교체 → 구독이 card_id로 참조하므로 다음 결제부터 자동으로 새 카드 사용.
6. **카드 삭제** `DELETE /cards/{user}`: 차단 규칙(§6) 통과 시 토스 빌링키 best-effort 삭제 → 카드행 삭제.
7. **단건 취소**: `toss_payment_key`로 환불·수수료(서버 정책), 카드 유지.

**유지되는 결제 3원칙**: PENDING 선커밋 / 타임아웃은 절대 FAILED 처리 안 함(PENDING 유지) / 멱등 order_id.

## 6. 오류 처리 · 엣지 케이스

- **카드 미등록 결제 시도**: 토스 호출 전 차단, 명확한 메시지(`등록된 카드가 없습니다. 먼저 카드를 등록하세요`).
- **카드 등록 실패(빌링키 발급 실패)**: 카드 저장 안 함, 기존 카드 유지, 오류 반환.
- **교체 시 옛 토스 빌링키 삭제는 best-effort**: 실패해도 교체 진행(로그/감사). 사용자가 카드 못 바꾸는 상황 방지.
- **동시 등록 경쟁**: `(service_id, external_user_id)` 유니크가 최종 심판, IntegrityError는 재조회로 흡수.
- **결제 중 토스 타임아웃**: 절대 FAILED 처리 안 함, PENDING 유지.
- **카드/빌링키 무효로 결제 실패**: 일반 결제 실패 경로(재시도·정지). 복구는 카드 재등록.

### 6.1 카드 삭제 차단 규칙(구독 상태 기준)
| 구독 상태 | 카드 삭제 |
|---|---|
| `TRIAL`·`ACTIVE`·`PAST_DUE`·`SUSPENDED`·`EXTENDED`(앞으로 청구될 상태) | **차단** |
| `CANCELED`(해지 예약, 만료까지 유지 — 더 이상 청구 없음) | **허용** |
| `EXPIRED`(종료) · 구독 없음 | **허용** |

→ `CANCELED`은 자동결제가 더 일어나지 않으므로 카드 불필요 → 삭제 허용. (`CANCELED → EXPIRED`는 기존 상태머신 그대로)

### 6.2 보안
- 빌링키 AES-GCM 암호화 저장, **응답 비노출**(마스킹만). `billing_key_hash`로 중복 조회.
- 카드 등록/교체/삭제마다 감사 로그(`card.register`/`card.replace`/`card.delete`).
- `/cards` 3종에 HMAC 서명·IP 화이트리스트·레이트리밋 적용. 등록/결제 민감 작업은 결제용 레이트리밋(`rate_limit_payment_per_minute`) 적용.

## 7. 어드민 / 샘플 서비스

- **어드민(htmx)**: 구독 상세에 연결 카드 마스킹 정보 표시(카드 테이블에서 읽음). 서비스/구독 맥락에서 등록 카드 조회. 어드민은 **등록 불가**(토스 SDK 필요), 조회 중심. 카드 삭제는 §6.1 규칙대로 허용(선택).
- **sample_service**: 카드 등록 페이지(토스 SDK→`POST /cards`) 추가, 구독·단건 데모 흐름을 "카드 선등록 → 등록 카드로 결제"로 갱신.

## 8. 마이그레이션 (운영 전)

- alembic: `cards` 테이블 생성 + `subscriptions`에서 빌링키 컬럼 4개 제거 + `card_id` 추가(NOT NULL, FK).
- 운영 데이터 없음 전제. dev/test DB에 남은 기존 구독 행은 **리셋 전제**(card_id NOT NULL 추가가 기존 행과 충돌하므로). 마이그레이션 주석에 명시.

## 9. 테스트

- **단위**: 카드 서비스(등록/교체/삭제, 빈·형식 검증, 빌링키 암호화/해시).
- **통합**: 등록→구독생성→갱신→단건결제→단건취소 / 카드 미등록 거부 / 카드삭제 차단(ACTIVE)·허용(CANCELED·EXPIRED).
- **API 인증**: 신규 `/cards` 3종 HMAC·IP·레이트리밋.
- **기존 테스트 수정**: `auth_key`를 넘기던 구독·단건 테스트를 카드 선등록 방식으로 변경.
- 상태 전이(transitions) 테스트는 변경 없음(상태값 유지).

## 10. 문서

- dev_manual: `04-subscription-create`, `07-one-off-payment`, `15-external-api-and-sample`, `02-database`, `admin/03-services`, `admin/05-subscriptions` 갱신 + `manual.html` 재빌드.
- 작업 워크로그(docs/audit) 작성.

## 11. 성공 기준 (DoD)

- 카드 미등록 상태에서 구독/단건결제 시 명확히 거부된다.
- 카드 등록 후 `auth_key` 없이 구독 생성·단건결제·자동 갱신이 동작한다.
- 카드 재등록으로 카드를 교체하면 구독이 자동으로 새 카드로 결제된다.
- 카드 삭제가 §6.1 규칙대로 차단/허용된다.
- 빌링키가 어떤 외부 응답에도 노출되지 않는다.
- 단건 취소·환불(서버 수수료 정책)이 그대로 동작한다.
- 전체 테스트 통과.
