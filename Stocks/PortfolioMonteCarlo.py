"""Monte Carlo portfolio simulator using historical daily returns.

Usage:
    python PortfolioMonteCarlo.py
    python PortfolioMonteCarlo.py --tickers AAPL,MSFT,NVDA --weights 0.4,0.3,0.3 --years 3 --sims 5000
"""

from __future__ import annotations

import argparse
import math
from typing import Sequence

import numpy as np
import pandas as pd
import yfinance as yf


DEFAULT_TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN"]
DEFAULT_WEIGHTS = [0.30, 0.30, 0.20, 0.20]


def parse_list(raw: str | None, fallback: Sequence[str]) -> list[str]:
    if not raw:
        return list(fallback)
    return [x.strip().upper().replace(".", "-") for x in raw.split(",") if x.strip()]


def parse_weights(raw: str | None, expected_len: int) -> np.ndarray:
    if not raw:
        if expected_len != len(DEFAULT_WEIGHTS):
            return np.repeat(1 / expected_len, expected_len)
        return np.array(DEFAULT_WEIGHTS, dtype=float)

    values = np.array([float(x.strip()) for x in raw.split(",") if x.strip()], dtype=float)
    if len(values) != expected_len:
        raise ValueError("Weights count must match tickers count.")
    if values.sum() <= 0:
        raise ValueError("Weights must sum to a positive number.")
    return values / values.sum()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a portfolio Monte Carlo simulation.")
    parser.add_argument("--tickers", help="Comma-separated symbols.")
    parser.add_argument("--weights", help="Comma-separated weights aligned to tickers.")
    parser.add_argument("--years", type=float, default=2.0, help="Simulation horizon in years.")
    parser.add_argument("--sims", type=int, default=3000, help="Number of simulation paths.")
    parser.add_argument("--initial", type=float, default=10000.0, help="Initial portfolio value.")
    args = parser.parse_args()

    tickers = parse_list(args.tickers, DEFAULT_TICKERS)
    weights = parse_weights(args.weights, len(tickers))
    days = max(1, int(args.years * 252))

    prices = yf.download(tickers, period="5y", progress=False)["Close"]
    if isinstance(prices, pd.Series):
        prices = prices.to_frame()
    prices = prices.dropna(how="any")
    if prices.empty:
        raise RuntimeError("No sufficient price history for chosen symbols.")

    returns = prices.pct_change().dropna()
    mu = returns.mean().values
    cov = returns.cov().values

    rng = np.random.default_rng(seed=42)
    drift = np.dot(weights, mu)
    vol = math.sqrt(np.dot(weights, np.dot(cov, weights)))

    shocks = rng.normal(loc=drift, scale=vol, size=(args.sims, days))
    growth = np.cumprod(1 + shocks, axis=1)
    terminal_values = args.initial * growth[:, -1]

    p05 = np.percentile(terminal_values, 5)
    p25 = np.percentile(terminal_values, 25)
    p50 = np.percentile(terminal_values, 50)
    p75 = np.percentile(terminal_values, 75)
    p95 = np.percentile(terminal_values, 95)
    prob_loss = float((terminal_values < args.initial).mean() * 100)

    print("\nPortfolio Monte Carlo")
    print("=" * 72)
    print(f"Tickers:           {', '.join(tickers)}")
    print(f"Weights:           {', '.join(f'{w:.2%}' for w in weights)}")
    print(f"Initial value:     ${args.initial:,.2f}")
    print(f"Horizon:           {args.years:.2f} years ({days} trading days)")
    print(f"Simulations:       {args.sims:,}")
    print("-" * 72)
    print(f"5th percentile:    ${p05:,.2f}")
    print(f"25th percentile:   ${p25:,.2f}")
    print(f"Median outcome:    ${p50:,.2f}")
    print(f"75th percentile:   ${p75:,.2f}")
    print(f"95th percentile:   ${p95:,.2f}")
    print(f"Probability loss:  {prob_loss:.2f}%")


if __name__ == "__main__":
    main()
