# Pay VM Handoff

작성일: 2026-06-19

Pay는 B2C 영역의 결제 API용 신규 VM 서비스입니다. 인프라는 Selly와 동일하게 Public IP 직결 VM + nginx + Let's Encrypt 패턴을 따릅니다.

현재 Azure VM/Network 리소스는 생성 완료 상태입니다. VM 내부의 `/opt/pay`, docker compose, nginx upstream, 앱 `.env`, TLS 발급/갱신, 배포 스크립트는 아직 구성 전입니다.

## Provisioned

| Env | VM | Public IP | Private IP | Size | OS Disk | RG |
|---|---|---:|---:|---|---:|---|
| stg | `vm-pay-api-stg` | `20.194.3.10` | `10.44.1.4` | `Standard_F4s_v2` | 50 GB | `rg-b2c-stg` |
| prod | `vm-pay-api-prod` | `20.214.104.37` | `10.45.1.4` | `Standard_D4s_v5` | 100 GB | `rg-b2c-prod` |

## Network

| Env | VNet | Subnet | NSG | Public IP |
|---|---|---|---|---|
| stg | `vnet-pay-stg` `10.44.0.0/16` | `snet-pay-api-stg` `10.44.1.0/24` | `nsg-pay-api-stg` | `pip-pay-api-stg` |
| prod | `vnet-pay-prod` `10.45.0.0/16` | `snet-pay-api-prod` `10.45.1.0/24` | `nsg-pay-api-prod` | `pip-pay-api-prod` |

Inbound NSG:

| Port | Source | Purpose |
|---:|---|---|
| 22 | `*` | SSH |
| 80 | `*` | HTTP / Let's Encrypt challenge |
| 443 | `*` | HTTPS |

The VM has a system-assigned managed identity with `AcrPull` on:

```text
acrcspmedisolveaishared
```

## SSH

User:

```text
azureuser
```

Key:

```text
seoul-region.pem
```

Fingerprint:

```text
2048 SHA256:gVfp9VvIkPIYlhL5ywTNcWKkprnz9Sml95aEbHIaYPI no comment (RSA)
```

Example:

```bash
chmod 600 ~/.ssh/pems/seoul-region.pem
ssh -i ~/.ssh/pems/seoul-region.pem azureuser@20.194.3.10
ssh -i ~/.ssh/pems/seoul-region.pem azureuser@20.214.104.37
```

Private key material is intentionally not embedded in this document. Share the `.pem` through the approved secret channel.

## DNS

Use A records.

| Env | FQDN | Type | Value |
|---|---|---|---|
| stg | `api-stg-pay.medisolveai.com` | A | `20.194.3.10` |
| prod | `api-pay.medisolveai.com` | A | `20.214.104.37` |

Suggested TTL: `300`.

Check:

```bash
dig +short api-stg-pay.medisolveai.com
dig +short api-pay.medisolveai.com
```

## TLS / nginx

TLS is not configured yet. After DNS propagation, issue certificates on each VM with the environment-specific FQDN.

```bash
sudo apt-get update
sudo apt-get install -y nginx certbot python3-certbot-nginx

# stg VM only
sudo certbot --nginx -d api-stg-pay.medisolveai.com

# prod VM only
sudo certbot --nginx -d api-pay.medisolveai.com
```

Check:

```bash
sudo certbot certificates
sudo nginx -t
sudo systemctl status nginx --no-pager
```

## Not Provisioned

- Pay DB is not created.
- App runtime is not bootstrapped.
- nginx upstream and app port are not finalized.
- TLS certificates are not issued yet.
- CI/CD is not wired.

DB should be handled by a separate request. Open decision:

| Option | Note |
|---|---|
| Shared `pg-b2c-{env}` with `pay_*` DB | simpler, follows current B2C shared PG pattern |
| Dedicated PostgreSQL | stronger isolation for payment domain |

## Notes

- Quota cleanup performed before prod apply: unused DAY VM stack and empty external subscription RGs were removed.
- Korea Central `Total Regional vCPUs` is currently at quota after Pay prod creation.
- STG Selly/Pay runtime SSH keys were aligned to `seoul-region.pem`.
