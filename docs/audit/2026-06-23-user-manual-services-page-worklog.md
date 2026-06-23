# 사용자 매뉴얼 '서비스 관리' 전용 페이지 추가 워크로그

- 날짜: 2026-06-23
- 작업자: seungjinhan (oasis@medisolveai.com)

## 배경

사용자 매뉴얼(`docs/user_manual/`)에 카드·구독·요금제·계정은 전용 페이지가 있는데 **서비스(목록·등록·키 발급) 전용 사용자 페이지가 누락**돼 있었다(서비스 생성/목록은 16번 기술 레퍼런스와 dev_manual에만 존재). 사용자 안내 페이지를 신설.

## 변경

- 신규 `docs/user_manual/19-admin-services.md` — "서비스 관리 (목록·등록·키 발급)":
  - 시작 전 안내(담당자 계정 먼저), 1. 서비스 목록(검색·상태필터·컬럼), 2. 서비스 등록(서비스명·담당자/대표·허용IP·**토스 시크릿 키**·취소정책), 3. 키 발급(1회성 API키/HMAC·재발급), 4. 상세 설정(탭·IP·취소정책·알림URL·토스키 설정/교체·상태·삭제·담당자·키회전).
  - SYSTEM_ADMIN 전용 명시, 관련 문서 상호링크(콘솔·계정·요금제·16 화면레퍼런스·11 API·15 알림·08 감사).
- `docs/user_manual/build.py` DOCS에 `19-admin-services.md`를 **'관리자 콘솔' 다음**(사용자 매뉴얼 그룹)으로 삽입 → 사이드바 순서 반영.
- `docs/user_manual/01-admin-console.md` "함께 보기"에 [서비스 관리] 링크 추가.

### 번호 표기
- 사용자/개발자 매뉴얼이 0~18 **연속 번호**를 공유해, '2'로 끼우면 두 그룹 전체가 연쇄 재번호되는 문제가 있어 **표시 번호 없이**(제목 "서비스 관리 …") 추가하고 사이드바 순서만 콘솔 다음으로 배치. 페이지 간 링크는 파일명 기반이라 영향 없음.

## 검증

- `uv run --with markdown python docs/user_manual/build.py` → 20개 문서 재빌드, `19-admin-services.html` 생성·index/사이드바 링크 노출 확인.
- docker dev 이미지 재빌드 → `GET /user-manual/19-admin-services.html` HTTP 200, 내용(토스 키·TOSS_KEY_NOT_CONFIGURED 등) 라이브 반영 확인.

## 참고

- 기술 레퍼런스(라우트·함수)는 `docs/user_manual/16-admin-screens.md` 16.4 및 `docs/manual/dev_manual/admin/03-services.md`에 유지.
