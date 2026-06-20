# 워크로그: Card Vault Task 11 — 어드민 구독 상세 카드 표시 수정

- **날짜**: 2026-06-19
- **작업자**: Claude (Task 11 자동 구현)
- **관련 태스크**: Card Vault Task 11

---

## 문제

`Subscription` 모델에서 `card_info` / `billing_key_encrypted` / `customer_key` / `billing_key_hash` 컬럼이 `cards` 테이블로 이동(card-vault 리팩터)된 후, 어드민 구독 상세 화면이 `sub.card_info`를 직접 참조해 `-`(빈값)를 표시하거나 Jinja AttributeError 위험이 있었음.

### grep 결과

```
app/admin/templates/subscriptions/detail.html:37 — sub.card_info.number
app/admin/templates/subscriptions/detail.html:52 — not sub.card_info (재결제 버튼 비활성 조건)
```

구독 목록 / 서비스 상세 구독 테이블에는 카드 정보 표시 없음 → 변경 불필요.

---

## 수정 내용

### 1. `app/admin/routes/subscriptions.py`

- `from app.services import cards as card_service` 임포트 추가
- `subscription_detail` 핸들러에서 `card = await card_service.get_card(db, service_id=sub.service_id, external_user_id=sub.external_user_id)` 호출
- `card` 객체를 템플릿 컨텍스트에 전달

### 2. `app/admin/templates/subscriptions/detail.html`

- 카드 표시 행: `sub.card_info.number if sub.card_info else '-'` → `card.card_info.number if card and card.card_info else '-'`
- 재결제 버튼 비활성 조건: `not sub.card_info` → `not card`
- 각 변경 위치에 한국어 주석 추가

### 3. `docs/manual/dev_manual/16-card-vault.md`

- **§10 어드민(htmx) UI** 절 신규 추가: 변경 파일·동작 규칙·조회 함수 서명 명시
- 유지보수 팁에 `sub.card_info` 사용 금지 경고 추가
- `build_html.py` 재실행으로 HTML 동기화

---

## 테스트 결과

```
.venv/bin/python -m pytest tests/e2e -k "subscription or service_detail or admin" -v
→ 127 passed, 1 failed (pre-existing: test_full_subscription_lifecycle — API 404 오류, 카드 UI와 무관)
```

```
.venv/bin/python -c "import app.main"
→ (오류 없음)
```

---

## 의도적으로 변경하지 않은 곳

| 파일 | 이유 |
|------|------|
| `app/admin/templates/subscriptions/_table.html` | 구독 목록 테이블 — 카드 정보 컬럼 없음 |
| `app/admin/templates/services/_subs_table.html` | 서비스 상세 구독 탭 — 카드 정보 컬럼 없음 |
| 구독 목록/엑셀 내보내기 라우트 | 카드 필드 미사용 |
