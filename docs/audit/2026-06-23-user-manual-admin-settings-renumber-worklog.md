# 워크로그 — 사용자 매뉴얼 08 전체 설정 번호 정정·기능 보강

- 날짜: 2026-06-23
- 작성자: oasis@medisolveai.com
- 대상: `docs/user_manual/08-admin-settings.md` (문서 번호 8)

## 배경
문서 본문이 "7." 번호 체계로 남아 있어 파일명(08)과 불일치. 코드 대조로 누락 기능을 보강.

## 변경 내용
1. 번호 정정
   - H1 `# 7. 전체 설정` → `# 8. 전체 설정`
   - `## 7.1~7.5` → `## 8.1~8.5`, 목차/앵커 링크 동기화
   - 하위 절차에 `### 8.x.1` 소번호 부여(바꾸는 방법/비활성화/다시 켜기)
2. 기능 보강 (코드 대조: `app/admin/routes/settings.py`, `app/services/app_settings.py`, `app/models/global_settings.py`)
   - 재시도: 입력 범위(retry_limit≥0, interval≥1, grace≥0)·기본값(4/12/30) 표 컬럼 추가, 검증 오류 메시지 명시
   - 보안·결제 정책: 런타임 즉시 적용 설명, 1 이상 검증 및 오류 메시지, .env 비상 폴백 관계, 단건결제 기본 100,000,000원
   - 어드민 IP: IPv4/IPv6 형식 검증, 루프백(127.0.0.1/::1) 항상 허용·미저장 안내
   - 킬스위치: disabled_reason의 503 응답 전달·기본 문구, disabled_at/by 기록(언제·누가), Redis 캐시 즉시 전파
   - 상단에 "모든 변경이 감사 로그에 기록"되는 점을 `> 중요:` 콜아웃으로 강조
3. 이미지: 기존 `assets/img/settings.png` 재사용. 신규 placeholder 추가 없음(파일 생성 안 함).

## 참조 이미지 경로
- `docs/user_manual/assets/img/settings.png` (기존 재사용)

## 비고
- 다른 문서 링크(07/09) 미변경, 파일명·build.py 미변경.
