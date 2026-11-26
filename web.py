# -*- coding: utf-8 -*-
"""Сторінка розкладу, яку бот відкриває прямо в Telegram.

Один процес обслуговує всіх клієнтів: /<клієнт>/ віддає сторінку,
/<клієнт>/api/... — дані з бази саме цього клієнта.

Хто це відкрив, кажемо не ми, а Telegram: він передає підписаний рядок
initData, підписаний токеном того самого бота. Перевіряємо підпис
токеном із config.json клієнта — отже сторінку неможливо відкрити
чужим посиланням.

Запуск:  python3 web.py <тека з clients/> [порт]
"""
import hashlib
import hmac
import json
import math
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import slots                     # ті самі правила розкладу, що й у боті

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

HERE = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8443
CLIENTS = os.path.join(HERE, "clients")
PAGE = os.path.join(HERE, "miniapp.html")
MAX_AGE = 24 * 3600          # скільки живе підпис Telegram
SAFE = re.compile(r"^[a-z0-9_-]{1,32}$")


def client_cfg(name):
    if not SAFE.match(name or ""):
        return None
    path = os.path.join(CLIENTS, name, "config.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def check_init_data(init_data, token):
    """Перевірити підпис Telegram і повернути дані користувача.

    Рахунок простий: усі поля, крім hash, сортуються і склеюються,
    ключем служить SHA256 від «WebAppData» та токена бота. Збігся
    підпис — дані справжні; не збігся — це хтось сторонній.
    """
    if not init_data or not token:
        return None
    try:
        pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
    except Exception:
        return None
    got = dict(pairs)
    their_hash = got.pop("hash", "")
    if not their_hash:
        return None
    check = "\n".join("%s=%s" % (k, got[k]) for k in sorted(got))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    mine = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mine, their_hash):
        return None
    try:
        if time.time() - int(got.get("auth_date", 0)) > MAX_AGE:
            return None
    except ValueError:
        return None
    try:
        return json.loads(got.get("user", "{}"))
    except ValueError:
        return None


def setting_of(name, key):
    """Значення з таблиці settings бази клієнта. Туди пише бот."""
    path = os.path.join(CLIENTS, name, "zayavky.db")
    if not os.path.isfile(path):
        return ""
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=5)
        try:
            row = conn.execute("SELECT value FROM settings WHERE key=?",
                               (key,)).fetchone()
        finally:
            conn.close()
        return row[0] if row and row[0] else ""
    except Exception:
        return ""


def group_of(name):
    """Чат робочої групи цього клієнта, якщо її підключили."""
    try:
        return int(setting_of(name, "group_id") or 0)
    except ValueError:
        return 0


_member_cache = {}
MEMBER_TTL = 300


def in_group(token, group_id, user_id):
    """Чи є людина в робочій групі.

    Питаємо в Telegram, а не віримо на слово: список учасників групи
    змінюється щодня, і тримати його копію в конфігу означало б, що
    звільнений майстер бачить розклад ще тиждень.
    """
    if not group_id or not user_id:
        return False
    key = (group_id, user_id)
    hit = _member_cache.get(key)
    if hit and time.time() - hit[1] < MEMBER_TTL:
        return hit[0]
    url = ("https://api.telegram.org/bot%s/getChatMember?chat_id=%d&user_id=%d"
           % (token, group_id, user_id))
    ok = False
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        status = ((data.get("result") or {}).get("status") or "")
        ok = status in ("creator", "administrator", "member", "restricted")
    except Exception:
        # Не змогли спитати — краще відмовити, ніж пустити зайвого.
        ok = False
    _member_cache[key] = (ok, time.time())
    return ok


def may_see(cfg, name, user_id):
    """Власник бота — завжди. Решта — лише якщо вони в робочій групі."""
    if user_id in (cfg.get("admin_ids") or []):
        return True
    return in_group(cfg.get("token"), group_of(name), user_id)


def rows_for(name, since, until):
    path = os.path.join(CLIENTS, name, "zayavky.db")
    if not os.path.isfile(path):
        return []
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT id, slot, name, phone, service, master_id, master_name,"
            " price, minutes, deposit, paid, status, username, comment"
            " FROM bookings WHERE slot >= ? AND slot < ? ORDER BY slot",
            (since, until))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def masters_of(cfg):
    out = []
    for m in cfg.get("masters") or []:
        out.append({"id": m.get("id"), "name": m.get("name"),
                    "role": m.get("role", "")})
    return out


def tz_of(cfg):
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(cfg.get("timezone", "Europe/Kyiv"))
    except Exception:
        return None


def now_of(cfg):
    z = tz_of(cfg)
    return datetime.now(z) if z else datetime.now()


def busy_by_master(cfg, name, since, until):
    """Зайняті клітинки сітки, окремо для кожного майстра.

    Рахуємо так само, як бот: запис довший за крок сітки забирає
    стільки клітинок, скільки реально триває.
    """
    step = slots.step_minutes(cfg)
    out = {}
    for r in rows_for(name, since, until):
        if r["status"] == "cancelled":
            continue
        try:
            start = datetime.fromisoformat(r["slot"])
        except (TypeError, ValueError):
            continue
        n = max(1, math.ceil((r["minutes"] or step) / step))
        box = out.setdefault(r["master_id"] or "", set())
        for i in range(n):
            box.add((start + timedelta(minutes=step * i)
                     ).isoformat(timespec="minutes"))
    return out


def free_horizon(cfg):
    return int(cfg.get("free_days_ahead", cfg.get("days_ahead", 30)))


