# -*- coding: utf-8 -*-
"""Сервер розкладу: підпис Telegram, права доступу, дані по днях."""
import hashlib, hmac, json, os, shutil, sqlite3, sys, tempfile, time, urllib.parse
from datetime import datetime, timedelta
R = []
def ok(c, l): R.append(bool(c)); print(("  OK   " if c else "  ПАДІННЯ ") + l)

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
W = tempfile.mkdtemp()
shutil.copy(os.path.join(SRC, "web.py"), W)
shutil.copy(os.path.join(SRC, "slots.py"), W)
shutil.copy(os.path.join(SRC, "miniapp.html"), W)

TOKEN_A = "111:AAA-token-lezo"
TOKEN_B = "222:BBB-token-kontur"

def make_client(name, token, admins, masters, rows):
    d = os.path.join(W, "clients", name)
    os.makedirs(d)
    cfg = {"token": token, "business": name.upper(), "admin_ids": admins,
           "masters": masters}
    json.dump(cfg, open(d + "/config.json", "w", encoding="utf-8"), ensure_ascii=False)
    c = sqlite3.connect(d + "/zayavky.db")
    c.execute("""CREATE TABLE bookings(id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER, username TEXT, service TEXT, slot TEXT, name TEXT,
        phone TEXT, comment TEXT, status TEXT, created TEXT, master_id TEXT,
        master_name TEXT, price TEXT, minutes INTEGER, deposit INTEGER,
        paid INTEGER DEFAULT 0)""")
    for r in rows:
        c.execute("INSERT INTO bookings(service, slot, name, phone, status,"
                  " master_id, master_name, price, minutes, deposit, paid,"
                  " username, comment) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", r)
    c.commit(); c.close()

D1 = "2026-12-05"
make_client("lezo", TOKEN_A, [777],
            [{"id": "ostap", "name": "Остап", "tg_id": 501},
             {"id": "artem", "name": "Артем"}],
            [("Стрижка", D1 + "T11:00", "Петро", "+3801", "new", "ostap", "Остап", "600 ₴", 60, 200, 1, "petro", ""),
             ("Борода", D1 + "T12:30", "Іван", "+3802", "new", "artem", "Артем", "400 ₴", 40, 0, 0, "", ""),
             ("Комплекс", "2026-12-09T15:00", "Тарас", "+3803", "new", "ostap", "Остап", "900 ₴", 90, 0, 0, "", ""),
             ("Скасований", D1 + "T16:00", "Хтось", "+3804", "cancelled", "ostap", "Остап", "600 ₴", 60, 0, 0, "", "")])
make_client("kontur", TOKEN_B, [888], [{"id": "lev", "name": "Лев"}],
            [("Тату", D1 + "T13:00", "Орест", "+3809", "new", "lev", "Лев", "6000 ₴", 180, 1800, 0, "", "")])

sys.argv = [sys.argv[0], W]; sys.path.insert(0, W)
import web

def init_data(token, user_id, age=0):
    user = json.dumps({"id": user_id, "first_name": "Т"}, ensure_ascii=False)
    fields = {"auth_date": str(int(time.time()) - age), "query_id": "x", "user": user}
    check = "\n".join("%s=%s" % (k, fields[k]) for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)

print("— перевірка підпису")
u = web.check_init_data(init_data(TOKEN_A, 777), TOKEN_A)
ok(u and u["id"] == 777, "справжній підпис приймається")
ok(web.check_init_data(init_data(TOKEN_A, 777), TOKEN_B) is None,
   "чужим токеном підпис не проходить")
ok(web.check_init_data("", TOKEN_A) is None, "порожнє не проходить")
bad = init_data(TOKEN_A, 777).replace("hash=", "hash=0")
ok(web.check_init_data(bad, TOKEN_A) is None, "зіпсований підпис не проходить")
ok(web.check_init_data(init_data(TOKEN_A, 777, age=90000), TOKEN_A) is None,
   "старий підпис не проходить")
tampered = init_data(TOKEN_A, 777)
tampered = tampered.replace("%22id%22%3A+777", "%22id%22%3A+999")
ok(web.check_init_data(tampered, TOKEN_A) is None,
   "підміна id користувача не проходить")

