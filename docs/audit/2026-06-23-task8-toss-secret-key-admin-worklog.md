# 워크로그: Task 8 — 어드민 서비스 등록/수정 폼에 toss_secret_key 입력 + 감사 라벨

**날짜**: 2026-06-23
**작업자**: Task 8 (SDD)
**목적**: 사내 구독/결제 서버 어드민 UI에 서비스별 토스 시크릿 키 등록·교체 기능 추가. 평문은 화면·로그·감사에 절대 노출 금지.

---

## 핵심 결정 사항

| 항목 | 결정 |
|------|------|
| 입력 타입 | `type="password"`, `autocomplete="off"` — 브라우저 자동완성 및 히스토리 차단 |
| 저장 방식 | AES 암호화는 서비스 레이어(`registry.set_toss_secret_key`) 위임 — 라우트는 단순 전달 |
| 상태 표시 | `toss_secret_key_encrypted` 유무로 "설정됨"/"미설정" 배지만 표시; 복호화 노출 없음 |
| 빈 값 처리 | 빈 제출 → 변경 없음(기존 키 유지); 감사 로그도 추가하지 않음 |
| 감사 라벨 | `service.toss_secret_key.set` → "토스 시크릿 키 설정", `service.toss_secret_key.changed` → "토스 시크릿 키 변경" |

---

## 변경 파일 목록

| 파일 | 유형 | 내용 |
|------|------|------|
| `app/admin/audit_labels.py` | 수정 | set/changed 한글 라벨 2개 추가 |
| `app/admin/routes/services.py` | 수정 | `set_toss_secret_key` 임포트; 등록 핸들러에 키 전달; 수정 핸들러 신규 |
| `app/admin/templates/services/new.html` | 수정 | 쓰기 전용 입력칸 추가 |
| `app/admin/templates/services/detail.html` | 수정 | 설정됨/미설정 배지 + 입력 카드 추가 |
| `tests/e2e/test_service_toss_secret_key.py` | 신규 | 통합 테스트 8개 |
| `docs/audit/2026-06-23-task8-toss-secret-key-admin-worklog.md` | 신규 | 이 파일 |

---

## 검증 결과

```
uv run pytest tests/security/test_admin_security.py \
  tests/e2e/test_service_detail_page.py \
  tests/e2e/test_admin_operations.py \
  tests/e2e/test_admin_services_plans.py \
  tests/e2e/test_service_notification_url.py \
  tests/e2e/test_service_toss_secret_key.py -q

120 passed in 23.92s
```

---

## 보안 검토

- 평문이 라우트 컨텍스트/응답/감사 detail에 포함되지 않음을 테스트로 검증
- `type="password"` + `autocomplete="off"` — 브라우저 저장/히스토리 차단
- `Cache-Control: no-store` 는 키 모달 핸들러에 이미 적용되어 있음(기존)
