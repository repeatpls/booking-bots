#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Заводить нового клієнта з шаблону його ніші.

    python new_client.py              показати, які шаблони є
    python new_client.py lezo barbershop
    python new_client.py vesna salon

Створює clients/<id>/config.json із заготовкою ніші. Код не чіпається:
bot.py один на всіх клієнтів, різниця тільки в конфігу.
"""

import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(HERE, "templates")
CLIENTS = os.path.join(HERE, "clients")


def available():
    if not os.path.isdir(TEMPLATES):
        return []
    return sorted(f[:-5] for f in os.listdir(TEMPLATES) if f.endswith(".json"))


def describe(name):
    try:
        c = json.load(open(os.path.join(TEMPLATES, name + ".json"), encoding="utf-8"))
    except Exception:
        return ""
    bits = ["%d послуг" % len(c.get("services", []))]
    ms = c.get("masters") or []
    bits.append("%d майстрів" % len(ms) if ms else "без майстрів")
    bits.append("крок %d хв" % c.get("slot_minutes", 60))
    return ", ".join(bits)


def show_templates():
    names = available()
    if not names:
        sys.exit("Теки templates/ нема або вона порожня.")
    print("Доступні шаблони:\n")
    for n in names:
        print("  %-14s %s" % (n, describe(n)))
    print("\nЗапуск:  python new_client.py <id клієнта> <шаблон>")


def main():
    if len(sys.argv) < 2:
        show_templates()
        return

    name = sys.argv[1].strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]+", name):
        sys.exit("Імʼя клієнта тільки латиницею, цифрами, дефісом чи підкресленням.")

    names = available()
    tpl = sys.argv[2].strip().lower() if len(sys.argv) > 2 else None
    if not tpl:
        show_templates()
        sys.exit("\nВкажіть шаблон другим словом: python new_client.py %s <шаблон>" % name)
    if tpl not in names:
        sys.exit("Шаблону «%s» нема. Є: %s" % (tpl, ", ".join(names)))

    path = os.path.join(CLIENTS, name)
    cfg_path = os.path.join(path, "config.json")
    if os.path.exists(cfg_path):
        sys.exit("Клієнт %s уже існує: %s" % (name, cfg_path))

    os.makedirs(os.path.join(path, "photos"), exist_ok=True)
    shutil.copy(os.path.join(TEMPLATES, tpl + ".json"), cfg_path)

    print("Створено %s (шаблон: %s)\n" % (cfg_path, tpl))
    print("Далі:")
    print("  1. Власник заводить бота в @BotFather і дає токен — вставте його у config.json")
    print("  2. Впишіть назву, контакти, послуги, ціни та майстрів клієнта")
    print("  3. Фото майстрів покладіть у %s" % os.path.join(path, "photos"))
    print("  4. Запустіть:  python bot.py clients/%s" % name)
    print("  5. Напишіть боту /id, впишіть число в admin_ids, перезапустіть")
    print("  6. Додайте рядок у реєстр клієнтів, ніша: %s" % tpl)


if __name__ == "__main__":
    main()