print("— хто має доступ")
cfg = web.client_cfg("lezo")
GROUP = -1009999

# підставляємо відповідь Telegram замість справжнього запиту
MEMBERS = {501, 502}
class FakeResp:
    def __init__(self, body): self._b = body.encode()
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False
import urllib.parse as _up
def fake_open(url, timeout=None):
    q = _up.parse_qs(_up.urlsplit(url).query)
    uid = int(q["user_id"][0])
    st = "member" if uid in MEMBERS else "left"
    return FakeResp(json.dumps({"ok": True, "result": {"status": st}}))
web.urllib.request.urlopen = fake_open

# підключаємо групу в базі клієнта
import sqlite3 as _sq
c = _sq.connect(os.path.join(W, "clients", "lezo", "zayavky.db"))
c.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)")
c.execute("INSERT OR REPLACE INTO settings VALUES('group_id', ?)", (str(GROUP),))
c.commit(); c.close()

ok(web.group_of("lezo") == GROUP, "група клієнта прочитана з бази")
ok(web.may_see(cfg, "lezo", 777), "власник бачить розклад завжди")
ok(web.may_see(cfg, "lezo", 501), "учасник групи бачить розклад")
ok(web.may_see(cfg, "lezo", 502), "будь-який учасник групи, не лише майстер")
ok(not web.may_see(cfg, "lezo", 31337), "стороння людина не бачить")

print("— звільнений перестає бачити")
MEMBERS.discard(502)
web._member_cache.clear()
ok(not web.may_see(cfg, "lezo", 502), "вийшов із групи — доступ зник")

print("— без групи доступ лише у власника")
c = _sq.connect(os.path.join(W, "clients", "lezo", "zayavky.db"))
c.execute("UPDATE settings SET value='0' WHERE key='group_id'"); c.commit(); c.close()
web._member_cache.clear()
ok(web.may_see(cfg, "lezo", 777), "власник бачить")
ok(not web.may_see(cfg, "lezo", 501), "решта — ні")
c = _sq.connect(os.path.join(W, "clients", "lezo", "zayavky.db"))
c.execute("UPDATE settings SET value=? WHERE key='group_id'", (str(GROUP),))
c.commit(); c.close()
web._member_cache.clear()

print("— Telegram недоступний: пускати не можна")
def boom(url, timeout=None): raise OSError("мережа лежить")
web.urllib.request.urlopen = boom
web._member_cache.clear()
ok(not web.may_see(cfg, "lezo", 501), "при збої відповіді доступ закритий")
ok(web.may_see(cfg, "lezo", 777), "власника це не стосується")
web.urllib.request.urlopen = fake_open
web._member_cache.clear()

print("— імена клієнтів перевіряються")
ok(web.client_cfg("../../etc") is None, "спроба вийти з теки не проходить")
ok(web.client_cfg("нема") is None, "неіснуючий клієнт — нічого")
ok(web.client_cfg("lezo") is not None, "справжній клієнт знайдений")

print("— дані за місяць")
a, b = web.month_bounds("2026-12")
rows = [r for r in web.rows_for("lezo", a, b) if r["status"] == "new"]
ok(len(rows) == 3, "три активні записи за грудень (%d)" % len(rows))
days = {}
for r in rows: days[r["slot"][:10]] = days.get(r["slot"][:10], 0) + 1
ok(days.get(D1) == 2 and days.get("2026-12-09") == 1, "розподіл по днях вірний")

print("— дані за день")
day = [r for r in web.rows_for("lezo", D1 + "T00:00", D1 + "T23:59")]
ok(len(day) == 3, "усі записи дня, включно зі скасованим (%d)" % len(day))
paid = [r for r in day if r["paid"]]
ok(len(paid) == 1 and paid[0]["deposit"] == 200, "видно передоплату")

