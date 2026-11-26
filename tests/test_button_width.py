# -*- coding: utf-8 -*-
"""Обхід усіх екранів і перевірка, що жоден напис на кнопці не задовгий."""
import json, os, shutil, sys, tempfile

LIMIT = 34
SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корінь репозиторію

def audit(cfg_path, title):
    W = tempfile.mkdtemp()
    shutil.copy(os.path.join(SRC, "bot.py"), W)
    shutil.copy(os.path.join(SRC, "slots.py"), W)
    c = json.load(open(cfg_path, encoding="utf-8"))
    c["token"] = "000:TEST"; c["admin_ids"] = [1]
    json.dump(c, open(W + "/config.json", "w", encoding="utf-8"), ensure_ascii=False)
    sys.argv = [sys.argv[0], W]
    for mod in [m for m in list(sys.modules) if m == "bot"]:
        del sys.modules[mod]
    sys.path.insert(0, W)
    import bot
    S = []
    bot.call = lambda m, **p: (S.append((m, p)) or {"username": "t"})
    bot.init_db()
    seen, bad = set(), []

    def grab():
        out = []
        for m, p in S:
            rm = p.get("reply_markup") or {}
            for r in rm.get("inline_keyboard", []):
                for b in r:
                    out.append((b.get("text", ""), b.get("callback_data", ""), len(r)))
        return out

    def press(data):
        try:
            bot.handle_callback({"id": "1", "data": data,
                                 "message": {"chat": {"id": 7}, "message_id": 1}})
        except Exception as e:
            bad.append(("помилка на %s: %s" % (data, e), 0))

    bot.handle_message({"chat": {"id": 7}, "from": {"username": "x"}, "text": "/start"})
    queue = [d for _, d, _ in grab() if d and d != "nop"]
    while queue:
        d = queue.pop(0)
        if d in seen or d.startswith(("day:", "time:", "cal:")):
            continue
        seen.add(d)
        before = len(S)
        press(d)
        for text, data, percol in grab()[before:]:
            if data and data not in seen and data != "nop" and not data.startswith(("day:", "time:", "cal:")):
                queue.append(data)
    widest = {}
    for text, data, percol in grab():
        if percol > 1 or not text.strip():
            continue
        if len(text) > LIMIT:
            bad.append((text, len(text)))
        widest[text] = len(text)

    print("\n%s — екранів пройдено: %d" % (title, len(seen)))
    top = sorted(widest.items(), key=lambda x: -x[1])[:5]
    for t, n in top:
        print("   %3d  %s" % (n, t))
    if bad:
        print("   ЗАДОВГІ (>%d):" % LIMIT)
        for t, n in bad:
            print("     %3d  %s" % (n, t))
    else:
        print("   усе вміщається")
    return not bad

import sys as _s
paths = [("/mnt/user-data/outputs/kontur_230039.json", "ТАТУ (новий)"),
         ("/home/claude/bot/config.json", "БАРБЕРШОП")]
res = [audit(p, t) for p, t in paths]
print("\n" + ("ВСЕ ВМІЩАЄТЬСЯ" if all(res) else "Є ЗАДОВГІ НАПИСИ"))
_s.exit(0 if all(res) else 1)
