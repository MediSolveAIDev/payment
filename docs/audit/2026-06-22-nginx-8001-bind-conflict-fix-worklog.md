# nginx 8001 bind 충돌 수정 (8001 블록 제거) 워크로그

- 날짜: 2026-06-22
- 작업자: seungjinhan (oasis@medisolveai.com)

## 증상

스테이징 VM(`vm-pay-api-stg`)에서 `sudo systemctl start nginx` 실패.
```
nginx: [emerg] bind() to 0.0.0.0:8001 failed (98: ...)
nginx: [emerg] still could not bind()
```
`sudo nginx -t` 는 통과(문법·인증서 정상). `ss -ltnp | grep :8001` → **`docker-proxy`가 0.0.0.0:8001/[::]:8001 점유**.

## 원인

직전 두 작업에서 잘못 설계함:
- [2026-06-22-nginx-8001-passthrough-worklog.md](2026-06-22-nginx-8001-passthrough-worklog.md): nginx에 `listen 8001` server 블록 추가.
- sample_service `docker-compose.yml`은 `"8001:8000"`으로 **호스트 0.0.0.0:8001을 docker가 직접 publish**.

→ docker와 nginx가 **같은 호스트 8001**을 동시에 못 가진다. nginx가 8001 bind 시도 → 충돌 → 기동 실패. `nginx -t`는 포트 bind를 하지 않아 못 잡음.

## 결정 (사용자 선택: A안)

A) **nginx 8001 블록 제거.** nginx는 80/443만 담당. docker가 0.0.0.0:8001을 그대로 서빙 → 외부는 방화벽만 열면 `http://<도메인>:8001`로 docker에 직결. 접속 URL·CSRF origin(`http://api-stg-pay.medisolveai.com:8001`)은 동일하게 유지되고, 8001은 평문 http라 nginx를 앞에 둬도 TLS 이득이 없어 더 단순한 A를 채택.

(B안=nginx 앞단 유지+docker를 127.0.0.1:8011로 이전 은 미채택)

## 변경

- `docker/nginx/host/payment-host.conf`, `docker/nginx/conf.d/payment.conf`
  - `listen 8001` server 블록 삭제 → "왜 nginx로 8001을 프록시하지 않는가" 설명 주석으로 대체.
- `docs/user_manual/10-install-deploy.md`
  - 5.4 nginx 코드 블록에서 8001 server 블록 삭제 + 설명 문단을 "8001은 nginx 미프록시(bind 충돌), docker 직결" 안내로 교체. `build.py`로 19개 문서 재빌드.
- `sample_service/README.md`
  - "외부에서 접속하기" 섹션 제목·본문을 "(docker 8001 직결 — nginx 거치지 않음)"으로 수정. ALLOWED_HOSTS·CSRF_TRUSTED_ORIGINS 설정은 그대로 유효(직전 워크로그).

## 서버 적용 (운영 VM)

```bash
# (a) 8001 server 블록만 제거 — 8001 주석 라인부터 EOF까지 삭제(8001 블록이 파일 마지막)
sudo cp /etc/nginx/conf.d/payment.conf /tmp/payment.conf.bak
sudo sed -i '/8001/,$d' /etc/nginx/conf.d/payment.conf   # ※ 주의: 아래 (b) 권장
# (b) 권장: 레포 최신 payment-host.conf 재복사
sudo cp <repo>/docker/nginx/host/payment-host.conf /etc/nginx/conf.d/payment.conf

sudo nginx -t && sudo systemctl start nginx
```
이후 Azure NSG에서 **8001 인바운드 허용** → `http://api-stg-pay.medisolveai.com:8001` 접속.

## 주의

- sample_service는 데모/테스트 전용. 8001 평문 직결. https 필요 시 별도 도메인·인증서·프록시 구성.