print("— список усіх найближчих записів")
from datetime import datetime as _dt, timedelta as _td
n = _dt.now()
a = n.strftime("%Y-%m-%dT00:00")
b = (n + _td(days=120)).isoformat(timespec="minutes")
soon = [r for r in web.rows_for("lezo", a, b) if r["status"] == "new"]
ok(len(soon) == 3, "три майбутні записи (%d)" % len(soon))
ok(soon == sorted(soon, key=lambda r: r["slot"]), "відсортовані за часом")
ok(all(r["status"] == "new" for r in soon), "скасованих у списку немає")
ok(any(r["master_name"] for r in soon), "видно майстра в кожному рядку")
past = web.rows_for("lezo", "2000-01-01T00:00", a)
ok(not any(r in soon for r in past), "минулі записи в список не потрапляють")

print("— клієнти не бачать одне одного")
ok(len(web.rows_for("kontur", a, b)) == 1, "у тату-студії свій єдиний запис")
k = web.client_cfg("kontur")
ok(not web.may_see(k, "kontur", 777),
   "власник барбершопу не має прав у тату-студії")
ok(web.check_init_data(init_data(TOKEN_A, 777), k["token"]) is None,
   "підпис бота барбершопу не відкриває тату-студію")

print("— база відкривається лише для читання")
path = os.path.join(W, "clients", "lezo", "zayavky.db")
c = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
try:
    c.execute("DELETE FROM bookings"); c.commit()
    ok(False, "сторінка змогла змінити базу")
except sqlite3.OperationalError:
    ok(True, "сторінка не може нічого змінити в базі")
c.close()

print("— сторінка на місці")
html = open(os.path.join(W, "miniapp.html"), encoding="utf-8").read()
ok("telegram-web-app.js" in html, "підключено скрипт Telegram")
ok("initData" in html, "сторінка передає підпис на сервер")
ok("showList" in html and 'api("list"' in html, "є розділ списку")
ok("Календар" in html and "Список" in html, "є дві вкладки вгорі")
ok(html.count("<script") == html.count("</script>"), "теги скриптів парні")

print("— вільні години рахуються тими самими правилами, що й у боті")
import slots
fcfg = {"slot_minutes": 60, "work_start": "10:00", "work_end": "14:00",
        "workdays": [0, 1, 2, 3, 4], "min_lead_minutes": 90,
        "masters": [{"id": "ostap", "name": "Остап", "role": "Барбер"},
                    {"id": "artem", "name": "Артем", "work_start": "12:00",
                     "work_end": "14:00", "workdays": [0, 1, 2, 3, 4],
                     "days_off": []}]}
# найближчий понеділок, щоб не впертись у вихідний чи в запас часу
base = datetime.now().date() + timedelta(days=3)
while base.weekday() != 0:
    base += timedelta(days=1)
DAY = base.isoformat()
nowdt = datetime.now()
free = slots.free_times(fcfg, DAY, fcfg["masters"][0], None, set(), nowdt)
ok([t[1] for t in free] == ["10:00", "11:00", "12:00", "13:00"],
   "повний робочий день розбитий на години (%s)" % [t[1] for t in free])
busy1 = {DAY + "T11:00"}
free = slots.free_times(fcfg, DAY, fcfg["masters"][0], None, busy1, nowdt)
ok([t[1] for t in free] == ["10:00", "12:00", "13:00"], "зайнята година зникла")
sat = (base + timedelta(days=5)).isoformat()
ok(slots.free_times(fcfg, sat, fcfg["masters"][0], None, set(), nowdt) == [],
   "у вихідний вільних годин немає")
off = dict(fcfg["masters"][0], days_off=[DAY])
ok(slots.free_times(fcfg, DAY, off, None, set(), nowdt) == [],
   "особистий вихідний майстра поважається")
late = slots.free_times(fcfg, DAY, fcfg["masters"][1], None, set(), nowdt)
ok([t[1] for t in late] == ["12:00", "13:00"], "свій графік майстра враховано")

print("— довгий запис забирає кілька клітинок")
make_client("dovhyi", "333:CCC", [999],
            [{"id": "lev", "name": "Лев"}],
            [("Тату", DAY + "T11:00", "Орест", "+1", "new", "lev", "Лев",
              "6000 ₴", 180, 0, 0, "", ""),
             ("Скасоване", DAY + "T10:00", "Хтось", "+2", "cancelled", "lev",
              "Лев", "600 ₴", 60, 0, 0, "", "")])
