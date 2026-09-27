#!/usr/bin/env python3
"""Моніторинг Prozorro: дайджест + короткий аудит + кнопка детального аудиту.
Запуск: python monitor.py --once   (GitHub Actions, Пн/Ср/Пт)
        python monitor.py --test   (тестове повідомлення)"""
import os, sys, html, sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from common import get, tender, tg_send, dt, API, TG_TOKEN, TG_CHAT, ANTHROPIC_KEY, log
from audit import short_audit

from directions import DIRECTIONS
MAX_SHORT_AUDITS = int(os.getenv("MAX_SHORT_AUDITS", "15"))   # ліміт коротких аудитів за запуск

for d in DIRECTIONS:
    d["prefixes"] = tuple(c.split("-")[0].rstrip("0") for c in d["cpv"])
STATUSES = {s for d in DIRECTIONS for s in d["statuses"]}
MIN_AMOUNT = min(d["min_amount"] for d in DIRECTIONS)

DB_PATH = os.getenv("DB_PATH", "state.db")
FIRST_RUN_HOURS = int(os.getenv("FIRST_RUN_HOURS", "72"))
KYIV = ZoneInfo("Europe/Kyiv")
PROC = {"aboveThreshold": "Відкриті торги з особливостями", "aboveThresholdUA": "Відкриті торги",
        "aboveThresholdEU": "Відкриті торги (англ.)", "belowThreshold": "Спрощена закупівля",
        "competitiveDialogueUA": "Конкурентний діалог", "priceQuotation": "Запит ціни пропозицій",
        "simple.defense": "Спрощені торги (оборона)", "esco": "ESCO", "closeFrameworkAgreementUA": "Рамкова угода"}

db = sqlite3.connect(DB_PATH)
db.execute("CREATE TABLE IF NOT EXISTS seen(id TEXT PRIMARY KEY, ts TEXT)")
db.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
meta_get = lambda k: (db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone() or [None])[0]
def meta_set(k, v): db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, v)); db.commit()
is_seen = lambda i: db.execute("SELECT 1 FROM seen WHERE id=?", (i,)).fetchone() is not None
def mark(i): db.execute("INSERT OR IGNORE INTO seen VALUES(?,?)", (i, datetime.now(timezone.utc).isoformat())); db.commit()


def changed_since(since):
    url, params = f"{API}/tenders", {"descending": 1, "limit": 100, "opt_fields": "status,value"}
    while True:
        d = get(url, params)
        items = d.get("data", [])
        if not items: return
        for t in items:
            if datetime.fromisoformat(t["dateModified"]) < since: return
            yield t
        nxt = (d.get("next_page") or {}).get("uri")
        if not nxt: return
        url, params = nxt, None


def match(t):
    """Повертає (напрям, коди ДК) для першого відповідного напряму або None."""
    amount = (t.get("value") or {}).get("amount") or 0
    region = ((t.get("procuringEntity") or {}).get("address") or {}).get("region") or ""
    cpvs = {(i.get("classification") or {}).get("id", "") for i in t.get("items") or []}
    for d in DIRECTIONS:
        if t.get("status") not in d["statuses"] or amount < d["min_amount"]:
            continue
        if d.get("max_amount") and amount > d["max_amount"]:
            continue
        if d["regions"] and not any(r.lower() in region.lower() for r in d["regions"]):
            continue
        hit = sorted(c for c in cpvs if c and (c.split("-")[0].startswith(d["prefixes"]) or c in d.get("exact", [])))
        if hit:
            return d, hit
    return None


def money(v): return f"{v:,.0f}".replace(",", " ")


def card(n, t, hit):
    e = lambda s: html.escape(str(s)) if s else "—"
    pe, v = t.get("procuringEntity") or {}, t.get("value") or {}
    return (f"<b>{n}. {e((t.get('title') or '')[:400])}</b>\n"
            f"💰 <b>{money(v.get('amount', 0))} {e(v.get('currency'))}</b>{' з ПДВ' if v.get('valueAddedTaxIncluded') else ''}\n"
            f"🏛 {e(pe.get('name'))} (ЄДРПОУ {e((pe.get('identifier') or {}).get('id'))})\n"
            f"📍 {e((pe.get('address') or {}).get('region'))}\n"
            f"📋 {e(PROC.get(t.get('procurementMethodType'), t.get('procurementMethodType')))}\n"
            f"🏷 {e(', '.join(hit[:5]))}{' …' if len(hit) > 5 else ''}\n"
            f"⏰ <b>Подання до: {dt((t.get('tenderPeriod') or {}).get('endDate'))}</b>\n"
            f"🔗 https://prozorro.gov.ua/tender/{t.get('tenderID', '')}")


def run_once():
    now = datetime.now(timezone.utc)
    last = meta_get("last_check")
    since = datetime.fromisoformat(last) - timedelta(minutes=5) if last else now - timedelta(hours=FIRST_RUN_HOURS)
    found = []
    for s in changed_since(since):
        if is_seen(s["id"]): continue
        if "status" in s and s["status"] not in STATUSES: continue
        if "value" in s and (s["value"].get("amount") or 0) < MIN_AMOUNT: continue
        t = tender(s["id"])
        m = match(t)
        if m: found.append((t, m[0], m[1]))
    date = datetime.now(KYIV).strftime("%d.%m.%Y")
    if not found:
        tg_send(f"📋 <b>Тендери Prozorro, {date}</b>\n\nНових тендерів за фільтрами немає.")
    else:
        tg_send(f"📋 <b>Тендери Prozorro, {date}: {len(found)}</b>\n"
                f"Натисніть «🔍 Детальний аудит» під тендером або надішліть боту посилання на будь-який тендер.")
    total, n = 0.0, 0
    for d in DIRECTIONS:
        group = [(t, hit) for t, dd, hit in found if dd is d]
        if not group: continue
        group.sort(key=lambda x: (x[0].get("tenderPeriod") or {}).get("endDate") or "")
        tg_send(f"<b>{html.escape(d['name'])}: {len(group)}</b>")
        for i, (t, hit) in enumerate(group, 1):
            n += 1
            text = card(i, t, hit)
            if ANTHROPIC_KEY and n <= MAX_SHORT_AUDITS:
                try:
                    a, cost = short_audit(t); total += cost
                    text += "\n\n🔍 <b>Експрес-оцінка:</b>\n" + html.escape(a)
                except Exception as e:
                    log.warning("short audit %s: %s", t.get("tenderID"), e)
            tg_send(text, buttons=[[{"text": "🔍 Детальний аудит", "callback_data": f"a:{t['id']}"}]])
            mark(t["id"])
    meta_set("last_check", now.isoformat())
    log.info("Надіслано %d, витрати на аудит ~$%.2f", len(found), total)


if __name__ == "__main__":
    if not TG_TOKEN or not TG_CHAT:
        sys.exit("Задайте TG_TOKEN і TG_CHAT_ID")
    if "--test" in sys.argv:
        tg_send("✅ Моніторинг Prozorro підключено"); sys.exit()
    run_once()
