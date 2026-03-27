# URC Predictor

This project predicts upcoming United Rugby Championship (URC) match outcomes using TheSportsDB data and a feature-based probabilistic model.

Main script: `urc_predictor.py`

## What The Script Does

1. Pulls upcoming URC fixtures in your target date window.
2. Pulls historical URC results (from `2021-01-01` onward by default) to train.
3. Builds team strength with Elo updates.
4. Builds a margin model using match features:
   - Elo strength difference
   - recent form difference (last 5 results)
   - head-to-head margin trend (last 5 meetings, directional)
   - away-team travel distance to venue city (approx)
5. Outputs:
   - predicted winner
   - win probability (%)
   - expected margin (home minus away, in points)

## Data Source

- API: [TheSportsDB](https://www.thesportsdb.com/)
- Default key in script: `123` (free key)
- URC league id used by default: `4446`

## How To Run

From the project folder:

```bash
python urc_predictor.py
```

Useful options:

```bash
python urc_predictor.py --start-date 2026-03-26 --window-days 7
python urc_predictor.py --output-csv urc_predictions.csv
python urc_predictor.py --debug
```

## CLI Arguments (Important)

- `--api-key` : TheSportsDB API key (default `123`)
- `--league-name` : defaults to `United Rugby Championship`
- `--league-id` : defaults to `4446`
- `--start-date` : prediction window start (YYYY-MM-DD), default is today
- `--window-days` : inclusive day window (default `7`)
- `--min-train-date` : training lower bound (default `2021-01-01`)
- `--elo-base` : initial Elo for unseen teams (default `1500`)
- `--k-factor` : Elo update aggressiveness (default `24`)
- `--home-adv` : Elo home advantage offset (default `80`)
- `--output-csv` : optional CSV output path
- `--debug` : prints model/debug details

## How Calculations Work

### 1) Elo Team Strength

For each historical match, the script:

- gets current team ratings `R_home`, `R_away`
- adds home advantage to home rating
- computes expected home result:

`E_home = 1 / (1 + 10^((R_away - (R_home + home_adv))/400))`

- converts actual match outcome to score:
  - home win: `S_home = 1`
  - draw: `S_home = 0.5`
  - away win: `S_home = 0`
- updates ratings:

`R_home <- R_home + K * (S_home - E_home)`

`R_away <- R_away - K * (S_home - E_home)`

This gives dynamic team-strength estimates over time.

### 2) Feature Construction For Margin Model

For each training match, features are computed before updating Elo for that match:

- **Intercept**: `1.0`
- **Elo diff (scaled)**:
  - `((R_home + home_adv) - R_away) / 400`
- **Recent form diff (last 5)**:
  - mean(home recent points) - mean(away recent points)
  - points per prior game: win=1, draw=0.5, loss=0
- **Head-to-head margin (last 5 meetings)**:
  - average of prior directional margins for that pairing
  - directional means home-vs-away sign is respected
- **Away travel (1000 km)**:
  - great-circle distance between approximate home cities
  - converted to thousands of km for scaling

Target value (`y`) is:

`margin = home_score - away_score`

### 3) Expected Margin (Regression)

The script fits a linear model:

`expected_margin = beta0 + beta1*x1 + beta2*x2 + beta3*x3 + beta4*x4`

where:

- `x1 = elo_diff_scaled`
- `x2 = recent_form_diff_last5`
- `x3 = head_to_head_margin_last5`
- `x4 = away_travel_1000km`

It solves normal equations with a tiny ridge term for stability.

Output meaning:

- positive expected margin -> home projected by that many points
- negative expected margin -> away projected by abs(value) points

### 4) Win Probability

After fitting the margin model, residual spread (`sigma`) is estimated from training errors.

Assumption:

`actual_margin ~ Normal(expected_margin, sigma^2)`

Then:

`P(home win) = Phi(expected_margin / sigma)`

where `Phi` is the standard normal CDF.

The script reports `win_probability_percent` for the predicted winner:

- if home is predicted winner: `P(home win)`
- if away is predicted winner: `1 - P(home win)`

## How Winner Is Selected

Winner is based on sign of expected margin:

- `expected_margin >= 0` -> `predicted_winner = home`
- `expected_margin < 0` -> `predicted_winner = away`

## Output Columns

- `date`
- `home`
- `away`
- `predicted_winner`
- `win_probability_percent`
- `expected_margin_points_home_minus_away`

## Notes And Limitations

- Uses TheSportsDB availability and free-tier rate limits.
- Travel distances use approximate team home-city coordinates.
- Probabilities are model-based estimates, not bookmaker odds.
- Accuracy depends on data coverage and freshness.

