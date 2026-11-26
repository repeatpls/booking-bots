# -*- coding: utf-8 -*-
"""Відповідь таблиці перевіряється по суті, а не за кодом 200.

Найнебезпечніший випадок: доступ «Тільки я». Google мовчки веде на
сторінку входу й віддає 200 — і запис зникає без жодної помилки.
"""
import io, json, os, shutil, sys, tempfile
R = []
def ok(c, l): R.append(bool(c)); print(("  OK   " if c else "  ПАДІННЯ ") + l)

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
W = tempfile.mkdtemp()
shutil.copy(os.path.join(SRC, "bot.py"), W)
shutil.copy(os.path.join(SRC, "slots.py"), W)
cfg = json.load(open(os.path.join(SRC, "templates/barbershop.json"), encoding="utf-8"))
cfg["token"] = "000:TEST"; cfg["admin_ids"] = [777]
cfg["sheet_webhook"] = "https://script.google.com/macros/s/TEST/exec"
cfg["sheet_secret"] = "kljuc"
json.dump(cfg, open(W + "/config.json", "w", encoding="utf-8"), ensure_ascii=False)
sys.argv = [sys.argv[0], W]; sys.path.insert(0, W)
import bot
bot.call = lambda m, **p: {"message_id": 1}
bot.init_db()


class Resp:
    def __init__(self, body, url="https://script.google.com/.../exec"):
        self._b = body.encode("utf-8"); self._u = url
    def read(self): return self._b
    def geturl(self): return self._u
    def __enter__(self): return self
    def __exit__(self, *a): return False


def serve(body, url="https://script.google.com/.../exec"):
    bot.urllib.request.urlopen = lambda req, timeout=None: Resp(body, url)


def fails(label):
    try:
        bot.sheet_post('{"x":1}')
        ok(False, label + " — помилки не було, рядок би зник")
    except OSError as e:
        ok(True, label + ": %s" % str(e)[:70])


print("— нормальна відповідь")
serve('{"ok":true}')
ok(bot.sheet_post('{"x":1}') is True, "скрипт прийняв рядок")

print("— доступ «Тільки я»: Google веде на вхід")
serve("<html><head><title>Увійти</title></head></html>",
      "https://accounts.google.com/ServiceLogin?...")
fails("сторінка входу розпізнана")

print("— html без редиректу теж не успіх")
serve("<!DOCTYPE html><html>щось зовсім інше</html>")
fails("html замість JSON")

print("— ключі не збіглись")
serve('{"ok":false,"error":"wrong secret"}')
try:
    bot.sheet_post('{"x":1}')
    ok(False, "відмову пропустили")
except OSError as e:
    ok("ключ" in str(e), "сказано саме про ключ: %s" % e)

print("— помилка всередині скрипта")
serve('{"ok":false,"error":"TypeError: cannot read"}')
fails("скрипт упав")

print("— сміття замість відповіді")
serve("не json і не html")
fails("незрозуміла відповідь")

print("— невдача лишає рядок у черзі")
with bot.db() as conn:
    conn.execute("DELETE FROM outbox")
    conn.execute("INSERT INTO outbox(payload, created) VALUES('{}','x')")
serve('{"ok":false,"error":"wrong secret"}')
err = io.StringIO(); _o, sys.stderr = sys.stderr, err
bot.sheet_flush()
sys.stderr = _o
with bot.db() as conn:
    left = conn.execute("SELECT COUNT(*) c FROM outbox").fetchone()["c"]
ok(left == 1, "рядок лишився в черзі, а не зник")
ok("ключ" in err.getvalue(), "причина в лозі: %s" % err.getvalue().strip()[:60])

print("— коли полагодили, черга йде далі")
serve('{"ok":true}')
ok(bot.sheet_flush() == 1, "рядок нарешті пішов")
with bot.db() as conn:
    ok(conn.execute("SELECT COUNT(*) c FROM outbox").fetchone()["c"] == 0,
       "черга спорожніла")

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)
