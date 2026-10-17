#!/usr/bin/env python3
"""把 paper/state.json 转成看板用的 data.json。

用法: python3 export_dashboard.py
输出: 与本脚本同目录的 data.json
持仓现价用 yfinance 拉一次（通常 0-3 只票）；失败则回退为成本价并标记 price_ok=false。
"""
import json, os, sys
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "..", "paper", "state.json")
OUT = os.path.join(HERE, "data.json")
NY = ZoneInfo("America/New_York")

STOP_PCT = 0.03
HALF_PROFIT_PCT = 0.09
REGIME_CN = {"bull": "牛市", "chop": "震荡", "bear": "熊市"}
REASON_CN = {
    "hard_stop": "硬止损(-3%)",
    "half_profit": "半仓止盈(+9%)",
    "trend_21dma": "趋势出清(<21DMA)",
    "circuit_breaker": "熔断全平",
}


def fetch_prices(tickers):
    if not tickers:
        return {}
    try:
        import yfinance as yf
        df = yf.download(" ".join(tickers), period="5d", interval="1d",
                         auto_adjust=False, progress=False, threads=True)
        closes = df["Close"]
        out = {}
        for t in tickers:
            try:
                s = closes[t].dropna() if len(tickers) > 1 else closes.dropna()
                if len(s):
                    out[t] = round(float(s.iloc[-1]), 2)
            except Exception:
                pass
        return out
    except Exception as e:
        print(f"价格拉取失败: {e}", file=sys.stderr)
        return {}


def main():
    with open(STATE) as f:
        state = json.load(f)

    prices = fetch_prices(list(state.get("positions", {}).keys()))
    price_ok = True

    positions = []
    for t, p in state.get("positions", {}).items():
        entry = p["entry_price"]
        cur = prices.get(t)
        if cur is None:
            cur = entry
            price_ok = False
        pnl_pct = (cur / entry - 1) * 100
        try:
            d0 = datetime.fromisoformat(p["entry_date"]).date()
            d1 = datetime.fromisoformat(state["last_run_date"]).date()
            days = max(0, (d1 - d0).days)
        except Exception:
            days = 0
        positions.append({
            "ticker": t, "sector": p.get("sector") or "-",
            "shares": round(p["shares"], 4), "entry_price": round(entry, 2),
            "cur_price": round(cur, 2), "pnl_pct": round(pnl_pct, 2),
            "stop_price": round(entry * (1 - STOP_PCT), 2),
            "target_price": round(entry * (1 + HALF_PROFIT_PCT), 2),
            "half_taken": bool(p.get("half_taken")), "days_held": days,
        })
    positions.sort(key=lambda x: x["pnl_pct"], reverse=True)

    legs = state.get("legs_closed", [])
    wins = [l for l in legs if l.get("R", 0) > 0]
    recent = []
    for l in legs[-15:][::-1]:
        recent.append({
            "ticker": l["ticker"], "exit_date": l["exit_date"],
            "R": l["R"], "return_pct": l["return_pct"],
            "reason": REASON_CN.get(l["reason"], l["reason"]),
        })

    curve = state.get("equity_history", [])
    peak, maxdd = 0, 0
    for _, eq in curve:
        peak = max(peak, eq)
        if peak > 0:
            maxdd = min(maxdd, eq / peak - 1)

    c = state.get("counters", {})
    regime = state.get("last_regime") or "chop"
    if state.get("broken"):
        status, status_cn = "broken", "熔断停止"
    elif state.get("xray_paused"):
        status, status_cn = "xray_paused", "X-ray 暂停中"
    else:
        status, status_cn = "running", "运行中"

    equity = curve[-1][1] if curve else state.get("cash", 1000.0)
    data = {
        "updated_at": datetime.now(NY).strftime("%Y-%m-%d %H:%M") + " 美东",
        "version": state.get("version", "v1e_r05"),
        "status": status, "status_cn": status_cn,
        "regime": regime, "regime_cn": REGIME_CN.get(regime, regime),
        "equity": round(equity, 2),
        "total_return_pct": round((equity / 1000.0 - 1) * 100, 2),
        "max_drawdown_pct": round(maxdd * 100, 2),
        "peak_equity": round(state.get("peak_equity", equity), 2),
        "cash": round(state.get("cash", 0), 2),
        "positions": positions,
        "pending": [
            {"ticker": e["ticker"], "signal_date": e["signal_date"],
             "sector": e.get("sector") or "-"}
            for e in state.get("pending_entries", [])],
        "recent_legs": recent,
        "stats": {
            "legs": len(legs),
            "win_rate": round(len(wins) / len(legs) * 100, 1) if legs else None,
            "avg_R": round(sum(l.get("R", 0) for l in legs) / len(legs), 3) if legs else None,
            "signals": c.get("signals", 0),
            "xray_triggers": c.get("xray_triggers", 0),
            "xray_paused_days": c.get("xray_paused_days", 0),
            "xray_cooldown_left": state.get("xray_cooldown_left", 0),
            "regime_days": {
                "bull": c.get("regime_bull_days", 0),
                "chop": c.get("regime_chop_days", 0),
                "bear": c.get("regime_bear_days", 0),
            },
        },
        "equity_curve": [[d, e] for d, e in curve],
        "price_ok": price_ok,
    }
    with open(OUT, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"OK: {OUT} 持仓{len(positions)}笔 平仓{len(legs)}笔")


if __name__ == "__main__":
    main()
