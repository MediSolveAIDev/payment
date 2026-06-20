# 2026-06-19 워크로그 — 카드 등록 기반 결제 흐름(Card Vault) 전체 구현

## 요청
결제를 "카드 등록 후 그 카드로 구독·결제"하는 방식으로 변경. 매번 `auth_key`를 보내던
방식을 **완전 대체**(카드 등록 선행 필수).

## 설계·계획 문서
- 스펙: `docs/superpowers/specs/2026-06-19-card-registration-payment-flow-design.md`
- 계획: `docs/superpowers/plans/2026-06-19-card-registration-payment-flow.md`
- 실행: superpowers brainstorming → writing-plans → subagent-driven-development(태스크별 구현+스펙리뷰+코드리뷰+수정 루프).

## 확정 결정
1. 완전 대체 — 카드 등록 선행 필수.
2. 사용자당 카드 1장 = 유니크 `(service_id, external_user_id)`. 재등록=교체.
3. `billing_key_hash`는 인덱스만(유니크 아님) → **같은 서비스의 다른 사용자도 동일 물리 카드 등록 가능**.
4. 운영 전이라 깔끔한 마이그레이션(데이터 이전 불필요).
5. 구독 상태명 `CANCELED` 유지(해지 예약→만료 시 EXPIRED). 카드 삭제는 CANCELED/EXPIRED에서만 허용.

## 구현 요약 (Task 1~13)
- **모델/마이그레이션**: `app/models/card.py`(cards 테이블) 신설. `subscriptions`에서 빌링키 컬럼 4개 제거 + `card_id` FK(이후 nullable로 조정 — 삭제 시 종료상태 구독 card_id NULL화). 마이그레이션 `a3b4c5d6e7f8_card_vault`, `b1c2d3e4f5a6_card_id_nullable`(단일 head).
- **카드 서비스** `app/services/cards.py`: `get_card`, `register_or_replace_card`(빌링키 발급→AES-GCM 암호화 저장, 재등록 교체+옛 빌링키 best-effort 삭제, IntegrityError 동시성 가드), `delete_card`(활성 구독이면 거부, CANCELED/EXPIRED는 card_id NULL화 후 삭제).
- **외부 API** `app/api/v1/cards.py`: `POST /api/v1/cards`(payment_rate_limit), `GET/DELETE /api/v1/cards/{external_user_id}`. `CardRegisterRequest`/`CardResponse`(마스킹만, 빌링키 비노출).
- **구독 생성** `create_subscription`: `auth_key`/`customer_key` 제거, 등록 카드 조회(없으면 NotFoundError), `card_id` 연결, 카드 빌링키로 첫 결제. 실패해도 카드 보존.
- **자동갱신·수동결제** `renewals.py`/`_perform_manual_charge`: 카드에서 빌링키 조회. card 없음/`card_id` NULL은 기존 실패 경로(재시도/정지)로 방어. 만료 시 카드 미삭제.
- **단건결제** `create_one_off_payment`: `auth_key`/`customer_key` 제거, 등록 카드 필수, 카드 영속(결제/취소 후 미삭제). 단건 취소·환불(서버 수수료 정책)은 그대로.
- **change-card 제거**: 카드 교체는 `POST /cards` 재등록으로 통합. 라우트·서비스·`CardChangeRequest` 삭제.
- **웹훅**: `billing_key_hash` 조회를 Subscription→Card 기준으로 변경 + service None 가드.
- **어드민**: 구독 상세가 `get_card(service_id, external_user_id)`로 카드 마스킹 정보 표시(빌링키 비노출).
- **샘플 서비스(Django)**: 로그인(이메일)→서비스선택→카드등록(Toss SDK, 키 미설정 시 authKey 수동입력 폴백)→구독→일반결제→카드변경(재등록) 데모로 전환. payment_client에 카드 메서드 추가, 구독/단건 본문에서 auth_key 제거.

## 검증
- 서버 전체 테스트 **564 passed, 0 failed**. sample_service Django **75 passed**.
- alembic 단일 head, upgrade/downgrade/upgrade 왕복 정상.
- 최종 통합 리뷰: **SHIP** — 빌링키 유출 없음, 머니 안전성(PENDING 선커밋·타임아웃=PENDING·멱등·이중결제 없음) 보존, 카드 생명주기 일관, 제거 컬럼 잔존 참조 없음.

## 산출 리포트(요청 반영)
- 전체 테스트 HTML 리포트: `docs/test_report/feature-test-report.html`(+`.md`) — 기능영역→테스트케이스→설명→결과.
- 데모 테스트 리포트: `sample_service/test_report.html`.

## 비고
- 어드민 카드 정보 키 = `(service_id, external_user_id)` (사용자 요청 확인) — 같은 서비스 다른 사용자 동일 카드 사용 허용 설계와 일치(별도 변경 불필요).
- 워킹트리에 세션 이전부터의 미커밋 변경이 다수 존재 — 본 기능 커밋은 파일 단위로 스테이징했으나 일부 기존 변경이 함께 포함됨. 정리 필요 시 후속.
- push/PR은 사용자 요청 시에만(메모리 규칙). 현재 main에 직접 누적.
