#!/bin/bash
# Налаштування сервера для ботів. Запускати на свіжому Ubuntu 24.04 під root.
# Робить усе: часовий пояс, Python, автозапуск, щоденні бекапи в Telegram.
set -e

DIR=/opt/botlids

echo "=== 1/6 Часовий пояс ==="
timedatectl set-timezone Europe/Kyiv

echo "=== 2/6 Оновлення та Python ==="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv tzdata rsync >/dev/null
python3 --version

echo "=== 3/6 Тека для ботів ==="
mkdir -p "$DIR"
chmod 750 "$DIR"

echo "=== 4/6 Служба автозапуску ==="
cat > /etc/systemd/system/botlids.service <<'UNIT'
[Unit]
Description=Telegram-боти клієнтів
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/botlids
ExecStart=/usr/bin/python3 /opt/botlids/run_all.py
Restart=always
RestartSec=10
StandardOutput=append:/var/log/botlids.log
StandardError=append:/var/log/botlids.log

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable botlids.service >/dev/null

echo "=== 5/6 Щоденний бекап ==="
cat > /etc/systemd/system/botlids-backup.service <<'UNIT'
[Unit]
Description=Копія баз клієнтів у Telegram

[Service]
Type=oneshot
WorkingDirectory=/opt/botlids
ExecStart=/usr/bin/python3 /opt/botlids/backup.py
UNIT
cat > /etc/systemd/system/botlids-backup.timer <<'UNIT'
[Unit]
Description=Копія баз щодня о 4:00

[Timer]
OnCalendar=*-*-* 04:00:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable botlids-backup.timer >/dev/null

echo "=== 6/6 Журнал і брандмауер ==="
touch /var/log/botlids.log
cat > /etc/logrotate.d/botlids <<'ROT'
/var/log/botlids.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
ROT
if command -v ufw >/dev/null 2>&1; then
    ufw allow OpenSSH >/dev/null 2>&1 || true
    ufw --force enable >/dev/null 2>&1 || true
fi

echo
echo "Сервер готовий. Тека: $DIR"
echo "Далі перенесіть файли ботів і запустіть:  systemctl start botlids"
