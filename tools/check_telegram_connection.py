"""Check configured bot/chat; --send-test sends one deduplicated setup message."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram import Bot

from crm.config import Settings
from crm.notifications import DeliveryLedger, TelegramNotifier


async def inspect_bot(cfg):
    async with Bot(cfg.telegram_token) as bot:
        chat = await bot.get_chat(cfg.chat_id, read_timeout=15, connect_timeout=10)
        print("Авторизация Telegram: OK")
        print("Бот: @" + bot.username)
        print("Настроенный чат найден; тип:", str(chat.type))
        return bot.id, chat.id


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--send-test", action="store_true")
    args = parser.parse_args()
    cfg = Settings.load()
    if not cfg.telegram_token or not cfg.chat_id:
        print("Заполните TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID локально")
        return 1
    bot_id, chat_id = asyncio.run(inspect_bot(cfg))
    if not args.send_test:
        return 0
    ledger = DeliveryLedger(cfg.data_dir / "setup/telegram.sqlite")
    event = hashlib.sha256(f"setup-message-v1:{bot_id}:{chat_id}".encode()).hexdigest()
    if not ledger.claim(event):
        print("Тест уже запускался; состояние:", ledger.state(event))
        print("Повторное сообщение не отправлено")
        return 0
    try:
        message = TelegramNotifier(cfg).send(
            "CRM: проверка Telegram\n\n"
            "Это тестовое уведомление из Python-сервиса CRM заявок на автоматизацию.\n"
            "Подключение бота проверено. AI в этом тесте не вызывался."
        )
    except Exception:
        ledger.finish(event, "uncertain")
        print("Отправка не подтверждена. Проверьте чат; автоматического повтора нет")
        raise
    else:
        ledger.finish(event, "sent")
        receipt = {"message_id": message.message_id, "sent_utc": message.date.isoformat(), "bot_username": message.from_user.username}
        path = cfg.data_dir / "setup/telegram_receipt.json"
        path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Telegram принял тестовое сообщение; message_id:", message.message_id)
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # URL в исключении SDK может содержать токен: не выводим текст и traceback.
        print("Проверка не завершена:", type(exc).__name__)
        raise SystemExit(1) from None
