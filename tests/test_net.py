# -*- coding: utf-8 -*-
"""Мережевий шар: одне з'єднання на всі запити, тиха спроба перез'єднатись.

Саме повторні TLS-рукостискання вішали бота на сервері: кожне натискання
відкривало з'єднання наново, а частина рукостискань падала в таймаут.
"""
import http.client, io, json, os, shutil, sys, tempfile, threading
R = []
def ok(c, l): R.append(bool(c)); print(("  OK   " if c else "  ПАДІННЯ ") + l)

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
W = tempfile.mkdtemp()
shutil.copy(os.path.join(SRC, "bot.py"), W)
shutil.copy(os.path.join(SRC, "slots.py"), W)
cfg = json.load(open(os.path.join(SRC, "templates/barbershop.json"), encoding="utf-8"))
cfg["token"] = "000:TEST"; cfg["admin_ids"] = [1]
json.dump(cfg, open(W + "/config.json", "w", encoding="utf-8"), ensure_ascii=False)
sys.argv = [sys.argv[0], W]; sys.path.insert(0, W)
import bot

CREATED = []          # скільки з'єднань відкрито
REQUESTS = []         # які запити пішли


class FakeSock:
    def settimeout(self, t): self.t = t


class FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._b = json.dumps(payload).encode("utf-8")
    def read(self): return self._b


class FakeConn:
    """Поводиться як HTTPSConnection. script — що робити на кожен запит."""
    script = []
    def __init__(self, host, timeout=None, context=None):
        self.host, self.timeout, self.sock = host, timeout, FakeSock()
        self.context = context
        self.closed = False
        CREATED.append(self)
    def request(self, m, path, body=None, headers=None):
        REQUESTS.append(path.rsplit("/", 1)[-1])
        act = FakeConn.script.pop(0) if FakeConn.script else ("ok", {})
        self._act = act
        if act[0] == "boom":
            raise ConnectionResetError("з'єднання скинуто")
    def getresponse(self):
        kind, payload = self._act
        if kind == "http400":
            return FakeResp(400, {"ok": False, "description": "Bad Request: test"})
        if kind == "junk":
            r = FakeResp(200, {}); r._b = b"<html>not json"; return r
        return FakeResp(200, {"ok": True, "result": payload})
    def close(self): self.closed = True


bot.Conn = FakeConn

print("— одне з'єднання на багато запитів")
CREATED.clear(); REQUESTS.clear(); bot.net_drop()
for i in range(10):
    bot.call("sendMessage", chat_id=1, text="x")
ok(len(CREATED) == 1, "10 запитів — %d з'єднання (було б 10 рукостискань)" % len(CREATED))
ok(len(REQUESTS) == 10, "усі 10 запитів пішли")

print("— таймаут виставляється на сокеті, з'єднання не перевідкривається")
CREATED.clear(); bot.net_drop()
bot.call("sendMessage", chat_id=1, text="x")
bot.call("getUpdates", _timeout=45, offset=1, timeout=25)
ok(len(CREATED) == 1, "довге опитування не змусило відкривати нове з'єднання")
ok(CREATED[0].sock.t == 45, "сокету виставлено 45 с на довге опитування")

print("— сервер закрив старе з'єднання: мовчазна друга спроба")
CREATED.clear(); REQUESTS.clear(); bot.net_drop()
FakeConn.script = [("boom", None), ("ok", {"message_id": 7})]
res = bot.call("sendMessage", chat_id=1, text="x")
ok(res == {"message_id": 7}, "запит усе одно виконано")
ok(len(CREATED) == 2, "відкрито саме одне нове з'єднання замість битого")
ok(CREATED[0].closed, "старе з'єднання закрито, дескриптор не тече")

print("— мережа лежить: дві спроби і чесна відмова")
CREATED.clear(); bot.net_drop()
FakeConn.script = [("boom", None), ("boom", None), ("boom", None)]
err = io.StringIO(); old_err, sys.stderr = sys.stderr, err
res = bot.call("sendMessage", chat_id=1, text="x")
sys.stderr = old_err
ok(res is None, "повертає None, а не вдає успіх")
ok(len(CREATED) == 2, "рівно дві спроби, без нескінченного циклу")
ok("Мережа" in err.getvalue(), "у лог написано, що це мережа")
ok(len(FakeConn.script) == 1, "третю спробу не робив")

