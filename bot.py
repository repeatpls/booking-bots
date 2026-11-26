#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Бот прийому заявок і запису на послуги.

Один файл, тільки стандартна бібліотека Python 3.9+.
Нічого встановлювати не треба: ні pip, ні віртуального оточення.

Запуск:   python3 bot.py              (клієнт у теці скрипта)
          python3 bot.py clients/lezo (конкретний клієнт)

Два режими, вмикаються самим конфігом:
  · без "masters" — один спільний графік, ціна в послузі
  · з "masters"   — у кожного майстра свій графік, свої послуги та свої ціни
"""

import calendar
import json
import math
import os
import re
import http.client
import socket
import sqlite3
import ssl
from contextlib import contextmanager
import sys
import threading
import time
import traceback
import urllib.error
import uuid
import urllib.request
from datetime import datetime, timedelta

import slots                     # спільні правила розкладу: бот і сторінка

try:
    from zoneinfo import ZoneInfo
except ImportError:                                    # Python 3.8
    ZoneInfo = None

# Тека клієнта. Передається аргументом, інакше береться тека самого скрипта.
# Саме тут і живе масштабування: один bot.py — скільки завгодно клієнтів.
#     python bot.py clients/lezo
#     python bot.py clients/bronza
# У кожного своя config.json і своя база заявок, вони ніяк не перетинаються.
HERE = (os.path.abspath(sys.argv[1]) if len(sys.argv) > 1
        else os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(HERE, "config.json")
DB_PATH = os.path.join(HERE, "zayavky.db")
API = "https://api.telegram.org/bot{token}/{method}"
API_HOST = "api.telegram.org"

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]
MONTHS = ["січня", "лютого", "березня", "квітня", "травня", "червня",
          "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"]
MONTHS_NOM = ["Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
              "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень"]
MONTHS_SHORT = ["Січ", "Лют", "Бер", "Кві", "Тра", "Чер",
                "Лип", "Сер", "Вер", "Жов", "Лис", "Гру"]


# --------------------------------------------------------------------------
# Конфіг
# --------------------------------------------------------------------------

def load_config():
    if not os.path.exists(CONFIG_PATH):
        sys.exit("Нема config.json у теці %s" % HERE)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["token"] = os.environ.get("BOT_TOKEN") or cfg.get("token", "")
    if not cfg["token"] or cfg["token"].startswith("ВСТАВТЕ"):
        sys.exit("У config.json не вказано token. Візьміть його у @BotFather.")
    if not cfg.get("admin_ids"):
        print("! У config.json порожній admin_ids — заявки нікому надсилати.\n"
              "  Напишіть боту /id, він підкаже ваш номер.", file=sys.stderr)
    return cfg


CFG = load_config()


def tz():
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(CFG.get("timezone", "Europe/Kyiv"))
    except Exception:
        return None


def now():
    return datetime.now(tz())


def parse_dt(value):
    """Час із бази. Старі записи могли зберегтись без часової зони
    (на Windows Python не має бази зон), тому зону добиваємо тут —
    інакше порівняння з now() падає з TypeError."""
    try:
        d = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if d.tzinfo is None and tz() is not None:
        d = d.replace(tzinfo=tz())
    return d


def step_minutes():
    return slots.step_minutes(CFG)


# --------------------------------------------------------------------------
# Майстри та послуги
# --------------------------------------------------------------------------

def masters():
    """Список майстрів. Порожній список = режим без майстрів."""
    return CFG.get("masters") or []


def has_masters():
    return bool(masters())


def master_by_id(mid):
    return slots.master_by_id(CFG, mid)


def service_by_id(sid):
    return slots.service_by_id(CFG, sid)


def master_services(m):
    """{service_id: ціна} майстра. Порожньо = робить усе за базовим прайсом."""
    return m.get("services") or {}


def master_does(m, sid):
    ms = master_services(m)
    return sid in ms if ms else True


def masters_for_service(sid):
    return [m for m in masters() if master_does(m, sid)]


def price_of(sid, m=None):
    """Ціна послуги: майстрова, якщо є, інакше базова з послуги."""
    if m is not None:
        p = master_services(m).get(sid)
        if p:
            return str(p)
    s = service_by_id(sid)
    return str(s.get("price", "")) if s else ""


def offered_services():
    """Послуги, які хтось реально робить."""
    out = []
    for s in CFG.get("services", []):
        if not has_masters() or masters_for_service(s["id"]):
            out.append(s)
    return out


def duration_of(sid):
    return slots.duration_of(CFG, sid)


def setting(key, default=None):
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?",
                           (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)",
                     (key, "" if value is None else str(value)))


def group_id():
    """Чат робочої групи, якщо її підключили командою /grupa."""
    raw = setting("group_id") or CFG.get("group_id") or 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def new_tkey():
    """Спільний ключ на всі копії одного повідомлення команді."""
    return uuid.uuid4().hex[:16]


def remember_thread(chat_id, message_id, client_chat, booking_id=None,
                    tkey=None, text=None):
    """Запам'ятати, якому клієнту належить це повідомлення в групі.

    Потрібно, щоб відповідь майстра в групі знайшла свого клієнта.
    """
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO threads(chat_id, message_id,"
                     " client_chat, booking_id, tkey, text)"
                     " VALUES(?,?,?,?,?,?)",
                     (chat_id, message_id, client_chat, booking_id, tkey, text))


def thread_client(chat_id, message_id):
    with db() as conn:
        row = conn.execute("SELECT client_chat, booking_id, tkey FROM threads"
                           " WHERE chat_id=? AND message_id=?",
                           (chat_id, message_id)).fetchone()
    if not row:
        return (None, None, None)
    return (row["client_chat"], row["booking_id"], row["tkey"])


def thread_copies(tkey):
    """Усі місця, де висить це саме повідомлення команді."""
    if not tkey:
        return []
    with db() as conn:
        rows = conn.execute("SELECT chat_id, message_id, text FROM threads"
                            " WHERE tkey=?", (tkey,)).fetchall()
    return [(r["chat_id"], r["message_id"], r["text"]) for r in rows]


def team_chats():
    """Кому слати новини: групі, якщо вона є, і особисто адмінам."""
    gid = group_id()
    out = [gid] if gid else []
    for a in CFG.get("admin_ids", []):
        if a != gid:
            out.append(a)
    return out


def notify(text, keyboard=None, client_chat=None, booking_id=None):
    """Одна точка, з якої команда дізнається про все.

    Раніше кожна новина розсилалась адмінам окремим шматком коду.
    Тепер і група, і адміни отримують однаково, а повідомлення в групі
    прив'язується до клієнта — щоб на нього можна було відповісти.
    """
    sent = []
    tkey = new_tkey() if client_chat else None
    for target in team_chats():
        res = send(target, text, keyboard)
        if isinstance(res, dict) and res.get("message_id"):
            sent.append((target, res["message_id"]))
            if client_chat:
                remember_thread(target, res["message_id"], client_chat,
                                booking_id, tkey, text)
    return sent


def remember_pay_msg(bid, chat_id, message_id):
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO pay_msgs(booking_id, chat_id,"
                     " message_id) VALUES(?,?,?)", (bid, chat_id, message_id))


def close_pay_buttons(bid, text):
    """Прибрати кнопки підтвердження з усіх місць, де вони висять.

    Те саме прохання підтвердити оплату йде і в групу, і кожному адміну.
    Щойно хтось один вирішив — решта копій мають перестати бути кнопками,
    інакше другий майстер натискає вже спрацьоване.
    """
    with db() as conn:
        rows = conn.execute("SELECT chat_id, message_id FROM pay_msgs"
                            " WHERE booking_id=?", (bid,)).fetchall()
    for r in rows:
        call("editMessageText", chat_id=r["chat_id"], message_id=r["message_id"],
             text=text, parse_mode="HTML")
    with db() as conn:
        conn.execute("DELETE FROM pay_msgs WHERE booking_id=?", (bid,))
    return len(rows)


def pay_cfg():
    return CFG.get("payment") or {}


def pay_mode():
    """off — вимкнено, link — посилання чи реквізити, invoice — Telegram Payments."""
    mode = (pay_cfg().get("mode") or "off").strip().lower()
    if mode == "invoice" and not pay_cfg().get("provider_token"):
        return "off"
    if mode == "link" and not (pay_cfg().get("link") or pay_cfg().get("card")
                               or pay_cfg().get("requisites")):
        return "off"
    return mode if mode in ("link", "invoice") else "off"


def parse_money(text):
    """«від 9 000 ₴» -> 9000. Повертає 0, якщо цифр нема.

    Ціну в config.json пишуть руками, тож вона буває і числом (600),
    і рядком («600 ₴»). Приймаємо обидва: інакше бот падає саме на
    розрахунку передоплати.
    """
    if isinstance(text, (int, float)):
        return int(text)
    digits = re.sub(r"[^\d]", "", (text or "").replace("\u00a0", ""))
    try:
        return int(digits)
    except ValueError:
        return 0


def deposit_for(d):
    """Скільки просити передоплати за цей запис, у гривнях."""
    dep = pay_cfg().get("deposit") or {}
    if dep.get("type") == "percent":
        base = parse_money(d.get("price"))
        if base:
            raw = base * float(dep.get("value") or 0) / 100.0
            step = int(dep.get("round_to") or 50)
            return max(step, int(round(raw / step) * step))
    try:
        return int(dep.get("amount") or 0)
    except (TypeError, ValueError):
        return 0


def money(n):
    return "{:,}".format(int(n)).replace(",", "\u00a0") + " ₴"


def loyalty():
    return CFG.get("loyalty") or {}


def repeat_percent():
    """На скільки дешевше тому, хто вже у нас був. 0 — знижки немає."""
    try:
        return max(0, min(90, int(loyalty().get("repeat_discount", 10))))
    except (TypeError, ValueError):
        return 0


def discount_price(price, pct):
    """«700 ₴» і 10 -> «630 ₴». «від 6 500 ₴» -> «від 5 850 ₴».

    Ціну пишуть руками і по-різному, тому зберігаємо все, що стоїть
    перед числом («від»), і міняємо саме число. Там, де числа немає
    («безкоштовно»), знижки не вигадуємо.
    """
    if not pct:
        return None
    base = parse_money(price)
    if base <= 0:
        return None
    txt = str(price or "")
    m = re.search(r"\d", txt)
    prefix = txt[:m.start()] if m else ""
    return prefix + money(int(round(base * (100 - pct) / 100.0)))


def norm_phone(phone):
    """Останні 9 цифр — щоб +380 67 і 067 були тим самим номером."""
    digits = re.sub(r"[^\d]", "", phone or "")
    return digits[-9:] if len(digits) >= 9 else digits


def last_visit(chat_id=None, phone=None):
    """Останній запис цієї людини, якщо вона вже у нас була."""
    with db() as conn:
        if chat_id:
            return conn.execute(
                "SELECT * FROM bookings WHERE chat_id=? AND status!='cancelled'"
                " ORDER BY id DESC LIMIT 1", (chat_id,)).fetchone()
        tail = norm_phone(phone)
        if len(tail) < 9:
            return None
        return conn.execute(
            "SELECT * FROM bookings WHERE status!='cancelled'"
            " AND REPLACE(REPLACE(REPLACE(REPLACE(phone,' ',''),'-',''),"
            "'(',''),')','') LIKE ? ORDER BY id DESC LIMIT 1",
            ("%" + tail,)).fetchone()


def visit_count(chat_id):
    with db() as conn:
        row = conn.execute("SELECT count(*) n FROM bookings WHERE chat_id=?"
                           " AND status!='cancelled'", (chat_id,)).fetchone()
    return row["n"] if row else 0


def apply_repeat_discount(d, returning):
    """Поставити знижку повторному клієнту або прибрати її."""
    d.pop("discount", None)
    d.pop("price_full", None)
    if not returning:
        return
    pct = repeat_percent()
    new = discount_price(d.get("price"), pct)
    if not new:
        return
    d["price_full"] = d.get("price")
    d["price"] = new
    d["discount"] = pct


def human_duration(minutes):
    """90 -> «1 год 30 хв», 60 -> «1 год», 30 -> «30 хв»."""
    m = int(minutes or 0)
    if m <= 0:
        return ""
    if m < 60:
        return "%d хв" % m
    h, rest = divmod(m, 60)
    return "%d год" % h if not rest else "%d год %d хв" % (h, rest)


def schedule_of(m):
    """Графік майстра, з відкотом до загального графіка закладу."""
    return slots.schedule_of(CFG, m)


def schedule_text(m):
    days, start, end, off = schedule_of(m)
    if len(days) == 7:
        d = "Щодня"
    else:
        d = ", ".join(WEEKDAYS[i] for i in sorted(days))
    t = "%s %s—%s" % (d, start, end)
    if off:
        t += "\nВихідні: " + ", ".join(pretty_date(x) for x in sorted(off))
    return t


# --------------------------------------------------------------------------
# Telegram API
# --------------------------------------------------------------------------

# Пул з'єднань із Telegram.
#
# Кожне натискання обробляє окремий потік, який живе секунду-дві. Тримати
# з'єднання всередині потоку марно: воно вмирає разом із ним, і кожен клік
# знову платить за TLS-рукостискання. Тому з'єднання лежать у спільній
# скриньці: потік бере вільне, користується і кладе назад.
#
# Спільний SSL-контекст на всі з'єднання — тоді навіть нове рукостискання
# відновлює сеанс замість повного обміну ключами.
_ssl_ctx = ssl.create_default_context()
_pool = []
_pool_lock = threading.Lock()
POOL_MAX = 6
POOL_WARM = 3          # скільки з'єднань тримати готовими заздалегідь
POOL_IDLE_MAX = 40     # через стільки секунд простою з'єднання вважаємо мертвим
CONNECT_TIMEOUT = 8    # скільки чекати на саме під'єднання до адреси


def net_addresses(host, port):
    """Адреси вузла, IPv4 першими.

    Якщо в сервера зламаний маршрут IPv6, система пробує його першим і
    висить ~25 секунд, перш ніж узяти IPv4. Саме стільки чекав другий бот
    при старті. Пробуємо IPv4 одразу, IPv6 лишаємо запасним.
    """
    try:
        info = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    v4 = [a for a in info if a[0] == socket.AF_INET]
    v6 = [a for a in info if a[0] != socket.AF_INET]
    return v4 + v6


class Conn(http.client.HTTPSConnection):
    """HTTPS-з'єднання, яке не застрягає на зламаному маршруті."""

    def connect(self):
        last = None
        for family, stype, proto, _canon, addr in net_addresses(self.host,
                                                                self.port):
            sock = socket.socket(family, stype, proto)
            sock.settimeout(CONNECT_TIMEOUT)
            try:
                sock.connect(addr)
            except Exception as e:
                last = e
                try:
                    sock.close()
                except Exception:
                    pass
                continue
            sock.settimeout(self.timeout)
            self.sock = _ssl_ctx.wrap_socket(sock, server_hostname=self.host)
            return
        raise last or OSError("не вдалося під'єднатися до %s" % self.host)


