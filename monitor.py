#!/usr/bin/env python3
"""Моніторинг тендерів Prozorro -> Telegram, щоденний дайджест.
Запуск:  python monitor.py          (працює постійно, дайджест щодня о SEND_HOUR за Києвом)
         python monitor.py --once   (один дайджест зараз: cron / GitHub Actions)
         python monitor.py --test   (тестове повідомлення в Telegram)
"""
import os, re, sys, time, html, sqlite3, logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests

# ---------- НАЛАШТУВАННЯ ----------
MIN_AMOUNT = 5_000_000
CPV = ["31120000-3", "31121000-0", "31121100-1", "34120000-4",
       "34130000-7", "34140000-0", "34200000-9", "44210000-5"]
STATUSES = {"active.enquiries", "active.tendering"}   # лише ті, де ще можна подати пропозицію
# ----------------------------------

API = "https://public-api.prozorro.gov.ua/api/2.5"
TG_TOKEN = os.environ.get("TG_TOKEN", "").strip()
TG_CHAT = os.environ.get("TG_CHAT_ID", "").strip().strip('"')
DB_PATH = os.getenv("DB_PATH", "state.db")
SEND_HOUR = int(os.getenv("SEND_HOUR", "9"))       # година відправки за Києвом
KYIV = ZoneInfo("Europe/Kyiv")
FIRST_RUN_HOURS = int(os.getenv("FIRST_RUN_HOURS", "24"))

CPV_PREFIXES = tuple(c.split("-")[0].rstrip("0") for c in CPV)  # 31120000 -> 3112 (включно з дочірніми кодами)
PROC = {
    "aboveThreshold": "Відкриті торги з особливостями", "aboveThresholdUA": "Відкриті торги",
    "aboveThresholdEU": "Відкриті торги (англ.)", "belowThreshold": "Спрощена закупівля",
    "competitiveDialogueUA": "Конкурентний діалог", "competitiveDialogueEU": "Конкурентний діалог (англ.)",
    "priceQuotation": "Запит ціни пропозицій", "simple.defense": "Спрощені торги (оборона)",
    "esco": "ESCO", "closeFrameworkAgreementUA": "Рамкова угода",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("monitor")
S = requests.Session()
S.headers["User-Agent"] = "tender-monitor/1.0"

db = sqlite3.connect(DB_PATH)
db.execute("CREATE TABLE IF NOT EXISTS seen(id TEXT PRIMARY KEY, ts TEXT)")
db.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")


def meta_get(k):
    r = db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else None

def meta_set(k, v):
    db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, v)); db.commit()

def is_seen(i):
    return db.execute("SELECT 1 FROM seen WHERE id=?", (i,)).fetchone() is not None

def mark(i):
    db.execute("INSERT OR IGNORE INTO seen VALUES(?,?)", (i, datetime.now(timezone.utc).isoformat())); db.commit()


def get(url, params=None):
    for i in range(5):
        try:
            r = S.get(url, params=params, timeout=30)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.warning("GET %s: %s (спроба %d)", url, e, i + 1)
            time.sleep(2 ** i)
    raise RuntimeError(f"API недоступне: {url}")


def changed_since(since):
    """Тендери, змінені після since (від найновіших до старіших)."""
    url, params = f"{API}/tenders", {"descending": 1, "limit": 100, "opt_fields": "status,value"}
    while True:
        d = get(url, params)
        items = d.get("data", [])
        if not items:
            return
        for t in items:
            if datetime.fromisoformat(t["dateModified"]) < since:
                return
            yield t
        nxt = (d.get("next_page") or {}).get("uri")
        if not nxt:
            return
        url, params = nxt, None


def match(t):
    amount = (t.get("value") or {}).get("amount") or 0
    if t.get("status") not in STATUSES or amount < MIN_AMOUNT:
        return None
    cpvs = {(i.get("classification") or {}).get("id", "") for i in t.get("items") or []}
    hit = sorted(c for c in cpvs if c and c.split("-")[0].startswith(CPV_PREFIXES))
    return hit or None


def money(v):
    return f"{v:,.2f}".replace(",", " ").replace(".", ",")

def dt(s):
    return datetime.fromisoformat(s).strftime("%d.%m.%Y %H:%M") if s else "—"