print("— помилка Telegram не повторюється")
CREATED.clear(); bot.net_drop()
FakeConn.script = [("http400", None), ("ok", {"m": 1})]
err = io.StringIO(); old_err, sys.stderr = sys.stderr, err
res = bot.call("answerCallbackQuery", callback_query_id="x")
sys.stderr = old_err
ok(res is None, "400 повертає None")
ok(len(CREATED) == 1, "на відмову Telegram не перез'єднується даремно")
ok("400" in err.getvalue() and "Bad Request" in err.getvalue(), "причину видно в лозі")
ok(len(FakeConn.script) == 1, "другої спроби не було")

print("— сміття замість JSON не валить бота")
bot.net_drop(); FakeConn.script = [("junk", None)]
err = io.StringIO(); old_err, sys.stderr = sys.stderr, err
res = bot.call("sendMessage", chat_id=1, text="x")
sys.stderr = old_err
ok(res is None, "пережив непрочитну відповідь")

print("— потоки беруть з'єднання зі спільної скриньки")
CREATED.clear(); bot.net_drop()
FakeConn.script = []
def worker():
    bot.call("sendMessage", chat_id=1, text="x")
    bot.call("sendMessage", chat_id=1, text="y")
ths = [threading.Thread(target=worker) for _ in range(4)]
for t in ths: t.start()
for t in ths: t.join()
ok(len(CREATED) <= 4, "8 запитів із 4 потоків — не більше 4 з'єднань (%d)"
   % len(CREATED))

print("— з'єднання переживає смерть потоку")
CREATED.clear()
for _ in range(5):
    t = threading.Thread(target=lambda: bot.call("sendMessage", chat_id=1, text="x"))
    t.start(); t.join()
ok(not CREATED, "п'ять коротких потоків поспіль — жодного нового рукостискання")

print("— скринька не роздувається")
CREATED.clear(); bot.net_drop()
def hold():
    bot.call("sendMessage", chat_id=1, text="x")
ths = [threading.Thread(target=hold) for _ in range(20)]
for t in ths: t.start()
for t in ths: t.join()
ok(len(bot._pool) <= bot.POOL_MAX,
   "у скриньці не більше %d з'єднань (%d)" % (bot.POOL_MAX, len(bot._pool)))

print("— після обриву битого з'єднання в скриньці не лишається")
CREATED.clear(); bot.net_drop()
bot.call("sendMessage", chat_id=1, text="x")          # у скриньці одне
FakeConn.script = [("boom", None), ("ok", {"ok": 1})]
bot.call("sendMessage", chat_id=1, text="x")
ok(all(not c.closed for c, _w in bot._pool), "у скриньці лише робочі з'єднання")

print("— з'єднання, що померло від простою")
import time as _tm
bot.net_drop(); CREATED.clear(); FakeConn.script = []
bot.call("sendMessage", chat_id=1, text="x")       # одне лягло у скриньку
ok(len(bot._pool) == 1, "у скриньці одне з'єднання")
# вдаємо, що воно пролежало довше за дозволене
c0, _w = bot._pool[0]
bot._pool[0] = (c0, _tm.time() - bot.POOL_IDLE_MAX - 5)
CREATED.clear()
bot.call("sendMessage", chat_id=1, text="x")
ok(len(CREATED) == 1, "протухле не чіпали, відкрили свіже")
ok(c0.closed, "протухле закрито, дескриптор не тече")

print("— після паузи вся скринька мертва, виклик усе одно проходить")
bot.net_drop(); CREATED.clear()
# кладемо у скриньку кілька свіжих на вигляд з'єднань
for _ in range(4):
    bot._pool.append((FakeConn(bot.API_HOST), _tm.time()))
ok(len(bot._pool) == 4, "у скриньці чотири з'єднання")
# усі вони відповідатимуть обривом — так поводиться закрите Telegram з'єднання
FakeConn.script = [("boom", None)] * len(bot._pool) + [("ok", {"message_id": 5})]
CREATED.clear()
res = bot.call("sendMessage", chat_id=1, text="x")
ok(res == {"message_id": 5}, "виклик виконано, а не провалено")
ok(len(CREATED) == 1, "відкрито лише одне нове з'єднання (%d)" % len(CREATED))

