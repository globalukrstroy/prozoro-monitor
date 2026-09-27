#!/usr/bin/env python3
"""Обробка повідомлень Telegram (запуск кожні 10 хв у GitHub Actions).
Кнопка «Детальний аудит» або посилання/номер тендеру -> детальний аудит у відповідь."""
import re, sys, html, traceback
from common import tg_call, tg_send, tg_send_file, tender, resolve_id, TG_TOKEN, TG_CHAT, ANTHROPIC_KEY, log, UA_RE
from audit import full_audit, to_html

HELP = ("🤖 <b>Аудит тендерів Prozorro</b>\n\n"
        "Надішліть посилання на тендер або його номер, наприклад:\n"
        "<code>UA-2026-09-15-001234-a</code>\n\n"
        "Або натисніть «🔍 Детальний аудит» під тендером у дайджесті.\n"
        "Відповідь надходить протягом 5–20 хвилин: бот перевіряє повідомлення раз на 10 хвилин.")


def process(tid):
    t = tender(tid)
    ua = t.get("tenderID", tid)
    tg_send(f"⏳ Готую детальний аудит <b>{html.escape(ua)}</b>…\nЦе займе 3–7 хвилин.")
    summary, report, cost = full_audit(t)
    head = (f"📑 <b>Аудит {html.escape(ua)}</b>\n{html.escape((t.get('title') or '')[:300])}\n"
            f"🔗 https://prozorro.gov.ua/tender/{ua}\n\n")
    tg_send(head + html.escape(summary) + f"\n\n<i>Повний звіт — у файлі нижче. Вартість аналізу ~${cost:.2f}</i>")
    tg_send_file(f"audit_{ua}.html", to_html(report, t), caption=f"Повний аудит {ua}")


def main():
    ups = tg_call("getUpdates", {"timeout": 0, "allowed_updates": ["message", "callback_query"]}) or []
    if not ups:
        log.info("Нових повідомлень немає"); return
    tg_call("getUpdates", {"offset": ups[-1]["update_id"] + 1, "timeout": 0})   # підтверджуємо, щоб не обробити двічі
    jobs = []
    for u in ups:
        if "callback_query" in u:
            q = u["callback_query"]
            if str(((q.get("message") or {}).get("chat") or {}).get("id")) != TG_CHAT: continue
            try: tg_call("answerCallbackQuery", {"callback_query_id": q["id"], "text": "Прийнято"})
            except Exception: pass
            if (q.get("data") or "").startswith("a:"): jobs.append(q["data"][2:])
        elif "message" in u:
            m = u["message"]
            if str(m["chat"]["id"]) != TG_CHAT: continue
            text = m.get("text") or m.get("caption") or ""
            if text.startswith(("/start", "/help")) or not text.strip():
                tg_send(HELP); continue
            tid = resolve_id(text)
            if tid: jobs.append(tid)
            elif UA_RE.search(text): tg_send("❌ Не знайшов такий тендер у Prozorro. Перевірте номер.")
            else: tg_send(HELP)
    for tid in dict.fromkeys(jobs):
        if not ANTHROPIC_KEY:
            tg_send("❌ Аудит недоступний: не задано ключ ANTHROPIC_API_KEY."); break
        try:
            process(tid)
        except Exception as e:
            log.error(traceback.format_exc())
            tg_send(f"❌ Помилка аудиту: {html.escape(str(e))[:500]}")


if __name__ == "__main__":
    if not TG_TOKEN or not TG_CHAT:
        sys.exit("Задайте TG_TOKEN і TG_CHAT_ID")
    main()
