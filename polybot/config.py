"""Settings, read from .env / environment variables."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _f(name, default):
    return float(os.getenv(name, default))


def _b(name, default):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    # Wallet / API (only needed when DRY_RUN=false)
    private_key: str = os.getenv("PRIVATE_KEY", "")
    funder: str = os.getenv("FUNDER_ADDRESS", "")
    signature_type: int = int(os.getenv("SIGNATURE_TYPE", "1"))

    # Safety
    dry_run: bool = _b("DRY_RUN", "true")
    dry_run_bankroll: float = _f("DRY_RUN_BANKROLL", "10")
    max_stake: float = _f("MAX_STAKE", "2")             # USDC per trade
    stop_balance: float = _f("STOP_BALANCE", "5")       # no new trades below this
    max_trades_per_day: int = int(os.getenv("MAX_TRADES_PER_DAY", "5"))
    kelly_fraction: float = _f("KELLY_FRACTION", "0.25")

    # Strategy filters
    min_edge: float = _f("MIN_EDGE", "0.08")            # own prob - price (after fees)
    min_prob: float = _f("MIN_PROB", "0.55")            # only fairly likely outcomes
    max_price: float = _f("MAX_PRICE", "0.95")
    min_local_hour: int = int(os.getenv("MIN_LOCAL_HOUR", "14"))  # station local time
    # Only trade once the day's peak is (almost) observed. Before that the markets
    # are sharper than our simple forecast model.
    max_hours_to_peak: float = _f("MAX_HOURS_TO_PEAK", "1")

    state_file: str = os.getenv("STATE_FILE", "state.json")
    log_file: str = os.getenv("LOG_FILE", "trades.jsonl")


CFG = Config()
