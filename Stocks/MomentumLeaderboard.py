"""Momentum leaderboard for a watchlist of stocks.

Usage:
    python MomentumLeaderboard.py
    python MomentumLeaderboard.py --tickers AAPL,MSFT,NVDA,TSLA --top 15 --save-csv momentum.csv
"""

from __future__ import annotations

import argparse
from typing import Iterable

import pandas as pd
import yfinance as yf


DEFAULT_TICKERS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "GOOGL",
    "TSLA",
    "AMD",
    "AVGO",
    "JPM",
    "V",
    "MA",
    "NFLX",
    "COST",
    "LLY",
]


def rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    value = 100 - (100 / (1 + rs))
    return float(value.iloc[-1])


def parse_tickers(raw: str | None) -> list[str]:
    if not raw:
        return DEFAULT_TICKERS
    return [t.strip().upper().replace(".", "-") for t in raw.split(",") if t.strip()]


def get_closes(tickers: Iterable[str], period: str = "1y") -> pd.DataFrame:
    data = yf.download(list(tickers), period=period, progress=False)["Close"]
    if isinstance(data, pd.Series):
        data = data.to_frame()
    return data.dropna(how="all")


def build_leaderboard(close_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for ticker in close_df.columns:
        s = close_df[ticker].dropna()
        if len(s) < 80:
            continue

        ret_1m = (s.iloc[-1] / s.iloc[-22] - 1) * 100
        ret_3m = (s.iloc[-1] / s.iloc[-64] - 1) * 100
        ret_6m = (s.iloc[-1] / s.iloc[-127] - 1) * 100 if len(s) >= 127 else pd.NA
        ma_50 = s.rolling(50).mean().iloc[-1]
        ma_200 = s.rolling(200).mean().iloc[-1] if len(s) >= 200 else pd.NA
        rsi_14 = rsi(s, 14)

        trend_bonus = 0.0
        if pd.notna(ma_50) and s.iloc[-1] > ma_50:
            trend_bonus += 2.0
        if pd.notna(ma_200) and s.iloc[-1] > ma_200:
            trend_bonus += 3.0

        base_score = float(ret_1m * 0.35 + ret_3m * 0.45 + (float(ret_6m) if pd.notna(ret_6m) else 0) * 0.20)
        score = base_score + trend_bonus

        rows.append(
            {
                "Ticker": ticker,
                "Price": round(float(s.iloc[-1]), 2),
                "1M %": round(float(ret_1m), 2),
                "3M %": round(float(ret_3m), 2),
                "6M %": round(float(ret_6m), 2) if pd.notna(ret_6m) else None,
                "RSI14": round(float(rsi_14), 1),
                "Momentum Score": round(score, 2),
            }
        )

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    return result.sort_values("Momentum Score", ascending=False).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a momentum leaderboard for stocks.")
    parser.add_argument("--tickers", help="Comma-separated symbols.")
    parser.add_argument("--top", type=int, default=10, help="Number of rows to display.")
    parser.add_argument("--save-csv", help="Optional output CSV path.")
    args = parser.parse_args()

    tickers = parse_tickers(args.tickers)
    close_df = get_closes(tickers)
    leaderboard = build_leaderboard(close_df)

    if leaderboard.empty:
        print("No usable data returned.")
        return

    print("\nMomentum Leaderboard")
    print("=" * 80)
    print(leaderboard.head(args.top).to_string(index=False))

    if args.save_csv:
        leaderboard.to_csv(args.save_csv, index=False)
        print(f"\nSaved full leaderboard to: {args.save_csv}")


if __name__ == "__main__":
    main()
