from collections import Counter
from datetime import datetime, timezone
import hashlib

from filelock import FileLock

from .analytics import summarize, render_dispatch, render_report, dashboard_tables
from .models import Lead, Score, RunResult, ACTIVE_STATUSES, priority
from .storage import atomic_json, ConcurrentEditError
from .scoring import ScoringFailure


def event_key(*parts):
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()


def run_pipeline(settings, store, scorer, notifier, ledger, *, lead_id=None, allow_scoring=True):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(str(settings.data_dir / "processing.lock"), timeout=0):
        result = RunResult(mode=settings.mode)
        rows = store.rows()
        counts = Counter(str(row.get("ID", "")).strip() for row in rows)
        attempts = 0
        for row in rows:
            if lead_id is not None and str(row.get("ID", "")).strip() != lead_id:
                continue
            if not str(row.get("ID", "")).strip() or counts[str(row.get("ID", "")).strip()] != 1:
                result.invalid += 1
                continue
            try:
                lead = Lead.from_row(row)
            except ValueError:
                result.invalid += 1
                try:
                    store.update(row, {"Ошибка": "INVALID_INPUT: проверьте ID, источник, бюджет, активность и статус", "AI балл": "", "Приоритет": "", "Объяснение": "", "Рекомендация": "", "Fingerprint": "", "Уведомление": ""})
                except ConcurrentEditError:
                    result.deferred += 1
                continue
            if lead.status not in ACTIVE_STATUSES:
                result.skipped += 1
                continue
            fingerprint = lead.fingerprint(scorer.version)
            cache_event = event_key(store.namespace, lead.id, fingerprint)
            if row.get("Fingerprint") == fingerprint and row.get("Ошибка"):
                # Не тратим баланс на одни и те же неудачные данные по таймеру.
                result.skipped += 1
                continue
            cached_row = row.get("Fingerprint") == fingerprint and str(row.get("AI балл", "")).isdigit() and 0 <= int(row["AI балл"]) <= 100
            if not cached_row:
                if not allow_scoring:
                    result.deferred += 1
                    continue
                if attempts >= settings.batch_size:
                    result.deferred += 1
                    continue
                attempts += 1
                cached = ledger.cached_score(cache_event)
                try:
                    evaluation = Score.model_validate_json(cached) if cached else scorer.score(lead)
                    ledger.cache_score(cache_event, evaluation.model_dump_json())
                except Exception as exc:
                    # Сохраняем только тип ошибки. Текст SDK может содержать URL с токеном.
                    reason = exc.code if isinstance(exc, ScoringFailure) else type(exc).__name__
                    store.update(row, {"Fingerprint": fingerprint, "AI балл": "", "Приоритет": "", "Объяснение": "", "Рекомендация": "", "Уведомление": "", "Ошибка": "SCORING_FAILED:" + reason})
                    result.errors += 1
                    # При сбое провайдера прекращаем пакет, без повторов и роста затрат.
                    break
                try:
                    store.update(row, {"AI балл": evaluation.score, "Приоритет": priority(evaluation.score, settings.threshold),
                                      "Объяснение": evaluation.explanation, "Рекомендация": evaluation.recommendation,
                                      "Обработано UTC": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                      "Режим оценки": scorer.label, "Fingerprint": fingerprint, "Ошибка": "", "Уведомление": ""})
                except ConcurrentEditError:
                    result.deferred += 1
                    continue
                result.processed += 1
            else:
                result.skipped += 1
            score = int(row["AI балл"])
            if score < settings.threshold:
                store.update(row, {"Приоритет": priority(score, settings.threshold), "Уведомление": "Не требуется"})
                continue
            event = event_key(cache_event, settings.threshold, notifier.transport)
            state = ledger.state(event)
            if state:
                store.update(row, {"Приоритет": "Горячий", "Уведомление": "ДЕМО: записано в файл" if state == "sent" and settings.mode == "demo" else "Отправлено" if state == "sent" else "Проверить доставку вручную"})
                continue
            # Сначала проверяем неизменность строки и фиксируем намерение отправки.
            try:
                store.update(row, {"Приоритет": "Горячий", "Уведомление": "Отправляется"})
            except ConcurrentEditError:
                result.deferred += 1
                continue
            if not ledger.claim(event):
                continue
            text = (f"{'ДЕМО · ' if settings.mode == 'demo' else ''}Горячий лид {lead.id}\n"
                    f"Клиент: {lead.name}\nОценка: {score}/100\nИсточник: {lead.source}\n"
                    f"Бюджет: {lead.budget} ₽\nСледующий шаг: {row.get('Рекомендация', '')}\n{store.url}")
            try:
                notifier.send(text)
            except Exception:
                ledger.finish(event, "uncertain")
                store.update(row, {"Уведомление": "Проверить доставку вручную"})
                result.errors += 1
            else:
                # Ledger сохраняется до записи в Google Sheets: сбой Sheets не создаст дубль.
                ledger.finish(event, "sent")
                result.notifications += 1
                store.update(row, {"Уведомление": "ДЕМО: записано в файл" if settings.mode == "demo" else "Отправлено"})
        current = store.rows()
        stats = summarize(current, settings.threshold, scorer.version)
        if hasattr(store, "refresh_dashboard"):
            store.refresh_dashboard(dashboard_tables(stats, settings.mode))
        report_dir = settings.data_dir / settings.mode
        atomic_json(report_dir / "analytics.json", stats)
        atomic_json(report_dir / "last_run.json", result.model_dump())
        (report_dir / "crm-dispatch.html").write_text(render_dispatch(current), encoding="utf-8")
        (report_dir / "dashboard.html").write_text(render_report(current, stats, "ДЕМО · правила без AI" if settings.mode == "demo" else "ProxyAPI · Google Sheets"), encoding="utf-8")
        return result
