"""Detect bullish and bearish breakouts using 20-day channels and volume surge.

Usage:
    python BreakoutDetector.py
    python BreakoutDetector.py --tickers AAPL,MSFT,NVDA,TSLA --lookback 20 --min-volume-ratio 1.2
"""

from __future__ import annotations

import argparse

import pandas as pd
import yfinance as yf


DEFAULT_TICKERS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "TSLA",
    "META",
    "AMZN",
    "AMD",
    "NFLX",
    "AVGO",
    "SPY",
    "QQQ",
]


def parse_tickers(raw: str | None) -> list[str]:
    if not raw:
        return DEFAULT_TICKERS
    return [t.strip().upper().replace(".", "-") for t in raw.split(",") if t.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan a watchlist for channel breakouts.")
    parser.add_argument("--tickers", help="Comma-separated symbols.")
    parser.add_argument("--lookback", type=int, default=20, help="Donchian channel lookback.")
    parser.add_argument("--min-volume-ratio", type=float, default=1.1, help="Min today's volume / 20d avg volume.")
    args = parser.parse_args()

    tickers = parse_tickers(args.tickers)
    data = yf.download(tickers, period="6mo", progress=False, auto_adjust=False)
    if data.empty:
        print("No data returned.")
        return

    rows = []
    for ticker in tickers:
        try:
            df = data.xs(ticker, axis=1, level=1).dropna()
        except Exception:
            continue
        if len(df) < args.lookback + 5:
            continue

        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]

        upper = high.rolling(args.lookback).max().shift(1)
        lower = low.rolling(args.lookback).min().shift(1)
        vol_avg20 = volume.rolling(20).mean()

        last_close = float(close.iloc[-1])
        upper_now = float(upper.iloc[-1])
        lower_now = float(lower.iloc[-1])
        vol_ratio = float(volume.iloc[-1] / vol_avg20.iloc[-1]) if vol_avg20.iloc[-1] else 0.0

        signal = "No Breakout"
        if last_close > upper_now and vol_ratio >= args.min_volume_ratio:
            signal = "Bullish Breakout"
        elif last_close < lower_now and vol_ratio >= args.min_volume_ratio:
            signal = "Bearish Breakdown"

        dist_upper = ((last_close / upper_now) - 1) * 100 if upper_now else 0.0
        dist_lower = ((last_close / lower_now) - 1) * 100 if lower_now else 0.0

        rows.append(
            {
                "Ticker": ticker,
                "Close": round(last_close, 2),
                "UpperChannel": round(upper_now, 2),
                "LowerChannel": round(lower_now, 2),
                "DistToUpper %": round(dist_upper, 2),
                "DistToLower %": round(dist_lower, 2),
                "VolRatio": round(vol_ratio, 2),
                "Signal": signal,
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        print("No usable symbols.")
        return

    order = pd.CategoricalDtype(["Bullish Breakout", "Bearish Breakdown", "No Breakout"], ordered=True)
    out["Signal"] = out["Signal"].astype(order)
    out = out.sort_values(["Signal", "VolRatio"], ascending=[True, False]).reset_index(drop=True)

    print("\nBreakout Detector")
    print("=" * 118)
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
