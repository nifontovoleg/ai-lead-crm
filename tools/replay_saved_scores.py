"""Replay current AI scores for unchanged lead features, without API inference."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from filelock import FileLock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crm.analytics import dashboard_tables, render_report, summarize
from crm.config import Settings
from crm.engine import event_key
from crm.models import Lead, priority
from crm.notifications import DeliveryLedger
from crm.scoring import ProxyScorer
from crm.storage import SheetsStore, atomic_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    cfg = replace(Settings.load(), mode="live")
    cfg.require_live()
    store = SheetsStore(cfg)
    scorer = ProxyScorer(cfg)
    # This tool never calls score(), Telegram or the model API.
    records = [json.loads(line) for line in scorer.audit_path.read_text(encoding="utf-8").splitlines()]
    ledger = DeliveryLedger(cfg.data_dir / "live/state.sqlite")
    changes = []
    with FileLock(str(cfg.data_dir / "processing.lock"), timeout=0):
        for row in store.rows():
            lead = Lead.from_row(row)
            matching = [r for r in records if r.get("scorer_version") == scorer.version and r.get("code") == "OK" and r.get("features") == lead.features()
                        and r.get("finish_reason") == "stop" and r.get("content") and not r.get("content_truncated")]
            if not matching:
                continue
            record = matching[-1]
            reply = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=record["content"]))])
            accepted = scorer._validate(reply)
            raw_score = json.loads(record["content"])["score"]
            changes.append({"lead_id": lead.id, "raw_model_score": raw_score, "accepted_score": accepted.score,
                            "source_reply_utc": record.get("time_utc"), "model": cfg.model})
            if args.apply:
                fingerprint = lead.fingerprint(scorer.version)
                ledger.cache_score(event_key(store.namespace, lead.id, fingerprint), accepted.model_dump_json())
                store.update(row, {"AI балл": accepted.score, "Приоритет": priority(accepted.score, cfg.threshold),
                                   "Объяснение": accepted.explanation, "Рекомендация": accepted.recommendation,
                                   "Обработано UTC": record["time_utc"], "Режим оценки": scorer.label,
                                   "Fingerprint": fingerprint, "Ошибка": "",
                                   "Уведомление": row.get("Уведомление", "") if accepted.score >= cfg.threshold else "Не требуется"})
        if args.apply:
            rows = store.rows()
            stats = summarize(rows, cfg.threshold, scorer.version)
            store.refresh_dashboard(dashboard_tables(stats, "live"))
            atomic_json(cfg.data_dir / "live/analytics.json", stats)
            atomic_json(cfg.data_dir / "live/offline_normalization.json", {"model_calls": 0, "telegram_calls": 0, "changes": changes})
            (cfg.data_dir / "live/dashboard.html").write_text(render_report(rows, stats, "ProxyAPI · оценки и рекомендации AI"), encoding="utf-8")
    print(json.dumps({"applied": args.apply, "model_calls": 0, "telegram_calls": 0, "changes": changes}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("Replay not completed:", type(exc).__name__)
        raise SystemExit(1) from None
