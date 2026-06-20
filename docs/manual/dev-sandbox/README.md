# 서비스 사전 개발용 — API 계약 & 덤프 데이터

실제 결제 서버가 **아직 오픈되지 않았을 때**, 서비스(외부) 개발자가 구독/결제 연동을
**미리** 개발할 수 있도록 제공하는 **응답 스키마 + 덤프 데이터(JSON 픽스처)** 모음입니다.

> 별도의 모의 서버를 띄울 필요 없이, 이 JSON들을 **가짜 응답(fixture/stub)**으로 그대로 사용해
> 화면·로직을 먼저 만들 수 있습니다. 실제 서버가 열리면 베이스 URL·키만 바꾸면 됩니다(스키마 동일).

자세한 설명은 매뉴얼 **개발 연동 > 사전 개발(덤프 데이터)** 문서(`11-dev-sandbox.html`)를 보세요.

## 덤프 파일 (`dump/`)
| 파일 | 대응 API | 내용 |
|---|---|---|
| `services.json` | `GET /api/v1/services` | 서비스 목록 |
| `plans.json` | `GET /api/v1/plans` | 요금제 4종(일반·첫구독할인·체험·일단위/무료) |
| `subscription.create.json` | `POST /api/v1/subscriptions` (201) | 구독 생성 결과(ACTIVE) |
| `subscription.get.json` | `GET /api/v1/subscriptions/{id}` | 구독 조회(ACTIVE, `access_allowed`) |
| `subscription.trial.json` | 〃 | 체험(TRIAL) |
| `subscription.past_due.json` | 〃 | 미수(PAST_DUE, retry_count) |
| `subscription.suspended.json` | 〃 | 정지(SUSPENDED, access_allowed=false) |
| `subscription.canceled.json` | 〃 | 해지 예약(CANCELED) |
| `subscription.expired.json` | 〃 | 만료(EXPIRED, card=null) |
| `payments.list.json` | `GET /api/v1/payments/{id}` | 결제 내역(구독 정기+단건, 성공/실패/취소 혼합) |
| `payments.empty.json` | 〃 | 결제 내역 없음(빈 목록) |
| `payment.oneoff.create.json` | `POST /api/v1/payments` (201) | 단건 결제 결과 |
| `payment.oneoff.cancel.json` | `POST /api/v1/payments/{order_id}/cancel` | 단건 취소(수수료·환불 포함) |
| `errors.json` | 공통 에러 | 401/403/402/404/409/422/429/503 예시 본문 |

## 쓰는 법(예)
- 프런트/백엔드 **목 응답**으로 import 해서 화면·로직 개발
- API 클라이언트의 HTTP 레이어를 stub 처리하고 이 JSON 반환
- Postman/MSW/WireMock 등 목 도구의 응답 본문으로 등록

```python
import json
plans = json.load(open("docs/manual/dev-sandbox/dump/plans.json"))["plans"]
# 실제 GET /api/v1/plans 응답과 동일한 구조
```

## 실제 서버로 전환할 때
1. 베이스 URL을 실제 결제 서버로 변경
2. 실제 **API 키·HMAC 시크릿**으로 요청 서명(매뉴얼 6장)
3. 프런트에 실제 **토스 클라이언트 키** + 카드 등록창(`requestBillingAuth`) 연동
4. 응답 스키마는 동일하므로 비즈니스 로직은 그대로 둡니다.

> 이 폴더는 문서·픽스처일 뿐이며 앱(`app/`) 코드와 무관합니다.
> 값은 예시이며 ID·시각은 고정 샘플입니다.
