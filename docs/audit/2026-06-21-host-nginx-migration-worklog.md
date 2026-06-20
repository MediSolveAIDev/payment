# 운영 배포를 호스트 nginx 방식으로 전환 워크로그

작성일: 2026-06-21
배경: 서버(VM)에 nginx가 이미 호스트 패키지로 설치되어 있어, compose의 nginx 컨테이너와 80/443 포트가 충돌. 컨테이너 nginx를 제거하고 호스트 nginx가 TLS 종단·리버스 프록시를 담당하도록 전환.

## 변경

### docker-compose.prod.yml
- **nginx 서비스 제거**(이미지·80/443 publish·conf/certs/certbot-www 마운트 전부 삭제).
- **app 노출 방식 변경**: `expose: 8000`(내부 전용) → `ports: ["127.0.0.1:8000:8000"]`(호스트 루프백에만 publish). 호스트 nginx가 `proxy_pass http://127.0.0.1:8000`로 접근. `0.0.0.0`이 아닌 `127.0.0.1` 바인딩이라 인터넷/사설망 직접 노출 없음.
- `TRUST_PROXY=true` / `TRUST_PROXY_HOPS=1` 유지 — 호스트 nginx도 프록시 1단이라 동일.
- 상단/하단 주석을 "2컨테이너(app·redis) + 호스트 nginx" 구조로 갱신.

### docker/nginx/host/payment-host.conf (신규)
- 호스트 nginx 사이트 설정 예시. 80 서버블록(server_name FQDN) + `proxy_pass http://127.0.0.1:8000` + XFF/Host/Proto 헤더 + `client_max_body_size 16m` + `proxy_read_timeout 90s`(토스 65s 대비).
- `certbot --nginx`가 443 블록·인증서·80→443 리다이렉트를 자동 주입하는 전제.

## 서버 적용 절차(요약)
1. 기존(실패하던) 스택 정리: `docker compose -f docker-compose.prod.yml down`
2. 재빌드·기동(2컨테이너): `docker compose -f docker-compose.prod.yml up -d --build` → `curl -I http://127.0.0.1:8000`로 app 확인
3. 호스트 nginx 설정 배치: `sudo cp docker/nginx/host/payment-host.conf /etc/nginx/conf.d/payment.conf` (server_name 환경에 맞게 수정)
4. `sudo nginx -t && sudo systemctl reload nginx`
5. `sudo certbot --nginx -d api-pay.medisolveai.com`(stg는 api-stg-pay) → TLS·리다이렉트 자동 구성

## 비고
- 레포의 컨테이너 nginx 자산(`docker/nginx/conf.d/payment.conf`, `certs/`, `certbot-www/`)은 이 방식에선 **미사용**(컨테이너 nginx 방식으로 되돌릴 때를 위해 보존). 정리를 원하면 별도 작업.
- 갱신은 certbot이 systemd 타이머로 자동 처리(호스트 설치형 장점). webroot는 `/var/www/html` 기준.
- `default.conf`의 기본 server_name과 충돌 시 `/etc/nginx/conf.d/default.conf` 제거 또는 server_name 명시.
