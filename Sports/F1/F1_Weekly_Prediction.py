import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ERGAST_BASE = "https://api.jolpi.ca/ergast/f1"


def today_date() -> dt.date:
    return dt.datetime.now().date()


def parse_date(s: Any) -> Optional[dt.date]:
    if s is None:
        return None
    text = str(s).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    text = str(x).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def get_json(url: str, cache_dir: Path, use_cache: bool, timeout_s: int = 30) -> Dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{cache_key(url)}.json"
    if use_cache and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    req = urllib.request.Request(url, headers={"User-Agent": "f1-predictor/1.0"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if use_cache:
        path.write_text(json.dumps(data), encoding="utf-8")
    return data


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def solve_linear_system(a: List[List[float]], b: List[float]) -> List[float]:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = col
        for r in range(col + 1, n):
            if abs(m[r][col]) > abs(m[pivot][col]):
                pivot = r
        if abs(m[pivot][col]) < 1e-12:
            return [0.0] * n
        m[col], m[pivot] = m[pivot], m[col]
        div = m[col][col]
        for c in range(col, n + 1):
            m[col][c] /= div
        for r in range(n):
            if r == col:
                continue
            fac = m[r][col]
            for c in range(col, n + 1):
                m[r][c] -= fac * m[col][c]
    return [m[i][n] for i in range(n)]


def weighted_ridge_fit(
    x_rows: List[Tuple[float, float]],
    y_vals: List[float],
    dates: List[dt.date],
    ref_date: dt.date,
    half_life_days: float = 300.0,
    ridge_lambda: float = 0.8,
) -> Tuple[Tuple[float, float, float], float]:
    if len(x_rows) < 12:
        return (0.0, 0.0, 0.0), 4.5

    xtwx = [[0.0] * 3 for _ in range(3)]
    xtwy = [0.0] * 3
    for i, (x1, x2) in enumerate(x_rows):
        age = max(0, (ref_date - dates[i]).days)
        w = math.exp(-math.log(2) * age / max(1.0, half_life_days))
        row = [1.0, x1, x2]
        y = y_vals[i]
        for r in range(3):
            xtwy[r] += w * row[r] * y
            for c in range(3):
                xtwx[r][c] += w * row[r] * row[c]
    for i in range(1, 3):
        xtwx[i][i] += ridge_lambda

    beta = solve_linear_system(xtwx, xtwy)

    sw = 0.0
    sse = 0.0
    for i, (x1, x2) in enumerate(x_rows):
        age = max(0, (ref_date - dates[i]).days)
        w = math.exp(-math.log(2) * age / max(1.0, half_life_days))
        pred = beta[0] + beta[1] * x1 + beta[2] * x2
        err = y_vals[i] - pred
        sw += w
        sse += w * err * err
    sigma = math.sqrt(sse / max(sw, 1e-9))
    return (beta[0], beta[1], beta[2]), sigma


def confidence_percent(abs_edge: float, sigma: float) -> float:
    scale = max(1.5, sigma)
    z = abs_edge / scale
    p = 1.0 / (1.0 + math.exp(-0.9 * (z - 1.0)))
    raw = 50.0 + 45.0 * p
    return max(55.0, min(92.0, raw))


def full_name(given: str, family: str) -> str:
    return f"{given} {family}".strip()


def fetch_schedule(
    season: int,
    cache_dir: Path,
    use_cache: bool,
) -> List[Dict[str, Any]]:
    url = f"{ERGAST_BASE}/{season}.json?limit=1000"
    data = get_json(url, cache_dir=cache_dir, use_cache=use_cache)
    races = (((data.get("MRData") or {}).get("RaceTable") or {}).get("Races") or [])
    return races


def fetch_season_results(
    season: int,
    cache_dir: Path,
    use_cache: bool,
) -> List[Dict[str, Any]]:
    url = f"{ERGAST_BASE}/{season}/results.json?limit=1000"
    data = get_json(url, cache_dir=cache_dir, use_cache=use_cache)
    races = (((data.get("MRData") or {}).get("RaceTable") or {}).get("Races") or [])
    return races


def race_location(race: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    circuit = race.get("Circuit") or {}
    location = circuit.get("Location") or {}
    lat = to_float(location.get("lat"))
    lon = to_float(location.get("long"))
    if lat is None or lon is None:
        return None
    return lat, lon


def race_name(race: Dict[str, Any]) -> str:
    return str(race.get("raceName") or "Unknown Race").strip()


def build_model(
    start_season: int,
    end_season: int,
    train_end: dt.date,
    cache_dir: Path,
    use_cache: bool,
    sleep_s: float,
) -> Tuple[
    Dict[str, List[float]],
    Dict[str, List[float]],
    Dict[str, str],
    Dict[str, str],
    Tuple[float, float, float],
    float,
]:
    driver_points_hist: Dict[str, List[float]] = {}
    constructor_points_hist: Dict[str, List[float]] = {}
    driver_name: Dict[str, str] = {}
    driver_constructor: Dict[str, str] = {}

    x_rows: List[Tuple[float, float]] = []
    y_vals: List[float] = []
    d_vals: List[dt.date] = []

    prev_loc: Optional[Tuple[float, float]] = None

    for season in range(start_season, end_season + 1):
        races = fetch_season_results(season, cache_dir=cache_dir, use_cache=use_cache)
        races.sort(key=lambda r: int(r.get("round") or 0))
        for race in races:
            d = parse_date(race.get("date"))
            if not d or d > train_end:
                continue
            results = race.get("Results") or []
            if len(results) < 2:
                continue

            participants: List[Tuple[str, str, float]] = []
            for res in results:
                driver = res.get("Driver") or {}
                constructor = res.get("Constructor") or {}
                did = str(driver.get("driverId") or "").strip()
                cid = str(constructor.get("constructorId") or "").strip()
                pts = to_float(res.get("points"))
                if not did or not cid or pts is None:
                    continue
                participants.append((did, cid, pts))
                driver_name[did] = full_name(str(driver.get("givenName") or ""), str(driver.get("familyName") or ""))
                driver_constructor[did] = cid

            if len(participants) < 2:
                continue

            scores: List[Tuple[str, float]] = []
            for did, cid, _ in participants:
                d_hist = driver_points_hist.get(did, [])
                c_hist = constructor_points_hist.get(cid, [])
                last5_driver = (sum(d_hist[-5:]) / min(5, len(d_hist))) if d_hist else 8.0
                last5_constructor = (sum(c_hist[-5:]) / min(5, len(c_hist))) if c_hist else 8.0
                season_driver = (sum(d_hist[-12:]) / min(12, len(d_hist))) if d_hist else 8.0
                score = 0.55 * last5_driver + 0.30 * last5_constructor + 0.15 * season_driver
                scores.append((did, score))

            scores.sort(key=lambda x: x[1], reverse=True)
            score_gap = max(0.0, scores[0][1] - scores[1][1])

            loc = race_location(race)
            travel_1000 = 0.0
            if prev_loc and loc:
                travel_1000 = haversine_km(prev_loc[0], prev_loc[1], loc[0], loc[1]) / 1000.0
            if loc:
                prev_loc = loc

            # Winner edge target: points gap P1 - P2.
            sorted_actual = sorted(participants, key=lambda x: x[2], reverse=True)
            winner_gap = float(sorted_actual[0][2] - sorted_actual[1][2])

            x_rows.append((score_gap, travel_1000))
            y_vals.append(winner_gap)
            d_vals.append(d)

            for did, cid, pts in participants:
                driver_points_hist.setdefault(did, []).append(pts)
                constructor_points_hist.setdefault(cid, []).append(pts)

            time.sleep(sleep_s)

    beta, sigma = weighted_ridge_fit(x_rows, y_vals, d_vals, ref_date=train_end)
    return driver_points_hist, constructor_points_hist, driver_name, driver_constructor, beta, sigma


def upcoming_races_in_window(
    season: int,
    start_date: dt.date,
    end_date: dt.date,
    cache_dir: Path,
    use_cache: bool,
) -> List[Dict[str, Any]]:
    races = fetch_schedule(season, cache_dir=cache_dir, use_cache=use_cache)
    out: List[Dict[str, Any]] = []
    for race in races:
        d = parse_date(race.get("date"))
        if d and start_date <= d <= end_date:
            out.append(race)
    out.sort(key=lambda r: parse_date(r.get("date")) or dt.date.max)
    return out


def build_grid_scores(
    driver_points_hist: Dict[str, List[float]],
    constructor_points_hist: Dict[str, List[float]],
    driver_constructor: Dict[str, str],
) -> List[Tuple[str, float]]:
    rows: List[Tuple[str, float]] = []
    for did, cid in driver_constructor.items():
        d_hist = driver_points_hist.get(did, [])
        c_hist = constructor_points_hist.get(cid, [])
        if not d_hist and not c_hist:
            continue
        last5_driver = (sum(d_hist[-5:]) / min(5, len(d_hist))) if d_hist else 8.0
        last5_constructor = (sum(c_hist[-5:]) / min(5, len(c_hist))) if c_hist else 8.0
        season_driver = (sum(d_hist[-12:]) / min(12, len(d_hist))) if d_hist else 8.0
        score = 0.55 * last5_driver + 0.30 * last5_constructor + 0.15 * season_driver
        rows.append((did, score))
    rows.sort(key=lambda x: x[1], reverse=True)
    return rows


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def softmax(values: List[float], temperature: float = 1.0) -> List[float]:
    if not values:
        return []
    t = max(1e-6, temperature)
    shifted = [v / t for v in values]
    m = max(shifted)
    exps = [math.exp(v - m) for v in shifted]
    total = sum(exps)
    if total <= 0:
        return [1.0 / len(values)] * len(values)
    return [x / total for x in exps]


def main() -> int:
    parser = argparse.ArgumentParser(description="F1 weekly race winner predictions with confidence percentage.")
    parser.add_argument("--start-date", default="", help="Prediction start date (YYYY-MM-DD). Default today.")
    parser.add_argument("--window-days", type=int, default=21, help="Days ahead for upcoming races.")
    parser.add_argument("--train-start-season", type=int, default=2018, help="First season included in training.")
    parser.add_argument("--cache-dir", default=".ergast_cache", help="Local cache directory.")
    parser.add_argument("--no-cache", action="store_true", help="Disable cache.")
    parser.add_argument("--sleep-s", type=float, default=0.08, help="Pause between API calls.")
    parser.add_argument("--output-csv", default="", help="Optional CSV output path.")
    args = parser.parse_args()

    start = parse_date(args.start_date) if args.start_date else today_date()
    if not start:
        print("Invalid --start-date. Use YYYY-MM-DD.")
        return 2
    end = start + dt.timedelta(days=max(1, args.window_days) - 1)
    season = start.year
    train_end = start - dt.timedelta(days=1)

    cache_dir = Path(args.cache_dir).resolve()
    use_cache = not args.no_cache

    driver_hist, constructor_hist, driver_name, driver_constructor, beta, sigma = build_model(
        start_season=args.train_start_season,
        end_season=season,
        train_end=train_end,
        cache_dir=cache_dir,
        use_cache=use_cache,
        sleep_s=args.sleep_s,
    )

    upcoming = upcoming_races_in_window(
        season=season,
        start_date=start,
        end_date=end,
        cache_dir=cache_dir,
        use_cache=use_cache,
    )
    if not upcoming:
        print("No F1 races found in the selected window.")
        return 1

    rows: List[Dict[str, Any]] = []
    for race in upcoming:
        grid = build_grid_scores(driver_hist, constructor_hist, driver_constructor)
        if len(grid) < 3:
            continue
        top3 = grid[:3]
        probs = softmax([s for _, s in top3], temperature=2.5)
        d = parse_date(race.get("date"))
        race_title = race_name(race)

        for rank_idx in range(3):
            did, score = top3[rank_idx]
            next_score = top3[rank_idx + 1][1] if rank_idx < 2 else top3[2][1] - 0.35
            score_gap = max(0.05, score - next_score)
            margin = beta[0] + beta[1] * score_gap + beta[2] * 0.0
            margin = max(0.1, margin)

            model_conf = confidence_percent(abs(margin), sigma)
            prob_conf = probs[rank_idx] * 100.0
            combined_conf = 0.5 * model_conf + 0.5 * prob_conf

            rows.append(
                {
                    "date": d.isoformat() if d else "",
                    "race": race_title,
                    "prediction_rank": str(rank_idx + 1),
                    "predicted_driver": driver_name.get(did, did),
                    "predicted_team": driver_constructor.get(did, "unknown"),
                    "predicted_winning_margin_points": f"{margin:.1f}",
                    "prediction_confidence_percent": f"{combined_conf:.1f}%",
                }
            )

    if not rows:
        print("No race predictions generated.")
        return 1

    print(f"F1 predictions from {start.isoformat()} to {end.isoformat()}")
    print("")
    print("Date       | Race                                     | Rk | Predicted Driver          | Team             | Margin | Confidence")
    print("-" * 144)
    for r in rows:
        print(
            f"{r['date']} | "
            f"{r['race'][:40]:40} | "
            f"{r['prediction_rank']:>2} | "
            f"{r['predicted_driver'][:24]:24} | "
            f"{r['predicted_team'][:16]:16} | "
            f"{float(r['predicted_winning_margin_points']):>6.1f} | "
            f"{r['prediction_confidence_percent']:>10}"
        )

    if args.output_csv:
        out = Path(args.output_csv).resolve()
        write_csv(out, rows)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
