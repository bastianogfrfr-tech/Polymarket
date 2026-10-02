"""Scan temperature markets, rate every bucket, trade the best one with positive EV.

Usage:
    python -m polybot.main            # one scan (for cron)
    python -m polybot.main --loop 600 # scan every 10 minutes
"""
import argparse
import json
import os
import time
from datetime import date, datetime, timedelta, timezone

from .config import CFG
from .markets import best_prices, fetch_temperature_events
from .trader import Trader
from .weather import estimate, observe


def load_state():
    if os.path.exists(CFG.state_file):
        with open(CFG.state_file) as f:
            return json.load(f)
    return {"traded_events": [], "trades_by_day": {}}


def save_state(state):
    with open(CFG.state_file, "w") as f:
        json.dump(state, f, indent=2)


def log(entry):
    entry["ts"] = datetime.now(timezone.utc).isoformat()
    with open(CFG.log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")


def fee_per_share(bps, price):
    # Conservative: taker fee scales with min(p, 1-p).
    return bps / 10000 * min(price, 1 - price)


def find_candidates(state):
    today = datetime.now(timezone.utc).date()
    days = {today - timedelta(days=1), today, today + timedelta(days=1)}
    cands = []
    for ev in fetch_temperature_events(days):
        if ev.slug in state["traded_events"]:
            continue
        try:
            obs = observe(ev.station, ev.day)
            if obs is None:
                continue  # local day has not started yet
            if obs.local_now.date() == ev.day and obs.local_now.hour < CFG.min_local_hour:
                continue  # too early: little information beyond the forecast
            est = estimate(obs, ev.day)
        except Exception as e:
            print(f"  ! {ev.city}: weather data failed ({e})")
            continue
        if est is None or est.hours_to_peak > CFG.max_hours_to_peak:
            continue
        probs = {b.market_id: est.prob(b.lo, b.hi, ev.unit) for b in ev.buckets}
        for b in ev.buckets:
            p = probs[b.market_id]
            for side, token, prob, snap in (("YES", b.yes_token, p, b.yes_ask),
                                            ("NO", b.no_token, 1 - p, b.no_ask)):
                if prob < CFG.min_prob or prob - snap < CFG.min_edge - 0.05:
                    continue
                try:
                    bid, ask, ask_size = best_prices(token)
                except Exception:
                    continue
                if ask > CFG.max_price or ask <= 0:
                    continue
                cost = ask + fee_per_share(b.taker_fee_bps, ask)
                edge = prob - cost
                if edge < CFG.min_edge:
                    continue
                cands.append({
                    "event": ev.slug, "city": ev.city, "day": str(ev.day), "station": ev.station,
                    "market": b.question, "bucket": b.title, "side": side, "token": token,
                    "tick": b.tick, "min_size": b.min_size, "prob": prob, "ask": ask, "bid": bid,
                    "ask_size": ask_size, "cost": cost, "edge": edge,
                    "ev_per_usd": prob / cost - 1,
                    "obs_max_c": est.obs.obs_max_c, "mu_c": est.mu_c, "sigma_c": est.sigma_c,
                    "bias_c": est.bias_c, "hours_to_peak": est.hours_to_peak,
                    "local_time": est.obs.local_now.strftime("%H:%M"), "source": est.source,
                })
    return sorted(cands, key=lambda c: c["edge"] * c["prob"], reverse=True)


def stake_for(c, bankroll):
    kelly = (c["prob"] - c["cost"]) / (1 - c["cost"])
    stake = min(CFG.max_stake, bankroll * CFG.kelly_fraction * kelly)
    min_stake = c["min_size"] * c["ask"]
    if stake < min_stake:
        stake = min_stake if min_stake <= CFG.max_stake else 0.0
    max_fill = c["ask_size"] * c["ask"]  # only take what sits at the best ask
    return round(min(stake, max_fill, bankroll), 2)


def report(c, stake):
    shares = stake / c["ask"]
    print(f"""
TRADE {'(DRY RUN) ' if CFG.dry_run else ''}
Market: {c['market']}
Position: {c['side']} @ {c['ask']:.3f}
Stake: {stake:.2f} USDC  ->  Potential Return: {shares:.2f} USDC
Estimated Probability (ESTIMATE): {c['prob']:.1%}
Market Probability: {c['ask']:.1%}
Edge (after fees): {c['edge']:+.1%}   EV per USDC: {c['ev_per_usd']:+.1%}
Station {c['station']} local {c['local_time']}: max so far {c['obs_max_c']:.1f}°C, \
rest-of-day high {c['mu_c']:.1f}±{c['sigma_c']:.1f}°C (bias {c['bias_c']:+.1f}, {c['source']})""")


def run_once(trader):
    state = load_state()
    day_key = date.today().isoformat()
    done_today = state["trades_by_day"].get(day_key, 0)
    print(f"[{datetime.now():%H:%M:%S}] scan (dry_run={CFG.dry_run}, trades today={done_today})")
    if done_today >= CFG.max_trades_per_day:
        print("  daily trade limit reached")
        return
    bankroll = trader.balance()
    if bankroll < CFG.stop_balance:
        print(f"  balance {bankroll:.2f} below STOP_BALANCE, not trading")
        return

    cands = find_candidates(state)
    if not cands:
        print("  NO TRADE: nothing with enough edge")
        return
    for c in cands[:5]:
        print(f"  cand {c['city']:<14} {c['bucket']:<14} {c['side']:<3} "
              f"p={c['prob']:.2f} ask={c['ask']:.2f} edge={c['edge']:+.2f}")

    for c in cands:
        stake = stake_for(c, bankroll)
        if stake <= 0:
            print(f"  skip {c['city']} {c['bucket']} {c['side']}: min order "
                  f"{c['min_size'] * c['ask']:.2f} > MAX_STAKE or no size at ask")
            continue
        report(c, stake)
        ok, info = trader.buy(c["token"], c["ask"], stake, c["tick"])
        log({**c, "stake": stake, "ok": ok, "info": info, "dry_run": CFG.dry_run})
        print(f"  -> {'FILLED' if ok else 'NOT FILLED'}: {info}")
        if ok and not CFG.dry_run:
            state["traded_events"].append(c["event"])
            state["trades_by_day"][day_key] = done_today + 1
            save_state(state)
        break  # one trade per scan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0, help="seconds between scans")
    args = ap.parse_args()
    trader = Trader()
    while True:
        try:
            run_once(trader)
        except Exception as e:
            print(f"  scan failed: {e}")
        if not args.loop:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
