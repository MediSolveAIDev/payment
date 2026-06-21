# 어드민 매뉴얼 화면 캡처 반영 워크로그

작성일: 2026-06-21
요청: 매뉴얼에 캡처 이미지를 넣음(사용자가 직접 캡처본 제공).

## 상황
- 사용자 매뉴얼 01~09 문서에는 이미 `<figure class="shot"><img src="assets/img/…png">` 이미지 블록이 배치돼 있었고(이전 작업), 실제 PNG 파일만 비어 있었다.
- 사용자가 어드민 콘솔 데모데이터 화면 18종을 캡처해 `docs/user_manual/assets/img/`에 드롭.

## 한 일
- **중복 방지**: 문서에 이미 figure 블록이 있으므로 마크다운 이미지를 추가하지 않음.
- **참조-파일 매칭 검증**: 문서가 참조하는 이미지 19개(18 어드민 + sample-01-login) 전부 폴더에 존재 확인. 깨진 참조 0, 미사용 파일 0.
- **재빌드**: `build.py` → 18개 문서. (build.py는 assets/img를 지우지 않으므로 드롭한 PNG 보존)

## 배치(문서↔이미지)
- 01 §1.1 login · 02 §2.2 cards-list/§2.3 card-detail · 03 §3.1 subscriptions-list/§3.3 subscription-detail
- 04 §4.1 plans-list/§4.2 plan-form · 05 §5.1 payments-list/§5.2 payment-detail
- 06 §6.2 accounts-list/§6.3 account-new · 07 settings · 08 §8.2 audit · 09 §9.1 dashboard/§9.4 settlement
- service-new/service-keys/service-detail(서비스 등록·키·상세)도 참조처 존재
- 17(샘플) §17.6 sample-01-login(제가 헤드리스로 캡처)

## 비고
- 데모데이터(han@han.com)·마스킹 카드번호라 민감정보 없음.
- 17장의 로그인 이후 화면(sample-02~08)은 아직 미제공 — 캡처 목록 callout으로 안내만 되어 있음(깨진 img 아님, 텍스트).
