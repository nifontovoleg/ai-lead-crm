import json
from pathlib import Path

from .models import COLUMNS, INPUT_COLUMNS, OUTPUT_COLUMNS


class ConcurrentEditError(Exception):
    pass


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class LocalStore:
    namespace = "demo"
    url = "Локальное демо"

    def __init__(self, path, seed):
        self.path = Path(path)
        if not self.path.exists():
            atomic_json(self.path, json.loads(Path(seed).read_text(encoding="utf-8")))

    def rows(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def update(self, original, changes):
        rows = self.rows()
        matches = [r for r in rows if r.get("ID") == original.get("ID")]
        if len(matches) != 1 or any(matches[0].get(k, "") != original.get(k, "") for k in INPUT_COLUMNS):
            raise ConcurrentEditError("LEAD_CHANGED")
        matches[0].update(changes)
        atomic_json(self.path, rows)
        original.update(changes)


class SheetsStore:
    def __init__(self, settings):
        import gspread
        self.client = gspread.service_account(filename=str(settings.credentials), scopes=["https://www.googleapis.com/auth/spreadsheets"])
        self.client.set_timeout(20)
        self.book = self.client.open_by_key(settings.spreadsheet_id)
        self.namespace = settings.spreadsheet_id
        self.url = f"https://docs.google.com/spreadsheets/d/{settings.spreadsheet_id}/edit"
        self.sheet = None

    def init(self):
        import gspread
        for title, columns in [("Лиды", len(COLUMNS)), ("Dashboard", 8)]:
            try:
                worksheet = self.book.worksheet(title)
            except gspread.WorksheetNotFound:
                worksheet = self.book.add_worksheet(title=title, rows=1000, cols=columns)
            if title == "Лиды":
                current = worksheet.get_all_values()
                if not current:
                    worksheet.update(range_name="A1:R1", values=[COLUMNS], value_input_option="RAW")
                elif current[0][:len(COLUMNS)] != COLUMNS:
                    raise ValueError("Заголовки листа Лиды отличаются. Существующие данные не изменены")
                worksheet.freeze(rows=1)
                metadata = self.book.fetch_sheet_metadata()
                has_native_table = any(s.get("tables") for s in metadata["sheets"] if s["properties"]["sheetId"] == worksheet.id)
                if has_native_table:
                    # Типизированные колонки native table уже управляют выпадающими списками.
                    continue
                worksheet.format("A1:R1", {"backgroundColor": {"red": 0.93, "green": 0.93, "blue": 0.93}, "textFormat": {"bold": True, "foregroundColor": {"red": 0, "green": 0, "blue": 0}}, "wrapStrategy": "WRAP"})
                sheet_id = worksheet.id
                requests = []
                for col, options in [(3, ["Рекомендация", "Сайт", "Telegram", "Реклама", "Другое"]), (7, ["Да", "Нет"]), (8, ["Новый", "В работе", "Переговоры", "Выигран", "Проигран"])]:
                    requests.append({"setDataValidation": {"range": {"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": col, "endColumnIndex": col + 1}, "rule": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": x} for x in options]}, "strict": True, "showCustomUi": True}}})
                self.book.batch_update({"requests": requests})
            else:
                current = worksheet.get_all_values()
                if current and current[0][0] != "CRM заявок на автоматизацию":
                    raise ValueError("Заголовки листа Dashboard отличаются. Существующие данные не изменены")
        self.sheet = self.book.worksheet("Лиды")

    def rows(self):
        if self.sheet is None:
            self.sheet = self.book.worksheet("Лиды")
        values = self.sheet.get_all_values()
        if not values or values[0][:len(COLUMNS)] != COLUMNS:
            raise ValueError("SHEET_SCHEMA_MISMATCH")
        return [dict(zip(COLUMNS, row + [""] * (len(COLUMNS) - len(row)))) for row in values[1:] if any(row)]

    def update(self, original, changes):
        # Повторно ищем строку по ID: сортировка листа не должна смещать запись.
        values = self.sheet.get_all_values()
        if not values or values[0][:len(COLUMNS)] != COLUMNS:
            raise ConcurrentEditError("SHEET_SCHEMA_CHANGED")
        matches = [(number, dict(zip(COLUMNS, row + [""] * len(COLUMNS)))) for number, row in enumerate(values[1:], 2) if row and row[0] == original["ID"]]
        if len(matches) != 1 or any(str(matches[0][1].get(k, "")) != str(original.get(k, "")) for k in INPUT_COLUMNS):
            raise ConcurrentEditError("LEAD_CHANGED")
        number, current = matches[0]
        current.update(changes)
        self.sheet.update(range_name=f"J{number}:R{number}", values=[[current.get(k, "") for k in OUTPUT_COLUMNS]], value_input_option="RAW")
        original.update(changes)

    def refresh_dashboard(self, tables):
        dashboard = self.book.worksheet("Dashboard")
        current = dashboard.get_all_values()
        if current and current[0][0] != "CRM заявок на автоматизацию":
            raise ValueError("Заголовки листа Dashboard отличаются. Существующие данные не изменены")
        # Dashboard — зарезервированный лист приложения, ручные данные здесь не хранятся.
        dashboard.batch_clear(["A1:H1000"])
        updates = []
        for cell, rows in tables:
            updates.append({"range": cell, "values": rows})
        dashboard.batch_update(updates, value_input_option="RAW")
        dashboard.format("A1:H1", {"textFormat": {"bold": True, "fontSize": 15}})
        dashboard.freeze(rows=1)
        info = self.book.fetch_sheet_metadata()
        charts = next((s.get("charts", []) for s in info["sheets"] if s["properties"]["sheetId"] == dashboard.id), [])
        if not any(c.get("spec", {}).get("title") == "Лиды по приоритету" for c in charts):
            self.book.batch_update({"requests": [{"addChart": {"chart": {
                "spec": {"title": "Лиды по приоритету", "pieChart": {
                    "legendPosition": "RIGHT_LEGEND",
                    "domain": {"sourceRange": {"sources": [{"sheetId": dashboard.id, "startRowIndex": 12, "endRowIndex": 15, "startColumnIndex": 0, "endColumnIndex": 1}]}},
                    "series": {"sourceRange": {"sources": [{"sheetId": dashboard.id, "startRowIndex": 12, "endRowIndex": 15, "startColumnIndex": 1, "endColumnIndex": 2}]}}
                }},
                "position": {"overlayPosition": {"anchorCell": {"sheetId": dashboard.id, "rowIndex": 17, "columnIndex": 0}}}
            }}}]})
