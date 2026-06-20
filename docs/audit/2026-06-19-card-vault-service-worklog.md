# 워크로그 — 카드 보관함 서비스 구현 (Task 4)

- **날짜**: 2026-06-19
- **작업자**: Claude (Task 4 / TDD)
- **범위**: `app/services/cards.py` 신규, `tests/integration/test_cards.py` 신규, `docs/dev_manual/16-card-vault.md` 신규

---

## 작업 내용

### 1. TDD — 실패 테스트 먼저 작성

`tests/integration/test_cards.py` 를 먼저 작성해 `ModuleNotFoundError` 확인 (TDD Step 2).

총 9개 테스트 케이스:
- 암호화 저장 확인
- 재등록 시 행 교체(같은 id)
- 교체 후 billing_key_hash 변경
- 미등록 카드 → None 반환
- 등록 후 조회 성공
- 잘못된 customer_key → InputValidationError
- 빈 external_user_id → InputValidationError
- 다른 사용자는 각자 카드 보유
- 교체 시 기존 빌링키 best-effort 삭제 호출 확인

### 2. 서비스 구현 (`app/services/cards.py`)

- `get_card(db, *, service_id, external_user_id) -> Card | None`
- `register_or_replace_card(db, toss, cipher, *, service, external_user_id, customer_key, auth_key) -> Card`

**의존 경로 확인:**
- `record_audit`: `app.services.audit` — `(db, *, actor_type, action, actor_service_id, target_type, target_id, detail)`
- `safe_delete_billing_key`: `app.services.payment_utils` — `(toss, billing_key) -> bool`
- `CUSTOMER_KEY_RE`: `app.services.payment_utils` — `^[A-Za-z0-9\-_=.@]{2,300}$`
- Toss 픽스처: `FakeTossClient` — conftest에서 `fake_toss`, 단위 테스트에서 로컬 `fake` 픽스처 사용
- `BillingKeyResult.billing_key`, `.card` 속성 확인

**핵심 설계 결정:**
- `db.flush()` 먼저 호출 → `card.id` 확정 후 감사 로그 기록 (신규 삽입 시 id 필요)
- 교체 시 `old_billing_key = cipher.decrypt(existing.billing_key_encrypted)` 캡처 → commit 후 삭제
- `CUSTOMER_KEY_RE.fullmatch()` 사용 (`^...$` 패턴 + fullmatch = 동일 효과)

### 3. 테스트 결과

```
9 passed in 0.60s
```

### 4. 문서 갱신

- `docs/dev_manual/16-card-vault.md` 신규 생성
- `docs/dev_manual/README.md` — 16번 항목 추가
- `uv run --with markdown python docs/dev_manual/build_html.py` — 28개 문서 재빌드

---

## 변경 파일

| 파일 | 변경 |
|------|------|
| `app/services/cards.py` | 신규 생성 |
| `tests/integration/test_cards.py` | 신규 생성 |
| `docs/dev_manual/16-card-vault.md` | 신규 생성 |
| `docs/dev_manual/README.md` | 16번 항목 추가 |
| `docs/dev_manual/manual.html` 등 | 재빌드 |

---

## 미구현 (다음 태스크)

- T5: `delete_card`
- T6: `POST /api/v1/cards` API 엔드포인트
- T7~T9: 구독·단건 결제에서 카드 보관함 빌링키 참조
