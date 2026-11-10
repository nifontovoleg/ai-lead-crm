import argparse
import json
import socket
import sys
from dataclasses import replace

from .config import Settings
from .runtime import process


def main():
    parser = argparse.ArgumentParser(description="CRM в Google Sheets с AI-скорингом")
    parser.add_argument("command", choices=["local", "demo", "process", "init-sheets", "check"])
    args = parser.parse_args()
    try:
        cfg = Settings.load()
        if args.command in {"local", "demo"}:
            result = process(replace(cfg, mode="demo", batch_size=20))
            print(result.model_dump_json(indent=2))
            print(f"Отчёт: {cfg.data_dir / 'demo/dashboard.html'}")
        elif args.command == "process":
            print(process(cfg).model_dump_json(indent=2))
        elif args.command == "init-sheets":
            from .storage import SheetsStore
            cfg.require_live()
            store = SheetsStore(cfg)
            store.init()
            print("Листы Лиды и Dashboard подготовлены. Лиды не добавлялись.")
        else:
            safe = {"mode": cfg.mode, "proxyapi_key_present": bool(cfg.api_key), "model": cfg.model,
                    "service_account_file_present": cfg.credentials.is_file(), "spreadsheet_id_present": bool(cfg.spreadsheet_id),
                    "telegram_token_present": bool(cfg.telegram_token), "chat_id_present": bool(cfg.chat_id),
                    "trigger_token_long_enough": len(cfg.trigger_token) >= 32, "live_processing_enabled": cfg.live_enabled}
            try:
                with socket.create_connection(("api.proxyapi.ru", 443), timeout=5):
                    safe["proxyapi_tcp_443"] = True
            except OSError:
                safe["proxyapi_tcp_443"] = False
            print(json.dumps(safe, ensure_ascii=False, indent=2))
    except Exception as exc:
        # Не выводим traceback/сообщения сторонних SDK с секретными URL.
        print("Операция не завершена: " + type(exc).__name__, file=sys.stderr)
        if isinstance(exc, ValueError) and str(exc).startswith(("Требуется настройка:", "Для этого проекта", "CRM_MODE", "Неверные", "Заголовки листа")):
            print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
