# 워크로그: sample_service 데모 흐름 재정렬

- **날짜**: 2026-06-19
- **작업자**: Claude (요청자: oasis@medisolveai.com)
- **범위**: `sample_service/` 전용 — 메인 payment_system 서버(app/) 미변경

---

## 작업 개요

샘플 서비스의 온보딩 데모 흐름 순서를 **서비스선택→이메일** 에서 **이메일→서비스→카드→구독/결제** 로 재정렬했다.

### 이전 흐름
`/` (서비스 선택, 비로그인 허용) → `/login` (이메일) → `/plans`

### 새 흐름
`/login` (이메일 선택, 1단계) → `/services` (서비스 선택, 2단계) → `/card` (카드 등록·보유 카드 조회, 3단계) → `/plans` 또는 `/pay` (4단계)

---

## 변경 파일 목록

| 파일 | 변경 내용 |
|------|-----------|
| `shop/views.py` | `_gate()` 순서 교체(로그인→서비스), `root_view()` 신규, `login_view()` 서비스 가드 제거+`/services` 리다이렉트, `logout_view()` `/login`으로, `services_view()` 로그인 필수 가드 추가·users 컨텍스트 제거, `service_select_view()` 이메일 파라미터 제거·`/card` 리다이렉트, `service_save_key_view()` `/card` 리다이렉트 |
| `shop/urls.py` | 루트 `""` → `root_view` |
| `shop/context.py` | `nav_user_email` 추가 (현재 로그인 이메일을 모든 템플릿에 주입) |
| `shop/templates/shop/base.html` | 내비 순서 재정렬(이메일→서비스→카드→요금제/결제/내구독/내역), 현재 이메일·서비스 상태 표시 |
| `shop/templates/shop/login.html` | 1단계로 변경, 기존 등록 이메일 빠른 선택 버튼 추가, 스텝 인디케이터 갱신 |
| `shop/templates/shop/services.html` | 2단계로 변경, 이메일 선택 섹션·JS 제거, 스텝 인디케이터 갱신, 선택 버튼 라벨 "선택"으로 단순화 |
| `shop/templates/shop/card.html` | 3단계로 변경, 보유 카드 조회 섹션 레이블 명확화, 카드 등록 후 `/plans`·`/pay` 버튼 추가, "등록된 카드 없음" 안내 추가 |
| `shop/tests.py` | 새 흐름 기준 전체 수정 (78개 테스트, 전부 통과) |
| `README.md` | 흐름 섹션 새 순서로 갱신 |
| `docs/dev_manual/15-external-api-and-sample.md` | 4-2절 전체 흐름 다이어그램 갱신, 게이트·루트·로그아웃 변경사항 명시 |

---

## 핵심 로직 변경 상세

### `_gate()` 순서 교체
```python
# 이전
if _active_cred(request) is None:  # 서비스 먼저
    return redirect("/")
if _current_user(request) is None:  # 그다음 로그인
    return redirect("/login")

# 이후
if _current_user(request) is None:  # 로그인 먼저
    return redirect("/login")
if _active_cred(request) is None:   # 그다음 서비스
    return redirect("/services")
```

### `root_view()` 신규 추가
루트 `/`가 이전처럼 `services_view`를 직접 렌더하지 않고, 세션 상태에 따라 `/login` → `/services` → `/card` 중 하나로 리다이렉트하는 순수 라우터가 됐다.

### `login_view()` 변경
- 서비스 필수 가드(`_active_cred` 체크) 제거 — 이메일이 첫 단계이므로 서비스 없어도 접근 가능
- POST 성공 후 리다이렉트 대상: `/plans` → `/services`
- `existing_users` 컨텍스트 추가 (기존 이메일 빠른 선택 버튼용)

### `service_select_view()` / `service_save_key_view()` 변경
- 이메일 파라미터 처리 로직 완전 제거 (이메일은 `/login`에서 처리)
- 성공 후 리다이렉트: `/plans` 또는 `/login` → `/card` (단일화)

### 보유 카드 조회 표시 (`/card`)
- 카드 있을 때 섹션 헤더를 "보유 카드"로 명확히 표시
- 카드 없을 때 "등록된 카드 없음" 안내 문구 추가
- 카드 등록 후 `/plans`(요금제 구독 →), `/pay`(일반 결제 →), `/my`(내 구독 확인) 버튼 표시

---

## 테스트 결과

```
Ran 78 tests in 0.312s
OK
```

- 변경·추가된 주요 테스트 클래스: `AuthFlowTest`(완전 재작성 12개), `ServiceFlowRedirectTest`(리다이렉트 대상 변경), `ServicesSelectTest`(`/plans`→`/card` 갱신)
- 전 클래스 가드 어서션 `/` → `/login` 일괄 수정
- 삭제된 테스트: `test_select_with_email_logs_in_and_redirects_to_plans`, `test_select_with_invalid_email_rejected` (이메일 선택이 서비스 화면에서 제거됨에 따라)
