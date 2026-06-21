# 샘플 서비스 키 입력 방식 변경 매뉴얼 동기화 워크로그

작성일: 2026-06-21
배경: 사용자가 `sample_service/.env`·`.env.example`에서 `SERVICE_API_KEY`/`SERVICE_HMAC_SECRET`를 제거(이제 `/services` 화면에서 직접 입력 → 세션 `ServiceCredential` 저장). 매뉴얼을 실제와 동기화.

## 근거(코드)
- 보호 화면은 `creds=_creds(request)`(세션 자격증명, `views.py:33 _active_cred`)를 사용. 키 없으면 `/services`로 리다이렉트(`views.py:62,226`).
- `payment_client._request`는 `creds=None`일 때만 `settings.SERVICE_API_KEY`로 폴백(`payment_client.py:44`). creds 없이 호출하는 곳은 무인증 `list_services()`뿐.
- `settings.SERVICE_API_KEY = os.environ.get(..., "")` → 없어도 빈 문자열(앱 정상).
- 결론: `.env`의 서비스 키는 사실상 불필요(레거시 폴백). UI 입력이 본 경로.

## 변경
- **docs/user_manual/17-sample-service.md** (17.5): `.env` 예시에서 `SERVICE_API_KEY`/`SERVICE_HMAC_SECRET` 제거, "키는 `/services` 화면에서 입력(세션 저장)" 안내 추가. 사전 단계의 키 복사 설명도 "/services에 입력"으로 정정.
- **docs/dev_manual/15-external-api-and-sample.md** (4-6): `.env` 블록에서 두 줄 제거, 폴백 설명을 "`.env`에 두지 않음 + /services 입력" 기준으로 갱신, 셋업 `cp` 주석 정정. → dev_manual 재빌드(정식 사본 docs/manual/dev_manual 동기화).
- **sample_service/README.md**: 키 복사 → `/services` 입력 안내, `cp` 주석 정정.

## 보존(의도)
- 코드 폴백 설명(`payment_client`의 `settings.SERVICE_API_KEY` 폴백)은 코드가 그대로라 유지.
- `docs/superpowers/plans|specs/*` 과거 계획·설계 문서는 기록이라 미수정.

## 검증
- user_manual 재빌드 19개, dev_manual 재빌드 30개(+사본 32개 동기화).
- 활성 매뉴얼(.md)에서 "`.env`에 SERVICE 키 채우기" 안내 잔여 0 확인.

## 보안 메모
- 사용자가 채팅에 붙여넣은 `SERVICE_HMAC_SECRET`는 노출로 간주 — 활성 키면 어드민에서 재발급(로테이션) 권고.
