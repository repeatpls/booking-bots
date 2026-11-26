# -*- coding: utf-8 -*-
"""Фото вивантажуються один раз при старті, клієнт чекає нуль секунд."""
import json, os, shutil, sys, tempfile
R = []
def ok(c, l): R.append(bool(c)); print(("  OK   " if c else "  ПАДІННЯ ") + l)

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію
W = tempfile.mkdtemp()
shutil.copy(os.path.join(SRC, "bot.py"), W)
shutil.copy(os.path.join(SRC, "slots.py"), W)
cfg = json.load(open(os.path.join(SRC, "templates/barbershop.json"), encoding="utf-8"))
cfg["token"] = "000:TEST"; cfg["admin_ids"] = [777]
json.dump(cfg, open(W + "/config.json", "w", encoding="utf-8"), ensure_ascii=False)

os.makedirs(W + "/photos/majster1")
PNG = (b"\x89PNG\r\n\x1a\n" + b"0" * 400)
for p in ("photos/majster1/1.jpg", "photos/majster1/2.jpg",
          "photos/spilne.png", "cover.jpg"):
    open(os.path.join(W, p), "wb").write(PNG)
open(os.path.join(W, "photos/нефото.txt"), "w").write("не картинка")

sys.argv = [sys.argv[0], W]; sys.path.insert(0, W)
import bot
UP, CALLS = [], []
def fake_multipart(method, fields, files):
    UP.append((method, fields.get("chat_id"), list(files)[0]))
    return {"message_id": 100 + len(UP),
            "photo": [{"file_id": "FID%d" % len(UP)}]}
bot.multipart = fake_multipart
bot.call = lambda m, **p: CALLS.append((m, p)) or {"username": "t", "message_id": 1}
bot.init_db()

print("— що саме прогріваємо")
files = bot.all_photo_files()
ok(len(files) == 4, "знайдено 4 картинки, а не 5 (txt не рахується): %d" % len(files))
ok(any(f.endswith("cover.jpg") for f in files), "обкладинка теж у списку")

print("— перший старт")
bot.warm_photos()
ok(len(UP) == 4, "вивантажено всі 4 фото один раз (%d)" % len(UP))
ok(all(c == "777" for _m, c, _f in UP), "усе пішло адміну, не клієнту")
ok(sum(1 for m, p in CALLS if m == "deleteMessage") == 4,
   "усі службові повідомлення прибрано з чату адміна")

print("— другий старт нічого не вивантажує")
UP.clear(); CALLS.clear()
bot.warm_photos()
ok(not UP, "кеш тримається між запусками, вивантажень 0")

print("— клієнт отримує фото миттєво, без вивантаження")
UP.clear(); CALLS.clear()
path = os.path.join(W, "photos/majster1/1.jpg")
path2 = os.path.join(W, "photos/majster1/2.jpg")
bot.send_album(555, "mcard:majster1", [("file", path), ("file", path2)], "Текст")
ok(not UP, "жодного вивантаження на натискання клієнта")
album = [p for m, p in CALLS if m == "sendMediaGroup"]
ok(album, "альбом надіслано")
media = json.loads(album[0]["media"]) if isinstance(album[0].get("media"), str) \
    else album[0]["media"]
ok(all(str(x["media"]).startswith("FID") for x in media),
   "пішли збережені номери фото, а не самі файли")

print("— те саме фото на іншому екрані теж не вивантажується")
UP.clear(); CALLS.clear()
bot.send_image(555, "cover", ("file", os.path.join(W, "cover.jpg")), "Вітаємо")
ok(not UP, "обкладинка взялася з кешу")

print("— замінене фото підхоплюється саме")
UP.clear(); CALLS.clear()
os.utime(path, (os.path.getmtime(path) + 100, os.path.getmtime(path) + 100))
bot.send_album(555, "mcard:majster1", [("file", path), ("file", path2)], "Текст")
ok(len(UP) == 1, "вивантажено рівно замінене фото, решта з кешу (%d)" % len(UP))

print("— без admin_ids бот не падає")
bot.CFG["admin_ids"] = []
with bot.db() as conn:
    conn.execute("DELETE FROM photos")
try:
    bot.warm_photos()
    ok(True, "пережив порожній admin_ids і попередив у лозі")
except Exception as e:
    ok(False, "упав: %r" % e)

print("\n%d з %d" % (sum(R), len(R)))
sys.exit(0 if all(R) else 1)
