"""Аудит тендеру: короткий (у дайджесті) і детальний (за запитом)."""
import re, html
from datetime import datetime
from common import collect_docs, blocks_for, claude, MODEL_FULL, MODEL_SHORT, log
import prompts


def short_audit(t):
    parts, skipped = collect_docs(t, budget=40_000, per_doc=25_000, max_scans=0)
    text, cost = claude(prompts.SHORT, blocks_for(t, parts, skipped), MODEL_SHORT, 600)
    return text.strip(), cost


def full_audit(t):
    parts, skipped = collect_docs(t)
    log.info("Документів у аналізі: %d, пропущено: %d", len(parts), len(skipped))
    out, cost = claude(prompts.FULL + prompts.company_note(), blocks_for(t, parts, skipped), MODEL_FULL, 16000)
    s = re.search(r"<summary>(.*?)</summary>", out, re.S)
    r = re.search(r"<report>(.*?)(?:</report>|$)", out, re.S)
    summary = (s.group(1) if s else out[:1200]).strip()
    report = (r.group(1) if r else out).strip()
    if skipped:
        report += "\n\n---\n**Не проаналізовано:** " + "; ".join(skipped)
    return summary, report, cost


CSS = """body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:900px;margin:0 auto;padding:16px;line-height:1.5;color:#1a1a1a}
h1{font-size:1.5em;border-bottom:2px solid #333}h2{font-size:1.25em;margin-top:1.6em;border-bottom:1px solid #ccc}h3{font-size:1.05em}
table{border-collapse:collapse;width:100%;display:block;overflow-x:auto;margin:8px 0}th,td{border:1px solid #bbb;padding:6px 8px;vertical-align:top;font-size:.92em}
th{background:#f0f0f0}.meta{color:#666;font-size:.85em}
@media (prefers-color-scheme:dark){body{background:#161616;color:#e6e6e6}th{background:#2a2a2a}th,td{border-color:#444}.meta{color:#999}}"""


def to_html(md_text, t):
    import markdown
    body = markdown.markdown(md_text, extensions=["tables", "sane_lists"])
    tid = t.get("tenderID", "")
    return (f"<!doctype html><html lang='uk'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Аудит {html.escape(tid)}</title><style>{CSS}</style></head><body>"
            f"<p class='meta'>Prozorro: <a href='https://prozorro.gov.ua/tender/{tid}'>{tid}</a> · "
            f"сформовано {datetime.now().strftime('%d.%m.%Y %H:%M')} · автоматичний аудит ШІ, перевірте ключові висновки</p>"
            f"{body}</body></html>").encode("utf-8")
