#!/bin/bash
# Розгортання сторінки розкладу. Запускати на сервері від root.
# Один раз. Далі сертифікат оновлюється сам.
set -e

DOMAIN="${1:-botlids.duckdns.org}"
DUCK_TOKEN="${2:-}"
DIR=/opt/botlids

echo "== 1. Прив'язуємо ім'я до цього сервера"
if [ -n "$DUCK_TOKEN" ]; then
  curl -s "https://www.duckdns.org/update?domains=${DOMAIN%%.duckdns.org}&token=$DUCK_TOKEN"
  echo
fi

echo "== 2. Відкриваємо порти"
ufw allow 80/tcp  >/dev/null 2>&1 || true
ufw allow 443/tcp >/dev/null 2>&1 || true

echo "== 3. Сертифікат Let's Encrypt"
apt-get update -qq
apt-get install -y -qq certbot
systemctl stop botlids-web 2>/dev/null || true
certbot certonly --standalone -n --agree-tos --register-unsafely-without-email \
        -d "$DOMAIN"

mkdir -p "$DIR/cert"
cat > /usr/local/bin/botlids-cert-sync <<SYNC
#!/bin/bash
cp /etc/letsencrypt/live/$DOMAIN/fullchain.pem $DIR/cert/fullchain.pem
cp /etc/letsencrypt/live/$DOMAIN/privkey.pem  $DIR/cert/privkey.pem
chmod 600 $DIR/cert/privkey.pem
systemctl restart botlids-web 2>/dev/null || true
SYNC
chmod +x /usr/local/bin/botlids-cert-sync
/usr/local/bin/botlids-cert-sync

echo "== 4. Оновлення сертифіката копіюється автоматично"
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
ln -sf /usr/local/bin/botlids-cert-sync \
       /etc/letsencrypt/renewal-hooks/deploy/botlids.sh

echo "== 5. Служба сторінки"
cat > /etc/systemd/system/botlids-web.service <<UNIT
[Unit]
Description=Сторінка розкладу для ботів
After=network-online.target

[Service]
WorkingDirectory=$DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 $DIR/web.py $DIR 443
Restart=always
RestartSec=5
StandardOutput=append:/var/log/botlids-web.log
StandardError=append:/var/log/botlids-web.log

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now botlids-web

echo "== 6. Оновлення адреси раз на добу (на випадок зміни IP)"
if [ -n "$DUCK_TOKEN" ]; then
  cat > /etc/cron.daily/duckdns <<CRON
#!/bin/bash
curl -s "https://www.duckdns.org/update?domains=${DOMAIN%%.duckdns.org}&token=$DUCK_TOKEN" >/dev/null
CRON
  chmod +x /etc/cron.daily/duckdns
fi

sleep 3
echo
echo "== Готово. Перевірка:"
systemctl is-active botlids-web && echo "служба працює"
curl -sk "https://$DOMAIN/lezo" -o /dev/null -w "сторінка віддає код %{http_code}\n"
echo "Адреса для config.json:  https://$DOMAIN/<ім'я клієнта>"
