# QuizKid VPS Deployment

This deploy path assumes:

- Hostinger VPS reachable through `ssh hostinger-vps`
- domain `quizkid.navi-services.com`
- HTTP exposed on `9080`
- HTTPS exposed on `9443`
- app code in `/opt/quizkid/app`
- persistent data in `/opt/quizkid/data`
- runtime user `svc_quizkid`
- Python virtualenv in `/opt/quizkid/app/.venv`

## Server packages

```bash
apt update
apt install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx
```

## Application install

```bash
id -u svc_quizkid >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash svc_quizkid
mkdir -p /opt/quizkid
chown svc_quizkid:svc_quizkid /opt/quizkid
mkdir -p /opt/quizkid/data
chown svc_quizkid:svc_quizkid /opt/quizkid/data
runuser -u svc_quizkid -- git clone https://github.com/vjamithireddy/quizkids.git /opt/quizkid/app
runuser -u svc_quizkid -- bash -lc 'cd /opt/quizkid/app && python3 -m venv .venv && .venv/bin/pip install --upgrade pip && .venv/bin/pip install -r requirements.txt'
runuser -u svc_quizkid -- bash -lc 'cd /opt/quizkid/app && cp deploy/env.production.example .env'
```

Update `.env` with a strong `QUIZKID_SECRET_KEY`.

On first boot, open the site and create the first admin account through the setup screen. Parent accounts can then self-register from the landing page.

## Systemd

```bash
cp /opt/quizkid/app/deploy/systemd/quizkid.service /etc/systemd/system/quizkid.service
systemctl daemon-reload
systemctl enable quizkid
systemctl restart quizkid
systemctl status quizkid
```

## Nginx

```bash
cp /opt/quizkid/app/deploy/nginx/quizkid.navi-services.com.conf /etc/nginx/sites-available/quizkid.navi-services.com
ln -sf /etc/nginx/sites-available/quizkid.navi-services.com /etc/nginx/sites-enabled/quizkid.navi-services.com
nginx -t
systemctl reload nginx
```

## Certificate

Provision a certificate for `quizkid.navi-services.com` so the referenced files exist:

```bash
certbot certonly --nginx -d quizkid.navi-services.com
```

## Smoke checks

```bash
curl http://127.0.0.1:8001/health
curl -I http://quizkid.navi-services.com:9080/
curl -k https://quizkid.navi-services.com:9443/health
```