def net_prewarm():
    """Відкрити кілька з'єднань заздалегідь.

    Довге опитування постійно тримає одне з'єднання зайнятим, тож перше
    натискання клієнта інакше завжди застає скриньку порожньою і платить
    за рукостискання саме тоді, коли на нього дивляться.
    """
    made, spent = 0, []
    for _ in range(POOL_WARM):
        c = Conn(API_HOST, timeout=25, context=_ssl_ctx)
        t0 = time.time()
        try:
            c.connect()
        except Exception as e:
            print("Не вдалося підготувати з'єднання: %s" % e, file=sys.stderr)
            break
        spent.append(time.time() - t0)
        net_give(c)
        made += 1
    if made:
        print("Готових з'єднань: %d, відкрились за %s с. "
              "Перше натискання не чекатиме."
              % (made, ", ".join("%.1f" % x for x in spent)))


def net_take(timeout):
    """Взяти вільне з'єднання зі скриньки або відкрити нове.

    Telegram закриває з'єднання, яке простояло без діла. Тому у скриньці
    тримаємо ще й час повернення: усе, що лежить довше за POOL_IDLE_MAX,
    вважаємо мертвим і не чіпаємо — інакше перше натискання після паузи
    дістає труп і виклик падає.

    Другим значенням повертає, чи довелось відкривати: саме відкриття
    коштує дорого, і в лозі треба бачити, за що заплачено.
    """
    c, stale = None, []
    deadline = time.time() - POOL_IDLE_MAX
    with _pool_lock:
        while _pool:
            cand, when = _pool.pop()
            if when < deadline:
                stale.append(cand)
                continue
            c = cand
            break
    for dead in stale:
        net_close(dead)
    if c is None:
        return Conn(API_HOST, timeout=timeout, context=_ssl_ctx), True
    sock = getattr(c, "sock", None)
    if sock is not None:
        try:
            sock.settimeout(timeout)
        except Exception:
            pass
    return c, False


def net_give(c):
    """Повернути робоче з'єднання у скриньку, запам'ятавши час."""
    with _pool_lock:
        if len(_pool) < POOL_MAX:
            _pool.append((c, time.time()))
            return
    net_close(c)


def net_close(c):
    try:
        c.close()
    except Exception:
        pass


def net_drop():
    """Закрити всі з'єднання — після збою мережі починаємо з чистого."""
    with _pool_lock:
        old, _pool[:] = list(_pool), []
    for c, _when in old:
        net_close(c)


def call(method, _timeout=25, **params):
    """Запит до Telegram. Одна тиха спроба перез'єднатись: з'єднання зі
    скриньки могло протухнути, і це нормальна подія, а не помилка."""
    path = "/bot%s/%s" % (CFG["token"], method)
    body = json.dumps(params).encode("utf-8")
    headers = {"Content-Type": "application/json", "Connection": "keep-alive"}
    return net_request(method, path, body, headers, _timeout)


SLOW_SECONDS = 3.0


def net_request(method, path, body, headers, timeout):
    last = None
    # Обрив на з'єднанні зі скриньки — не збій, а природна смерть простою.
    # Він безкоштовний (падає миттєво), тож ліміту спроб не витрачає;
    # рахуємо лише спроби на свіжовідкритих з'єднаннях.
    fresh_tries, guard = 0, 0
    while fresh_tries < 2 and guard < POOL_MAX + 3:
        guard += 1
        started = time.time()
        c, fresh = net_take(timeout)
        if fresh:
            fresh_tries += 1
        try:
            c.request("POST", path, body=body, headers=headers)
            r = c.getresponse()
            raw = r.read()
            status = r.status
        except Exception as e:
            last = e
            net_close(c)
            if fresh:
                print("Обрив на %s через %.1f с (нове з'єднання): %s"
                      % (method, time.time() - started, e), file=sys.stderr)
            continue
        net_give(c)
        spent = time.time() - started
        if spent > SLOW_SECONDS and method != "getUpdates":
            print("ПОВІЛЬНО: %s %.1f с (%s з'єднання)"
                  % (method, spent, "нове" if fresh else "готове"),
                  file=sys.stderr)
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            print("Непрочитна відповідь на %s" % method, file=sys.stderr)
            return None
        if status != 200 or not data.get("ok"):
            print("HTTP %s на %s: %s" % (status, method,
                                         data.get("description")), file=sys.stderr)
            return None
        return data.get("result")
    print("Мережа (%s): %s" % (method, last), file=sys.stderr)
    return None


def send(chat_id, text, keyboard=None, remove_keyboard=False, reply_to=None):
    params = {"chat_id": chat_id, "text": text,
              "parse_mode": "HTML", "disable_web_page_preview": True}
    if reply_to:
        params["reply_to_message_id"] = reply_to
        params["allow_sending_without_reply"] = True
    if keyboard is not None:
        params["reply_markup"] = keyboard
    elif remove_keyboard:
        params["reply_markup"] = {"remove_keyboard": True}
    return call("sendMessage", **params)


def edit(chat_id, message_id, text, keyboard=None):
    params = {"chat_id": chat_id, "message_id": message_id,
              "text": text, "parse_mode": "HTML"}
    if keyboard is not None:
        params["reply_markup"] = keyboard
    return call("editMessageText", **params)


def inline(rows):
    return {"inline_keyboard": rows}


def btn(text, data):
    return {"text": text, "callback_data": data}


def lnk(text, url):
    """Кнопка, яка відкриває зовнішню адресу."""
    return {"text": text, "url": url}


def wapp(text, url):
    """Кнопка, яка відкриває сторінку просто в Telegram, поверх чату."""
    return {"text": text, "web_app": {"url": url}}


# --------------------------------------------------------------------------
# База
# --------------------------------------------------------------------------