def card(n, t, hit):
    e = lambda s: html.escape(str(s)) if s else "—"
    pe = t.get("procuringEntity") or {}
    v = t.get("value") or {}
    items = t.get("items") or []
    cpv_main = (items[0].get("classification") or {}) if items else {}
    title = (t.get("title") or "")[:400]
    tid = t.get("tenderID", "")
    return (
        f"<b>{n}. {e(title)}</b>\n"
        f"💰 <b>{money(v.get('amount', 0))} {e(v.get('currency'))}</b>"
        f"{' з ПДВ' if v.get('valueAddedTaxIncluded') else ''}\n"
        f"🏛 {e(pe.get('name'))} (ЄДРПОУ {e((pe.get('identifier') or {}).get('id'))})\n"
        f"📍 {e((pe.get('address') or {}).get('region'))}\n"
        f"📋 {e(PROC.get(t.get('procurementMethodType'), t.get('procurementMethodType')))}"
        f"{' · лотів: ' + str(len(t['lots'])) if t.get('lots') else ''}\n"
        f"🏷 {e(', '.join(hit))}\n"
        f"❓ Уточнення до: {dt((t.get('enquiryPeriod') or {}).get('endDate'))}\n"
        f"⏰ <b>Пропозиції до: {dt((t.get('tenderPeriod') or {}).get('endDate'))}</b>\n"
        f"🔗 https://prozorro.gov.ua/tender/{tid}"
    )


def tg(text):
    for _ in range(3):
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                          json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=30)
        if r.status_code == 429:
            time.sleep(r.json().get("parameters", {}).get("retry_after", 5)); continue
        if r.ok:
            break
        err = r.json().get("description", r.text)
        if "parse entities" in err:          # помилка розмітки -> надсилаємо простим текстом
            plain = re.sub(r"<[^>]+>", "", text)
            r2 = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                               json={"chat_id": TG_CHAT, "text": html.unescape(plain),
                                     "disable_web_page_preview": True}, timeout=30)
            if r2.ok:
                break
            err = r2.json().get("description", r2.text)
        raise RuntimeError(f"Telegram відповів: {err} (chat_id={TG_CHAT!r})")
    time.sleep(1.1)   # ліміт Telegram ~1 повід./сек у чат


def send_digest(found):
    date = datetime.now(KYIV).strftime("%d.%m.%Y")
    if not found:
        tg(f"📋 <b>Тендери Prozorro за {date}</b>\n\nНових тендерів за фільтром немає."); return
    found.sort(key=lambda x: (x[0].get("tenderPeriod") or {}).get("endDate") or "")  # найближчий дедлайн першим
    msg = f"📋 <b>Тендери Prozorro за {date}: {len(found)}</b>\n"
    for n, (t, hit) in enumerate(found, 1):
        block = "\n" + card(n, t, hit) + "\n"
        if len(msg) + len(block) > 4000:           # ліміт Telegram 4096 символів
            tg(msg); msg = ""
        msg += block
    if msg:
        tg(msg)


def run_once():
    now = datetime.now(timezone.utc)
    last = meta_get("last_check")
    since = datetime.fromisoformat(last) - timedelta(minutes=5) if last else now - timedelta(hours=FIRST_RUN_HOURS)
    scanned = checked = 0
    found = []
    for s in changed_since(since):
        scanned += 1
        if is_seen(s["id"]):
            continue
        if "status" in s and s["status"] not in STATUSES:
            continue
        if "value" in s and (s["value"].get("amount") or 0) < MIN_AMOUNT:
            continue
        t = get(f"{API}/tenders/{s['id']}")["data"]
        checked += 1
        hit = match(t)
        if hit:
            found.append((t, hit))
    send_digest(found)
    for t, _ in found:
        mark(t["id"])
    meta_set("last_check", now.isoformat())
    log.info("Переглянуто %d, перевірено %d, у дайджесті %d", scanned, checked, len(found))


def sleep_until_send_hour():
    now = datetime.now(KYIV)
    nxt = now.replace(hour=SEND_HOUR, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    log.info("Наступний дайджест: %s", nxt.strftime("%d.%m.%Y %H:%M"))
    time.sleep((nxt - now).total_seconds())


if __name__ == "__main__":
    if not TG_TOKEN or not TG_CHAT:
        sys.exit("Задайте змінні середовища TG_TOKEN і TG_CHAT_ID")
    if "--test" in sys.argv:
        tg("✅ Моніторинг Prozorro підключено"); sys.exit()
    if "--once" in sys.argv:
        run_once(); sys.exit()
    while True:
        sleep_until_send_hour()
        try:
            run_once()
        except Exception as e:
            log.exception("Помилка: %s", e)
