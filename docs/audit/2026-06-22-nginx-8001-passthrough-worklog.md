# nginx 8001 통과 프록시 추가 워크로그

- 날짜: 2026-06-22
- 작업자: seungjinhan (oasis@medisolveai.com)

## 배경

호스트 nginx 설정은 현재 80/443만 인터넷에 노출하고 443에서 TLS를 종단해 docker app(`127.0.0.1:8000`)으로 프록시한다. 같은 VM의 **별도 백엔드 서비스를 8001 포트 그대로 외부에 노출**할 필요가 생겨, nginx가 8001을 리슨해 `127.0.0.1:8001`로 통과 프록시하는 server 블록을 추가했다.

요구 해석: AskUserQuestion으로 확인 → "외부 8001 → 백엔드 8001 통과 프록시(별도 서비스 포트 그대로 노출)" 선택.

## 변경

### 1) nginx 설정 (server 블록 추가, 평문 http)
- `docker/nginx/host/payment-host.conf` — canonical(배포 시 `/etc/nginx/conf.d/payment.conf`로 복사)
- `docker/nginx/conf.d/payment.conf` — 동기화용 사본

추가한 블록:
```nginx
server {
    listen 8001;
    listen [::]:8001;
    server_name api-stg-pay.medisolveai.com;
    client_max_body_size 16m;
    location / {
        proxy_pass http://127.0.0.1:8001;     # 별도 백엔드 서비스(루프백 publish)
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection        "";
        proxy_read_timeout 90s;
    }
}
```
- 80/443 도메인 프록시와 독립. 평문(http) 통과 — TLS 미종단.
- XFF/X-Forwarded-Proto 헤더는 443 블록과 동일하게 부착(백엔드 `TRUST_PROXY_HOPS=1` 정합).

### 2) 매뉴얼 반영 + 재빌드 (정식 매뉴얼)
- `docs/user_manual/10-install-deploy.md` 5.4 호스트 nginx + TLS 섹션의 `payment.conf` 전체 코드 블록에 8001 server 블록 추가 + 하단 설명 문단에 8001 블록 안내 추가.
- `uv run --with markdown python docs/user_manual/build.py` 로 19개 문서 재빌드 → `docs/user_manual/10-install-deploy.html` 갱신(8001 표기 확인).

## 주의/후속

- **방화벽/보안그룹**: 8001은 VM 인바운드를 별도로 열어야 외부에서 접근된다(Azure NSG 등).
- **백엔드 기동**: `127.0.0.1:8001`에 실제로 서비스가 떠 있어야 한다(미기동 시 502).
- **평문 노출**: 8001은 TLS 미적용. 외부 https가 필요하면 별도 도메인·인증서 구성 권장.
- 적용: `sudo cp docker/nginx/host/payment-host.conf /etc/nginx/conf.d/payment.conf && sudo nginx -t && sudo systemctl reload nginx`
