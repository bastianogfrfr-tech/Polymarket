"""Order execution via the official Polymarket CLOB client. Does nothing in dry-run."""
import math

from .config import CFG

CLOB = "https://clob.polymarket.com"


class Trader:
    def __init__(self):
        self.client = None
        if CFG.dry_run:
            return
        if not CFG.private_key:
            raise SystemExit("DRY_RUN=false but PRIVATE_KEY is missing in .env")
        from py_clob_client.client import ClobClient

        self.client = ClobClient(CLOB, key=CFG.private_key, chain_id=137,
                                 signature_type=CFG.signature_type,
                                 funder=CFG.funder or None)
        self.client.set_api_creds(self.client.create_or_derive_api_creds())

    def balance(self):
        if CFG.dry_run:
            return CFG.dry_run_bankroll
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

        res = self.client.get_balance_allowance(BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL, signature_type=CFG.signature_type))
        return int(res["balance"]) / 1e6

    def buy(self, token_id, price, stake, tick):
        """Fill-or-kill buy of `stake` USDC at most at `price`. Returns (ok, info)."""
        price = round(round(price / tick) * tick, 4)
        shares = math.floor(stake / price * 100) / 100
        if CFG.dry_run:
            return True, {"dry_run": True, "price": price, "shares": shares}
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        try:
            order = self.client.create_order(OrderArgs(token_id=token_id, price=price,
                                                       size=shares, side=BUY))
            res = self.client.post_order(order, OrderType.FOK)
            return bool(res.get("success")), res
        except Exception as e:  # rejected, not filled, network...
            return False, {"error": str(e)}