@contextmanager
def db():
    """З'єднання з базою на один блок `with`.

    Раніше тут повертався сам sqlite3.Connection. Його `with` фіксує
    транзакцію, але файл НЕ закриває — дескриптори течуть із кожного
    натискання і з кожного тіку нагадувань. Через кілька годин connect()
    падає, виняток ковтається, і бот просто перестає відповідати.
    Тепер закриваємо явно, а busy_timeout дає потокам дочекатись
    одне одного замість помилки «database is locked».
    """
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=20000")
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def init_db():
    with db() as conn:
        # WAL: один потік пише, інші читають без очікування.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS bookings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER, username TEXT,
            service TEXT, slot TEXT,
            name TEXT, phone TEXT, comment TEXT,
            status TEXT DEFAULT 'new',
            created TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS state(
            chat_id INTEGER PRIMARY KEY,
            step TEXT, data TEXT, updated TEXT)""")
        conn.execute("""CREATE INDEX IF NOT EXISTS i_slot ON bookings(slot)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS reminders(
            booking_id INTEGER, minutes INTEGER, sent_at TEXT,
            PRIMARY KEY (booking_id, minutes))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS winback(
            chat_id INTEGER PRIMARY KEY, sent_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS optout(
            chat_id INTEGER PRIMARY KEY, since TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS pay_msgs(
            booking_id INTEGER, chat_id INTEGER, message_id INTEGER,
            PRIMARY KEY(chat_id, message_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS outbox(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payload TEXT, tries INTEGER DEFAULT 0, created TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY, value TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS threads(
            chat_id INTEGER, message_id INTEGER, client_chat INTEGER,
            booking_id INTEGER, tkey TEXT, text TEXT,
            PRIMARY KEY(chat_id, message_id))""")
        have = {r["name"] for r in conn.execute("PRAGMA table_info(threads)")}
        for col in ("tkey", "text"):
            if col not in have:
                conn.execute("ALTER TABLE threads ADD COLUMN %s TEXT" % col)
        conn.execute("""CREATE TABLE IF NOT EXISTS photos(
            key TEXT PRIMARY KEY, file_id TEXT)""")
        # Доповнення старих баз: нові колонки під майстрів.
        have = {r["name"] for r in conn.execute("PRAGMA table_info(bookings)")}
        for col in ("master_id", "master_name", "price"):
            if col not in have:
                conn.execute("ALTER TABLE bookings ADD COLUMN %s TEXT" % col)
        if "minutes" not in have:
            conn.execute("ALTER TABLE bookings ADD COLUMN minutes INTEGER")
        for col, decl in (("paid", "INTEGER DEFAULT 0"), ("deposit", "INTEGER"),
                          ("discount", "INTEGER DEFAULT 0"),
                          ("paid_at", "TEXT"), ("pay_mode", "TEXT"),
                          ("rating", "INTEGER"), ("rating_text", "TEXT"),
                          ("asked_at", "TEXT")):
            if col not in have:
                conn.execute("ALTER TABLE bookings ADD COLUMN %s %s" % (col, decl))


def get_state(chat_id):
    with db() as conn:
        row = conn.execute("SELECT step, data FROM state WHERE chat_id=?",
                           (chat_id,)).fetchone()
    if not row:
        return "menu", {}
    try:
        return row["step"], json.loads(row["data"] or "{}")
    except Exception:
        return "menu", {}


def set_state(chat_id, step, data):
    with db() as conn:
        conn.execute(
            "INSERT INTO state(chat_id, step, data, updated) VALUES(?,?,?,?) "
            "ON CONFLICT(chat_id) DO UPDATE SET step=excluded.step, "
            "data=excluded.data, updated=excluded.updated",
            (chat_id, step, json.dumps(data, ensure_ascii=False),
             now().isoformat(timespec="seconds")))


def clear_state(chat_id):
    with db() as conn:
        conn.execute("DELETE FROM state WHERE chat_id=?", (chat_id,))


def occupied(master_id):
    """Зайняті клітинки сітки для одного майстра, з урахуванням тривалості."""
    step = step_minutes()
    with db() as conn:
        rows = conn.execute(
            "SELECT slot, minutes FROM bookings WHERE status!='cancelled' "
            "AND IFNULL(master_id,'')=?", (master_id or "",)).fetchall()
    busy = set()
    for r in rows:
        start = parse_dt(r["slot"])
        if start is None:
            continue
        n = max(1, math.ceil((r["minutes"] or step) / step))
        for i in range(n):
            busy.add((start + timedelta(minutes=step * i)).isoformat(timespec="minutes"))
    return busy


def save_booking(d, chat_id, username):
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO bookings(chat_id, username, service, slot, name, phone,"
            " comment, created, master_id, master_name, price, minutes,"
            " discount) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (chat_id, username, d.get("service_name"), d.get("slot"),
             d.get("name"), d.get("phone"), d.get("comment"),
             now().isoformat(timespec="seconds"),
             d.get("master_id", ""), d.get("master_name", ""),
             d.get("price", ""), d.get("minutes") or step_minutes(),
             d.get("discount") or 0))
        return cur.lastrowid


# --------------------------------------------------------------------------
# Вільний час
# --------------------------------------------------------------------------

def pretty_date(iso_date):
    try:
        d = datetime.fromisoformat(iso_date)
    except Exception:
        return iso_date
    return "%d %s" % (d.day, MONTHS[d.month - 1])


def pretty_slot(iso):
    try:
        d = datetime.fromisoformat(iso)
    except Exception:
        return iso
    return "%d %s, %s" % (d.day, MONTHS[d.month - 1], d.strftime("%H:%M"))


def free_times(iso_date, master_id, sid, busy=None):
    """Вільні початки для послуги sid у майстра master_id на дату."""
    if busy is None:
        busy = occupied(master_id)
    return slots.free_times(CFG, iso_date, master_by_id(master_id) if master_id
                            else None, sid, busy, now(), tz())


def open_days(master_id, sid):
    out = []
    today = now().date()
    for i in range(int(CFG.get("days_ahead", 7)) + 1):
        day = today + timedelta(days=i)
        iso = day.isoformat()
        if not free_times(iso, master_id, sid):
            continue
        label = "%s, %d %s" % (WEEKDAYS[day.weekday()], day.day, MONTHS[day.month - 1])
        if i == 0:
            label = "Сьогодні, " + label
        elif i == 1:
            label = "Завтра, " + label
        out.append((iso, label))
        if len(out) >= 6:
            break
    return out


# --------------------------------------------------------------------------
# Фото майстрів
# --------------------------------------------------------------------------

def photo_of(m):
    """('url', адреса) | ('file', шлях) | None.

    Береться поле "photo" майстра. Якщо його нема, бот шукає файл
    photos/<id>.jpg (або .png/.jpeg/.webp) у теці клієнта.
    """
    val = (m.get("photo") or "").strip()
    if val:
        if val.startswith("http://") or val.startswith("https://"):
            return ("url", val)
        path = val if os.path.isabs(val) else os.path.join(HERE, val)
        return ("file", path) if os.path.exists(path) else None
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        path = os.path.join(HERE, "photos", m["id"] + ext)
        if os.path.exists(path):
            return ("file", path)
    return None


def photo_list(m):
    """Усі картинки майстра: портрет, далі аркуш робіт.

    Береться список "photos", інакше одиночне "photo", інакше файли
    photos/<id>.jpg і photos/<id>_roboty.jpg у теці клієнта.
    """
    out = []
    vals = m.get("photos")
    if isinstance(vals, str):
        vals = [vals]
    if not vals:
        vals = [m.get("photo")] if m.get("photo") else []
    if vals:
        for v in vals:
            v = (v or "").strip()
            if not v:
                continue
            if v.startswith("http://") or v.startswith("https://"):
                out.append(("url", v))
            else:
                path = v if os.path.isabs(v) else os.path.join(HERE, v)
                if os.path.exists(path):
                    out.append(("file", path))
        return out
    for suffix in ("", "_roboty"):
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            path = os.path.join(HERE, "photos", m["id"] + suffix + ext)
            if os.path.exists(path):
                out.append(("file", path))
                break
    return out


def photo_key(kind, value):
    """Ключ кешу = сам файл, а не місце, де його показують.

    Раніше ключ містив назву екрана й номер у ряду, тож одне фото
    вивантажувалось заново для кожного екрана. Telegram віддає file_id
    на вміст — отже і ключ має залежати тільки від вмісту. Час зміни
    у ключі робить так, що замінене фото підхопиться саме.
    """
    if kind == "url":
        return "u|" + value
    try:
        return "f|%s|%d" % (value, int(os.path.getmtime(value)))
    except OSError:
        return "f|" + value


def cached_file_id(key):
    with db() as conn:
        row = conn.execute("SELECT file_id FROM photos WHERE key=?", (key,)).fetchone()
    return row["file_id"] if row else None


def remember_file_id(key, file_id):
    if not file_id:
        return
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO photos(key, file_id) VALUES(?,?)",
                     (key, file_id))


def multipart(method, fields, files):
    """POST multipart/form-data вручну, без бібліотек.
    files: {імʼя_поля: (назва_файлу, байти)}"""
    boundary = "----b" + os.urandom(12).hex()
    parts = []
    for k, v in fields.items():
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                      % (boundary, k, v)).encode("utf-8"))
    for field, (fname, blob) in files.items():
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"; '
                      'filename="%s"\r\nContent-Type: application/octet-stream\r\n\r\n'
                      % (boundary, field, fname)).encode("utf-8"))
        parts.append(blob)
        parts.append(b"\r\n")
    parts.append(("--%s--\r\n" % boundary).encode("utf-8"))
    body = b"".join(parts)
    path = "/bot%s/%s" % (CFG["token"], method)
    headers = {"Content-Type": "multipart/form-data; boundary=%s" % boundary,
               "Content-Length": str(len(body)), "Connection": "keep-alive"}
    return net_request(method, path, body, headers, 120)


def read_file(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except Exception as e:
        print("Файл %s: %s" % (path, e), file=sys.stderr)
        return None


def upload_photo(chat_id, path, caption, keyboard):
    blob = read_file(path)
    if blob is None:
        return None
    fields = {"chat_id": str(chat_id), "caption": caption, "parse_mode": "HTML"}
    if keyboard is not None:
        fields["reply_markup"] = json.dumps(keyboard, ensure_ascii=False)
    return multipart("sendPhoto", fields,
                     {"photo": (os.path.basename(path), blob)})


def send_album(chat_id, key_id, refs, caption=None):
    """Кілька фото одним повідомленням. Кнопки до альбому Telegram не чіпляє,
    тому вони йдуть окремо — це обмеження API, не вибір бота."""
    media, files = [], {}
    for i, (kind, value) in enumerate(refs):
        key = photo_key(kind, value)
        fid = cached_file_id(key)
        if fid:
            src = fid
        elif kind == "url":
            src = value
        else:
            blob = read_file(value)
            if blob is None:
                continue
            field = "f%d" % i
            files[field] = (os.path.basename(value), blob)
            src = "attach://" + field
        item = {"type": "photo", "media": src}
        if i == 0 and caption:
            item["caption"] = caption
            item["parse_mode"] = "HTML"
        media.append(item)

    if not media:
        return None
    fields = {"chat_id": str(chat_id),
              "media": json.dumps(media, ensure_ascii=False)}
    res = multipart("sendMediaGroup", fields, files) if files else \
        call("sendMediaGroup", chat_id=chat_id, media=media)
    if not res:
        return None
    # запамʼятовуємо номери, щоб удруге не вивантажувати
    if not isinstance(res, list):
        return res
    for i, msg in enumerate(res):
        if not isinstance(msg, dict):
            continue
        sizes = msg.get("photo") or []
        if sizes and i < len(refs):
            remember_file_id(photo_key(refs[i][0], refs[i][1]),
                             sizes[-1].get("file_id"))
    return res


def all_photo_files():
    """Усі картинки цього клієнта: обкладинка і тека photos."""
    found, seen = [], set()
    ref = cover_ref()
    if ref and ref[0] == "file":
        found.append(ref[1]); seen.add(ref[1])
    pdir = os.path.join(HERE, "photos")
    if os.path.isdir(pdir):
        for root, _dirs, names in os.walk(pdir):
            for n in sorted(names):
                if os.path.splitext(n)[1].lower() in (".jpg", ".jpeg", ".png", ".webp"):
                    p = os.path.join(root, n)
                    if p not in seen:
                        found.append(p); seen.add(p)
    return found


def warm_photos():
    """Вивантажити всі фото один раз при старті й запам'ятати їхні номери.

    Інакше перший клієнт, який відкриє майстрів, чекає на вивантаження
    кількох мегабайтів — а якщо мережа смикнеться, не дочекається взагалі
    й наступний чекатиме знову. Тут ми платимо цю ціну самі, до клієнтів:
    шлемо фото адміну й одразу прибираємо повідомлення.
    """
    net_prewarm()
    admin = (CFG.get("admin_ids") or [None])[0]
    files = all_photo_files()
    todo = [p for p in files if not cached_file_id(photo_key("file", p))]
    if not files:
        return
    if not todo:
        print("Фото готові: %d, вивантажувати нічого." % len(files))
        return
    if not admin:
        print("Фото не прогріті (%d): нема admin_ids у config.json — "
              "перший клієнт чекатиме на вивантаження." % len(todo),
              file=sys.stderr)
        return

    done = 0
    for path in todo:
        blob = read_file(path)
        if blob is None:
            continue
        res = multipart("sendPhoto", {"chat_id": str(admin),
                                      "disable_notification": "true"},
                        {"photo": (os.path.basename(path), blob)})
        if not isinstance(res, dict):
            continue
        sizes = res.get("photo") or []
        if sizes:
            remember_file_id(photo_key("file", path), sizes[-1].get("file_id"))
            done += 1
        if res.get("message_id"):
            call("deleteMessage", chat_id=admin, message_id=res["message_id"])
    print("Прогріто фото: %d з %d. Клієнти не чекатимуть на вивантаження."
          % (done, len(todo)))


def cover_ref():
    """Обкладинка бота: поле "cover" або файл cover.jpg у теці клієнта."""
    val = (CFG.get("cover") or "").strip()
    if val:
        if val.startswith("http://") or val.startswith("https://"):
            return ("url", val)
        path = val if os.path.isabs(val) else os.path.join(HERE, val)
        return ("file", path) if os.path.exists(path) else None
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        path = os.path.join(HERE, "cover" + ext)
        if os.path.exists(path):
            return ("file", path)
    return None


def send_photo(chat_id, m, caption, keyboard=None):
    refs = photo_list(m)
    return send_image(chat_id, m["id"], refs[0] if refs else None, caption, keyboard)


def send_image(chat_id, key_id, ref, caption, keyboard=None):
    """Картинка з підписом. Завантажений файл кешується по file_id, тому
    вдруге він не вивантажується, а відправляється миттєво. Якщо картинки
    нема або Telegram її не прийняв — іде звичайний текст."""
    if not ref:
        return send(chat_id, caption, keyboard)
    kind, value = ref
    key = photo_key(kind, value)

    fid = cached_file_id(key)
    if fid:
        res = call("sendPhoto", chat_id=chat_id, photo=fid, caption=caption,
                   parse_mode="HTML", reply_markup=keyboard)
        if res:
            return res

    if kind == "url":
        res = call("sendPhoto", chat_id=chat_id, photo=value, caption=caption,
                   parse_mode="HTML", reply_markup=keyboard)
    else:
        res = upload_photo(chat_id, value, caption, keyboard)

    if not res:
        return send(chat_id, caption, keyboard)
    sizes = res.get("photo") or []
    if sizes:
        remember_file_id(key, sizes[-1].get("file_id"))
    return res


def show(chat_id, message_id, text, keyboard=None):
    """Перемалювати поточне повідомлення, а якщо його нема — надіслати нове.
    Повідомлення з фото перемалювати як текст не можна, тому в таких
    випадках message_id не передається і бот шле нове."""
    if message_id:
        return edit(chat_id, message_id, text, keyboard)
    return send(chat_id, text, keyboard)


# --------------------------------------------------------------------------
# Вигрузка в Google Таблицю
#
# Писати в таблицю напряму бот не може: для цього потрібні бібліотеки Google
# і ключі сервісного акаунта, а бот навмисно тримається на чистому Python.
# Тому в самій таблиці клієнта живе маленький скрипт, а бот шле йому рядок.
# Якщо таблиця недоступна — рядок лягає в чергу і піде наступного разу,
# тож жоден запис не губиться.
# --------------------------------------------------------------------------

def sheet_url():
    return (CFG.get("sheet_webhook") or "").strip()


def stamp_parts(value):
    """2026-11-22T16:01:12+02:00 -> ("22.11.2026", "16:01")

    У таблиці дата й час стоять окремими стовпцями: так їх можна
    сортувати й фільтрувати за днями, а не читати машинний рядок.
    """
    d = parse_dt(value)
    if d is None:
        return ("", "")
    return ("%02d.%02d.%d" % (d.day, d.month, d.year),
            "%02d:%02d" % (d.hour, d.minute))


def due_amount(row):
    """Скільки клієнт довносить на місці: вартість мінус передоплата.

    Ціна може бути записана словами («від 9 000 ₴»), тому беремо з неї
    число. Якщо ціни нема — лишаємо порожнім, щоб у таблиці не з'явився
    вигаданий нуль.
    """
    base = parse_money(row["price"])
    if not base:
        return ""
    return max(0, base - int(row["deposit"] or 0))


def sheet_payload(row, event):
    made_d, made_t = stamp_parts(row["created"])
    slot_d, slot_t = stamp_parts(row["slot"])
    paid_d, paid_t = stamp_parts(row["paid_at"])
    return {"secret": CFG.get("sheet_secret", ""),
            "event": event,
            "id": row["id"],
            "created_date": made_d,
            "created_time": made_t,
            "status": row["status"],
            "service": row["service"],
            "master": row["master_name"] or "",
            "slot_date": slot_d,
            "slot_time": slot_t,
            "name": row["name"],
            "phone": row["phone"],
            "username": row["username"] or "",
            "price": row["price"] or "",
            "discount": ("−%d%%" % row["discount"]) if (
                "discount" in row.keys() and row["discount"]) else "",
            "deposit": row["deposit"] or 0,
            "due": due_amount(row),
            "paid": int(row["paid"] or 0),
            "paid_date": paid_d,
            "paid_time": paid_t,
            "comment": row["comment"] or "",
            "rating": row["rating"] or "",
            # Старі назви полів: якщо в таблиці ще не оновлений скрипт,
            # він заповнить свої стовпці, а не лишить їх порожніми.
            "created": ("%s о %s" % (made_d, made_t)).strip(" о"),
            "when": ("%s о %s" % (slot_d, slot_t)).strip(" о"),
            "paid_at": ("%s о %s" % (paid_d, paid_t)).strip(" о")}


def sheet_send(booking_id, event):
    """Поставити запис у чергу на вигрузку і спробувати відправити зараз."""
    if not sheet_url():
        return
    row = booking_of(booking_id)
    if not row:
        return
    body = json.dumps(sheet_payload(row, event), ensure_ascii=False)
    with db() as conn:
        conn.execute("INSERT INTO outbox(payload, created) VALUES(?,?)",
                     (body, now().isoformat(timespec="seconds")))
    threading.Thread(target=sheet_flush, daemon=True).start()


def sheet_post(body):
    """Надіслати рядок у таблицю і переконатись, що його справді взяли.

    Раніше тут перевірявся лише код відповіді — і це було небезпечно.
    Коли в розгортанні стоїть доступ «Тільки я», Google мовчки веде на
    сторінку входу й віддає 200. Бот рахував це за успіх і викидав рядок
    із черги. Запис зникав, і ніде жодної помилки. Тепер успіхом
    вважається лише відповідь самого скрипта.
    """
    req = urllib.request.Request(
        sheet_url(), data=body.encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = r.read().decode("utf-8", "replace")
        landed = r.geturl()

    if "accounts.google.com" in landed or raw.lstrip()[:1] == "<":
        raise OSError("скрипт вимагає входу в Google — у розгортанні "
                      "стоїть доступ «Тільки я» замість «Будь-хто»")
    try:
        data = json.loads(raw)
    except ValueError:
        raise OSError("незрозуміла відповідь таблиці: %s" % raw[:120])
    if not data.get("ok"):
        reason = data.get("error") or "без пояснення"
        if "secret" in str(reason):
            reason = "ключ у скрипті не збігається з sheet_secret у config.json"
        raise OSError("таблиця відмовила: %s" % reason)
    return True


_sheet_lock = threading.Lock()


def sheet_flush(limit=25):
    """Відправити все, що чекає в черзі. Порядок зберігаємо."""
    if not sheet_url() or not _sheet_lock.acquire(blocking=False):
        return 0
    try:
        with db() as conn:
            rows = conn.execute("SELECT id, payload, tries FROM outbox"
                                " ORDER BY id LIMIT ?", (limit,)).fetchall()
        done = 0
        for r in rows:
            try:
                sheet_post(r["payload"])
            except Exception as e:
                with db() as conn:
                    conn.execute("UPDATE outbox SET tries=tries+1 WHERE id=?",
                                 (r["id"],))
                if r["tries"] == 0:
                    print("Таблиця недоступна, рядок чекає в черзі: %s" % e,
                          file=sys.stderr)
                break
            with db() as conn:
                conn.execute("DELETE FROM outbox WHERE id=?", (r["id"],))
            done += 1
        return done
    finally:
        _sheet_lock.release()


# --------------------------------------------------------------------------
# Екрани
# --------------------------------------------------------------------------

def hello_again(chat_id):
    """Особисте привітання тому, хто вже у нас був.

    Людина не має щоразу відчувати себе новою: бот памʼятає імʼя,
    останній візит і те, що на наступний у неї є знижка.
    """
    row = last_visit(chat_id=chat_id)
    if not row or not row["name"]:
        return ""
    first = (row["name"] or "").split()[0]
    n = visit_count(chat_id)
    out = ["<b>З поверненням, %s.</b>" % first]
    was = "Минулого разу: %s" % (row["service"] or "візит")
    if row["master_name"]:
        was += " у %s" % row["master_name"]
    was += ", %s." % pretty_date((row["slot"] or "")[:10])
    out.append(was if n == 1 else was + " Це буде візит №%d." % (n + 1))
    if repeat_percent():
        out.append("Для своїх знижка %d%% — порахую її на цей запис."
                   % repeat_percent())
    return "\n".join(out)


def screen_menu(chat_id, message_id=None, cover=False):
    clear_state(chat_id)
    text = CFG.get("greeting") or ("<b>%s</b>\nОберіть, що вас цікавить."
                                   % CFG.get("business", "Вітаємо"))
    if cover:
        back = hello_again(chat_id)
        if back:
            text = back + "\n\n" + text
    rows = []
    mine = active_booking(chat_id)
    if mine:
        rows.append([btn("Мій запис і оплата" if mine["deposit"] else "Мій запис",
                         "act:mybooking")])
    rows += [[btn("Записатися", "act:book")]]
    fb = free_button()
    if fb:
        rows.append([fb])
    rows.append([btn("Послуги та ціни", "act:prices")])
    if has_masters():
        rows.append([btn("Майстри", "act:masters")])
    rows.append([btn("Поставити запитання", "act:ask")])
    rows.append([btn("Контакти", "act:contacts")])
    # Обкладинка показується на старті. При поверненні в меню кнопкою її не
    # шлемо: картинка під кожним «назад» перетворюється на спам.
    if cover and not message_id:
        ref = cover_ref()
        if ref and len(text) <= 1000:
            send_image(chat_id, "cover", ref, text, inline(rows))
            return
    if message_id:
        show(chat_id, message_id, text, inline(rows))
    else:
        send(chat_id, text, inline(rows))


def screen_services(chat_id, message_id):
    rows = []
    for s in offered_services():
        label = s["name"]
        d = human_duration(duration_of(s["id"]))
        if d:
            label += " · ~" + d
        if not has_masters():
            p = price_of(s["id"])
            if p:
                label += " — " + p
        rows.append([btn(label, "svc:" + s["id"])])
    rows.append([btn("← Назад", "act:menu")])
    note = CFG.get("time_note") or "Час орієнтовний — точніше скаже майстер."
    show(chat_id, message_id, "Оберіть послугу:\n<i>%s</i>" % note, inline(rows))


def screen_masters_for_service(chat_id, message_id, d):
    sid = d["service_id"]
    ms = masters_for_service(sid)
    if not ms:
        screen_services(chat_id, message_id)
        return
    rows = []
    for m in ms:
        p = price_of(sid, m)
        label = m["name"]
        if m.get("role"):
            label += " · " + m["role"]
        if p:
            label += " — " + p
        rows.append([btn(label, "mst:" + m["id"])])
    rows.append([btn("← Інша послуга", "act:book")])
    show(chat_id, message_id,
         "<b>%s</b>\nДо кого записати?" % d.get("service_name", ""),
         inline(rows))


def month_bounds():
    """Перший і останній день, у межах яких взагалі можна записатись."""
    today = now().date()
    return today, today + timedelta(days=int(CFG.get("days_ahead", 30)))


def shift_month(year, month, delta):
    m = month - 1 + delta
    return year + m // 12, m % 12 + 1


def screen_days(chat_id, message_id, d, ym=None):
    """Календар на місяць. Клікаються тільки дні, коли майстер справді вільний."""
    master_id = d.get("master_id", "")
    sid = d["service_id"]
    first, last = month_bounds()

    # Якщо місяць не переданий явно, беремо той, який клієнт гортав останнім.
    if not ym:
        ym = d.get("ym")
    if ym:
        year, month = int(ym[:4]), int(ym[5:7])
    else:
        year, month = first.year, first.month
    # не даємо піти в минуле чи за горизонт запису
    if (year, month) < (first.year, first.month):
        year, month = first.year, first.month
    if (year, month) > (last.year, last.month):
        year, month = last.year, last.month

    busy = occupied(master_id)
    rows = []

    prev_y, prev_m = shift_month(year, month, -1)
    next_y, next_m = shift_month(year, month, 1)
    back_ok = (prev_y, prev_m) >= (first.year, first.month)
    fwd_ok = (next_y, next_m) <= (last.year, last.month)
    # Три рівні кнопки: Telegram ділить рядок порівну, і це не обходиться.
    # Замість порожніх стрілок на бокових кнопках стоять назви сусідніх
    # місяців — так ширина стає змістом, а не витраченим місцем.
    left = ("‹ " + MONTHS_SHORT[prev_m - 1]) if back_ok else " "
    right = (MONTHS_SHORT[next_m - 1] + " ›") if fwd_ok else " "
    # Рік на кнопці лише тоді, коли він не поточний: "Грудень 2026" не влазить
    # у третину рядка й обрізається, а "Грудень" поміщається повністю.
    center = MONTHS_NOM[month - 1]
    if year != first.year:
        center += " " + str(year)
    rows.append([
        btn(left, "cal:%04d-%02d" % (prev_y, prev_m) if back_ok else "nop"),
        btn(center, "nop"),
        btn(right, "cal:%04d-%02d" % (next_y, next_m) if fwd_ok else "nop"),
    ])
    rows.append([btn(x, "nop") for x in WEEKDAYS])

    free_count = 0
    for week in calendar.Calendar(firstweekday=0).monthdayscalendar(year, month):
        row = []
        for dayn in week:
            if dayn == 0:
                row.append(btn(" ", "nop"))
                continue
            day = datetime(year, month, dayn).date()
            iso = day.isoformat()
            if day < first or day > last:
                row.append(btn("·", "nop"))
            elif free_times(iso, master_id, sid, busy):
                free_count += 1
                row.append(btn(str(dayn), "day:" + iso))
            else:
                row.append(btn("·", "nop"))
        rows.append(row)

    back = "svc:" + sid if has_masters() else "act:book"
    rows.append([btn("← Назад", back)])

    head = d.get("service_name", "")
    if d.get("master_name"):
        head += " · " + d["master_name"]
    tail = ("Оберіть день." if free_count
            else "Вільних днів цього місяця нема — гортайте далі.")
    show(chat_id, message_id, "<b>%s</b>\n%s %d — %s"
         % (head, MONTHS_NOM[month - 1], year, tail), inline(rows))


def screen_times(chat_id, message_id, d):
    times = free_times(d["day"], d.get("master_id", ""), d["service_id"])
    if not times:
        screen_days(chat_id, message_id, d)
        return
    rows, row = [], []
    for iso, label in times:
        row.append(btn(label, "time:" + iso))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([btn("← Інший день", "mst:" + d.get("master_id", "")
                     if has_masters() else "svc:" + d["service_id"])])
    show(chat_id, message_id, "Оберіть час:", inline(rows))


def named_role(m):
    """«Олег (старший барбер)» — роль у дужках, з малої літери."""
    role = (m.get("role") or "").strip()
    if not role:
        return m["name"]
    return "%s (%s)" % (m["name"], role[0].lower() + role[1:])


def screen_prices(chat_id, message_id):
    lines = ["<b>Послуги та ціни</b>", ""]
    for s in offered_services():
        sid = s["id"]
        dur = human_duration(duration_of(sid))
        head = s["name"] + (" · ~%s" % dur if dur else "")
        if has_masters():
            ms = masters_for_service(sid)
            prices = {price_of(sid, m) for m in ms}
            if len(prices) == 1:
                lines.append("<b>%s</b> — %s" % (head, prices.pop()))
            else:
                lines.append("<b>%s</b>" % head)
                for m in ms:
                    lines.append("   %s — %s" % (named_role(m), price_of(sid, m)))
        else:
            lines.append("<b>%s</b> — %s" % (head, price_of(sid)))
        if s.get("note"):
            lines.append("<i>%s</i>" % s["note"])
        lines.append("")
    note = CFG.get("time_note") or "Час орієнтовний — точніше скаже майстер."
    lines.append("<i>%s</i>" % note)
    show(chat_id, message_id, "\n".join(lines).strip(),
         inline([[btn("Записатися", "act:book")], [btn("← Назад", "act:menu")]]))


def short_schedule(m):
    days, start, end, _ = schedule_of(m)
    d = "Щодня" if len(days) == 7 else ", ".join(WEEKDAYS[i] for i in sorted(days))
    return "%s · %s—%s" % (d, start, end)


def screen_masters_list(chat_id, message_id):
    """Один екран зі списком кнопок. Фото показується в картці майстра:
    вставити зображення в саму кнопку Telegram не дозволяє."""
    rows = []
    for m in masters():
        label = m["name"]
        if m.get("role"):
            label += " · " + m["role"]
        rows.append([btn(label, "mcard:" + m["id"])])
    rows.append([btn("← Назад", "act:menu")])
    show(chat_id, message_id,
         "<b>Наші майстри</b>\nОберіть, щоб подивитись фото, графік і послуги.",
         inline(rows))


def screen_master_card(chat_id, message_id, mid):
    m = master_by_id(mid)
    if not m:
        screen_masters_list(chat_id, message_id)
        return
    lines = ["<b>%s</b>" % m["name"]]
    if m.get("role"):
        lines.append(m["role"])
    if m.get("about"):
        lines += ["", m["about"]]
    lines += ["", "<b>Графік</b>", schedule_text(m), "", "<b>Послуги</b>"]
    ms = master_services(m)
    sids = list(ms.keys()) if ms else [s["id"] for s in CFG.get("services", [])]
    for sid in sids:
        s = service_by_id(sid)
        if s:
            dur = human_duration(duration_of(sid))
            lines.append("%s%s — %s" % (s["name"], " · ~" + dur if dur else "",
                                        price_of(sid, m)))
    text = "\n".join(lines)
    kb = inline([[btn("Записатися до майстра", "mbook:" + mid)],
                 [btn("← Усі майстри", "act:masters")]])

    refs = photo_list(m)
    if not refs:
        show(chat_id, message_id, text, kb)
        return

    if len(refs) == 1:
        if len(text) <= 1000:
            send_image(chat_id, m["id"], refs[0], text, kb)
        else:
            send_image(chat_id, m["id"], refs[0], "<b>%s</b>" % m["name"])
            send(chat_id, text, kb)
        return

    # Два й більше фото — альбомом, одним повідомленням. Кнопки окремо:
    # до альбому Telegram інлайн-клавіатуру не чіпляє.
    if send_album(chat_id, m["id"], refs[:10],
                  text if len(text) <= 1000 else None) is None:
        send_image(chat_id, m["id"], refs[0], text, kb)
        return
    if len(text) > 1000:
        send(chat_id, text)
    send(chat_id, "Записатися до майстра?", kb)


def screen_master_services(chat_id, message_id, mid):
    m = master_by_id(mid)
    if not m:
        screen_masters_list(chat_id, message_id)
        return
    ms = master_services(m)
    sids = list(ms.keys()) if ms else [s["id"] for s in CFG.get("services", [])]
    rows = []
    for sid in sids:
        s = service_by_id(sid)
        if s:
            rows.append([btn("%s — %s" % (s["name"], price_of(sid, m)),
                             "msvc:%s:%s" % (mid, sid))])
    rows.append([btn("← Назад", "mcard:" + mid)])
    show(chat_id, message_id, "<b>%s</b>\nОберіть послугу:" % m["name"],
         inline(rows))


def screen_my_booking(chat_id, message_id):
    row = active_booking(chat_id)
    if not row:
        show(chat_id, message_id, "Активних записів нема.",
             inline([[btn("Записатися", "act:book")], [btn("← Меню", "act:menu")]]))
        return

    lines = ["<b>Ваш запис #%d</b>" % row["id"], "",
             "Послуга: %s" % row["service"]]
    if row["master_name"]:
        lines.append("Майстер: %s" % row["master_name"])
    lines += ["Коли: %s" % pretty_slot(row["slot"]),
              "Імʼя: %s" % row["name"],
              "Телефон: %s" % row["phone"]]

    rows = []
    dep = row["deposit"] or 0
    if dep and row["paid"]:
        lines += ["", "<b>Передоплата %s — оплачено.</b>" % money(dep),
                  "Чекаємо вас у призначений час."]
    elif dep:
        lines += ["", "<b>Передоплата %s — ще не оплачена.</b>" % money(dep)]
        url = (pay_cfg().get("link") or "").strip()
        if url:
            param = (pay_cfg().get("link_amount_param") or "").strip()
            if param:
                url += ("&" if "?" in url else "?") + "%s=%d" % (param, dep)
            rows.append([{"text": "Оплатити %s" % money(dep), "url": url}])
        else:
            card = (pay_cfg().get("card") or "").strip()
            if card:
                lines += ["", "Переказ на картку — натисніть на номер, "
                              "щоб скопіювати:", "<code>%s</code>" % card]
                if pay_cfg().get("card_holder"):
                    lines.append("Отримувач: %s" % pay_cfg()["card_holder"])
            elif pay_cfg().get("requisites"):
                lines += ["", pay_cfg()["requisites"]]
        lines += ["", PROOF_NOTE]
        rows.append([btn("Я оплатив", "paid:%d" % row["id"])])

    rows.append([btn("Скасувати запис", "unbook:%d" % row["id"])])
    rows.append([btn("← Меню", "act:menu")])
    show(chat_id, message_id, "\n".join(lines), inline(rows))


def ask_name(chat_id):
    send(chat_id, "Як вас записати? Напишіть ім'я.",
         inline([[btn("← Скасувати запис", "act:menu")]]))


def screen_whofor(chat_id, message_id, d, row):
    """Повторний клієнт: себе чи когось іншого він записує."""
    head = "<b>%s</b>" % d.get("service_name", "")
    if d.get("master_name"):
        head += " · " + d["master_name"]
    head += "\n" + pretty_slot(d.get("slot", ""))
    text = head + "\n\nКого записуємо?"
    rows = [[btn("Себе", "who:self")],
            [btn("Іншу людину", "who:other")],
            [btn("← Скасувати запис", "act:menu")]]
    set_state(chat_id, "whofor", d)
    if message_id:
        show(chat_id, message_id, text, inline(rows))
    else:
        send(chat_id, text, inline(rows))


def screen_mydata(chat_id, message_id, d):
    """Звірка даних: щоб не питати ім'я й телефон удруге."""
    text = ("<b>Ваші дані</b>\n\n%s\n%s\n\nВсе вірно?"
            % (d.get("name", ""), d.get("phone", "")))
    rows = [[btn("Так, записати", "data:ok")],
            [btn("Змінити дані", "data:edit")],
            [btn("← Скасувати запис", "act:menu")]]
    set_state(chat_id, "mydata", d)
    if message_id:
        show(chat_id, message_id, text, inline(rows))
    else:
        send(chat_id, text, inline(rows))


def after_slot(chat_id, message_id, d):
    """Час обрано. Далі або звичайне питання імені, або гілка для своїх."""
    row = last_visit(chat_id=chat_id)
    if row and row["name"] and row["phone"]:
        d["last_name"] = row["name"]
        d["last_phone"] = row["phone"]
        screen_whofor(chat_id, message_id, d, row)
        return
    set_state(chat_id, "name", d)
    ask_name(chat_id)


def ask_phone(chat_id):
    kb = {"keyboard": [[{"text": "Надіслати мій номер", "request_contact": True}]],
          "resize_keyboard": True, "one_time_keyboard": True}
    send(chat_id, "Залиште номер телефону — майстер передзвонить, "
                  "якщо щось зміниться.", kb)


ASK_HINT = "<i>Відповідайте на це повідомлення — відповідь піде клієнту.</i>"

PROOF_NOTE = ("Після переказу надішліть сюди <b>скрін або квитанцію</b> — "
              "адміністратор перевірить і підтвердить оплату.")


def ask_payment(chat_id, bid, d):
    """Після запису просимо передоплату. Повертає True, якщо крок показано."""
    mode = pay_mode()
    amount = deposit_for(d)
    if mode == "off" or amount <= 0:
        return False

    with db() as conn:
        conn.execute("UPDATE bookings SET deposit=?, pay_mode=? WHERE id=?",
                     (amount, mode, bid))

    note = pay_cfg().get("note") or (
        "Час тримається за вами після передоплати. Решту платите на місці.")

    if mode == "invoice":
        ok = call("sendInvoice", chat_id=chat_id,
                  title="Передоплата · %s" % d.get("service_name", "запис"),
                  description="%s, %s. %s" % (
                      d.get("master_name") or CFG.get("business", ""),
                      pretty_slot(d.get("slot", "")), note),
                  payload="booking:%d" % bid,
                  provider_token=pay_cfg()["provider_token"],
                  currency=pay_cfg().get("currency", "UAH"),
                  prices=[{"label": "Передоплата", "amount": amount * 100}])
        if ok:
            return True
        print("Рахунок не надіслався, показуємо реквізити", file=sys.stderr)

    # Кнопка прямого переходу: одне натискання — і клієнт уже на оплаті.
    # Працює тільки з посиланням платіжного сервісу: з самого номера картки
    # такої сторінки не існує, тому для картки показуємо номер, який
    # копіюється дотиком.
    url = (pay_cfg().get("link") or "").strip()
    if url:
        param = (pay_cfg().get("link_amount_param") or "").strip()
        if param:
            url += ("&" if "?" in url else "?") + "%s=%d" % (param, amount)

    lines = ["<b>Передоплата %s</b>" % money(amount), "", note]
    rows = []
    if url:
        rows.append([{"text": "Оплатити %s" % money(amount), "url": url}])
    else:
        card = (pay_cfg().get("card") or "").strip()
        if card:
            lines += ["", "Переказ на картку — натисніть на номер, "
                          "щоб скопіювати:", "<code>%s</code>" % card]
            if pay_cfg().get("card_holder"):
                lines.append("Отримувач: %s" % pay_cfg()["card_holder"])
            lines.append("Сума: <b>%s</b>" % money(amount))
        elif pay_cfg().get("requisites"):
            lines += ["", pay_cfg()["requisites"]]
    lines += ["", PROOF_NOTE]
    rows.append([btn("Я оплатив", "paid:%d" % bid)])
    rows.append([btn("Скасувати запис", "unbook:%d" % bid)])

    send(chat_id, "\n".join(lines), inline(rows))
    return True


def mark_paid(bid, by_admin=False):
    with db() as conn:
        conn.execute("UPDATE bookings SET paid=1, paid_at=? WHERE id=?",
                     (now().isoformat(timespec="seconds"), bid))
        row = conn.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()
    if not row:
        return None
    sheet_send(bid, "Оплата")
    lines = ["<b>Оплату прийнято, запис підтверджено.</b>", "",
             row["service"]]
    if row["master_name"]:
        lines.append("Майстер: %s" % row["master_name"])
    lines += [pretty_slot(row["slot"]), "", "Чекаємо вас."]
    send(row["chat_id"], "\n".join(lines))
    for target in team_chats():
        if by_admin and target == row["chat_id"]:
            continue
        send(target, "Запис #%d оплачено (%s)." % (bid, money(row["deposit"] or 0)))
    return row


def reminder_minutes():
    """За скільки хвилин до візиту нагадувати. Порожньо — нагадування вимкнені."""
    r = CFG.get("reminders") or {}
    if not r.get("enabled"):
        return []
    out = []
    for m in (r.get("before") or [1440, 60]):
        try:
            m = int(m)
        except (TypeError, ValueError):
            continue
        if m > 0:
            out.append(m)
    return sorted(set(out), reverse=True)


def reminder_sent(bid, minutes):
    with db() as conn:
        return conn.execute(
            "SELECT 1 FROM reminders WHERE booking_id=? AND minutes=?",
            (bid, minutes)).fetchone() is not None


def mark_reminder(bid, minutes):
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO reminders(booking_id, minutes, sent_at)"
                     " VALUES(?,?,?)",
                     (bid, minutes, now().isoformat(timespec="seconds")))


def when_phrase(minutes, slot):
    if minutes >= 1440:
        return "Нагадуємо про запис завтра"
    if minutes >= 60:
        h = minutes // 60
        return "Нагадуємо: ваш запис за %d %s" % (
            h, "годину" if h == 1 else ("години" if h < 5 else "годин"))
    return "Нагадуємо: ваш запис за %d хв" % minutes


def send_reminder(row, minutes):
    lines = [ "<b>%s</b>" % when_phrase(minutes, row["slot"]), "",
              "%s" % row["service"]]
    if row["master_name"]:
        lines.append("Майстер: %s" % row["master_name"])
    lines.append("Коли: %s" % pretty_slot(row["slot"]))

    rows = []
    dep = row["deposit"] or 0
    if dep and not row["paid"]:
        lines += ["", "Передоплата %s ще не надійшла." % money(dep)]
        url = (pay_cfg().get("link") or "").strip()
        if url:
            param = (pay_cfg().get("link_amount_param") or "").strip()
            if param:
                url += ("&" if "?" in url else "?") + "%s=%d" % (param, dep)
            rows.append([{"text": "Оплатити %s" % money(dep), "url": url}])
        elif pay_cfg().get("card"):
            lines += ["Картка: <code>%s</code>" % pay_cfg()["card"].strip()]
        rows.append([btn("Я оплатив", "paid:%d" % row["id"])])

    rows.append([btn("Буду", "willcome:%d" % row["id"]),
                 btn("Скасувати", "unbook:%d" % row["id"])])
    send(row["chat_id"], "\n".join(lines), inline(rows))


_last_tick = 0.0


def miniapp_url():
    """Адреса сторінки розкладу. Telegram відкриває лише https."""
    u = (CFG.get("miniapp_url") or "").strip()
    return u if u.startswith("https://") else ""


def staff_ids():
    """Кому показувати розклад в особистому чаті.

    Лише власник. Майстри дивляться його в робочій групі — там доступ
    дає саме членство в групі, і не треба збирати їхні id руками.
    """
    return set(CFG.get("admin_ids") or [])


def freeapp_url():
    """Та сама сторінка, але в режимі «вільні години».

    Клієнту нема чого бачити чужі імена й телефони, тому режим вирішує
    не сторінка, а кнопка: сервер віддає клієнту лише порожні місця.
    """
    u = miniapp_url()
    if not u:
        return ""
    return u + ("&" if "?" in u else "?") + "v=free"


def free_button(label="Вільні години"):
    u = freeapp_url()
    return wapp(label, u) if u else None


def miniapp_link():
    """Пряме посилання t.me на застосунок розкладу.

    У групах Telegram не дозволяє кнопки типу web_app — тільки приватні
    чати. Зате звичайне посилання t.me відкриває той самий застосунок
    усередині Telegram, і в групі воно працює.
    """
    u = (CFG.get("miniapp_link") or "").strip()
    return u if u.startswith("https://t.me/") else ""


def schedule_button(label="Розклад", group=False):
    """У приватному чаті — кнопка-застосунок, у групі — посилання."""
    if group:
        link = miniapp_link()
        return inline([[lnk(label, link)]]) if link else None
    u = miniapp_url()
    return inline([[wapp(label, u)]]) if u else None


def reset_menu_button(chat_id):
    """Кнопка біля поля вводу — список команд.

    Telegram дає там одне місце: або команди, або застосунок. Тримаємо
    команди, бо вони показують усе, що бот уміє; розклад і вільні
    години відкриваються кнопками з головного меню.
    """
    return call("setChatMenuButton", chat_id=chat_id,
                menu_button={"type": "commands"}) is not None


def pin_schedule(chat_id, title=None):
    """Надіслати кнопку розкладу в групу і закріпити її вгорі чату."""
    kb = schedule_button(group=True)
    if not kb:
        return False
    res = send(chat_id, title or "<b>Розклад закладу</b>", kb)
    if isinstance(res, dict) and res.get("message_id"):
        call("pinChatMessage", chat_id=chat_id,
             message_id=res["message_id"], disable_notification=True)
    return True


def send_schedule(chat_id, title=None):
    """Надіслати кнопку, яка відкриває календар."""
    kb = schedule_button()
    if not kb:
        send(chat_id, "Розклад не підключено: у config.json порожній "
                      "<code>miniapp_url</code>.")
        return False
    send(chat_id, title or
         "<b>Розклад закладу</b>\n\nУсі записи в одному місці: місяць, "
         "окремий день і кожен майстер своєю колонкою. Дотик по запису — "
         "клієнт, телефон, послуга та стан оплати.", kb)
    return True


def fb_cfg():
    c = CFG.get("feedback") or {}
    return c if c.get("enabled") else {}


def wb_cfg():
    c = CFG.get("winback") or {}
    return c if c.get("enabled") else {}


def opted_out(chat_id):
    with db() as conn:
        return conn.execute("SELECT 1 FROM optout WHERE chat_id=?",
                            (chat_id,)).fetchone() is not None


def booking_end(row):
    """Коли візит закінчився. Тривалість беремо з послуги, інакше година."""
    start = parse_dt(row["slot"])
    if start is None:
        return None
    return start + timedelta(minutes=int(row["minutes"] or 60))


def reminders_tick(force=False):
    """Викликається з головного циклу. Шле нагадування, яких ще не було."""
    global _last_tick
    if not force and time.time() - _last_tick < 60:
        return 0
    _last_tick = time.time()
    mins = reminder_minutes()
    if not mins:
        return 0
    n = now()
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM bookings WHERE status='new' AND slot > ?",
            (n.isoformat(timespec="minutes"),)).fetchall()
    sent = 0
    for r in rows:
        slot = parse_dt(r["slot"])
        if slot is None:
            continue
        for m in mins:
            if reminder_sent(r["id"], m):
                continue
            trigger = slot - timedelta(minutes=m)
            if n < trigger:
                continue
            # Якщо бот був вимкнений і час нагадування давно минув —
            # не шлемо запізніле, просто закриваємо його.
            grace = timedelta(minutes=min(m, 120))
            if n <= trigger + grace:
                send_reminder(r, m)
                sent += 1
            mark_reminder(r["id"], m)
    return sent


_last_fb_tick = 0


def feedback_tick(force=False):
    """Через кілька годин після візиту питаємо, як усе пройшло.

    Сенс не в самих зірочках: задоволених ведемо туди, де відгук
    побачать інші, а невдоволеного ловимо приватно — до того, як він
    напише це публічно.
    """
    global _last_fb_tick
    cfg = fb_cfg()
    if not cfg:
        return 0
    if not force and time.time() - _last_fb_tick < 300:
        return 0
    _last_fb_tick = time.time()

    after = timedelta(hours=float(cfg.get("after_hours", 3)))
    n = now()
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM bookings WHERE status='new' AND asked_at IS NULL"
            " AND rating IS NULL").fetchall()
    asked = 0
    for r in rows:
        end = booking_end(r)
        if end is None or n < end + after:
            continue
        # Якщо візит був давно, питати вже пізно — просто закриваємо.
        if n <= end + after + timedelta(hours=48) and not opted_out(r["chat_id"]):
            stars = [btn("%d" % i, "fb:%d:%d" % (r["id"], i)) for i in range(1, 6)]
            send(r["chat_id"],
                 "Як вам візит %s?\nОцініть від 1 до 5 — це займе секунду."
                 % (r["service"] or "до нас"),
                 inline([stars]))
            asked += 1
        with db() as conn:
            conn.execute("UPDATE bookings SET asked_at=? WHERE id=?",
                         (n.isoformat(timespec="seconds"), r["id"]))
    return asked


def save_rating(bid, stars):
    with db() as conn:
        conn.execute("UPDATE bookings SET rating=? WHERE id=?", (stars, bid))
    return booking_of(bid)


def thanks_for_rating(chat_id, message_id, row, stars):
    cfg = fb_cfg()
    if stars >= 4:
        link = (cfg.get("link") or "").strip()
        rows = []
        if link.startswith("http"):
            rows.append([lnk("Залишити відгук · %s"
                             % (cfg.get("link_label") or "онлайн"), link)])
        rows.append([btn("← Меню", "act:menu")])
        show(chat_id, message_id,
             "Дякуємо! Дуже приємно.\n\n" +
             ("Якщо не важко, напишіть пару слів там — це справді допомагає нам."
              if link.startswith("http")
              else "Будемо раді бачити вас знову."),
             inline(rows))
    else:
        set_state(chat_id, "fb_text", {"bid": row["id"]})
        show(chat_id, message_id,
             "Шкода, що так вийшло. Напишіть одним повідомленням, "
             "що було не так — прочитає власник, не майстер.",
             inline([[btn("← Пропустити", "act:menu")]]))


def pass_complaint(chat_id, bid, text, username):
    with db() as conn:
        conn.execute("UPDATE bookings SET rating_text=? WHERE id=?", (text[:1000], bid))
    row = booking_of(bid)
    clear_state(chat_id)
    send(chat_id, "Дякуємо, передали власнику. Розберемось.",
         inline([[btn("← Меню", "act:menu")]]))
    if row:
        notify("<b>Низька оцінка: %s з 5</b>\nЗапис #%d · %s%s\n%s · %s%s\n\n%s"
               % (row["rating"], row["id"], row["service"],
                  (" · " + row["master_name"]) if row["master_name"] else "",
                  row["name"], row["phone"],
                  ("\n@" + username) if username else "",
                  text[:1000]),
               client_chat=chat_id, booking_id=bid)


# --------------------------------------------------------------------------
# Повернення сплячих клієнтів
# --------------------------------------------------------------------------

def sleeping_clients():
    """Хто давно не був і не має запису попереду."""
    cfg = wb_cfg()
    if not cfg:
        return []
    days = int(cfg.get("after_days", 90))
    cool = int(cfg.get("cooldown_days", 90))
    n = now()
    edge = (n - timedelta(days=days)).isoformat(timespec="seconds")
    cool_edge = (n - timedelta(days=cool)).isoformat(timespec="seconds")
    future = n.isoformat(timespec="minutes")
    with db() as conn:
        rows = conn.execute("""
            SELECT b.chat_id, MAX(b.slot) AS last_slot, MAX(b.name) AS name
            FROM bookings b
            WHERE b.status='new' AND b.chat_id > 0
            GROUP BY b.chat_id
            HAVING MAX(b.slot) < ?
               AND (SELECT COUNT(*) FROM bookings f
                    WHERE f.chat_id = b.chat_id AND f.status='new'
                      AND f.slot > ?) = 0
               AND (SELECT COUNT(*) FROM optout o
                    WHERE o.chat_id = b.chat_id) = 0
               AND (SELECT COUNT(*) FROM winback w
                    WHERE w.chat_id = b.chat_id AND w.sent_at > ?) = 0
            """, (edge, future, cool_edge)).fetchall()
    return rows


def winback_send(limit=50):
    """Надіслати сплячим запрошення повернутись. Повертає, скільком пішло."""
    cfg = wb_cfg()
    if not cfg:
        return 0
    text = (cfg.get("text") or "").strip() or (
        "Давно вас не бачили. Якщо захочете — запис відкритий, "
        "обрати час можна просто тут.")
    sent = 0
    for r in sleeping_clients()[:limit]:
        greet = ("%s, " % r["name"]) if r["name"] else ""
        ok = send(r["chat_id"], greet + text,
                  inline([[btn("Записатися", "act:book")],
                          [btn("Більше не писати", "nomail")]]))
        with db() as conn:
            conn.execute("INSERT OR REPLACE INTO winback(chat_id, sent_at)"
                         " VALUES(?,?)",
                         (r["chat_id"], now().isoformat(timespec="seconds")))
        if ok:
            sent += 1
        time.sleep(0.2)   # Telegram не любить залпів
    return sent


def active_booking(chat_id):
    """Найближчий незавершений запис клієнта, або None."""
    with db() as conn:
        return conn.execute(
            "SELECT * FROM bookings WHERE chat_id=? AND status='new' "
            "AND slot >= ? ORDER BY slot LIMIT 1",
            (chat_id, now().isoformat(timespec="minutes"))).fetchone()


def booking_of(bid):
    with db() as conn:
        return conn.execute("SELECT * FROM bookings WHERE id=?", (bid,)).fetchone()


def confirm(chat_id, d, username):
    bid = save_booking(d, chat_id, username)
    lines = ["<b>Записали.</b>", "",
             "Послуга: %s" % d.get("service_name")]
    if d.get("master_name"):
        lines.append("Майстер: %s" % d["master_name"])
    if d.get("price"):
        if d.get("discount") and d.get("price_full"):
            lines.append("Ціна: <s>%s</s> %s  <b>−%d%%</b>"
                         % (d["price_full"], d["price"], d["discount"]))
        else:
            lines.append("Ціна: %s" % d["price"])
    lines += ["Коли: %s" % pretty_slot(d.get("slot", "")),
              "Ім'я: %s" % d.get("name"),
              "Телефон: %s" % d.get("phone")]
    if d.get("discount"):
        lines.append("")
        lines.append("Знижка %d%% — ви вже були у нас." % d["discount"]
                     if d.get("for") != "other" else
                     "Знижка %d%% — ця людина вже є в нашій базі." % d["discount"])
    lines += ["",
              "Номер запису #%d. Якщо плани зміняться — надішліть /skasuvaty." % bid]
    send(chat_id, "\n".join(lines), remove_keyboard=True)
    clear_state(chat_id)
    ask_payment(chat_id, bid, d)

    note = ["<b>Новий запис #%d</b>" % bid, d.get("service_name", "")]
    if d.get("master_name"):
        note.append("Майстер: %s" % d["master_name"])
    if d.get("discount"):
        note.append("Ціна: %s замість %s (знижка %d%%)"
                    % (d.get("price"), d.get("price_full"), d["discount"]))
    note += [pretty_slot(d.get("slot", "")),
             "%s · %s" % (d.get("name"), d.get("phone")),
             "@%s" % (username or "без username")]
    if d.get("for") == "other":
        note.append("<i>Записав %s</i>" % ("@" + username if username
                                           else "клієнт із бази"))
    notify("\n".join(note), client_chat=chat_id, booking_id=bid)
    sheet_send(bid, "Новий запис")
    return bid


# --------------------------------------------------------------------------
# Обробка подій
# --------------------------------------------------------------------------

PHONE_RE = re.compile(r"[\d\+][\d\s\-\(\)]{8,}")


def parse_slot_link(payload):
    """Розібрати «t-0-202611271130» з посилання календаря вільних годин.

    Майстра передаємо номером у списку, а не його id: id буває кирилицею
    чи з пробілом, а Telegram пускає в посилання лише латиницю, цифри,
    дефіс і підкреслення.
    """
    parts = (payload or "").split("-")
    if len(parts) != 3 or parts[0] != "t":
        return None, None
    who, when = parts[1], parts[2]
    if len(when) != 12 or not when.isdigit():
        return None, None
    try:
        slot = datetime(int(when[0:4]), int(when[4:6]), int(when[6:8]),
                        int(when[8:10]), int(when[10:12]))
    except ValueError:
        return None, None
    # час у базі зберігається з поясом — інакше він не збігся б із сіткою
    if tz():
        slot = slot.replace(tzinfo=tz())
    m = None
    if has_masters():
        if not who.isdigit() or int(who) >= len(masters()):
            return None, None
        m = masters()[int(who)]
    return m, slot.isoformat(timespec="minutes")


def screen_services_for_slot(chat_id, d, message_id=None):
    """Клієнт уже обрав годину — лишилось спитати, на що саме він іде."""
    head = "<b>%s</b>" % pretty_slot(d["slot_wanted"])
    if d.get("master_name"):
        head += " · " + d["master_name"]
    rows = []
    for sv in offered_services():
        if d.get("master_id"):
            m = master_by_id(d["master_id"])
            if m and not master_does(m, sv["id"]):
                continue
        label = sv["name"]
        dur = human_duration(duration_of(sv["id"]))
        if dur:
            label += " · ~" + dur
        p = price_of(sv["id"], master_by_id(d["master_id"])
                     if d.get("master_id") else None)
        if p:
            label += " — " + p
        rows.append([btn(label, "wsvc:" + sv["id"])])
    if not rows:
        send(chat_id, "На цю годину зараз немає доступних послуг.",
             inline([[btn("Записатися", "act:book")],
                     [btn("← Меню", "act:menu")]]))
        return
    rows.append([btn("← Інший час", "act:book")])
    set_state(chat_id, "wslot", d)
    text = head + "\n\nОберіть послугу — і я одразу займу цю годину."
    if message_id:
        show(chat_id, message_id, text, inline(rows))
    else:
        send(chat_id, text, inline(rows))


def open_slot_from_link(chat_id, payload):
    """Вхід у запис з календаря вільних годин."""
    m, slot = parse_slot_link(payload)
    if slot is None:
        return False
    d = {"slot_wanted": slot, "day": slot[:10], "ym": slot[:7]}
    if m:
        d["master_id"] = m["id"]
        d["master_name"] = m["name"]
    screen_services_for_slot(chat_id, d)
    return True


def start_time_step(chat_id, message_id, d):
    set_state(chat_id, "day", d)
    screen_days(chat_id, message_id, d)


def handle_callback(cq):
    chat_id = cq["message"]["chat"]["id"]
    message_id = cq["message"]["message_id"]
    # Повідомлення з фото не можна перемалювати як текст: шлемо нове.
    if cq["message"].get("photo"):
        message_id = None
    data = cq.get("data", "")
    call("answerCallbackQuery", callback_query_id=cq["id"])
    step, d = get_state(chat_id)

    if data == "act:menu":
        screen_menu(chat_id, message_id)

    elif data == "act:mybooking":
        screen_my_booking(chat_id, message_id)

    elif data == "act:book":
        screen_services(chat_id, message_id)

    elif data == "act:prices":
        screen_prices(chat_id, message_id)

    elif data == "act:masters":
        screen_masters_list(chat_id, message_id)

    elif data == "act:contacts":
        show(chat_id, message_id, CFG.get("contacts", "Контакти не вказані."),
             inline([[btn("← Назад", "act:menu")]]))

    elif data == "act:ask":
        set_state(chat_id, "question", {})
        show(chat_id, message_id,
             "Напишіть запитання одним повідомленням — передамо адміністратору.",
             inline([[btn("← Передумав", "act:menu")]]))

    elif data.startswith("mcard:"):
        screen_master_card(chat_id, message_id, data[6:])

    elif data.startswith("mbook:"):
        screen_master_services(chat_id, message_id, data[6:])

    elif data.startswith("msvc:"):
        _, mid, sid = data.split(":", 2)
        m, s = master_by_id(mid), service_by_id(sid)
        if not m or not s:
            screen_menu(chat_id, message_id)
            return
        d = {"service_id": sid, "service_name": s["name"],
             "master_id": mid, "master_name": m["name"],
             "price": price_of(sid, m), "minutes": duration_of(sid)}
        start_time_step(chat_id, message_id, d)

    elif data.startswith("wsvc:"):
        sid = data[5:]
        sv = service_by_id(sid)
        want = d.get("slot_wanted")
        if not sv or not want:
            screen_menu(chat_id, message_id)
            return
        mid = d.get("master_id", "")
        free = [iso for iso, _ in free_times(want[:10], mid, sid)]
        if want not in free:
            # поки людина обирала послугу, годину могли зайняти; буває й так,
            # що послуга довша за вільний проміжок
            d2 = {"service_id": sid, "service_name": sv["name"],
                  "minutes": duration_of(sid), "day": want[:10],
                  "ym": want[:7]}
            if mid:
                m = master_by_id(mid)
                d2["master_id"] = mid
                d2["master_name"] = m["name"] if m else ""
                d2["price"] = price_of(sid, m)
            else:
                d2["price"] = price_of(sid)
            set_state(chat_id, "time", d2)
            send(chat_id, "Цю годину вже зайняли або вона закоротка для "
                          "«%s». Ось що лишилось вільного того дня."
                 % sv["name"])
            screen_times(chat_id, None, d2)
            return
        m = master_by_id(mid) if mid else None
        d.update({"service_id": sid, "service_name": sv["name"],
                  "minutes": duration_of(sid), "slot": want,
                  "price": price_of(sid, m)})
        show(chat_id, message_id, "<b>%s</b>%s\n%s"
             % (sv["name"], (" · " + d["master_name"])
                if d.get("master_name") else "", pretty_slot(want)))
        after_slot(chat_id, None, d)

    elif data == "who:self":
        if not d.get("slot"):
            screen_menu(chat_id, message_id)
            return
        d["for"] = "self"
        d["name"] = d.get("last_name", "")
        d["phone"] = d.get("last_phone", "")
        if not d["name"] or not d["phone"]:
            set_state(chat_id, "name", d)
            ask_name(chat_id)
            return
        screen_mydata(chat_id, message_id, d)

    elif data == "who:other":
        if not d.get("slot"):
            screen_menu(chat_id, message_id)
            return
        d["for"] = "other"
        set_state(chat_id, "name", d)
        show(chat_id, message_id, "Записуємо іншу людину.")
        send(chat_id, "Як її звати? Напишіть ім'я.",
             inline([[btn("← Скасувати запис", "act:menu")]]))

    elif data == "data:ok":
        if not d.get("slot") or not d.get("phone"):
            screen_menu(chat_id, message_id)
            return
        apply_repeat_discount(d, True)
        show(chat_id, message_id, "Дані підтверджено.")
        confirm(chat_id, d, (cq.get("from") or {}).get("username", ""))

    elif data == "data:edit":
        if not d.get("slot"):
            screen_menu(chat_id, message_id)
            return
        d["for"] = "self"
        set_state(chat_id, "name", d)
        show(chat_id, message_id, "Добре, запишемо наново.")
        ask_name(chat_id)

    elif data.startswith("svc:"):
        s = service_by_id(data[4:])
        if not s:
            screen_menu(chat_id, message_id)
            return
        d = {"service_id": s["id"], "service_name": s["name"],
             "minutes": duration_of(s["id"])}
        if has_masters():
            set_state(chat_id, "master", d)
            screen_masters_for_service(chat_id, message_id, d)
        else:
            d["price"] = price_of(s["id"])
            start_time_step(chat_id, message_id, d)

    elif data.startswith("mst:"):
        m = master_by_id(data[4:])
        if not m or not d.get("service_id"):
            screen_menu(chat_id, message_id)
            return
        d["master_id"] = m["id"]
        d["master_name"] = m["name"]
        d["price"] = price_of(d["service_id"], m)
        start_time_step(chat_id, message_id, d)

    elif data == "nop":
        pass

    elif data.startswith("paid:"):
        bid = int(data[5:])
        row = booking_of(bid)
        if not row or row["chat_id"] != chat_id:
            return
        if row["paid"]:
            send(chat_id, "Цей запис уже позначено оплаченим.")
            return
        show(chat_id, message_id,
             "Дякуємо. Передали адміністратору — щойно побачить надходження, "
             "підтвердить оплату.")
        for tgt, mid in notify(
                "<b>Клієнт каже, що оплатив</b>\nЗапис #%d · %s\n%s · %s\n"
                "Передоплата: %s"
                % (bid, row["service"], row["name"], row["phone"],
                   money(row["deposit"] or 0)),
                inline([[btn("Підтвердити оплату", "payok:%d" % bid),
                         btn("Ні, не бачу", "payno:%d" % bid)]]),
                client_chat=chat_id, booking_id=bid):
            remember_pay_msg(bid, tgt, mid)

    elif data == "wbsend":
        if chat_id not in team_chats():
            return
        show(chat_id, message_id, "Розсилаю…")
        n = winback_send()
        send(chat_id, "Надіслано: <b>%d</b>." % n)

    elif data.startswith("fb:"):
        _, bid, stars = data.split(":")
        row = save_rating(int(bid), int(stars))
        if row:
            thanks_for_rating(chat_id, message_id, row, int(stars))

    elif data == "nomail":
        with db() as conn:
            conn.execute("INSERT OR REPLACE INTO optout(chat_id, since)"
                         " VALUES(?,?)",
                         (chat_id, now().isoformat(timespec="seconds")))
        show(chat_id, message_id,
             "Готово, більше не писатимемо. Записатись можна будь-коли — /start")

    elif data.startswith("payok:"):
        if chat_id not in team_chats():
            return
        bid = int(data[6:])
        before = booking_of(bid)
        if not before:
            show(chat_id, message_id, "Запис не знайдено.")
            return
        if before["paid"]:
            # Хтось уже встиг: кнопки прибираємо, другого разу не зараховуємо.
            close_pay_buttons(bid, "Оплату запису #%d вже підтверджено." % bid)
            return
        mark_paid(bid, by_admin=True)
        close_pay_buttons(bid, "Оплату запису #%d підтвердив %s."
                          % (bid, who(cq.get("from") or {})))

    elif data.startswith("payno:"):
        if chat_id not in team_chats():
            return
        bid = int(data[6:])
        row = booking_of(bid)
        if not row:
            show(chat_id, message_id, "Запис не знайдено.")
            return
        if row["paid"]:
            close_pay_buttons(bid, "Оплату запису #%d вже підтверджено." % bid)
            return
        send(row["chat_id"],
             "Оплату поки не видно. Якщо ви вже платили — надішліть, будь ласка, "
             "квитанцію сюди, адміністратор перевірить вручну.")
        close_pay_buttons(bid, "Запис #%d: %s не бачить надходження, "
                               "клієнта попросили надіслати квитанцію."
                          % (bid, who(cq.get("from") or {})))

    elif data.startswith("willcome:"):
        bid = int(data[9:])
        row = booking_of(bid)
        if not row or row["chat_id"] != chat_id:
            return
        show(chat_id, message_id, "Дякуємо, чекаємо вас %s."
             % pretty_slot(row["slot"]))

    elif data.startswith("unbook:"):
        bid = int(data[7:])
        row = booking_of(bid)
        if not row or row["chat_id"] != chat_id:
            return
        with db() as conn:
            conn.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (bid,))
        show(chat_id, message_id, "Запис #%d скасовано, час звільнено." % bid)
        notify("<b>Скасування #%d</b>\n%s\n%s"
               % (bid, row["service"], pretty_slot(row["slot"])),
               client_chat=chat_id, booking_id=bid)
        sheet_send(bid, "Скасування")

    elif data.startswith("cal:"):
        if not d.get("service_id"):
            screen_menu(chat_id, message_id)
            return
        d["ym"] = data[4:]
        set_state(chat_id, "day", d)
        screen_days(chat_id, message_id, d, data[4:])

    elif data.startswith("day:"):
        if not d.get("service_id"):
            screen_menu(chat_id, message_id)
            return
        d["day"] = data[4:]
        d["ym"] = data[4:11]
        set_state(chat_id, "time", d)
        screen_times(chat_id, message_id, d)

    elif data.startswith("time:"):
        slot = data[5:]
        if not d.get("service_id"):
            screen_menu(chat_id, message_id)
            return
        if slot in occupied(d.get("master_id", "")):
            screen_times(chat_id, message_id, d)
            return
        d["slot"] = slot
        head = "<b>%s</b>" % d.get("service_name")
        if d.get("master_name"):
            head += " · " + d["master_name"]
        show(chat_id, message_id, head + "\n" + pretty_slot(slot))
        after_slot(chat_id, None, d)


def handle_proof(chat_id, msg, username):
    """Клієнт надіслав скрін або квитанцію про оплату.

    Без цього бот просив би доказ і мовчки його ігнорував. Пересилаємо
    адміну разом із кнопками підтвердження — йому лишається один дотик.
    """
    row = active_booking(chat_id)
    if not row or not (row["deposit"] or 0):
        send(chat_id, "Дякуємо, але зараз цей файл нема до чого прикласти. "
                      "Якщо це про оплату — відкрийте свій запис у меню.",
             inline([[btn("← Меню", "act:menu")]]))
        return
    if row["paid"]:
        send(chat_id, "Цей запис уже оплачено — надсилати нічого не треба.",
             inline([[btn("Мій запис", "act:mybooking")]]))
        return

    send(chat_id, "<b>Отримали.</b> Передали адміністратору — щойно перевірить, "
                  "підтвердить оплату.",
         inline([[btn("Мій запис", "act:mybooking")]]))
    tkey = new_tkey()
    for admin in team_chats():
        call("copyMessage", chat_id=admin, from_chat_id=chat_id,
             message_id=msg["message_id"])
        res = send(admin, "<b>Клієнт надіслав підтвердження оплати</b>\n"
                    "Запис #%d · %s\n%s\n%s · %s%s\nПередоплата: %s"
             % (row["id"], row["service"], pretty_slot(row["slot"]),
                row["name"], row["phone"],
                ("\n@" + username) if username else "",
                money(row["deposit"] or 0)),
             inline([[btn("Підтвердити оплату", "payok:%d" % row["id"]),
                      btn("Ні, не бачу", "payno:%d" % row["id"])]]))
        if isinstance(res, dict) and res.get("message_id"):
            remember_thread(admin, res["message_id"], chat_id, row["id"],
                            tkey, res.get("text"))
            remember_pay_msg(row["id"], admin, res["message_id"])


def esc_html(text):
    return (str(text or "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def plain_name(text):
    """Імʼя як звичайний текст, без випадкових посилань.

    Telegram сам робить посиланням усе, що схоже на адресу, і нік
    «di.ma» перетворюється на сайт у Марокко. Ставимо між крапкою
    й наступною літерою невидимий знак — текст виглядає так само,
    а розпізнавач адреси вже не спрацьовує.
    """
    return re.sub(r"\.(?=\w)", ".\u2060", esc_html(text))


def who_answered(user):
    """Як підписати того, хто відповів клієнту.

    Клікабельним лишаємо тільки нік: він веде в чат із людиною.
    """
    name = plain_name(((user or {}).get("first_name") or "").strip())
    un = (user or {}).get("username")
    link = ('<a href="https://t.me/%s">@%s</a>' % (un, esc_html(un))) if un else ""
    if name and link:
        return "%s (%s)" % (name, link)
    return link or name or "хтось із команди"


def answer_client(from_chat, reply_msg, text, user):
    """Відповідь на запитання клієнта — звідки б її не написали.

    Працює однаково в робочій групі і в особистому чаті власника.
    Решті команди показуємо, що на це запитання вже відповіли, —
    інакше двоє пишуть клієнту одне й те саме.
    """
    client, _bid, tkey = thread_client(from_chat, (reply_msg or {}).get("message_id"))
    if not client:
        return False
    if not text:
        send(from_chat, "Відповідь клієнту поки що тільки текстом.",
             reply_to=(reply_msg or {}).get("message_id"))
        return True
    ok = send(client, "<b>Відповідь від %s</b>\n\n%s"
              % (CFG.get("business", "нас"), text),
              inline([[btn("← Меню", "act:menu")]]))
    if not ok:
        send(from_chat, "Не вдалося надіслати — можливо, клієнт заблокував бота.",
             reply_to=(reply_msg or {}).get("message_id"))
        return True

    # Переписуємо саме запитання, а не шлемо окреме повідомлення:
    # інакше в групі накопичуються службові рядки, а підказка
    # «відповідайте на це повідомлення» лишається висіти неправдою.
    mark = "<i>✓ Відповів(ла) %s</i>" % who_answered(user)
    copies = thread_copies(tkey) or [(from_chat,
                                      (reply_msg or {}).get("message_id"),
                                      None)]
    for chat, mid, old in copies:
        if not mid:
            continue
        if old and ASK_HINT in old:
            fresh = old.replace(ASK_HINT, mark)
        elif old:
            fresh = old + "\n\n" + mark
        else:
            fresh = None
        if fresh is None or not edit(chat, mid, fresh):
            send(chat, mark, reply_to=mid)
        else:
            with db() as conn:
                conn.execute("UPDATE threads SET text=? WHERE chat_id=?"
                             " AND message_id=?", (fresh, chat, mid))
    return True


def is_group_boss(chat_id, user_id):
    """Чи має ця людина право підключати групу."""
    if not user_id:
        return False
    if user_id in CFG.get("admin_ids", []):
        return True
    m = call("getChatMember", chat_id=chat_id, user_id=user_id)
    return isinstance(m, dict) and m.get("status") in ("creator", "administrator")


def who(user):
    """Як назвати людину з команди в повідомленні."""
    name = (user.get("first_name") or "").strip()
    if user.get("username"):
        return "%s (@%s)" % (name, user["username"]) if name else "@" + user["username"]
    return name or "хтось із команди"


def handle_group(msg):
    """Повідомлення з робочої групи.

    Telegram показує боту в групі лише команди і відповіді на його власні
    повідомлення — цього саме достатньо: підключити групу і відповісти
    клієнту. Читати все підряд бот не може і не повинен.
    """
    chat_id = msg["chat"]["id"]
    user = msg.get("from") or {}
    text = (msg.get("text") or "").strip()
    cmd = text.split()[0].split("@")[0].lower() if text else ""
    if cmd:
        print("група %s: %s" % (chat_id, cmd))

    if cmd in ("/grupa", "/start"):
        if not is_group_boss(chat_id, user.get("id")):
            send(chat_id, "Підключити групу може лише її адміністратор.")
            return
        if group_id() == chat_id:
            # Інструкція потрібна один раз. Далі вона лише засмічує групу.
            send(chat_id, "<b>Групу вже підключено.</b>\n\n"
                          "Відключити: /vidkluchyty")
            return
        set_setting("group_id", chat_id)
        send(chat_id,
             "<b>Групу підключено.</b>\n\n"
             "Сюди приходитимуть нові записи, скасування, оплати і запитання.\n"
             "• Оплату підтверджуйте кнопкою просто тут.\n"
             "• Щоб відповісти клієнту — відповідайте на повідомлення бота, "
             "і текст піде йому в особисті.\n\n"
             "Відключити: /vidkluchyty")
        if miniapp_url():
            pin_schedule(chat_id, "<b>Розклад закладу</b>\n\n"
             "Усі записи в одному місці: місяць, окремий день і кожен "
             "майстер своєю колонкою. Видно вільні вікна й зайняті години.\n"
             "Дотик по запису — клієнт, телефон, послуга та стан оплати.\n\n"
             "Оновлюється сам, щойно хтось записався або скасував.")
        return

    if cmd == "/vidkluchyty":
        if not is_group_boss(chat_id, user.get("id")):
            return
        set_setting("group_id", 0)
        send(chat_id, "Групу відключено. Сповіщення сюди більше не йдуть.")
        return

    if cmd == "/rozklad":
        if not pin_schedule(chat_id, "<b>Розклад закладу</b>\n\n"
             "Усі записи в одному місці: місяць, окремий день і кожен "
             "майстер своєю колонкою. Видно вільні вікна й зайняті години.\n"
             "Дотик по запису — клієнт, телефон, послуга та стан оплати.\n\n"
             "Оновлюється сам, щойно хтось записався або скасував."):
            send(chat_id, "Для груп потрібне пряме посилання на застосунок.\n"
                          "Створіть його в BotFather командою /newapp і впишіть "
                          "у config.json як <code>miniapp_link</code>.")
        return

    reply = msg.get("reply_to_message") or {}
    if reply:
        answer_client(chat_id, reply, text, user)


def handle_my_chat_member(upd):
    """Бота щойно додали в групу — підказуємо, що робити далі."""
    chat = upd.get("chat") or {}
    if chat.get("type") not in ("group", "supergroup"):
        return
    status = ((upd.get("new_chat_member") or {}).get("status") or "")
    if status not in ("member", "administrator"):
        return
    if group_id() == chat.get("id"):
        return
    send(chat["id"], "Вітаю. Щоб сюди приходили записи й оплати, "
                     "адміністратор групи має написати /grupa")


def handle_message(msg):
    chat_id = msg["chat"]["id"]
    kind = (msg.get("chat") or {}).get("type")
    if kind in ("group", "supergroup"):
        handle_group(msg)
        return
    if kind == "channel":
        print("канал %s: бот у каналі не працює" % chat_id)
        return
    username = msg.get("from", {}).get("username", "")
    text = (msg.get("text") or "").strip()
    contact = msg.get("contact")

    paid = msg.get("successful_payment")
    if paid:
        payload = paid.get("invoice_payload", "")
        if payload.startswith("booking:"):
            mark_paid(int(payload.split(":", 1)[1]))
        return
    if msg.get("photo") or msg.get("document"):
        handle_proof(chat_id, msg, username)
        return

    step, d = get_state(chat_id)
    is_admin = chat_id in CFG.get("admin_ids", [])

    # Власник відповів прямо в себе в чаті з ботом, а не в групі.
    reply = msg.get("reply_to_message")
    if reply and is_admin and answer_client(chat_id, reply, text,
                                            msg.get("from")):
        return

    if text.startswith("/start ") and open_slot_from_link(
            chat_id, text.split(None, 1)[1].strip()):
        return

    if text in ("/start", "/menu"):
        reset_menu_button(chat_id)
        if chat_id in staff_ids():
            publish_admin_commands(chat_id)
        screen_menu(chat_id, cover=True)
        return

    if text == "/id":
        send(chat_id, "Ваш chat_id: <code>%d</code>\n"
                      "Впишіть його в admin_ids у config.json." % chat_id)
        return

    if text == "/skasuvaty":
        with db() as conn:
            row = conn.execute(
                "SELECT id, service, slot, master_name FROM bookings "
                "WHERE chat_id=? AND status='new' ORDER BY id DESC LIMIT 1",
                (chat_id,)).fetchone()
            if not row:
                send(chat_id, "Активних записів нема.")
                return
            conn.execute("UPDATE bookings SET status='cancelled' WHERE id=?",
                         (row["id"],))
        send(chat_id, "Запис #%d скасовано. Час звільнено." % row["id"])
        notify("<b>Скасування #%d</b>\n%s%s\n%s"
               % (row["id"], row["service"],
                  (" · " + row["master_name"]) if row["master_name"] else "",
                  pretty_slot(row["slot"])),
               client_chat=chat_id, booking_id=row["id"])
        sheet_send(row["id"], "Скасування")
        return

    if step == "fb_text" and text:
        pass_complaint(chat_id, d.get("bid"), text, username)
        return

    if text.split("@")[0].lower() in ("/vilni", "/вільні"):
        fb = free_button()
        if not fb:
            send(chat_id, "Календар вільних годин не підключено.")
            return
        send(chat_id, "<b>Вільні години</b>\n\nКалендар на місяць уперед: "
                      "видно, які дні ще відкриті, а дотик по дню показує "
                      "вільні години кожного майстра.", inline([[fb]]))
        return

    if text.split("@")[0].lower() in ("/rozklad", "/розклад"):
        if chat_id not in staff_ids():
            fb = free_button()
            send(chat_id, "Розклад із записами відкритий власнику закладу, "
                          "майстри дивляться його в робочій групі.\n\n"
                          "Ось календар вільних годин." if fb else
                          "Розклад відкритий власнику закладу. "
                          "Майстри дивляться його в робочій групі.",
                 inline([[fb]]) if fb else None)
            return
        send_schedule(chat_id)
        return

    if text == "/splyachi" and is_admin:
        if not wb_cfg():
            send(chat_id, "Повернення сплячих вимкнене: у config.json "
                          "немає <code>winback.enabled</code>.")
            return
        people = sleeping_clients()
        if not people:
            send(chat_id, "Сплячих нема: усі або були нещодавно, "
                          "або вже мають запис попереду.")
            return
        send(chat_id, "Знайшов <b>%d</b> клієнтів, які давно не були "
                      "і не мають запису.\nНадіслати їм запрошення?" % len(people),
             inline([[btn("Надіслати", "wbsend")],
                     [btn("Не треба", "act:menu")]]))
        return

    if text == "/vidhuky" and is_admin:
        with db() as conn:
            rows = conn.execute(
                "SELECT rating, COUNT(*) c FROM bookings WHERE rating IS NOT NULL"
                " GROUP BY rating ORDER BY rating DESC").fetchall()
            bad = conn.execute(
                "SELECT id, rating, name, service, rating_text FROM bookings"
                " WHERE rating <= 3 AND rating_text IS NOT NULL"
                " ORDER BY id DESC LIMIT 5").fetchall()
        if not rows:
            send(chat_id, "Оцінок поки немає.")
            return
        total = sum(r["c"] for r in rows)
        avg = sum(r["rating"] * r["c"] for r in rows) / total
        lines = ["<b>Оцінки</b>  ·  середня %.1f з %d" % (avg, total), ""]
        for r in rows:
            lines.append("%s — %d" % ("★" * r["rating"], r["c"]))
        if bad:
            lines += ["", "<b>Що писали незадоволені</b>"]
            for b in bad:
                lines.append("#%d · %s · %s\n%s" % (b["id"], b["name"],
                                                    "★" * b["rating"],
                                                    b["rating_text"][:200]))
        send(chat_id, "\n".join(lines))
        return

    if text == "/tablycja" and is_admin:
        # Перевірка зв'язку з Google Таблицею: найчастіша помилка — забуте
        # розгортання або розбіжність ключів, і без перевірки це виявляється
        # аж тоді, коли загубиться перший справжній запис.
        if not sheet_url():
            send(chat_id, "Таблиця не підключена: у config.json порожній "
                          "<code>sheet_webhook</code>.")
            return
        made_d, made_t = stamp_parts(now().isoformat())
        probe = {"secret": CFG.get("sheet_secret", ""), "event": "Перевірка",
                 "id": 0, "created_date": made_d, "created_time": made_t,
                 "status": "перевірка", "service": "Перевірка зв'язку",
                 "master": "", "slot_date": "", "slot_time": "", "name": "—",
                 "phone": "—", "username": "", "price": "", "deposit": 0,
                 "paid": 0, "paid_date": "", "paid_time": "", "due": "",
                 "comment": "Цей рядок можна видалити",
                 "created": "%s о %s" % (made_d, made_t),
                 "when": "", "paid_at": ""}
        try:
            sheet_post(json.dumps(probe, ensure_ascii=False))
        except Exception as e:
            send(chat_id, "<b>Таблиця не відповіла.</b>\n%s\n\n"
                          "Найчастіші причини: не зроблено розгортання, "
                          "або в розгортанні стоїть доступ «Тільки я»." % e)
            return
        waiting = 0
        with db() as conn:
            waiting = conn.execute("SELECT COUNT(*) c FROM outbox").fetchone()["c"]
        send(chat_id, "<b>Таблиця відповіла.</b>\nУ ній має з'явитись рядок "
                      "«Перевірка зв'язку» з номером 0 — видаліть його.\n"
                      "У черзі на вигрузку: %d." % waiting)
        if waiting:
            threading.Thread(target=sheet_flush, daemon=True).start()
        return

    if text == "/zayavky" and is_admin:
        with db() as conn:
            rows = conn.execute(
                "SELECT * FROM bookings WHERE status='new' "
                "ORDER BY slot LIMIT 15").fetchall()
        if not rows:
            send(chat_id, "Записів нема.")
            return
        out = ["<b>Найближчі записи</b>", ""]
        for r in rows:
            line = "#%d · %s\n%s" % (r["id"], pretty_slot(r["slot"]), r["service"])
            if r["master_name"]:
                line += " · " + r["master_name"]
            line += "\n%s · %s" % (r["name"], r["phone"])
            if r["deposit"]:
                line += "\n%s %s" % (money(r["deposit"]),
                                      "оплачено" if r["paid"] else "— не оплачено")
            out += [line, ""]
        send(chat_id, "\n".join(out))
        return

    if text == "/export" and is_admin:
        with db() as conn:
            rows = conn.execute("SELECT * FROM bookings ORDER BY id").fetchall()
        path = os.path.join(HERE, "zayavky.csv")
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write("id;послуга;майстер;ціна;коли;ім'я;телефон;статус;створено\n")
            for r in rows:
                f.write(";".join([str(r["id"]), r["service"] or "",
                                  r["master_name"] or "", r["price"] or "",
                                  r["slot"] or "", r["name"] or "",
                                  r["phone"] or "", r["status"] or "",
                                  r["created"] or ""]) + "\n")
        send(chat_id, "Вивантажено %d записів у zayavky.csv" % len(rows))
        return

    # --- кроки сценарію ---
    if step == "name" and text:
        d["name"] = text[:80]
        set_state(chat_id, "phone", d)
        ask_phone(chat_id)
        return

    if step == "phone":
        phone = contact.get("phone_number") if contact else text
        if not phone or not PHONE_RE.search(phone):
            send(chat_id, "Схоже, це не номер. Напишіть у форматі +380...")
            return
        d["phone"] = phone.strip()[:30]
        # Знижку вирішуємо тут, бо аж тепер видно, кого саме записують:
        # себе чи іншу людину, і чи була вона у нас раніше.
        if d.get("for") == "other":
            seen = last_visit(phone=d["phone"])
        else:
            seen = last_visit(chat_id=chat_id)
        apply_repeat_discount(d, bool(seen))
        confirm(chat_id, d, username)
        return

    if step == "question" and text:
        clear_state(chat_id)
        send(chat_id, "Передали. Відповімо найближчим часом.")
        notify(("<b>Запитання</b>\nвід @%s (id %d)\n\n%s\n\n"
                % (username or "без username", chat_id, text[:1500]))
               + ASK_HINT, client_chat=chat_id)
        return

    screen_menu(chat_id)


# --------------------------------------------------------------------------
# Головний цикл
# --------------------------------------------------------------------------

_chat_locks = {}
_locks_guard = threading.Lock()


def chat_lock(chat_id):
    """Один замок на користувача: його натискання обробляються по черзі,
    а різні користувачі — одночасно."""
    with _locks_guard:
        if chat_id not in _chat_locks:
            _chat_locks[chat_id] = threading.Lock()
        return _chat_locks[chat_id]


def update_chat_id(u):
    for key in ("callback_query", "message"):
        part = u.get(key) or {}
        msg = part.get("message") if key == "callback_query" else part
        if msg and msg.get("chat"):
            return msg["chat"].get("id")
    return 0


def handle_update(u):
    """Одне оновлення в окремому потоці, щоб повільна відправка медіа
    не зупиняла весь бот."""
    try:
        if "pre_checkout_query" in u:
            # Відповісти треба протягом 10 секунд, інакше Telegram
            # сам скасує платіж.
            call("answerPreCheckoutQuery",
                 pre_checkout_query_id=u["pre_checkout_query"]["id"], ok=True)
            return
        with chat_lock(update_chat_id(u)):
            if "callback_query" in u:
                handle_callback(u["callback_query"])
            elif "message" in u:
                handle_message(u["message"])
    except Exception:
        # Клієнт не повинен дивитись у «зависле» вікно: кажемо, що сталось,
        # і повертаємо в меню. У лог — чат і кнопка, щоб не шукати навмання.
        chat_id = update_chat_id(u)
        what = ((u.get("callback_query") or {}).get("data")
                or ((u.get("message") or {}).get("text") or "")[:40] or "?")
        print("ЗБІЙ у чаті %s на «%s»:" % (chat_id, what))
        traceback.print_exc()
        if chat_id:
            try:
                send(chat_id, "Не вдалося виконати цю дію. Спробуйте ще раз "
                              "або почніть з меню — /start",
                     inline([[btn("← Меню", "act:menu")]]))
            except Exception:
                traceback.print_exc()


class TaggedStream:
    """Кожен рядок у лозі з часом і назвою клієнта.

    Боти всіх клієнтів пишуть в один файл. Без цього неможливо сказати,
    чий це збій і коли він стався — старі помилки злипаються з новими.
    Заодно знімає потребу в PYTHONUNBUFFERED: пишемо одразу на диск.
    """

    def __init__(self, stream, tag):
        self._s, self._tag, self._fresh = stream, tag, True

    def write(self, text):
        if not text:
            return 0
        out = []
        for part in text.splitlines(True):
            if self._fresh and part.strip():
                out.append("%s [%s] " % (datetime.now().strftime("%H:%M:%S"),
                                         self._tag))
            out.append(part)
            self._fresh = part.endswith("\n")
        self._s.write("".join(out))
        self._s.flush()
        return len(text)

    def flush(self):
        self._s.flush()

    def isatty(self):
        return False


def drop_backlog():
    """Скинути чергу натискань, що накопичилась, поки бот не працював.

    Telegram зберігає оновлення кілька днів. Після простою бот інакше
    отримає натискання, зроблені багато хвилин тому: відповісти на них
    уже не можна (кнопка протухла), а клієнт отримає екрани, яких зараз
    не просив. Беремо тільки останнє і рахуємо з нього.
    """
    last = call("getUpdates", _timeout=20, offset=-1, timeout=0)
    if not last:
        return None
    skipped = last[-1]["update_id"]
    print("Пропущено старі натискання, рахуємо з %d." % (skipped + 1))
    return skipped + 1


def publish_admin_commands(chat_id):
    """Власнику — свій список команд, видимий лише в його чаті."""
    cmds = [{"command": "start", "description": "Головне меню"}]
    if freeapp_url():
        cmds.append({"command": "vilni", "description": "Вільні години"})
    if miniapp_url():
        cmds.append({"command": "rozklad", "description": "Розклад із записами"})
    if (CFG.get("sheet_webhook") or "").strip():
        cmds.append({"command": "tablycja", "description": "Перевірити Google Таблицю"})
    if fb_cfg():
        cmds.append({"command": "vidhuky", "description": "Відгуки клієнтів"})
    if wb_cfg():
        cmds.append({"command": "splyachi", "description": "Сплячі клієнти"})
    return call("setMyCommands", commands=cmds,
                scope={"type": "chat", "chat_id": chat_id}) is not None


def publish_commands():
    """Списки команд: окремо для клієнтів і окремо для робочої групи.

    У групі клієнтських команд бути не повинно, а «розклад» і керування
    групою мають з'являтись у списку по «/» без підказок від нас.
    """
    private = [{"command": "start", "description": "Головне меню"},
               {"command": "skasuvaty", "description": "Скасувати мій запис"}]
    if freeapp_url():
        private.insert(1, {"command": "vilni",
                           "description": "Вільні години"})
    call("setMyCommands", commands=private)
    group = [{"command": "rozklad", "description": "Розклад закладу"},
             {"command": "grupa", "description": "Підключити цю групу до бота"},
             {"command": "vidkluchyty", "description": "Відключити групу"}]
    call("setMyCommands", commands=group,
         scope={"type": "all_group_chats"})
    call("setMyCommands", commands=group,
         scope={"type": "all_chat_administrators"})


def main():
    tag = os.path.basename(HERE)
    sys.stdout = TaggedStream(sys.stdout, tag)
    sys.stderr = TaggedStream(sys.stderr, tag)

    init_db()
    me = call("getMe")
    if not me:
        sys.exit("Не вдалося підключитися до Telegram. Перевірте токен і мережу.")
    if me.get("username"):
        set_setting("bot_username", me["username"])
    print("Бот @%s запущений. Майстрів: %d. Ctrl+C щоб зупинити."
          % (me.get("username"), len(masters())))

    threading.Thread(target=warm_photos, daemon=True).start()

    publish_commands()

    offset = drop_backlog()
    while True:
        try:
            updates = call("getUpdates", _timeout=45, offset=offset, timeout=25,
                           allowed_updates=["message", "callback_query",
                                            "pre_checkout_query",
                                            "my_chat_member"])
            reminders_tick()
            feedback_tick()
            sheet_flush()
            if updates is None:
                time.sleep(3)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                threading.Thread(target=handle_update, args=(u,),
                                 daemon=True).start()
        except KeyboardInterrupt:
            print("\nЗупинено.")
            return
        except Exception:
            traceback.print_exc()
            time.sleep(5)


if __name__ == "__main__":
    main()
