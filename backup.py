#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Щоденна копія баз і конфігів усіх клієнтів — у Telegram.

Складає архів із clients/*/config.json і clients/*/zayavky.db
(фото не беремо, вони не змінюються) і надсилає його власнику.
Налаштування — у backup.json поруч: {"token": "...", "chat_id": 123}.
"""
import json
import os
import sys
import tarfile
import tempfile
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENTS = os.path.join(HERE, "clients")
CFG = os.path.join(HERE, "backup.json")


def load():
    if not os.path.exists(CFG):
        sys.exit("Нема backup.json — нема куди слати копію.")
    with open(CFG, encoding="utf-8") as f:
        c = json.load(f)
    if not c.get("token") or not c.get("chat_id"):
        sys.exit("У backup.json бракує token або chat_id.")
    return c


def collect(path):
    n = 0
    with tarfile.open(path, "w:gz") as tar:
        for name in sorted(os.listdir(CLIENTS)):
            d = os.path.join(CLIENTS, name)
            if not os.path.isdir(d):
                continue
            for fn in ("config.json", "zayavky.db"):
                p = os.path.join(d, fn)
                if os.path.exists(p):
                    tar.add(p, arcname="%s/%s" % (name, fn))
                    n += 1
    return n


def send(cfg, path, caption):
    boundary = "----b" + os.urandom(12).hex()
    with open(path, "rb") as f:
        blob = f.read()
    parts = []
    for k, v in (("chat_id", str(cfg["chat_id"])), ("caption", caption)):
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                      % (boundary, k, v)).encode("utf-8"))
    parts.append(('--%s\r\nContent-Disposition: form-data; name="document"; '
                  'filename="%s"\r\nContent-Type: application/gzip\r\n\r\n'
                  % (boundary, os.path.basename(path))).encode("utf-8"))
    parts.append(blob)
    parts.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/sendDocument" % cfg["token"],
        data=b"".join(parts),
        headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.loads(r.read().decode("utf-8"))
    if not data.get("ok"):
        sys.exit("Telegram відмовив: %s" % data.get("description"))


def main():
    cfg = load()
    if not os.path.isdir(CLIENTS):
        sys.exit("Нема теки clients/")
    stamp = datetime.now().strftime("%Y-%m-%d")
    out = os.path.join(tempfile.gettempdir(), "boty-%s.tar.gz" % stamp)
    n = collect(out)
    if not n:
        sys.exit("Нічого копіювати.")
    size = os.path.getsize(out) / 1024.0
    send(cfg, out, "Копія за %s · файлів: %d · %.0f КБ" % (stamp, n, size))
    os.remove(out)
    print("Надіслано: %d файлів, %.0f КБ" % (n, size))


if __name__ == "__main__":
    main()
