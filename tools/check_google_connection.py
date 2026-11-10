"""Read CRM and verify write access using an empty cell, without AI or Telegram."""
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crm.config import Settings
from crm.storage import SheetsStore


def main():
    settings = Settings.load()
    settings.require_live()
    store = SheetsStore(settings)
    rows = store.rows()
    print("Чтение листа Лиды и схема: OK")
    print("Лидов:", len(rows))
    sheet = store.book.worksheet("Dashboard")
    cell = "Z1000"
    original = sheet.acell(cell, value_render_option="FORMULA").value
    if original not in (None, ""):
        print("Проверка записи пропущена: служебная ячейка занята")
        return 1
    marker = "crm-connection-check-" + uuid4().hex
    try:
        sheet.update(range_name=cell, values=[[marker]], value_input_option="RAW")
        assert sheet.acell(cell).value == marker, "Write readback mismatch"
        print("Запись и обратное чтение Python: OK")
    finally:
        current = sheet.acell(cell, value_render_option="FORMULA").value
        if current == marker:
            sheet.batch_clear([cell])
            assert sheet.acell(cell).value in (None, ""), "Cleanup readback mismatch"
            print("Служебная ячейка восстановлена: OK")
        elif current not in (None, ""):
            print("Служебная ячейка изменена другим редактором; очистка пропущена")
    print("Лидов после проверки:", len(store.rows()))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        cause = exc.__cause__ or exc
        response = getattr(cause, "response", None)
        print("Проверка не завершена:", type(exc).__name__)
        print("HTTP статус:", getattr(response, "status_code", "нет"))
        raise SystemExit(1) from None
