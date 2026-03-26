# F1 Weekly Prediction

`F1_Weekly_Prediction.py` is the Formula 1 version of your weekly predictor workflow.

## What it does

- Pulls upcoming races for the selected date window.
- Trains on historical race results from previous seasons.
- Builds a driver performance score from:
  - driver last-5 points form
  - constructor last-5 points form
  - recent season points trend
- Predicts:
  - race winner
  - winner edge (`predicted_winning_margin_points`)
  - confidence percentage (`prediction_confidence_percent`)

## Output columns

- `date`
- `race`
- `predicted_winner`
- `predicted_team`
- `predicted_winning_margin_points`
- `prediction_confidence_percent`

## Usage

Run with defaults:

```bash
python F1_Weekly_Prediction.py
```

Custom window + CSV:

```bash
python F1_Weekly_Prediction.py --window-days 30 --output-csv f1_predictions.csv
```

Train from a different start season:

```bash
python F1_Weekly_Prediction.py --train-start-season 2016
```

## Options

- `--start-date YYYY-MM-DD` (default today)
- `--window-days` (default `21`)
- `--train-start-season` (default `2018`)
- `--cache-dir` (default `.ergast_cache`)
- `--no-cache`
- `--sleep-s` (default `0.08`)
- `--output-csv`

## Notes

- Confidence is shown directly as a percentage.
- This script is intended for analytics/experimentation and not betting advice.
