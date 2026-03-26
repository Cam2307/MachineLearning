"""Scan a watchlist for volatility regime changes.

Usage:
    python VolatilityRegimeScanner.py
    python VolatilityRegimeScanner.py --tickers AAPL,MSFT,NVDA,TSLA
"""

from __future__ import annotations

import argparse
import math

import pandas as pd
import yfinance as yf


DEFAULT_TICKERS = [
    "SPY",
    "QQQ",
    "AAPL",
    "MSFT",
    "NVDA",
    "TSLA",
    "AMD",
    "META",
    "AMZN",
    "JPM",
]


def parse_tickers(raw: str | None) -> list[str]:
    if not raw:
        return DEFAULT_TICKERS
    return [t.strip().upper().replace(".", "-") for t in raw.split(",") if t.strip()]


def classify(vol_pctile: float) -> str:
    if vol_pctile >= 80:
        return "High Volatility"
    if vol_pctile >= 40:
        return "Normal Volatility"
    return "Calm Volatility"


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify stocks by current volatility regime.")
    parser.add_argument("--tickers", help="Comma-separated symbols.")
    parser.add_argument("--window", type=int, default=20, help="Rolling window for vol.")
    args = parser.parse_args()

    tickers = parse_tickers(args.tickers)
    data = yf.download(tickers, period="2y", progress=False)["Close"]
    if isinstance(data, pd.Series):
        data = data.to_frame()
    data = data.dropna(how="all")

    rows = []
    for ticker in data.columns:
        s = data[ticker].dropna()
        if len(s) < 120:
            continue

        returns = s.pct_change().dropna()
        realized_vol = returns.rolling(args.window).std() * math.sqrt(252) * 100
        rv_now = float(realized_vol.iloc[-1])
        rv_series = realized_vol.dropna()
        pctile = float((rv_series <= rv_now).mean() * 100)

        one_day_move = float(returns.iloc[-1] * 100)
        five_day_move = float((s.iloc[-1] / s.iloc[-6] - 1) * 100)

        rows.append(
            {
                "Ticker": ticker,
                "Price": round(float(s.iloc[-1]), 2),
                "1D %": round(one_day_move, 2),
                "5D %": round(five_day_move, 2),
                "Realized Vol %": round(rv_now, 2),
                "Vol Percentile": round(pctile, 1),
                "Regime": classify(pctile),
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        print("No usable data returned.")
        return

    out = out.sort_values(["Vol Percentile", "Realized Vol %"], ascending=False).reset_index(drop=True)
    print("\nVolatility Regime Scanner")
    print("=" * 95)
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
