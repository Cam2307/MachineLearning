# URC Weekly Prediction

`URC_Weekly_Prediction.py` predicts upcoming United Rugby Championship fixtures and expected winning margins.

## Model Overview

The script combines two layers:

- **Recency-weighted Elo**
  - Trained on historical URC results.
  - Uses dynamic K updates (larger rating updates for larger score margins).
  - Applies home advantage in rating difference.

- **Weighted ridge margin model**
  - Predicts expected margin (`home points - away points`) from:
    - Elo gap (`elo_x`)
    - Last-5 form gap (`home_last5 - away_last5`)
    - Away travel distance (km scaled by 1000)
  - Uses exponential time decay so recent matches influence the model more.
  - Includes ridge regularization to keep coefficients stable.

This gives stronger and more stable predictions than plain Elo-only margin rules.

## Features Used (Internal)

These are used inside the model and are not printed as standalone output columns:

- `H_Last5` and `A_Last5` (rolling 5-game points, win=1, draw=0.5, loss=0)
- `AwayTravelKM` (distance from away team location to home team location)

## Output

Printed and CSV columns:

- `date`
- `game`
- `predicted_winner`
- `predicted_winning_margin_points`
- `prediction_confidence` (`Low` / `Medium` / `High`)
- `confidence_score_100`

## Data Sources

- TheSportsDB API:
  - Upcoming league fixtures
  - Upcoming team fixtures (used to fill missing league endpoint games)
  - Historical season results
  - Team coordinates (for travel distance)

## Usage

Basic:

```bash
python URC_Weekly_Prediction.py
```

With debug and CSV:

```bash
python URC_Weekly_Prediction.py --window-days 14 --output-csv urc_predictions.csv --debug
```

Custom date window:

```bash
python URC_Weekly_Prediction.py --start-date 2026-03-26 --window-days 14
```

## Important Options

- `--api-key` (default `123`, or env `THESPORTSDB_API_KEY`)
- `--league-name` (default `United Rugby Championship`)
- `--league-id` (default `4446`)
- `--start-date YYYY-MM-DD` (default today)
- `--window-days` (default `14`)
- `--min-train-date` (default `2021-01-01`)
- `--elo-base` (default `1500`)
- `--k-factor` (default `24`)
- `--home-adv` (default `80`)
- `--output-csv` (optional)
- `--cache-dir` (default `.thesportsdb_cache`)
- `--no-cache`
- `--debug`

## Notes

- If coordinates are missing for teams, travel feature contribution becomes `0` for those fixtures.
- Confidence is derived from margin size relative to model residual variance.
- This is a predictive analytics tool for experimentation, not financial or betting advice.
