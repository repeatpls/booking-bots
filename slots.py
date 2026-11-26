# -*- coding: utf-8 -*-
"""Правила розкладу: графіки, тривалість послуг, вільні години.

Цей модуль — єдине місце, де вирішується, коли майстер вільний.
Ним користуються і бот (коли пропонує години клієнту), і сторінка
розкладу. Якби кожен рахував сам, вони рано чи пізно розійшлись би,
і клієнт бачив би одні години, а майстер — інші.

Функції навмисно чисті: ніякої бази, ніяких глобальних змінних.
Усе потрібне передається аргументами.
"""
import math
from datetime import datetime, timedelta


def masters_of(cfg):
    return cfg.get("masters") or []


def master_by_id(cfg, mid):
    for m in masters_of(cfg):
        if m.get("id") == mid:
            return m
    return None


def service_by_id(cfg, sid):
    for s in cfg.get("services", []):
        if s.get("id") == sid:
            return s
    return None


def step_minutes(cfg):
    return int(cfg.get("slot_minutes", 60))


def duration_of(cfg, sid):
    s = service_by_id(cfg, sid)
    if s and s.get("duration"):
        return int(s["duration"])
    return step_minutes(cfg)


def schedule_of(cfg, m):
    """Графік майстра, з відкотом до загального графіка закладу."""
    if m is None:
        m = {}
    return (
        m.get("workdays", cfg.get("workdays", [0, 1, 2, 3, 4])),
        m.get("work_start", cfg.get("work_start", "09:00")),
        m.get("work_end", cfg.get("work_end", "19:00")),
        set(m.get("days_off", [])),
    )


def free_times(cfg, iso_date, master, sid, busy, now_dt, tzinfo=None):
    """Вільні початки для послуги sid у цього майстра на вказану дату.

    busy — множина зайнятих початків у вигляді «2026-11-25T12:00».
    now_dt — поточний час; усе, що раніше за нього плюс запас, не пропонуємо.
    """
    days, w_start, w_end, days_off = schedule_of(cfg, master)
    day = datetime.fromisoformat(iso_date)
    if day.weekday() not in days or iso_date in days_off:
        return []

    sh, sm = [int(x) for x in w_start.split(":")]
    eh, em = [int(x) for x in w_end.split(":")]
    cur = day.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = day.replace(hour=eh, minute=em, second=0, microsecond=0)
    if tzinfo:
        cur = cur.replace(tzinfo=tzinfo)
        end = end.replace(tzinfo=tzinfo)

    step = step_minutes(cfg)
    dur = duration_of(cfg, sid)
    need = max(1, math.ceil(dur / step))
    earliest = now_dt + timedelta(minutes=int(cfg.get("min_lead_minutes", 90)))

    out = []
    while cur + timedelta(minutes=dur) <= end:
        if cur > earliest:
            free = True
            for i in range(need):
                t = (cur + timedelta(minutes=step * i)).isoformat(timespec="minutes")
                if t in busy:
                    free = False
                    break
            if free:
                out.append((cur.isoformat(timespec="minutes"),
                            cur.strftime("%H:%M")))
        cur += timedelta(minutes=step)
    return out