print("— якщо мережа справді лежить, спроб усе одно дві")
bot.net_drop(); CREATED.clear()
FakeConn.script = [("boom", None)] * 10
err = io.StringIO(); _o, sys.stderr = sys.stderr, err
res = bot.call("sendMessage", chat_id=1, text="x")
sys.stderr = _o
ok(res is None, "чесна відмова")
ok(len(CREATED) == 2, "рівно дві спроби на нових з'єднаннях (%d)" % len(CREATED))


print("— повільний виклик сам себе показує в лозі")
import io as _io, time as _t
bot.net_drop(); FakeConn.script = []
slow = FakeConn.getresponse
def slow_resp(self):
    _t.sleep(3.2)
    return slow(self)
FakeConn.getresponse = slow_resp
err = _io.StringIO(); _old, sys.stderr = sys.stderr, err
bot.call("sendPhoto", chat_id=1)
sys.stderr = _old
FakeConn.getresponse = slow
out = err.getvalue()
ok("ПОВІЛЬНО" in out and "sendPhoto" in out, "повільний виклик названо поіменно")
ok("нове з'єднання" in out, "видно, що платили за відкриття з'єднання")

print("— швидкий виклик лог не засмічує")
bot.net_drop()
err = _io.StringIO(); _old, sys.stderr = sys.stderr, err
bot.call("sendMessage", chat_id=1, text="x")
sys.stderr = _old
ok("ПОВІЛЬНО" not in err.getvalue(), "нормальна швидкість нічого не пише")

print("— IPv4 пробуємо першим")
import socket as _s
fake_info = [
    (_s.AF_INET6, _s.SOCK_STREAM, 6, "", ("2001:67c:4e8:f004::9", 443, 0, 0)),
    (_s.AF_INET, _s.SOCK_STREAM, 6, "", ("149.154.166.110", 443)),
]
_real_gai, _s.getaddrinfo = _s.getaddrinfo, lambda *a, **k: fake_info
order = [a[0] for a in bot.net_addresses("api.telegram.org", 443)]
_s.getaddrinfo = _real_gai
ok(order and order[0] == _s.AF_INET, "IPv4 стоїть перед IPv6")
ok(len(order) == 2, "IPv6 лишився запасним, не викинутий")

print("— у лозі видно час і клієнта")
buf = _io.StringIO()
ts = bot.TaggedStream(buf, "lezo")
ts.write("Бот запущений\n")
ts.write("Мережа: таймаут\n")
line = buf.getvalue().splitlines()
ok(all(l.count("[lezo]") == 1 for l in line), "кожен рядок підписано клієнтом")
ok(all(l[2] == ":" and l[5] == ":" for l in line), "кожен рядок починається з часу")

buf2 = _io.StringIO()
ts2 = bot.TaggedStream(buf2, "kontur")
ts2.write("Traceback:\n")
ts2.write("  File ")          # шматок рядка без переносу
ts2.write("bot.py\n")
got = buf2.getvalue().splitlines()
ok(len(got) == 2, "рядок, зібраний із кількох шматків, не розривається")
ok(got[1].count("[kontur]") == 1, "підпис не дублюється посеред рядка")
ok("\n\n" not in buf2.getvalue(), "порожніх рядків не додалось")


print("— стара черга натискань після простою")
SEEN = []
def fake_call(method, **p):
    SEEN.append((method, p))
    if method == "getUpdates":
        return [{"update_id": 40}, {"update_id": 41}, {"update_id": 42}]
    return {}
old_call, bot.call = bot.call, fake_call
off = bot.drop_backlog()
ok(off == 43, "рахунок починається з наступного після останнього (%s)" % off)
ok(SEEN[0][1].get("offset") == -1, "запитано тільки останнє оновлення")
ok(SEEN[0][1].get("timeout") == 0, "без очікування — старт не гальмується")

SEEN.clear()
bot.call = lambda m, **p: SEEN.append((m, p)) or []
ok(bot.drop_backlog() is None, "порожня черга не ламає старт")
bot.call = old_call

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)
