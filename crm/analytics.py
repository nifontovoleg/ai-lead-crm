import json
from pathlib import Path
from datetime import datetime, timezone
from html import escape
import pandas as pd

from .models import Lead, priority, ACTIVE_STATUSES


def summarize(rows, threshold, scorer_version):
    frame = pd.DataFrame(rows)
    if frame.empty:
        frame = pd.DataFrame(columns=["Статус", "Источник", "Бюджет"])
    tiers = {"Горячий": 0, "Тёплый": 0, "Холодный": 0}
    fresh = 0
    for row in rows:
        try:
            lead = Lead.from_row(row)
            if lead.status not in ACTIVE_STATUSES:
                continue
            if row.get("Fingerprint") != lead.fingerprint(scorer_version):
                continue
            score = int(row["AI балл"])
            if not 0 <= score <= 100:
                continue
            tiers[priority(score, threshold)] += 1
            fresh += 1
        except (ValueError, TypeError, KeyError):
            continue
    status_counts = frame["Статус"].value_counts().to_dict()
    sources = frame["Источник"].value_counts().to_dict()
    budgets = pd.to_numeric(frame["Бюджет"], errors="coerce").fillna(0)
    active = frame["Статус"].isin(ACTIVE_STATUSES)
    return {
        "updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total": len(rows), "active": int(active.sum()), "scored_active": fresh,
        "unscored_active": int(active.sum()) - fresh,
        "pipeline_budget": int(budgets[active].clip(lower=0).sum()),
        "won": int(status_counts.get("Выигран", 0)), "tiers": tiers,
        "statuses": {str(k): int(v) for k, v in status_counts.items()},
        "sources": {str(k): int(v) for k, v in sources.items()},
    }


def dashboard_tables(stats, mode):
    return [
        ("A1", [["CRM заявок на автоматизацию"]]),
        ("E1", [["ДЕМО · правила без AI" if mode == "demo" else "ProxyAPI · рабочий режим"]]),
        ("A3", [["Показатель", "Значение"], ["Всего лидов", stats["total"]], ["Активные", stats["active"]],
                ["Оценены активные", stats["scored_active"]], ["Ждут оценки", stats["unscored_active"]],
                ["Бюджет активных ₽", stats["pipeline_budget"]], ["Выиграны", stats["won"]],
                ["Обновлено UTC", stats["updated_utc"].replace("T", " ")[:16]]]),
        ("A12", [["Приоритет активных", "Лидов"]] + [[k, v] for k, v in stats["tiers"].items()]),
        ("D3", [["Источник", "Лидов"]] + [[k, v] for k, v in stats["sources"].items()]),
        ("G3", [["Статус", "Лидов"]] + [[k, v] for k, v in stats["statuses"].items()]),
        ("D12", [["AI-балл — приоритет обработки, не вероятность сделки."], ["Бюджет воронки — сумма заявленных бюджетов, не прогноз выручки."]]),
        ("D14", [["Все клиенты вымышлены. Демо оценки рассчитываются по правилам без вызова модели." if mode == "demo" else "Оценки и рекомендации получены через ProxyAPI."]]),
    ]


def render_report(rows, stats, mode):
    def e(value):
        return escape(str(value))
    cards = "".join(f'<article><small>{e(label)}</small><strong>{e(value)}</strong></article>' for label, value in [
        ("Всего лидов", stats["total"]), ("Горячие активные", stats["tiers"]["Горячий"]),
        ("Ждут оценки", stats["unscored_active"]), ("Бюджет воронки", f'{stats["pipeline_budget"]:,} ₽'.replace(",", " "))])
    bars = "".join(f'<div class="bar"><span>{e(k)}</span><meter min="0" max="{max(stats["active"], 1)}" value="{v}">{v}</meter><b>{v}</b></div>' for k, v in stats["tiers"].items())
    body = "".join("<tr>" + "".join(f"<td>{e(row.get(k, ''))}</td>" for k in ["ID", "Имя", "Источник", "Бюджет", "AI балл", "Приоритет", "Рекомендация", "Уведомление"]) + "</tr>" for row in rows)
    mode_tag = "" if mode.startswith("ДЕМО") else f'<p class="tag">{e(mode)}</p>'
    return f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CRM — отчёт</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;background:#f2f5f3;color:#16352d;margin:0}}main{{max-width:1250px;margin:40px auto;padding:0 24px}}h1{{font-size:32px;margin-bottom:8px}}.tag{{display:inline-block;background:#dcebe3;padding:6px 12px;border-radius:6px}}section{{display:flex;gap:16px;flex-wrap:wrap;margin:28px 0}}article{{background:white;border:1px solid #d4dfd8;padding:20px;flex:1;min-width:170px}}small{{display:block}}strong{{font-size:30px;display:block}}.bar{{display:flex;gap:16px;align-items:center;margin:12px 0}}.bar span{{width:100px}}meter{{width:240px;height:22px}}.table{{overflow:auto;background:white}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{text-align:left;padding:14px;border-bottom:1px solid #d4dfd8;vertical-align:top}}th{{background:#16352d;color:white}}td:nth-child(7){{min-width:240px}}footer{{color:#49665b;margin-top:24px}}</style>
<main>{mode_tag}<h1>Заявки на автоматизацию</h1><p>Приоритеты обработки и следующий шаг для менеджера</p><section>{cards}</section><h2>Приоритет активных лидов</h2>{bars}
<h2>Лиды и рекомендации</h2><div class="table"><table><thead><tr>{''.join('<th>'+x+'</th>' for x in ['ID','Клиент','Источник','Бюджет ₽','Балл','Приоритет','Следующий шаг','Уведомление'])}</tr></thead><tbody>{body}</tbody></table></div>
<footer>Обновлено: {e(stats['updated_utc'])}<br>AI-балл — приоритет обработки, не вероятность сделки. Бюджет воронки не является прогнозом выручки.<br>В демонстрации используются только вымышленные данные.</footer></main></html>'''


def render_dispatch(rows):
    fields = ("ID", "Имя", "Источник", "Бюджет", "AI балл", "Приоритет", "Рекомендация")
    values = [[row.get(field, "") for field in fields] for row in rows]
    payload = json.dumps(values, ensure_ascii=False, default=str).replace("<", chr(92) + "u003c")
    template = Path(__file__).with_name("dispatch.html").read_text(encoding="utf-8")
    return template.replace("__CRM_ROWS__", payload, 1)
