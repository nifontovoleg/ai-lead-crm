from types import SimpleNamespace

import pytest

from crm.models import COLUMNS
from crm.storage import SheetsStore, ConcurrentEditError


def fake_store(rows):
    store = object.__new__(SheetsStore)
    values = [COLUMNS] + [[row.get(k, "") for k in COLUMNS] for row in rows]
    calls = []
    store.sheet = SimpleNamespace(get_all_values=lambda: values, update=lambda **kwargs: calls.append(kwargs))
    return store, values, calls


def test_write_finds_id_after_sort_and_uses_raw_values():
    first = dict(zip(COLUMNS, ["L1", "2026-10-30", "Демо", "Сайт", "50000", "1", "2", "Да", "Новый"]))
    second = {**first, "ID": "L2"}
    store, values, calls = fake_store([second, first])
    store.update(first.copy(), {"Рекомендация": "=IMPORTXML(unsafe)", "AI балл": 81})
    assert calls[0]["range_name"] == "J3:R3"
    assert calls[0]["value_input_option"] == "RAW"
    assert "=IMPORTXML(unsafe)" in calls[0]["values"][0]


def test_changed_input_is_not_overwritten():
    row = {"ID": "L1", "Бюджет": "1"}
    store, values, calls = fake_store([{**row, "Бюджет": "50000"}])
    with pytest.raises(ConcurrentEditError):
        store.update(row, {"AI балл": 100})
    assert calls == []


def test_duplicate_id_cannot_receive_result():
    row = {"ID": "L1"}
    store, values, calls = fake_store([row, row])
    with pytest.raises(ConcurrentEditError):
        store.update(row, {"AI балл": 100})
    assert calls == []


def test_schema_mismatch_is_rejected():
    store, values, calls = fake_store([])
    values[0] = ["Чужой лист"]
    with pytest.raises(ValueError, match="SCHEMA_MISMATCH"):
        store.rows()


def test_init_preserves_native_table_validation():
    calls = []
    leads = SimpleNamespace(id=42, get_all_values=lambda: [COLUMNS], freeze=lambda **kwargs: calls.append("freeze"), format=lambda *args: calls.append("format"))
    dashboard = SimpleNamespace(get_all_values=lambda: [["CRM заявок на автоматизацию"]])
    store = object.__new__(SheetsStore)
    store.book = SimpleNamespace(
        worksheet=lambda title: leads if title == "Лиды" else dashboard,
        fetch_sheet_metadata=lambda: {"sheets": [{"properties": {"sheetId": 42}, "tables": [{"name": "CRMLeads"}]}]},
        batch_update=lambda body: calls.append("batch_update"),
    )
    store.init()
    assert calls == ["freeze"]
    assert store.sheet is leads


def test_dashboard_cannot_clear_existing_unrelated_content():
    calls = []
    dashboard = SimpleNamespace(get_all_values=lambda: [["Ручной отчёт пользователя"]], batch_clear=lambda ranges: calls.append("clear"))
    store = object.__new__(SheetsStore)
    store.book = SimpleNamespace(worksheet=lambda title: dashboard)
    with pytest.raises(ValueError, match="Dashboard отличаются"):
        store.refresh_dashboard([])
    assert calls == []


def test_dashboard_writes_to_merged_cell_anchors():
    from crm.analytics import dashboard_tables
    stats = {"total": 0, "active": 0, "scored_active": 0, "unscored_active": 0, "pipeline_budget": 0,
             "won": 0, "updated_utc": "2026-10-30T06:03:55+00:00", "tiers": {}, "sources": {}, "statuses": {}}
    tables = dict(dashboard_tables(stats, "live"))
    assert tables["A1"] == [["CRM заявок на автоматизацию"]]
    assert tables["E1"] == [["ProxyAPI · рабочий режим"]]
    assert tables["A3"][-1] == ["Обновлено UTC", "2026-10-30 06:03"]
