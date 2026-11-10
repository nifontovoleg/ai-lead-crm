from .config import ROOT
from .storage import LocalStore, SheetsStore
from .scoring import DemoScorer, ProxyScorer
from .notifications import DeliveryLedger, DemoNotifier, TelegramNotifier
from .engine import run_pipeline


def process(settings):
    if settings.mode == "live":
        settings.require_live(processing=True)
        store = SheetsStore(settings)
        scorer = ProxyScorer(settings)
        notifier = TelegramNotifier(settings)
    else:
        store = LocalStore(settings.data_dir / "demo/leads.json", ROOT / "examples/leads.json")
        scorer = DemoScorer()
        notifier = DemoNotifier(settings.data_dir / "demo/notifications.txt")
    ledger = DeliveryLedger(settings.data_dir / settings.mode / "state.sqlite")
    return run_pipeline(settings, store, scorer, notifier, ledger)