def free_day(cfg, day_iso, busy, tzinfo, now_dt):
    """Вільні години цього дня, по майстрах. Порожній список — вихідний."""
    out = []
    for i, m in enumerate(cfg.get("masters") or [None]):
        mid = (m or {}).get("id", "")
        times = slots.free_times(cfg, day_iso, m, None,
                                 busy.get(mid, set()), now_dt, tzinfo)
        if not times:
            continue
        out.append({"id": mid, "i": i if m else "x",
                    "name": (m or {}).get("name", cfg.get("business", "")),
                    "role": (m or {}).get("role", ""),
                    "times": [t[1] for t in times]})
    return out


def month_bounds(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    start = datetime(y, m, 1)
    end = datetime(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")


class Handler(BaseHTTPRequestHandler):
    server_version = "rozklad"

    def log_message(self, fmt, *args):
        pass                      # у лог бота це не потрібно

    def reply(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parts = urllib.parse.urlsplit(self.path)
        bits = [p for p in parts.path.split("/") if p]
        if not bits:
            return self.reply(404, {"error": "not found"})
        name = bits[0]
        cfg = client_cfg(name)
        if not cfg:
            return self.reply(404, {"error": "not found"})

        if len(bits) == 1:
            try:
                with open(PAGE, "rb") as f:
                    return self.reply(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                return self.reply(500, {"error": "page missing"})

        if bits[1] != "api":
            return self.reply(404, {"error": "not found"})

        q = urllib.parse.parse_qs(parts.query)
        user = check_init_data((q.get("init") or [""])[0], cfg.get("token"))
        if not user:
            return self.reply(401, {"error": "Відкрийте розклад кнопкою в боті"})
        uid = int(user.get("id") or 0)
        what = bits[2] if len(bits) > 2 else ""

        # Вільні години бачить будь-хто, хто відкрив сторінку з бота:
        # це те саме, що бот і так показує при записі, лише одним екраном.
        if what in ("freemonth", "freeday"):
            z = tz_of(cfg)
            n = now_of(cfg)
            horizon = (n + timedelta(days=free_horizon(cfg))
                       ).date().isoformat()

            if what == "freemonth":
                ym = (q.get("m") or [n.strftime("%Y-%m")])[0]
                if not re.match(r"^\d{4}-\d{2}$", ym):
                    return self.reply(400, {"error": "bad month"})
                a, b = month_bounds(ym)
                busy = busy_by_master(cfg, name, a, b)
                days = {}
                day = datetime.fromisoformat(a).date()
                last = datetime.fromisoformat(b).date()
                while day < last:
                    iso = day.isoformat()
                    if n.date() <= day <= datetime.fromisoformat(
                            horizon).date():
                        free = free_day(cfg, iso, busy, z, n)
                        if free:
                            days[iso] = sum(len(x["times"]) for x in free)
                    day += timedelta(days=1)
                return self.reply(200, {
                    "month": ym, "days": days, "horizon": horizon,
                    "business": cfg.get("business", ""),
                    "bot": setting_of(name, "bot_username"),
                    "masters": masters_of(cfg)})

            d = (q.get("d") or [n.strftime("%Y-%m-%d")])[0]
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
                return self.reply(400, {"error": "bad day"})
            busy = busy_by_master(cfg, name, d + "T00:00", d + "T23:59")
            return self.reply(200, {"day": d,
                                    "free": free_day(cfg, d, busy, z, n),
                                    "bot": setting_of(name, "bot_username"),
                                    "horizon": horizon})

        if not may_see(cfg, name, uid):
            return self.reply(403, {"error": "Розклад видно власнику "
                                             "та учасникам робочої групи"})
        only = None

        if what == "month":
            ym = (q.get("m") or [datetime.now().strftime("%Y-%m")])[0]
            if not re.match(r"^\d{4}-\d{2}$", ym):
                return self.reply(400, {"error": "bad month"})
            a, b = month_bounds(ym)
            rows = [r for r in rows_for(name, a, b) if r["status"] == "new"]
            if only:
                rows = [r for r in rows if r["master_id"] == only]
            days = {}
            for r in rows:
                days[r["slot"][:10]] = days.get(r["slot"][:10], 0) + 1
            return self.reply(200, {"month": ym, "days": days,
                                    "business": cfg.get("business", ""),
                                    "masters": masters_of(cfg), "only": only})

        if what == "list":
            # Усі найближчі записи поспіль, без розбивки по днях.
            n = datetime.now()
            a = n.strftime("%Y-%m-%dT00:00")
            b = (n + timedelta(days=120)).isoformat(timespec="minutes")
            rows = [r for r in rows_for(name, a, b) if r["status"] == "new"]
            if only:
                rows = [r for r in rows if r["master_id"] == only]
            return self.reply(200, {"rows": rows, "masters": masters_of(cfg),
                                    "only": only})

        if what == "day":
            d = (q.get("d") or [datetime.now().strftime("%Y-%m-%d")])[0]
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
                return self.reply(400, {"error": "bad day"})
            rows = rows_for(name, d + "T00:00", d + "T23:59")
            rows = [r for r in rows if r["status"] == "new"]
            if only:
                rows = [r for r in rows if r["master_id"] == only]
            return self.reply(200, {"day": d, "rows": rows,
                                    "masters": masters_of(cfg), "only": only})

        return self.reply(404, {"error": "not found"})


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    cert = os.path.join(HERE, "cert", "fullchain.pem")
    key = os.path.join(HERE, "cert", "privkey.pem")
    if os.path.isfile(cert) and os.path.isfile(key):
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        print("Розклад на https://0.0.0.0:%d" % PORT, flush=True)
    else:
        print("Розклад на http://0.0.0.0:%d (без сертифіката — "
              "Telegram таку адресу не відкриє)" % PORT, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