dcfg = web.client_cfg("dovhyi")
dcfg.update({k: v for k, v in fcfg.items() if k != "masters"})
busy = web.busy_by_master(dcfg, "dovhyi", DAY + "T00:00", DAY + "T23:59")
ok(busy.get("lev") == {DAY + "T11:00", DAY + "T12:00", DAY + "T13:00"},
   "тригодинний сеанс закрив три клітинки (%s)" % sorted(busy.get("lev", [])))
fd = web.free_day(dcfg, DAY, busy, None, nowdt)
ok(len(fd) == 1 and fd[0]["times"] == ["10:00"],
   "лишилась єдина вільна година (%s)" % fd)
ok("Скасоване" not in str(busy), "скасований запис місце не тримає")

print("— сторінка вільних годин відкрита клієнтові")
import http.client, threading
from http.server import ThreadingHTTPServer
srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT = srv.server_address[1]

def get(path, token, uid):
    # власним клієнтом, бо urlopen у цьому тесті підмінений заглушкою
    url = "%s%sinit=%s" % (path, "&" if "?" in path else "?",
                           urllib.parse.quote(init_data(token, uid)))
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    try:
        c.request("GET", url)
        r = c.getresponse()
        return r.status, json.loads(r.read().decode("utf-8"))
    finally:
        c.close()

STRANGER = 31337
web.urllib.request.urlopen = fake_open
web._member_cache.clear()
code, j = get("/lezo/api/freemonth?m=" + DAY[:7], TOKEN_A, STRANGER)
ok(code == 200, "стороння людина бачить вільні години (%d)" % code)
ok("days" in j and "horizon" in j, "у відповіді дні й горизонт запису")
ok("rows" not in str(j) and "+3801" not in str(j),
   "жодного чужого телефону у відповіді")
code, j = get("/lezo/api/freeday?d=" + DAY, TOKEN_A, STRANGER)
ok(code == 200, "день із вільними годинами теж відкритий")
ok(all("phone" not in m and "times" in m for m in j["free"]),
   "по кожному майстру лише години")
code, _ = get("/lezo/api/month?m=" + DAY[:7], TOKEN_A, STRANGER)
ok(code == 403, "а повний розклад сторонньому закритий (%d)" % code)
code, _ = get("/lezo/api/freemonth?m=" + DAY[:7], TOKEN_B, STRANGER)
ok(code == 401, "чужим підписом вільні години теж не відкрити (%d)" % code)
code, _ = get("/lezo/api/freemonth?m=2026-1", TOKEN_A, STRANGER)
ok(code == 400, "кривий місяць відкинуто (%d)" % code)
srv.shutdown()

print("— клієнтський режим у самій сторінці")
ok("v=free" in html and "FREE" in html, "сторінка знає про режим вільних годин")
ok("showFree" in html and 'api("freemonth"' in html, "є календар вільних годин")
ok('api("freeday"' in html, "є розклад вільних годин на день")
ok("function take(" in html and "openTelegramLink" in html,
   "дотик по годині веде назад у бот")
ok('data-t="' in html and 'data-i="' in html, "година і майстер є в кнопці")

print("— сторінка знає, в який бот вертати людину")
c = _sq.connect(os.path.join(W, "clients", "dovhyi", "zayavky.db"))
c.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)")
c.execute("INSERT OR REPLACE INTO settings VALUES('bot_username','demo_bot')")
c.commit(); c.close()
ok(web.setting_of("dovhyi", "bot_username") == "demo_bot", "імʼя бота прочитане")
ok(web.setting_of("dovhyi", "нема") == "", "відсутній ключ не ламає")
ok(web.group_of("lezo") == GROUP, "група читається через той самий механізм")
fd2 = web.free_day(dcfg, DAY, busy, None, nowdt)
ok(fd2 and fd2[0]["i"] == 0, "номер майстра для посилання переданий")

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)
