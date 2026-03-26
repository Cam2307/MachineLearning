# Stocks Analysis Programs

Collection of Python stock-analysis tools for momentum, volatility, breakouts, portfolio simulation, and GUI ranking.

## Programs Included

### `StockRanker.py`

Interactive desktop GUI for quick market movers.

- Downloads a broad stock universe and 1 year of prices.
- Ranks **gainers** and **losers** for a selected timeframe (`1D`, `1W`, `1M`, `3M`, `6M`, `1Y`).
- Displays rank index, ticker, company name, percentage change, and EasyEquities availability (`Yes`/`No`).
- Best for: quickly checking "what's moving now" without using the terminal.

### `MomentumLeaderboard.py`

Momentum scoring engine for a watchlist.

- Calculates 1-month, 3-month, and 6-month returns.
- Computes RSI(14) and trend context using moving averages.
- Produces a **Momentum Score** and sorts highest-to-lowest.
- Optional CSV export for tracking over time.
- Best for: finding trend-following candidates.

### `VolatilityRegimeScanner.py`

Volatility regime classifier.

- Uses rolling realized volatility (annualized) from daily returns.
- Calculates each stock's current volatility percentile vs recent history.
- Labels symbols as `Calm Volatility`, `Normal Volatility`, or `High Volatility`.
- Best for: position sizing and risk context before entries.

### `BreakoutDetector.py`

Price breakout/breakdown signal scanner.

- Builds a channel from recent highs/lows (Donchian-style lookback).
- Flags `Bullish Breakout` or `Bearish Breakdown` when price exits channel.
- Confirms signal strength with volume ratio (today volume vs 20-day average).
- Best for: momentum breakout setups and watchlist alerts.

### `PortfolioMonteCarlo.py`

Portfolio projection and risk simulation.

- Uses historical return statistics for selected tickers and weights.
- Runs many random simulation paths over your chosen time horizon.
- Reports percentile outcomes (5/25/50/75/95) and probability of finishing below initial capital.
- Best for: understanding upside/downside range, not exact prediction.

## Quick Setup

```bash
pip install yfinance pandas requests numpy
```

## Usage

### 1) Stock Ranker (GUI)

```bash
python StockRanker.py
```

What it does:
- Auto-loads market data on startup
- Shows gainers (left) and losers (right)
- Includes ticker, company name, % change, and EasyEquities flag

---

### 2) Momentum Leaderboard

```bash
python MomentumLeaderboard.py
```

Custom run:

```bash
python MomentumLeaderboard.py --tickers AAPL,MSFT,NVDA,TSLA --top 15 --save-csv momentum.csv
```

Output:
- Console table sorted by Momentum Score
- Optional file output: full rankings in CSV

---

### 3) Volatility Regime Scanner

```bash
python VolatilityRegimeScanner.py
```

Custom run:

```bash
python VolatilityRegimeScanner.py --tickers AAPL,MSFT,NVDA,TSLA --window 20
```

Output:
- Console table with realized vol, percentile, and regime label

---

### 4) Breakout Detector

```bash
python BreakoutDetector.py
```

Custom run:

```bash
python BreakoutDetector.py --tickers AAPL,MSFT,NVDA --lookback 20 --min-volume-ratio 1.2
```

Output:
- Console table with channel levels, distance to channel, volume ratio, and signal type

---

### 5) Portfolio Monte Carlo

```bash
python PortfolioMonteCarlo.py
```

Custom run:

```bash
python PortfolioMonteCarlo.py --tickers AAPL,MSFT,NVDA --weights 0.4,0.3,0.3 --years 3 --sims 5000
```

Output:
- Percentile portfolio outcomes and probability of loss

## Notes

- These tools use Yahoo Finance data (`yfinance`), so occasional symbol/network errors can happen.
- Outputs are for learning/research and are not financial advice.
