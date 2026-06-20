# 06. 인증·보안 — 세 개의 문과 그 열쇠

> 목표: 401/403이 났을 때 어느 단계에서 막혔는지 바로 짚을 수 있다.

## 1. 외부 API — 3중 인증 (app/api/deps.py `authenticate_service`)

요청 헤더 4개(`x-service-key`·`x-timestamp`·`x-nonce`·`x-signature`)로 6단계 검증:

```
0 킬스위치(전역설정) → 1 API키 해시 대조 → 2 IP 화이트리스트 → 3 rate limit
→ 4 타임스탬프 ±300초 → 5 HMAC 서명(본문 포함) → 6 nonce 1회용
```

서명 = `HMAC_SHA256(secret, "METHOD\n경로\n타임스탬프\nnonce\nsha256(본문)")`.
**클라이언트 구현은 `sample_service/shop/payment_client.py`를 복사하는 게 정답** —
서버 구현(`app/core/security.py:sign_request`)의 검증된 미러다.

401 디버깅 순서: ① 서버 시계 오차(±300초) ② 키 재발급 여부(어드민에서 rotate하면
즉시 옛 키 무효) ③ nonce 재사용 ④ 본문 그대로 서명했는지(JSON 직렬화 차이).

## 2. 어드민 — 세션 + CSRF

- 로그인 실패 5회 → 15분 잠금. **IP당 분당 10회 rate limit**(무차별 시도 차단).
- 세션: Redis, 유휴 30분 연장형 + **절대 수명 12시간**(연장돼도 12시간 후 재로그인).
- 모든 POST는 CSRF 토큰 검증. 역할: SYSTEM_ADMIN(전체) / SERVICE_MANAGER(담당 서비스만 —
  남의 리소스는 403이 아니라 **404**로 숨긴다).
- 어드민 접속 IP 제한은 전역설정에서 — 현재 IP를 빼고 저장하면 잠기므로 검증이 막아준다.

## 3. 운영 장치

| 장치 | 무엇 | 어디서 |
|---|---|---|
| **킬스위치** | 외부 API 전체를 503으로 차단(어드민은 영향 없음) | 어드민 → 전체설정 → 서버 비활성화(본인 비밀번호 재확인). 5초 TTL Redis 캐시 — 전환 시 즉시 무효화 |
| **키 회전** | 서비스 API키/HMAC 즉시 재발급(옛 키 무효) | 서비스 상세 → 재발급. 외부 서비스에 새 키 전달 필수 |
| **감사 로그** | 누가 언제 무엇을 — 모든 변경 기록 | 어드민 → 감사 로그(검색·엑셀) |
| 웹훅 IP 검증 | 토스 공식 발신 IP만 허용 | `WEBHOOK_IP_CHECK_ENABLED` (dev에선 끄고 테스트) |

## 4. 운영 배포 체크리스트 (prod 반영 전 반드시)

1. `APP_ENV=prod` 설정 — dev 로그인 프리필 차단 + HSTS 활성화 조건
2. `TRUST_PROXY=true`면 **`TRUST_PROXY_HOPS`를 실제 프록시 단 수로** — XFF는
   "오른쪽에서 n번째"를 신뢰한다(왼쪽은 위조 가능). 잘못 설정하면 정상 요청이 거부됨(안전한 방향)
3. 인터넷 직노출이면 `PUBLIC_SERVICE_LIST_ENABLED=false` (무인증 서비스 목록 차단)
4. `uv run alembic upgrade head`
5. docker-compose.yml은 **개발 전용** — 운영은 별도 자격증명의 관리형 DB/Redis

> 더 깊이: [dev_manual 03(인증·보안)·13(계정)·14(전체설정)](../dev_manual/manual.html)
