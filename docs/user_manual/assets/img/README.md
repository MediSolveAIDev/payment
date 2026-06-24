# 매뉴얼 캡처 이미지 폴더

여기에 어드민 화면 캡처(PNG)를 넣으면, 매뉴얼 본문(docs/user_manual/*.md)에
`![캡션](assets/img/<파일명>.png)` 으로 삽입한 뒤 `build.py`로 재빌드한다.

## 캡처 가이드
- 브라우저 폭 ~1280px, 라이트 모드, 민감정보(실명·실카드·실이메일)는 데모 데이터로.
- PNG 권장. 파일명은 아래 표대로 맞추면 자동 매핑이 쉽다(다르면 채팅으로 알려주세요).

## 파일명 → 들어갈 문서/섹션
| 파일명 | 문서 | 위치 |
| --- | --- | --- |
| `login.png` | 01-admin-console | 로그인 화면 |
| `dashboard.png` | 09-dashboard | 대시보드 전체 |
| `service-new.png` | 00-overview / 01 | 서비스 등록 폼 |
| `service-keys.png` | 00-overview | API 키·HMAC 시크릿 1회 표시 |
| `service-detail.png` | 01 / 16 | 서비스 상세(키·카드·요금제 탭) |
| `cards-list.png` | 02-admin-card | 등록 카드 목록 |
| `card-detail.png` | 02-admin-card | 카드 상세/활성 토글 |
| `subscriptions-list.png` | 03-admin-subscription | 구독 목록(상태 필터) |
| `subscription-detail.png` | 03-admin-subscription | 구독 상세(강제취소·연장·재결제) |
| `plans-list.png` | 04-admin-plan | 요금제 목록 |
| `plan-form.png` | 04-admin-plan | 요금제 생성/수정 폼 |
| `payments-list.png` | 05-admin-payment-refund | 결제 목록(구독+단건) |
| `payment-detail.png` | 05-admin-payment-refund | 결제 상세·환불 |
| `accounts-list.png` | 06-admin-accounts | 계정 목록 |
| `account-new.png` | 06-admin-accounts | 계정 생성 폼 |
| `settings.png` | 07-admin-settings | 전체 설정(킬스위치 등) |
| `audit.png` | 08-admin-audit | 감사 로그 |
| `settlement.png` | 09-dashboard | 정산 보기 |
| `sample-07-receipt.png` | 19-sample-service | 결제 내역 → 매출전표(영수증) 보기(토스 영수증 새 탭) — *현재 플레이스홀더, 실제 캡처로 교체* |
