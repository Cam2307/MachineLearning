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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

THESPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json"


@dataclass(frozen=True)
class Fixture:
    id_event: str
    event_date: dt.date
    home: str
    away: str

    @property
    def game(self) -> str:
        return f"{self.home} vs {self.away}"


def now_date() -> dt.date:
    return dt.datetime.now().date()


def parse_date(value: Any) -> Optional[dt.date]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def normalize_team(name: str) -> str:
    return " ".join((name or "").strip().split())


def cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def get_json(url: str, cache_dir: Path, use_cache: bool, timeout_s: int = 30) -> Dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    file_path = cache_dir / f"{cache_key(url)}.json"
    if use_cache and file_path.exists():
        return json.loads(file_path.read_text(encoding="utf-8"))

    req = urllib.request.Request(url, headers={"User-Agent": "urc-predictor/3.1"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if use_cache:
        file_path.write_text(json.dumps(data), encoding="utf-8")
    return data


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def team_directory(api_key: str, league_name: str, cache_dir: Path, use_cache: bool) -> Tuple[List[str], Dict[str, Tuple[float, float]]]:
    url = f"{THESPORTSDB_BASE}/{api_key}/search_all_teams.php?l={urllib.parse.quote_plus(league_name)}"
    data = get_json(url, cache_dir=cache_dir, use_cache=use_cache)
    teams = data.get("teams") or []

    ids: List[str] = []
    coords: Dict[str, Tuple[float, float]] = {}
    for t in teams:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("idTeam") or "").strip()
        name = normalize_team(str(t.get("strTeam") or ""))
        lat = to_float(t.get("strLatitude"))
        lon = to_float(t.get("strLongitude"))
        if tid:
            ids.append(tid)
        if name and lat is not None and lon is not None:
            coords[name] = (lat, lon)
    return ids, coords


def fetch_fixtures(
    api_key: str,
    league_id: str,
    league_name: str,
    start_date: dt.date,
    end_date: dt.date,
    cache_dir: Path,
    use_cache: bool,
    sleep_s: float,
) -> List[Fixture]:
    fixtures: Dict[str, Fixture] = {}

    def add_event(ev: Dict[str, Any]) -> None:
        eid = str(ev.get("idEvent") or "").strip()
        d = parse_date(ev.get("dateEvent"))
        home = normalize_team(str(ev.get("strHomeTeam") or ""))
        away = normalize_team(str(ev.get("strAwayTeam") or ""))
        if not eid or not d or not home or not away:
            return
        if start_date <= d <= end_date:
            fixtures[eid] = Fixture(eid, d, home, away)

    league_url = f"{THESPORTSDB_BASE}/{api_key}/eventsnextleague.php?id={league_id}"
    for ev in (get_json(league_url, cache_dir=cache_dir, use_cache=use_cache).get("events") or []):
        if isinstance(ev, dict):
            add_event(ev)

    team_ids, _ = team_directory(api_key, league_name, cache_dir, use_cache)
    for tid in team_ids:
        url = f"{THESPORTSDB_BASE}/{api_key}/eventsnext.php?id={tid}"
        try:
            data = get_json(url, cache_dir=cache_dir, use_cache=use_cache)
        except Exception:
            time.sleep(sleep_s)
            continue
        for ev in data.get("events") or []:
            if isinstance(ev, dict):
                add_event(ev)
        time.sleep(sleep_s)

    return sorted(fixtures.values(), key=lambda f: (f.event_date.isoformat(), f.game))


def current_season_start_year(today: dt.date) -> int:
    return today.year if today.month >= 8 else today.year - 1


def season_candidates(start_year: int, end_year: int) -> List[str]:
    vals: List[str] = []
    for y in range(start_year, end_year + 1):
        vals.append(f"{y}-{y+1}")
        vals.append(f"{y}/{y+1}")
    seen: set[str] = set()
    out: List[str] = []
    for v in vals:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def result_points(team_score: float, opp_score: float) -> float:
    if team_score > opp_score:
        return 1.0
    if team_score < opp_score:
        return 0.0
    return 0.5


def last5_points(history: Dict[str, List[float]], team: str) -> float:
    vals = history.get(team, [])
    return float(sum(vals[-5:])) if vals else 0.0


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
            factor = m[r][col]
            for c in range(col, n + 1):
                m[r][c] -= factor * m[col][c]
    return [m[i][n] for i in range(n)]


def weighted_ridge_fit(
    x_rows: List[Tuple[float, float, float]],
    y_vals: List[float],
    dates: List[dt.date],
    ref_date: dt.date,
    half_life_days: float = 240.0,
    ridge_lambda: float = 0.8,
) -> Tuple[Tuple[float, float, float, float], float]:
    if len(x_rows) < 10:
        return (0.0, 0.0, 0.0, 0.0), 10.0

    xtwx = [[0.0] * 4 for _ in range(4)]
    xtwy = [0.0] * 4
    for i, (x1, x2, x3) in enumerate(x_rows):
        age = max(0, (ref_date - dates[i]).days)
        w = math.exp(-math.log(2) * age / max(1.0, half_life_days))
        row = [1.0, x1, x2, x3]
        y = y_vals[i]
        for r in range(4):
            xtwy[r] += w * row[r] * y
            for c in range(4):
                xtwx[r][c] += w * row[r] * row[c]
    for i in range(1, 4):
        xtwx[i][i] += ridge_lambda

    beta = solve_linear_system(xtwx, xtwy)

    sw = 0.0
    sse = 0.0
    for i, (x1, x2, x3) in enumerate(x_rows):
        age = max(0, (ref_date - dates[i]).days)
        w = math.exp(-math.log(2) * age / max(1.0, half_life_days))
        pred = beta[0] + beta[1] * x1 + beta[2] * x2 + beta[3] * x3
        err = y_vals[i] - pred
        sw += w
        sse += w * err * err
    sigma = math.sqrt(sse / max(sw, 1e-9))
    return (beta[0], beta[1], beta[2], beta[3]), sigma


def train_model(
    api_key: str,
    league_id: str,
    train_start: dt.date,
    train_end: dt.date,
    elo_base: float,
    k_factor: float,
    home_adv: float,
    team_coords: Dict[str, Tuple[float, float]],
    cache_dir: Path,
    use_cache: bool,
    sleep_s: float,
) -> Tuple[Dict[str, float], Dict[str, List[float]], Tuple[float, float, float, float], float]:
    elo: Dict[str, float] = {}
    form_hist: Dict[str, List[float]] = {}

    def rating(team: str) -> float:
        if team not in elo:
            elo[team] = elo_base
        return elo[team]

    events: List[Dict[str, Any]] = []
    for season in season_candidates(2021, current_season_start_year(now_date())):
        url = f"{THESPORTSDB_BASE}/{api_key}/eventsseason.php?id={league_id}&s={urllib.parse.quote_plus(season)}"
        try:
            data = get_json(url, cache_dir=cache_dir, use_cache=use_cache)
        except Exception:
            time.sleep(sleep_s)
            continue
        for ev in data.get("events") or []:
            if not isinstance(ev, dict):
                continue
            d = parse_date(ev.get("dateEvent"))
            hs = to_float(ev.get("intHomeScore"))
            aw = to_float(ev.get("intAwayScore"))
            if not d or hs is None or aw is None:
                continue
            if train_start <= d <= train_end:
                events.append(ev)
        time.sleep(sleep_s)

    events.sort(key=lambda ev: ((parse_date(ev.get("dateEvent")) or dt.date.max).isoformat(), str(ev.get("strTime") or "")))

    x_rows: List[Tuple[float, float, float]] = []
    y_vals: List[float] = []
    d_vals: List[dt.date] = []

    for ev in events:
        home = normalize_team(str(ev.get("strHomeTeam") or ""))
        away = normalize_team(str(ev.get("strAwayTeam") or ""))
        d = parse_date(ev.get("dateEvent"))
        hs = to_float(ev.get("intHomeScore"))
        aw = to_float(ev.get("intAwayScore"))
        if not home or not away or not d or hs is None or aw is None:
            continue

        rh = rating(home)
        ra = rating(away)
        elo_x = ((rh + home_adv) - ra) / 400.0
        form_diff = last5_points(form_hist, home) - last5_points(form_hist, away)

        travel_km = 0.0
        if home in team_coords and away in team_coords:
            hlat, hlon = team_coords[home]
            alat, alon = team_coords[away]
            travel_km = haversine_km(alat, alon, hlat, hlon)
        travel_1000 = travel_km / 1000.0

        margin = hs - aw
        x_rows.append((elo_x, form_diff, travel_1000))
        y_vals.append(float(margin))
        d_vals.append(d)

        expected_home = 1.0 / (1.0 + 10 ** ((ra - (rh + home_adv)) / 400.0))
        actual_home = result_points(hs, aw)
        k_adj = k_factor * (1.0 + min(2.0, math.log1p(abs(margin)) / 2.0))
        delta = k_adj * (actual_home - expected_home)
        elo[home] = rh + delta
        elo[away] = ra - delta

        form_hist.setdefault(home, []).append(result_points(hs, aw))
        form_hist.setdefault(away, []).append(result_points(aw, hs))

    beta, sigma = weighted_ridge_fit(x_rows, y_vals, d_vals, ref_date=train_end)
    return elo, form_hist, beta, sigma


def confidence_percent(abs_margin: float, resid_sigma: float) -> float:
    sigma = max(2.5, resid_sigma)
    z = abs_margin / sigma
    p = 1.0 / (1.0 + math.exp(-1.35 * (z - 1.0)))
    return 50.0 + 50.0 * p


def predict_fixture(
    fixture: Fixture,
    elo: Dict[str, float],
    form_hist: Dict[str, List[float]],
    team_coords: Dict[str, Tuple[float, float]],
    elo_base: float,
    home_adv: float,
    beta: Tuple[float, float, float, float],
    resid_sigma: float,
) -> Dict[str, Any]:
    rh = elo.get(fixture.home, elo_base)
    ra = elo.get(fixture.away, elo_base)

    elo_x = ((rh + home_adv) - ra) / 400.0
    form_diff = last5_points(form_hist, fixture.home) - last5_points(form_hist, fixture.away)

    travel_km = 0.0
    if fixture.home in team_coords and fixture.away in team_coords:
        hlat, hlon = team_coords[fixture.home]
        alat, alon = team_coords[fixture.away]
        travel_km = haversine_km(alat, alon, hlat, hlon)
    travel_1000 = travel_km / 1000.0

    margin = beta[0] + beta[1] * elo_x + beta[2] * form_diff + beta[3] * travel_1000
    winner = fixture.home if margin >= 0 else fixture.away
    abs_margin = abs(margin)
    conf_pct = confidence_percent(abs_margin, resid_sigma)

    return {
        "date": fixture.event_date.isoformat(),
        "game": fixture.game,
        "predicted_winner": winner,
        "predicted_winning_margin_points": f"{abs_margin:.1f}",
        "prediction_confidence_percent": f"{conf_pct:.1f}%",
    }


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="URC weekly predictions with confidence percentage.")
    parser.add_argument("--api-key", default=os.environ.get("THESPORTSDB_API_KEY", "123"))
    parser.add_argument("--league-name", default="United Rugby Championship")
    parser.add_argument("--league-id", default="4446")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--window-days", type=int, default=14)
    parser.add_argument("--min-train-date", default="2021-01-01")
    parser.add_argument("--elo-base", type=float, default=1500.0)
    parser.add_argument("--k-factor", type=float, default=24.0)
    parser.add_argument("--home-adv", type=float, default=80.0)
    parser.add_argument("--cache-dir", default=".thesportsdb_cache")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--sleep-s", type=float, default=0.25)
    parser.add_argument("--output-csv", default="")
    args = parser.parse_args()

    start = parse_date(args.start_date) if args.start_date else now_date()
    train_start = parse_date(args.min_train_date)
    if not start or not train_start:
        print("Invalid date input. Use YYYY-MM-DD.")
        return 2
    end = start + dt.timedelta(days=max(1, args.window_days) - 1)

    cache_dir = Path(args.cache_dir).resolve()
    use_cache = not args.no_cache

    _, team_coords = team_directory(args.api_key, args.league_name, cache_dir, use_cache)
    fixtures = fetch_fixtures(
        api_key=args.api_key,
        league_id=args.league_id,
        league_name=args.league_name,
        start_date=start,
        end_date=end,
        cache_dir=cache_dir,
        use_cache=use_cache,
        sleep_s=args.sleep_s,
    )
    if not fixtures:
        print("No fixtures found in selected window.")
        return 1

    elo, form_hist, beta, sigma = train_model(
        api_key=args.api_key,
        league_id=args.league_id,
        train_start=train_start,
        train_end=start - dt.timedelta(days=1),
        elo_base=args.elo_base,
        k_factor=args.k_factor,
        home_adv=args.home_adv,
        team_coords=team_coords,
        cache_dir=cache_dir,
        use_cache=use_cache,
        sleep_s=args.sleep_s,
    )

    rows = [
        predict_fixture(
            fixture=f,
            elo=elo,
            form_hist=form_hist,
            team_coords=team_coords,
            elo_base=args.elo_base,
            home_adv=args.home_adv,
            beta=beta,
            resid_sigma=sigma,
        )
        for f in fixtures
    ]

    print(f"URC predictions from {start.isoformat()} to {end.isoformat()}")
    print("")
    print("Date       | Game                                                         | Winner                         | Winning Margin | Confidence")
    print("-" * 150)
    for r in rows:
        print(
            f"{r['date']} | "
            f"{r['game'][:60]:60} | "
            f"{r['predicted_winner'][:30]:30} | "
            f"{float(r['predicted_winning_margin_points']):>6.1f} | "
            f"{r['prediction_confidence_percent']:>10}"
        )

    if args.output_csv:
        out = Path(args.output_csv).resolve()
        write_csv(out, rows)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

THESPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json"


@dataclass(frozen=True)
class Fixture:
    id_event: str
    event_date: dt.date
    home: str
    away: str

    @property
    def game(self) -> str:
        return f"{self.home} vs {self.away}"


def now_date() -> dt.date:
    return dt.datetime.now().date()


def parse_date(value: Any) -> Optional[dt.date]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def normalize_team(name: str) -> str:
    return " ".join((name or "").strip().split())


def cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def get_json(url: str, cache_dir: Path, use_cache: bool, timeout_s: int = 30) -> Dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    file_path = cache_dir / f"{cache_key(url)}.json"
    if use_cache and file_path.exists():
        return json.loads(file_path.read_text(encoding="utf-8"))

    req = urllib.request.Request(url, headers={"User-Agent": "urc-predictor/3.0"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if use_cache:
        file_path.write_text(json.dumps(data), encoding="utf-8")
    return data


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def team_directory(
    api_key: str,
    league_name: str,
    cache_dir: Path,
    use_cache: bool,
) -> Tuple[List[str], Dict[str, Tuple[float, float]]]:
    url = f"{THESPORTSDB_BASE}/{api_key}/search_all_teams.php?l={urllib.parse.quote_plus(league_name)}"
    data = get_json(url, cache_dir=cache_dir, use_cache=use_cache)
    teams = data.get("teams") or []

    ids: List[str] = []
    coords: Dict[str, Tuple[float, float]] = {}
    for t in teams:
        if not isinstance(t, dict):
            continue
        team_id = str(t.get("idTeam") or "").strip()
        team_name = normalize_team(str(t.get("strTeam") or ""))
        lat = to_float(t.get("strLatitude"))
        lon = to_float(t.get("strLongitude"))
        if team_id:
            ids.append(team_id)
        if team_name and lat is not None and lon is not None:
            coords[team_name] = (lat, lon)
    return ids, coords


def fetch_fixtures(
    api_key: str,
    league_id: str,
    league_name: str,
    start_date: dt.date,
    end_date: dt.date,
    cache_dir: Path,
    use_cache: bool,
    sleep_s: float,
    debug: bool,
) -> List[Fixture]:
    fixtures: Dict[str, Fixture] = {}

    def add_event(ev: Dict[str, Any]) -> None:
        event_id = str(ev.get("idEvent") or "").strip()
        d = parse_date(ev.get("dateEvent"))
        home = normalize_team(str(ev.get("strHomeTeam") or ""))
        away = normalize_team(str(ev.get("strAwayTeam") or ""))
        if not event_id or not d or not home or not away:
            return
        if start_date <= d <= end_date:
            fixtures[event_id] = Fixture(event_id, d, home, away)

    url = f"{THESPORTSDB_BASE}/{api_key}/eventsnextleague.php?id={league_id}"
    for ev in (get_json(url, cache_dir=cache_dir, use_cache=use_cache).get("events") or []):
        if isinstance(ev, dict):
            add_event(ev)

    team_ids, _ = team_directory(api_key, league_name, cache_dir, use_cache)
    if debug:
        print(f"[debug] team ids: {len(team_ids)}")
    for team_id in team_ids:
        url = f"{THESPORTSDB_BASE}/{api_key}/eventsnext.php?id={team_id}"
        try:
            data = get_json(url, cache_dir=cache_dir, use_cache=use_cache)
        except Exception as e:
            if debug:
                print(f"[debug] eventsnext failed for {team_id}: {e}")
            time.sleep(sleep_s)
            continue
        for ev in data.get("events") or []:
            if isinstance(ev, dict):
                add_event(ev)
        time.sleep(sleep_s)

    out = sorted(fixtures.values(), key=lambda f: (f.event_date.isoformat(), f.game))
    if debug:
        print(f"[debug] fixtures found: {len(out)}")
    return out


def current_season_start_year(today: dt.date) -> int:
    return today.year if today.month >= 8 else today.year - 1


def season_candidates(start_year: int, end_year_inclusive: int) -> List[str]:
    values: List[str] = []
    for y in range(start_year, end_year_inclusive + 1):
        values.append(f"{y}-{y+1}")
        values.append(f"{y}/{y+1}")
    seen: set[str] = set()
    dedup: List[str] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            dedup.append(v)
    return dedup


def result_points(team_score: float, opp_score: float) -> float:
    if team_score > opp_score:
        return 1.0
    if team_score < opp_score:
        return 0.0
    return 0.5


def last5_points(points_history: Dict[str, List[float]], team: str) -> float:
    values = points_history.get(team, [])
    return float(sum(values[-5:])) if values else 0.0


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
            factor = m[r][col]
            for c in range(col, n + 1):
                m[r][c] -= factor * m[col][c]

    return [m[i][n] for i in range(n)]


def weighted_ridge_fit(
    x_rows: List[Tuple[float, float, float]],
    y_vals: List[float],
    dates: List[dt.date],
    ref_date: dt.date,
    half_life_days: float = 240.0,
    ridge_lambda: float = 0.8,
) -> Tuple[Tuple[float, float, float, float], float]:
    if len(x_rows) < 10:
        return (0.0, 0.0, 0.0, 0.0), 10.0

    # Parameters: b0, b1, b2, b3
    xtwx = [[0.0] * 4 for _ in range(4)]
    xtwy = [0.0] * 4

    for i, (x1, x2, x3) in enumerate(x_rows):
        age_days = max(0, (ref_date - dates[i]).days)
        w = math.exp(-math.log(2) * age_days / max(1.0, half_life_days))
        row = [1.0, x1, x2, x3]
        y = y_vals[i]
        for r in range(4):
            xtwy[r] += w * row[r] * y
            for c in range(4):
                xtwx[r][c] += w * row[r] * row[c]

    for i in range(1, 4):
        xtwx[i][i] += ridge_lambda

    beta = solve_linear_system(xtwx, xtwy)

    # Weighted residual sigma for confidence calibration.
    sw = 0.0
    sse = 0.0
    for i, (x1, x2, x3) in enumerate(x_rows):
        age_days = max(0, (ref_date - dates[i]).days)
        w = math.exp(-math.log(2) * age_days / max(1.0, half_life_days))
        pred = beta[0] + beta[1] * x1 + beta[2] * x2 + beta[3] * x3
        err = y_vals[i] - pred
        sw += w
        sse += w * err * err
    sigma = math.sqrt(sse / max(sw, 1e-9))

    return (beta[0], beta[1], beta[2], beta[3]), sigma


def train_model(
    api_key: str,
    league_id: str,
    train_start: dt.date,
    train_end: dt.date,
    elo_base: float,
    k_factor: float,
    home_adv: float,
    team_coords: Dict[str, Tuple[float, float]],
    cache_dir: Path,
    use_cache: bool,
    sleep_s: float,
    debug: bool,
) -> Tuple[Dict[str, float], Dict[str, List[float]], Tuple[float, float, float, float], float]:
    elo: Dict[str, float] = {}
    form_hist: Dict[str, List[float]] = {}

    def rating(team: str) -> float:
        if team not in elo:
            elo[team] = elo_base
        return elo[team]

    events: List[Dict[str, Any]] = []
    for season in season_candidates(2021, current_season_start_year(now_date())):
        url = f"{THESPORTSDB_BASE}/{api_key}/eventsseason.php?id={league_id}&s={urllib.parse.quote_plus(season)}"
        try:
            data = get_json(url, cache_dir=cache_dir, use_cache=use_cache)
        except Exception as e:
            if debug:
                print(f"[debug] season fetch failed {season}: {e}")
            time.sleep(sleep_s)
            continue

        for ev in data.get("events") or []:
            if not isinstance(ev, dict):
                continue
            d = parse_date(ev.get("dateEvent"))
            hs = to_float(ev.get("intHomeScore"))
            aw = to_float(ev.get("intAwayScore"))
            if not d or hs is None or aw is None:
                continue
            if train_start <= d <= train_end:
                events.append(ev)
        time.sleep(sleep_s)

    events.sort(key=lambda ev: ((parse_date(ev.get("dateEvent")) or dt.date.max).isoformat(), str(ev.get("strTime") or "")))
    if debug:
        print(f"[debug] training matches: {len(events)}")

    x_rows: List[Tuple[float, float, float]] = []
    y_vals: List[float] = []
    d_vals: List[dt.date] = []

    for ev in events:
        home = normalize_team(str(ev.get("strHomeTeam") or ""))
        away = normalize_team(str(ev.get("strAwayTeam") or ""))
        d = parse_date(ev.get("dateEvent"))
        hs = to_float(ev.get("intHomeScore"))
        aw = to_float(ev.get("intAwayScore"))
        if not home or not away or not d or hs is None or aw is None:
            continue

        rh = rating(home)
        ra = rating(away)
        elo_x = ((rh + home_adv) - ra) / 400.0
        form_diff = last5_points(form_hist, home) - last5_points(form_hist, away)

        travel_km = 0.0
        if home in team_coords and away in team_coords:
            hlat, hlon = team_coords[home]
            alat, alon = team_coords[away]
            travel_km = haversine_km(alat, alon, hlat, hlon)
        travel_1000 = travel_km / 1000.0

        margin = hs - aw
        x_rows.append((elo_x, form_diff, travel_1000))
        y_vals.append(float(margin))
        d_vals.append(d)

        expected_home = 1.0 / (1.0 + 10 ** ((ra - (rh + home_adv)) / 400.0))
        actual_home = result_points(hs, aw)
        k_adj = k_factor * (1.0 + min(2.0, math.log1p(abs(margin)) / 2.0))
        delta = k_adj * (actual_home - expected_home)
        elo[home] = rh + delta
        elo[away] = ra - delta

        form_hist.setdefault(home, []).append(result_points(hs, aw))
        form_hist.setdefault(away, []).append(result_points(aw, hs))

    beta, resid_sigma = weighted_ridge_fit(x_rows, y_vals, d_vals, ref_date=train_end)
    if debug:
        print(
            f"[debug] beta={tuple(round(v, 3) for v in beta)} "
            f"residual_sigma={resid_sigma:.2f}"
        )
    return elo, form_hist, beta, resid_sigma


def confidence_label(abs_margin: float, resid_sigma: float) -> Tuple[str, float]:
    sigma = max(2.5, resid_sigma)
    z = abs_margin / sigma
    p = 1.0 / (1.0 + math.exp(-1.35 * (z - 1.0)))
    score = 50.0 + 50.0 * p
    if score >= 78:
        return "High", score
    if score >= 64:
        return "Medium", score
    return "Low", score


def predict_fixture(
    fixture: Fixture,
    elo: Dict[str, float],
    form_hist: Dict[str, List[float]],
    team_coords: Dict[str, Tuple[float, float]],
    elo_base: float,
    home_adv: float,
    beta: Tuple[float, float, float, float],
    resid_sigma: float,
) -> Dict[str, Any]:
    rh = elo.get(fixture.home, elo_base)
    ra = elo.get(fixture.away, elo_base)

    elo_x = ((rh + home_adv) - ra) / 400.0
    form_diff = last5_points(form_hist, fixture.home) - last5_points(form_hist, fixture.away)

    travel_km = 0.0
    if fixture.home in team_coords and fixture.away in team_coords:
        hlat, hlon = team_coords[fixture.home]
        alat, alon = team_coords[fixture.away]
        travel_km = haversine_km(alat, alon, hlat, hlon)
    travel_1000 = travel_km / 1000.0

    margin = beta[0] + beta[1] * elo_x + beta[2] * form_diff + beta[3] * travel_1000
    winner = fixture.home if margin >= 0 else fixture.away
    abs_margin = abs(margin)
    conf_label, conf_score = confidence_label(abs_margin, resid_sigma)

    return {
        "date": fixture.event_date.isoformat(),
        "game": fixture.game,
        "predicted_winner": winner,
        "predicted_winning_margin_points": f"{abs_margin:.1f}",
        "prediction_confidence": conf_label,
        "confidence_score_100": f"{conf_score:.1f}",
    }


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="URC weekly predictions using recency-weighted Elo and margin regression."
    )
    parser.add_argument("--api-key", default=os.environ.get("THESPORTSDB_API_KEY", "123"))
    parser.add_argument("--league-name", default="United Rugby Championship")
    parser.add_argument("--league-id", default="4446")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--window-days", type=int, default=14)
    parser.add_argument("--min-train-date", default="2021-01-01")
    parser.add_argument("--elo-base", type=float, default=1500.0)
    parser.add_argument("--k-factor", type=float, default=24.0)
    parser.add_argument("--home-adv", type=float, default=80.0)
    parser.add_argument("--cache-dir", default=".thesportsdb_cache")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--sleep-s", type=float, default=0.25)
    parser.add_argument("--output-csv", default="")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    start = parse_date(args.start_date) if args.start_date else now_date()
    train_start = parse_date(args.min_train_date)
    if not start or not train_start:
        print("Invalid date input. Use YYYY-MM-DD.", file=sys.stderr)
        return 2
    end = start + dt.timedelta(days=max(1, args.window_days) - 1)

    cache_dir = Path(args.cache_dir).resolve()
    use_cache = not args.no_cache

    team_ids, team_coords = team_directory(args.api_key, args.league_name, cache_dir, use_cache)
    if args.debug:
        print(f"[debug] teams={len(team_ids)} coords={len(team_coords)}")

    fixtures = fetch_fixtures(
        api_key=args.api_key,
        league_id=args.league_id,
        league_name=args.league_name,
        start_date=start,
        end_date=end,
        cache_dir=cache_dir,
        use_cache=use_cache,
        sleep_s=args.sleep_s,
        debug=args.debug,
    )
    if not fixtures:
        print("No fixtures found in selected window.", file=sys.stderr)
        return 1

    elo, form_hist, beta, resid_sigma = train_model(
        api_key=args.api_key,
        league_id=args.league_id,
        train_start=train_start,
        train_end=start - dt.timedelta(days=1),
        elo_base=args.elo_base,
        k_factor=args.k_factor,
        home_adv=args.home_adv,
        team_coords=team_coords,
        cache_dir=cache_dir,
        use_cache=use_cache,
        sleep_s=args.sleep_s,
        debug=args.debug,
    )

    rows = [
        predict_fixture(
            fixture=f,
            elo=elo,
            form_hist=form_hist,
            team_coords=team_coords,
            elo_base=args.elo_base,
            home_adv=args.home_adv,
            beta=beta,
            resid_sigma=resid_sigma,
        )
        for f in fixtures
    ]

    print(f"URC predictions from {start.isoformat()} to {end.isoformat()}")
    print("")
    print("Date       | Game                                                         | Winner                         | Margin | Conf   | Score")
    print("-" * 154)
    for r in rows:
        print(
            f"{r['date']} | "
            f"{r['game'][:60]:60} | "
            f"{r['predicted_winner'][:30]:30} | "
            f"{float(r['predicted_winning_margin_points']):>6.1f} | "
            f"{r['prediction_confidence'][:6]:6} | "
            f"{float(r['confidence_score_100']):>5.1f}"
        )

    if args.output_csv:
        out = Path(args.output_csv).resolve()
        write_csv(out, rows)
        if args.debug:
            print(f"[debug] csv written: {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

THESPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json"


def utc_now_date() -> dt.date:
    return dt.datetime.now().date()


def parse_date(s: str) -> Optional[dt.date]:
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def parse_float_or_none(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def cache_key_for_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def cached_get_json(url: str, cache_dir: Path, timeout_s: int = 30, use_cache: bool = True) -> Dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{cache_key_for_url(url)}.json"
    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    req = urllib.request.Request(url, headers={"User-Agent": "urc-predictor/2.2"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    if use_cache:
        cache_file.write_text(json.dumps(data), encoding="utf-8")
    return data


def normalize_team_name(name: str) -> str:
    return " ".join((name or "").strip().split())


@dataclass(frozen=True)
class Fixture:
    id_event: str
    event_date: dt.date
    home: str
    away: str

    @property
    def game(self) -> str:
        return f"{self.home} vs {self.away}"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def confidence_from_margin(abs_margin: float) -> str:
    # Simple interpretable bands from predicted winning margin.
    if abs_margin >= 14:
        return "High"
    if abs_margin >= 7:
        return "Medium"
    return "Low"


def get_team_data(
    api_key: str,
    league_name: str,
    cache_dir: Path,
    use_cache: bool,
) -> Tuple[List[str], Dict[str, Tuple[float, float]]]:
    url = f"{THESPORTSDB_BASE}/{api_key}/search_all_teams.php?l={urllib.parse.quote_plus(league_name)}"
    data = cached_get_json(url, cache_dir=cache_dir, use_cache=use_cache)
    teams = data.get("teams") or []

    team_ids: List[str] = []
    coords: Dict[str, Tuple[float, float]] = {}
    for t in teams:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("idTeam") or "").strip()
        name = normalize_team_name(str(t.get("strTeam") or ""))
        lat = parse_float_or_none(t.get("strLatitude"))
        lon = parse_float_or_none(t.get("strLongitude"))

        if tid:
            team_ids.append(tid)
        if name and lat is not None and lon is not None:
            coords[name] = (lat, lon)
    return team_ids, coords


def list_fixtures_for_range(
    api_key: str,
    league_id: str,
    league_name: str,
    start_date: dt.date,
    end_date: dt.date,
    cache_dir: Path,
    use_cache: bool,
    sleep_s: float,
    debug: bool,
) -> List[Fixture]:
    fixtures: Dict[str, Fixture] = {}

    def maybe_add_event(ev: Dict[str, Any]) -> None:
        if not isinstance(ev, dict):
            return
        id_event = str(ev.get("idEvent") or "").strip()
        event_date = parse_date(ev.get("dateEvent") or "")
        home = normalize_team_name(str(ev.get("strHomeTeam") or ""))
        away = normalize_team_name(str(ev.get("strAwayTeam") or ""))
        if not id_event or not event_date or not home or not away:
            return
        if start_date <= event_date <= end_date:
            fixtures[id_event] = Fixture(id_event=id_event, event_date=event_date, home=home, away=away)

    league_url = f"{THESPORTSDB_BASE}/{api_key}/eventsnextleague.php?id={league_id}"
    data = cached_get_json(league_url, cache_dir=cache_dir, use_cache=use_cache)
    for ev in data.get("events") or []:
        maybe_add_event(ev)

    team_ids, _ = get_team_data(api_key, league_name, cache_dir, use_cache)
    if debug:
        print(f"[debug] Team IDs discovered: {len(team_ids)}")
    for tid in team_ids:
        team_url = f"{THESPORTSDB_BASE}/{api_key}/eventsnext.php?id={tid}"
        try:
            team_data = cached_get_json(team_url, cache_dir=cache_dir, use_cache=use_cache)
        except Exception as e:
            if debug:
                print(f"[debug] eventsnext failed for team {tid}: {e}")
            time.sleep(sleep_s)
            continue
        for ev in team_data.get("events") or []:
            maybe_add_event(ev)
        time.sleep(sleep_s)

    out = sorted(fixtures.values(), key=lambda x: (x.event_date.isoformat(), x.game))
    if debug:
        print(f"[debug] Fixtures in requested window: {len(out)}")
    return out


def current_season_start_year(today: dt.date) -> int:
    return today.year if today.month >= 8 else today.year - 1


def season_strings(start_year: int, end_year_inclusive: int) -> List[str]:
    out: List[str] = []
    for y in range(start_year, end_year_inclusive + 1):
        out.append(f"{y}-{y+1}")
        out.append(f"{y}/{y+1}")
    seen: set[str] = set()
    dedup: List[str] = []
    for s in out:
        if s not in seen:
            seen.add(s)
            dedup.append(s)
    return dedup


def points_from_result(team_score: float, opp_score: float) -> float:
    if team_score > opp_score:
        return 1.0
    if team_score < opp_score:
        return 0.0
    return 0.5


def get_form_last5(points_hist: Dict[str, List[float]], team: str) -> float:
    vals = points_hist.get(team, [])
    return float(sum(vals[-5:])) if vals else 0.0


def fit_linear_regression(features: List[Tuple[float, float, float]], targets: List[float]) -> Tuple[float, float, float, float]:
    try:
        import numpy as np
    except Exception:
        return 0.0, 0.0, 0.0, 0.0

    if len(features) < 8:
        return 0.0, 0.0, 0.0, 0.0

    x = np.array([[1.0, f[0], f[1], f[2]] for f in features], dtype=float)
    y = np.array(targets, dtype=float)
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    return float(beta[0]), float(beta[1]), float(beta[2]), float(beta[3])


def fetch_and_train_model(
    api_key: str,
    league_id: str,
    train_start_date: dt.date,
    train_end_date_inclusive: dt.date,
    elo_base: float,
    k_factor: float,
    home_adv: float,
    team_coords: Dict[str, Tuple[float, float]],
    cache_dir: Path,
    use_cache: bool,
    sleep_s: float,
    debug: bool,
) -> Tuple[Dict[str, float], Dict[str, List[float]], Tuple[float, float, float, float]]:
    elo: Dict[str, float] = {}
    form_points: Dict[str, List[float]] = {}
    all_events: List[Dict[str, Any]] = []

    def get_r(team: str) -> float:
        if team not in elo:
            elo[team] = elo_base
        return elo[team]

    candidate_seasons = season_strings(2021, current_season_start_year(utc_now_date()))
    for s in candidate_seasons:
        url = f"{THESPORTSDB_BASE}/{api_key}/eventsseason.php?id={league_id}&s={urllib.parse.quote_plus(s)}"
        try:
            data = cached_get_json(url, cache_dir=cache_dir, use_cache=use_cache)
        except Exception as e:
            if debug:
                print(f"[debug] eventsseason failed for {s}: {e}")
            time.sleep(sleep_s)
            continue

        for ev in data.get("events") or []:
            if not isinstance(ev, dict):
                continue
            d = parse_date(ev.get("dateEvent") or "")
            hs = parse_float_or_none(ev.get("intHomeScore"))
            aw = parse_float_or_none(ev.get("intAwayScore"))
            if not d or hs is None or aw is None:
                continue
            if d < train_start_date or d > train_end_date_inclusive:
                continue
            all_events.append(ev)
        time.sleep(sleep_s)

    all_events.sort(key=lambda ev: ((parse_date(ev.get("dateEvent") or "") or dt.date.max).isoformat(), str(ev.get("strTime") or "")))
    if debug:
        print(f"[debug] Training events used: {len(all_events)}")

    features: List[Tuple[float, float, float]] = []
    targets: List[float] = []

    for ev in all_events:
        home = normalize_team_name(str(ev.get("strHomeTeam") or ""))
        away = normalize_team_name(str(ev.get("strAwayTeam") or ""))
        hs = parse_float_or_none(ev.get("intHomeScore"))
        aw = parse_float_or_none(ev.get("intAwayScore"))
        if not home or not away or hs is None or aw is None:
            continue

        r_home = get_r(home)
        r_away = get_r(away)

        adjusted_home = r_home + home_adv
        expected_home = 1.0 / (1.0 + 10 ** ((r_away - adjusted_home) / 400.0))
        score_home = points_from_result(hs, aw)
        delta = k_factor * (score_home - expected_home)
        elo[home] = r_home + delta
        elo[away] = r_away - delta

        # Internal model features only.
        home_form = get_form_last5(form_points, home)
        away_form = get_form_last5(form_points, away)
        form_diff = home_form - away_form

        travel_km = 0.0
        if home in team_coords and away in team_coords:
            hlat, hlon = team_coords[home]
            alat, alon = team_coords[away]
            travel_km = haversine_km(alat, alon, hlat, hlon)
        travel_1000km = travel_km / 1000.0

        elo_x = ((r_home + home_adv) - r_away) / 400.0
        margin = float(hs - aw)

        features.append((elo_x, form_diff, travel_1000km))
        targets.append(margin)

        form_points.setdefault(home, []).append(points_from_result(hs, aw))
        form_points.setdefault(away, []).append(points_from_result(aw, hs))

    model = fit_linear_regression(features, targets)
    if debug:
        b0, b1, b2, b3 = model
        print(f"[debug] model intercept={b0:.3f}, elo_coef={b1:.3f}, form_coef={b2:.3f}, travel_coef={b3:.3f}")
    return elo, form_points, model


def predict_fixture(
    elo: Dict[str, float],
    form_points: Dict[str, List[float]],
    fixture: Fixture,
    team_coords: Dict[str, Tuple[float, float]],
    elo_base: float,
    home_adv: float,
    model: Tuple[float, float, float, float],
) -> Dict[str, Any]:
    b0, b1, b2, b3 = model

    r_home = elo.get(fixture.home, elo_base)
    r_away = elo.get(fixture.away, elo_base)
    elo_x = ((r_home + home_adv) - r_away) / 400.0

    # Internal model features only.
    form_diff = get_form_last5(form_points, fixture.home) - get_form_last5(form_points, fixture.away)
    travel_km = 0.0
    if fixture.home in team_coords and fixture.away in team_coords:
        hlat, hlon = team_coords[fixture.home]
        alat, alon = team_coords[fixture.away]
        travel_km = haversine_km(alat, alon, hlat, hlon)
    travel_1000km = travel_km / 1000.0

    pred_margin = b0 + b1 * elo_x + b2 * form_diff + b3 * travel_1000km
    abs_margin = abs(pred_margin)
    winner = fixture.home if pred_margin >= 0 else fixture.away

    return {
        "date": fixture.event_date.isoformat(),
        "game": fixture.game,
        "predicted_winner": winner,
        "predicted_winning_margin_points": f"{abs_margin:.1f}",
        "prediction_confidence": confidence_from_margin(abs_margin),
    }


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Predict URC weekly fixtures using Elo + form + travel features.")
    parser.add_argument("--api-key", default=os.environ.get("THESPORTSDB_API_KEY", "123"))
    parser.add_argument("--league-name", default="United Rugby Championship")
    parser.add_argument("--league-id", default="4446")
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--start-date", default="")
    parser.add_argument("--min-train-date", default="2021-01-01")
    parser.add_argument("--elo-base", type=float, default=1500.0)
    parser.add_argument("--k-factor", type=float, default=24.0)
    parser.add_argument("--home-adv", type=float, default=80.0)
    parser.add_argument("--output-csv", default="")
    parser.add_argument("--cache-dir", default=".thesportsdb_cache")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--sleep-s", type=float, default=0.25)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    start = parse_date(args.start_date) if args.start_date else utc_now_date()
    if not start:
        print("Invalid --start-date. Use YYYY-MM-DD.", file=sys.stderr)
        return 2
    min_train = parse_date(args.min_train_date)
    if not min_train:
        print("Invalid --min-train-date. Use YYYY-MM-DD.", file=sys.stderr)
        return 2
    end = start + dt.timedelta(days=args.window_days - 1)

    cache_dir = Path(args.cache_dir).resolve()
    use_cache = not args.no_cache

    team_ids, team_coords = get_team_data(args.api_key, args.league_name, cache_dir, use_cache)
    if args.debug:
        print(f"[debug] Team count: {len(team_ids)} | Team coords: {len(team_coords)}")

    fixtures = list_fixtures_for_range(
        api_key=args.api_key,
        league_id=args.league_id,
        league_name=args.league_name,
        start_date=start,
        end_date=end,
        cache_dir=cache_dir,
        use_cache=use_cache,
        sleep_s=args.sleep_s,
        debug=args.debug,
    )
    if not fixtures:
        print("No fixtures found for the requested window.", file=sys.stderr)
        return 1

    elo, form_points, model = fetch_and_train_model(
        api_key=args.api_key,
        league_id=args.league_id,
        train_start_date=min_train,
        train_end_date_inclusive=start - dt.timedelta(days=1),
        elo_base=args.elo_base,
        k_factor=args.k_factor,
        home_adv=args.home_adv,
        team_coords=team_coords,
        cache_dir=cache_dir,
        use_cache=use_cache,
        sleep_s=args.sleep_s,
        debug=args.debug,
    )

    rows = [
        predict_fixture(
            elo=elo,
            form_points=form_points,
            fixture=f,
            team_coords=team_coords,
            elo_base=args.elo_base,
            home_adv=args.home_adv,
            model=model,
        )
        for f in fixtures
    ]

    print(f"URC predictions from {start.isoformat()} to {end.isoformat()}")
    print("")
    print(
        "Date       | Game                                                         | Predicted Winner                | Margin  | Confidence"
    )
    print("-" * 146)
    for r in rows:
        print(
            f"{r['date']} | "
            f"{r['game'][:60]:60} | "
            f"{r['predicted_winner'][:30]:30} | "
            f"{float(r['predicted_winning_margin_points']):>6.1f} | "
            f"{r['prediction_confidence']}"
        )

    if args.output_csv:
        out = Path(args.output_csv).resolve()
        write_csv(out, rows)
        if args.debug:
            print(f"[debug] wrote CSV: {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

THESPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json"


def utc_now_date() -> dt.date:
    return dt.datetime.now().date()


def parse_date(s: str) -> Optional[dt.date]:
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def parse_float_or_none(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def cache_key_for_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def cached_get_json(url: str, cache_dir: Path, timeout_s: int = 30, use_cache: bool = True) -> Dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{cache_key_for_url(url)}.json"
    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    req = urllib.request.Request(url, headers={"User-Agent": "urc-predictor/2.1"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    if use_cache:
        cache_file.write_text(json.dumps(data), encoding="utf-8")
    return data


def normalize_team_name(name: str) -> str:
    return " ".join((name or "").strip().split())


@dataclass(frozen=True)
class Fixture:
    id_event: str
    event_date: dt.date
    home: str
    away: str

    @property
    def game(self) -> str:
        return f"{self.home} vs {self.away}"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_team_data(
    api_key: str,
    league_name: str,
    cache_dir: Path,
    use_cache: bool,
) -> Tuple[List[str], Dict[str, Tuple[float, float]]]:
    url = f"{THESPORTSDB_BASE}/{api_key}/search_all_teams.php?l={urllib.parse.quote_plus(league_name)}"
    data = cached_get_json(url, cache_dir=cache_dir, use_cache=use_cache)
    teams = data.get("teams") or []

    team_ids: List[str] = []
    coords: Dict[str, Tuple[float, float]] = {}
    for t in teams:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("idTeam") or "").strip()
        name = normalize_team_name(str(t.get("strTeam") or ""))
        lat = parse_float_or_none(t.get("strLatitude"))
        lon = parse_float_or_none(t.get("strLongitude"))

        if tid:
            team_ids.append(tid)
        if name and lat is not None and lon is not None:
            coords[name] = (lat, lon)
    return team_ids, coords


def list_fixtures_for_range(
    api_key: str,
    league_id: str,
    league_name: str,
    start_date: dt.date,
    end_date: dt.date,
    cache_dir: Path,
    use_cache: bool,
    sleep_s: float,
    debug: bool,
) -> List[Fixture]:
    fixtures: Dict[str, Fixture] = {}

    def maybe_add_event(ev: Dict[str, Any]) -> None:
        if not isinstance(ev, dict):
            return
        id_event = str(ev.get("idEvent") or "").strip()
        event_date = parse_date(ev.get("dateEvent") or "")
        home = normalize_team_name(str(ev.get("strHomeTeam") or ""))
        away = normalize_team_name(str(ev.get("strAwayTeam") or ""))
        if not id_event or not event_date or not home or not away:
            return
        if start_date <= event_date <= end_date:
            fixtures[id_event] = Fixture(id_event=id_event, event_date=event_date, home=home, away=away)

    # 1) Upcoming fixtures from league endpoint.
    league_url = f"{THESPORTSDB_BASE}/{api_key}/eventsnextleague.php?id={league_id}"
    data = cached_get_json(league_url, cache_dir=cache_dir, use_cache=use_cache)
    for ev in data.get("events") or []:
        maybe_add_event(ev)

    # 2) Supplement from each team endpoint to avoid missing fixtures.
    team_ids, _ = get_team_data(api_key, league_name, cache_dir, use_cache)
    if debug:
        print(f"[debug] Team IDs discovered: {len(team_ids)}")

    for tid in team_ids:
        team_url = f"{THESPORTSDB_BASE}/{api_key}/eventsnext.php?id={tid}"
        try:
            team_data = cached_get_json(team_url, cache_dir=cache_dir, use_cache=use_cache)
        except Exception as e:
            if debug:
                print(f"[debug] eventsnext failed for team {tid}: {e}")
            time.sleep(sleep_s)
            continue
        for ev in team_data.get("events") or []:
            maybe_add_event(ev)
        time.sleep(sleep_s)

    out = sorted(fixtures.values(), key=lambda x: (x.event_date.isoformat(), x.game))
    if debug:
        print(f"[debug] Fixtures in requested window: {len(out)}")
    return out


def current_season_start_year(today: dt.date) -> int:
    return today.year if today.month >= 8 else today.year - 1


def season_strings(start_year: int, end_year_inclusive: int) -> List[str]:
    out: List[str] = []
    for y in range(start_year, end_year_inclusive + 1):
        out.append(f"{y}-{y+1}")
        out.append(f"{y}/{y+1}")
    seen: set[str] = set()
    dedup: List[str] = []
    for s in out:
        if s not in seen:
            seen.add(s)
            dedup.append(s)
    return dedup


def points_from_result(team_score: float, opp_score: float) -> float:
    if team_score > opp_score:
        return 1.0
    if team_score < opp_score:
        return 0.0
    return 0.5


def get_form_last5(points_hist: Dict[str, List[float]], team: str) -> float:
    vals = points_hist.get(team, [])
    return float(sum(vals[-5:])) if vals else 0.0


def fit_linear_regression(features: List[Tuple[float, float, float]], targets: List[float]) -> Tuple[float, float, float, float]:
    try:
        import numpy as np
    except Exception:
        return 0.0, 0.0, 0.0, 0.0

    if len(features) < 8:
        return 0.0, 0.0, 0.0, 0.0

    x = np.array([[1.0, f[0], f[1], f[2]] for f in features], dtype=float)
    y = np.array(targets, dtype=float)
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    return float(beta[0]), float(beta[1]), float(beta[2]), float(beta[3])


def fetch_and_train_model(
    api_key: str,
    league_id: str,
    train_start_date: dt.date,
    train_end_date_inclusive: dt.date,
    elo_base: float,
    k_factor: float,
    home_adv: float,
    team_coords: Dict[str, Tuple[float, float]],
    cache_dir: Path,
    use_cache: bool,
    sleep_s: float,
    debug: bool,
) -> Tuple[Dict[str, float], Dict[str, List[float]], Tuple[float, float, float, float]]:
    elo: Dict[str, float] = {}
    form_points: Dict[str, List[float]] = {}
    all_events: List[Dict[str, Any]] = []

    def get_r(team: str) -> float:
        if team not in elo:
            elo[team] = elo_base
        return elo[team]

    candidate_seasons = season_strings(2021, current_season_start_year(utc_now_date()))
    for s in candidate_seasons:
        url = f"{THESPORTSDB_BASE}/{api_key}/eventsseason.php?id={league_id}&s={urllib.parse.quote_plus(s)}"
        try:
            data = cached_get_json(url, cache_dir=cache_dir, use_cache=use_cache)
        except Exception as e:
            if debug:
                print(f"[debug] eventsseason failed for {s}: {e}")
            time.sleep(sleep_s)
            continue

        for ev in data.get("events") or []:
            if not isinstance(ev, dict):
                continue
            d = parse_date(ev.get("dateEvent") or "")
            hs = parse_float_or_none(ev.get("intHomeScore"))
            aw = parse_float_or_none(ev.get("intAwayScore"))
            if not d or hs is None or aw is None:
                continue
            if d < train_start_date or d > train_end_date_inclusive:
                continue
            all_events.append(ev)
        time.sleep(sleep_s)

    all_events.sort(key=lambda ev: ((parse_date(ev.get("dateEvent") or "") or dt.date.max).isoformat(), str(ev.get("strTime") or "")))
    if debug:
        print(f"[debug] Training events used: {len(all_events)}")

    features: List[Tuple[float, float, float]] = []
    targets: List[float] = []

    for ev in all_events:
        home = normalize_team_name(str(ev.get("strHomeTeam") or ""))
        away = normalize_team_name(str(ev.get("strAwayTeam") or ""))
        hs = parse_float_or_none(ev.get("intHomeScore"))
        aw = parse_float_or_none(ev.get("intAwayScore"))
        if not home or not away or hs is None or aw is None:
            continue

        r_home = get_r(home)
        r_away = get_r(away)

        adjusted_home = r_home + home_adv
        expected_home = 1.0 / (1.0 + 10 ** ((r_away - adjusted_home) / 400.0))
        score_home = points_from_result(hs, aw)
        delta = k_factor * (score_home - expected_home)
        elo[home] = r_home + delta
        elo[away] = r_away - delta

        # Features requested by user, used in model only.
        home_form = get_form_last5(form_points, home)
        away_form = get_form_last5(form_points, away)
        form_diff = home_form - away_form

        travel_km = 0.0
        if home in team_coords and away in team_coords:
            hlat, hlon = team_coords[home]
            alat, alon = team_coords[away]
            travel_km = haversine_km(alat, alon, hlat, hlon)
        travel_1000km = travel_km / 1000.0

        elo_x = ((r_home + home_adv) - r_away) / 400.0
        margin = float(hs - aw)

        features.append((elo_x, form_diff, travel_1000km))
        targets.append(margin)

        form_points.setdefault(home, []).append(points_from_result(hs, aw))
        form_points.setdefault(away, []).append(points_from_result(aw, hs))

    model = fit_linear_regression(features, targets)
    if debug:
        b0, b1, b2, b3 = model
        print(f"[debug] model intercept={b0:.3f}, elo_coef={b1:.3f}, form_coef={b2:.3f}, travel_coef={b3:.3f}")
    return elo, form_points, model


def predict_fixture(
    elo: Dict[str, float],
    form_points: Dict[str, List[float]],
    fixture: Fixture,
    team_coords: Dict[str, Tuple[float, float]],
    elo_base: float,
    home_adv: float,
    model: Tuple[float, float, float, float],
) -> Dict[str, Any]:
    b0, b1, b2, b3 = model

    r_home = elo.get(fixture.home, elo_base)
    r_away = elo.get(fixture.away, elo_base)
    elo_x = ((r_home + home_adv) - r_away) / 400.0

    # Features remain internal.
    form_diff = get_form_last5(form_points, fixture.home) - get_form_last5(form_points, fixture.away)
    travel_km = 0.0
    if fixture.home in team_coords and fixture.away in team_coords:
        hlat, hlon = team_coords[fixture.home]
        alat, alon = team_coords[fixture.away]
        travel_km = haversine_km(alat, alon, hlat, hlon)
    travel_1000km = travel_km / 1000.0

    pred_margin = b0 + b1 * elo_x + b2 * form_diff + b3 * travel_1000km
    winner = fixture.home if pred_margin >= 0 else fixture.away

    return {
        "date": fixture.event_date.isoformat(),
        "game": fixture.game,
        "predicted_winner": winner,
        "predicted_winning_margin_points": f"{abs(pred_margin):.1f}",
    }


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Predict URC weekly fixtures using Elo + form + travel features.")
    parser.add_argument("--api-key", default=os.environ.get("THESPORTSDB_API_KEY", "123"))
    parser.add_argument("--league-name", default="United Rugby Championship")
    parser.add_argument("--league-id", default="4446")
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--start-date", default="")
    parser.add_argument("--min-train-date", default="2021-01-01")
    parser.add_argument("--elo-base", type=float, default=1500.0)
    parser.add_argument("--k-factor", type=float, default=24.0)
    parser.add_argument("--home-adv", type=float, default=80.0)
    parser.add_argument("--output-csv", default="")
    parser.add_argument("--cache-dir", default=".thesportsdb_cache")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--sleep-s", type=float, default=0.25)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    start = parse_date(args.start_date) if args.start_date else utc_now_date()
    if not start:
        print("Invalid --start-date. Use YYYY-MM-DD.", file=sys.stderr)
        return 2
    min_train = parse_date(args.min_train_date)
    if not min_train:
        print("Invalid --min-train-date. Use YYYY-MM-DD.", file=sys.stderr)
        return 2
    end = start + dt.timedelta(days=args.window_days - 1)

    cache_dir = Path(args.cache_dir).resolve()
    use_cache = not args.no_cache

    team_ids, team_coords = get_team_data(args.api_key, args.league_name, cache_dir, use_cache)
    if args.debug:
        print(f"[debug] Team count: {len(team_ids)} | Team coords: {len(team_coords)}")

    fixtures = list_fixtures_for_range(
        api_key=args.api_key,
        league_id=args.league_id,
        league_name=args.league_name,
        start_date=start,
        end_date=end,
        cache_dir=cache_dir,
        use_cache=use_cache,
        sleep_s=args.sleep_s,
        debug=args.debug,
    )
    if not fixtures:
        print("No fixtures found for the requested window.", file=sys.stderr)
        return 1

    elo, form_points, model = fetch_and_train_model(
        api_key=args.api_key,
        league_id=args.league_id,
        train_start_date=min_train,
        train_end_date_inclusive=start - dt.timedelta(days=1),
        elo_base=args.elo_base,
        k_factor=args.k_factor,
        home_adv=args.home_adv,
        team_coords=team_coords,
        cache_dir=cache_dir,
        use_cache=use_cache,
        sleep_s=args.sleep_s,
        debug=args.debug,
    )

    rows = [
        predict_fixture(
            elo=elo,
            form_points=form_points,
            fixture=f,
            team_coords=team_coords,
            elo_base=args.elo_base,
            home_adv=args.home_adv,
            model=model,
        )
        for f in fixtures
    ]

    print(f"URC predictions from {start.isoformat()} to {end.isoformat()}")
    print("")
    print("Date       | Game                                                         | Predicted Winner                | Predicted Margin")
    print("-" * 132)
    for r in rows:
        print(
            f"{r['date']} | "
            f"{r['game'][:60]:60} | "
            f"{r['predicted_winner'][:30]:30} | "
            f"{float(r['predicted_winning_margin_points']):>6.1f} pts"
        )

    if args.output_csv:
        out = Path(args.output_csv).resolve()
        write_csv(out, rows)
        if args.debug:
            print(f"[debug] wrote CSV: {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

THESPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json"


def utc_now_date() -> dt.date:
    return dt.datetime.now().date()


def parse_date(s: str) -> Optional[dt.date]:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def parse_float_or_none(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def cache_key_for_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def cached_get_json(url: str, cache_dir: Path, timeout_s: int = 30, use_cache: bool = True) -> Dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = cache_key_for_url(url)
    cache_file = cache_dir / f"{key}.json"
    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    req = urllib.request.Request(url, headers={"User-Agent": "urc-predictor/2.0"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    if use_cache:
        cache_file.write_text(json.dumps(data), encoding="utf-8")
    return data


def normalize_team_name(name: str) -> str:
    return " ".join((name or "").strip().split())


@dataclass(frozen=True)
class Fixture:
    id_event: str
    event_date: dt.date
    home: str
    away: str


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_team_coordinates(api_key: str, league_name: str, cache_dir: Path, use_cache: bool) -> Dict[str, Tuple[float, float]]:
    url = f"{THESPORTSDB_BASE}/{api_key}/search_all_teams.php?l={urllib.parse.quote_plus(league_name)}"
    data = cached_get_json(url, cache_dir=cache_dir, use_cache=use_cache)
    teams = data.get("teams") or []
    out: Dict[str, Tuple[float, float]] = {}
    for t in teams:
        if not isinstance(t, dict):
            continue
        name = normalize_team_name(str(t.get("strTeam") or ""))
        if not name:
            continue
        lat = parse_float_or_none(t.get("strLatitude"))
        lon = parse_float_or_none(t.get("strLongitude"))
        if lat is None or lon is None:
            continue
        out[name] = (lat, lon)
    return out


def list_fixtures_for_range(
    api_key: str,
    league_id: str,
    start_date: dt.date,
    end_date: dt.date,
    cache_dir: Path,
    use_cache: bool,
) -> List[Fixture]:
    fixtures: Dict[str, Fixture] = {}
    url = f"{THESPORTSDB_BASE}/{api_key}/eventsnextleague.php?id={league_id}"
    data = cached_get_json(url, cache_dir=cache_dir, use_cache=use_cache)
    for ev in data.get("events") or []:
        if not isinstance(ev, dict):
            continue
        id_event = str(ev.get("idEvent") or "").strip()
        event_date = parse_date(str(ev.get("dateEvent") or ""))
        home = normalize_team_name(str(ev.get("strHomeTeam") or ""))
        away = normalize_team_name(str(ev.get("strAwayTeam") or ""))
        if not id_event or not event_date or not home or not away:
            continue
        if start_date <= event_date <= end_date:
            fixtures[id_event] = Fixture(id_event=id_event, event_date=event_date, home=home, away=away)
    return sorted(fixtures.values(), key=lambda x: x.event_date.isoformat())


def current_season_start_year(today: dt.date) -> int:
    return today.year if today.month >= 8 else today.year - 1


def season_strings(start_year: int, end_year_inclusive: int) -> List[str]:
    out: List[str] = []
    for y in range(start_year, end_year_inclusive + 1):
        out.append(f"{y}-{y+1}")
        out.append(f"{y}/{y+1}")
    seen: set[str] = set()
    dedup: List[str] = []
    for s in out:
        if s not in seen:
            seen.add(s)
            dedup.append(s)
    return dedup


def points_from_result(team_score: float, opp_score: float) -> float:
    if team_score > opp_score:
        return 1.0
    if team_score < opp_score:
        return 0.0
    return 0.5


def get_form_last5(team_points_history: Dict[str, List[float]], team: str) -> float:
    vals = team_points_history.get(team, [])
    if not vals:
        return 0.0
    return sum(vals[-5:])


def fit_linear_regression(features: List[Tuple[float, float, float]], targets: List[float]) -> Tuple[float, float, float, float]:
    # OLS on [1, elo_x, form_diff, travel_1000km]
    try:
        import numpy as np
    except Exception:
        # Fallback if numpy unavailable.
        return 0.0, 0.0, 0.0, 0.0

    if len(features) < 8:
        return 0.0, 0.0, 0.0, 0.0

    x = np.array([[1.0, f[0], f[1], f[2]] for f in features], dtype=float)
    y = np.array(targets, dtype=float)
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    return float(beta[0]), float(beta[1]), float(beta[2]), float(beta[3])


def fetch_and_train_model(
    api_key: str,
    league_id: str,
    train_start_date: dt.date,
    train_end_date_inclusive: dt.date,
    elo_base: float,
    k_factor: float,
    home_adv: float,
    team_coords: Dict[str, Tuple[float, float]],
    cache_dir: Path,
    use_cache: bool,
    sleep_s: float,
    debug: bool,
) -> Tuple[Dict[str, float], Dict[str, List[float]], Tuple[float, float, float, float]]:
    elo: Dict[str, float] = {}
    form_points: Dict[str, List[float]] = {}
    all_events: List[Dict[str, Any]] = []

    def get_r(team: str) -> float:
        team = normalize_team_name(team)
        if team not in elo:
            elo[team] = elo_base
        return elo[team]

    today = utc_now_date()
    candidate_seasons = season_strings(2021, current_season_start_year(today))

    for s in candidate_seasons:
        url = f"{THESPORTSDB_BASE}/{api_key}/eventsseason.php?id={league_id}&s={urllib.parse.quote_plus(s)}"
        try:
            data = cached_get_json(url, cache_dir=cache_dir, use_cache=use_cache)
        except Exception as e:
            if debug:
                print(f"[debug] eventsseason failed for {s}: {e}")
            time.sleep(sleep_s)
            continue
        for ev in data.get("events") or []:
            if not isinstance(ev, dict):
                continue
            d = parse_date(str(ev.get("dateEvent") or ""))
            hs = parse_float_or_none(ev.get("intHomeScore"))
            aw = parse_float_or_none(ev.get("intAwayScore"))
            if not d or hs is None or aw is None:
                continue
            if d < train_start_date or d > train_end_date_inclusive:
                continue
            all_events.append(ev)
        time.sleep(sleep_s)

    all_events.sort(key=lambda ev: ((parse_date(str(ev.get("dateEvent") or "")) or dt.date.max).isoformat(), str(ev.get("strTime") or "")))
    if debug:
        print(f"[debug] Training events used: {len(all_events)}")

    features: List[Tuple[float, float, float]] = []
    targets: List[float] = []

    for ev in all_events:
        home = normalize_team_name(str(ev.get("strHomeTeam") or ""))
        away = normalize_team_name(str(ev.get("strAwayTeam") or ""))
        hs = parse_float_or_none(ev.get("intHomeScore"))
        aw = parse_float_or_none(ev.get("intAwayScore"))
        if not home or not away or hs is None or aw is None:
            continue

        r_home = get_r(home)
        r_away = get_r(away)

        # Elo expected probability and update.
        adjusted_home = r_home + home_adv
        expected_home = 1.0 / (1.0 + 10 ** ((r_away - adjusted_home) / 400.0))
        score_home = points_from_result(hs, aw)
        delta = k_factor * (score_home - expected_home)
        elo[home] = r_home + delta
        elo[away] = r_away - delta

        # Last 5 games form feature (home minus away).
        home_form = get_form_last5(form_points, home)
        away_form = get_form_last5(form_points, away)
        form_diff = home_form - away_form

        # Away travel distance feature (in thousands of km).
        travel_km = 0.0
        if home in team_coords and away in team_coords:
            hlat, hlon = team_coords[home]
            alat, alon = team_coords[away]
            travel_km = haversine_km(alat, alon, hlat, hlon)
        travel_1000km = travel_km / 1000.0

        elo_x = ((r_home + home_adv) - r_away) / 400.0
        margin = float(hs - aw)
        features.append((elo_x, form_diff, travel_1000km))
        targets.append(margin)

        # Update form histories after match.
        form_points.setdefault(home, []).append(points_from_result(hs, aw))
        form_points.setdefault(away, []).append(points_from_result(aw, hs))

    model = fit_linear_regression(features, targets)
    if debug:
        b0, b1, b2, b3 = model
        print(f"[debug] model intercept={b0:.3f}, elo_coef={b1:.3f}, form_coef={b2:.3f}, travel_coef={b3:.3f}")
    return elo, form_points, model


def predict_fixture(
    elo: Dict[str, float],
    form_points: Dict[str, List[float]],
    fixture: Fixture,
    team_coords: Dict[str, Tuple[float, float]],
    elo_base: float,
    home_adv: float,
    model: Tuple[float, float, float, float],
) -> Dict[str, Any]:
    b0, b1, b2, b3 = model
    home = fixture.home
    away = fixture.away
    r_home = elo.get(home, elo_base)
    r_away = elo.get(away, elo_base)
    elo_x = ((r_home + home_adv) - r_away) / 400.0

    home_form_last5 = get_form_last5(form_points, home)
    away_form_last5 = get_form_last5(form_points, away)
    form_diff = home_form_last5 - away_form_last5

    travel_km = 0.0
    if home in team_coords and away in team_coords:
        hlat, hlon = team_coords[home]
        alat, alon = team_coords[away]
        travel_km = haversine_km(alat, alon, hlat, hlon)
    travel_1000km = travel_km / 1000.0

    pred_margin = b0 + b1 * elo_x + b2 * form_diff + b3 * travel_1000km
    winner = home if pred_margin >= 0 else away

    return {
        "date": fixture.event_date.isoformat(),
        "home": home,
        "away": away,
        "home_last5_points": f"{home_form_last5:.1f}",
        "away_last5_points": f"{away_form_last5:.1f}",
        "away_travel_km": f"{travel_km:.0f}",
        "predicted_winner": winner,
        "predicted_winning_margin_points": f"{abs(pred_margin):.1f}",
    }


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Predict URC weekly fixtures using Elo + last-5 form + away travel distance."
    )
    parser.add_argument("--api-key", default=os.environ.get("THESPORTSDB_API_KEY", "123"))
    parser.add_argument("--league-name", default="United Rugby Championship")
    parser.add_argument("--league-id", default="4446")
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--start-date", default="")
    parser.add_argument("--min-train-date", default="2021-01-01")
    parser.add_argument("--elo-base", type=float, default=1500.0)
    parser.add_argument("--k-factor", type=float, default=24.0)
    parser.add_argument("--home-adv", type=float, default=80.0)
    parser.add_argument("--output-csv", default="")
    parser.add_argument("--cache-dir", default=".thesportsdb_cache")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--sleep-s", type=float, default=0.25)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    start = parse_date(args.start_date) if args.start_date else utc_now_date()
    if not start:
        print("Invalid --start-date. Use YYYY-MM-DD.", file=sys.stderr)
        return 2
    min_train = parse_date(args.min_train_date)
    if not min_train:
        print("Invalid --min-train-date. Use YYYY-MM-DD.", file=sys.stderr)
        return 2
    end = start + dt.timedelta(days=args.window_days - 1)

    cache_dir = Path(args.cache_dir).resolve()
    use_cache = not args.no_cache

    team_coords = get_team_coordinates(args.api_key, args.league_name, cache_dir, use_cache)
    fixtures = list_fixtures_for_range(
        api_key=args.api_key,
        league_id=args.league_id,
        start_date=start,
        end_date=end,
        cache_dir=cache_dir,
        use_cache=use_cache,
    )
    if not fixtures:
        print("No fixtures found for the requested window.", file=sys.stderr)
        return 1

    elo, form_points, model = fetch_and_train_model(
        api_key=args.api_key,
        league_id=args.league_id,
        train_start_date=min_train,
        train_end_date_inclusive=start - dt.timedelta(days=1),
        elo_base=args.elo_base,
        k_factor=args.k_factor,
        home_adv=args.home_adv,
        team_coords=team_coords,
        cache_dir=cache_dir,
        use_cache=use_cache,
        sleep_s=args.sleep_s,
        debug=args.debug,
    )

    rows: List[Dict[str, Any]] = [
        predict_fixture(
            elo=elo,
            form_points=form_points,
            fixture=f,
            team_coords=team_coords,
            elo_base=args.elo_base,
            home_adv=args.home_adv,
            model=model,
        )
        for f in fixtures
    ]

    print(f"URC predictions from {start.isoformat()} to {end.isoformat()}")
    print("")
    print(
        "Date       | Home                         | Away                         | H_Last5 | A_Last5 | AwayTravelKM | Winner                       | Margin"
    )
    print("-" * 148)
    for r in rows:
        print(
            f"{r['date']} | "
            f"{r['home'][:28]:28} | "
            f"{r['away'][:28]:28} | "
            f"{float(r['home_last5_points']):>7.1f} | "
            f"{float(r['away_last5_points']):>7.1f} | "
            f"{int(float(r['away_travel_km'])):>12} | "
            f"{r['predicted_winner'][:28]:28} | "
            f"{float(r['predicted_winning_margin_points']):>5.1f}"
        )

    if args.output_csv:
        out = Path(args.output_csv).resolve()
        write_csv(out, rows)
        if args.debug:
            print(f"[debug] wrote CSV: {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


THESPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json"


def utc_now_date() -> dt.date:
    # Use local date so "coming week" matches the user's machine expectation.
    return dt.datetime.now().date()


def parse_date(s: str) -> Optional[dt.date]:
    if not s:
        return None
    s = s.strip()
    # TheSportsDB typically uses YYYY-MM-DD.
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def parse_float_or_none(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def cache_key_for_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def cached_get_json(
    url: str,
    cache_dir: Path,
    timeout_s: int = 30,
    use_cache: bool = True,
) -> Dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = cache_key_for_url(url)
    cache_file = cache_dir / f"{key}.json"
    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    req = urllib.request.Request(url, headers={"User-Agent": "urc-predictor/1.0"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    if use_cache:
        cache_file.write_text(json.dumps(data), encoding="utf-8")
    return data


@dataclass(frozen=True)
class Fixture:
    id_event: str
    event_date: dt.date
    home: str
    away: str


def normalize_team_name(name: str) -> str:
    # TheSportsDB team names sometimes have minor differences; keep it simple.
    return " ".join((name or "").strip().split())


def resolve_urc_league_id(api_key: str, league_name: str, cache_dir: Path, use_cache: bool) -> str:
    q = urllib.parse.quote_plus(league_name)
    url = f"{THESPORTSDB_BASE}/{api_key}/searchleague.php?l={q}"
    data = cached_get_json(url, cache_dir=cache_dir, use_cache=use_cache)
    leagues = data.get("countryleagues") or data.get("leagues") or data.get("results") or []
    if not leagues:
        # Some responses use "countryleagues" only when querying by country;
        # do a best-effort fallback on raw dict keys.
        leagues = []
        for v in data.values():
            if isinstance(v, list):
                leagues = v
                break

    if not leagues:
        raise RuntimeError("Could not find any leagues from TheSportsDB search.")

    target = league_name.lower().strip()
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for l in leagues:
        if not isinstance(l, dict):
            continue
        str_league = (l.get("strLeague") or "").lower()
        if not str_league:
            continue
        score = 0
        if target == str_league:
            score += 100
        if target in str_league:
            score += 50
        # Prefer rugby union.
        sport = (l.get("strSport") or "").lower()
        if "rugby" in sport:
            score += 10
        scored.append((score, l))

    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][1] if scored else None
    league_id = (best or {}).get("idLeague")
    if not league_id:
        raise RuntimeError("Found leagues but no idLeague field in TheSportsDB response.")
    return str(league_id)


def list_fixtures_for_range(
    api_key: str,
    league_id: str,
    league_name: str,
    start_date: dt.date,
    end_date: dt.date,
    cache_dir: Path,
    use_cache: bool,
    sleep_s: float,
    debug: bool,
) -> List[Fixture]:
    fixtures: Dict[str, Fixture] = {}

    def maybe_add_event(event: Dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        # TheSportsDB fields.
        id_event = str(event.get("idEvent") or "").strip()
        # TheSportsDB v1 uses `dateEvent` for match date.
        event_date = parse_date(event.get("dateEvent") or "")
        home = normalize_team_name(event.get("strHomeTeam") or "")
        away = normalize_team_name(event.get("strAwayTeam") or "")
        if not id_event or not event_date or not home or not away:
            return
        if not (start_date <= event_date <= end_date):
            return
        fixtures[id_event] = Fixture(
            id_event=id_event,
            event_date=event_date,
            home=home,
            away=away,
        )

    # 1) Grab some upcoming fixtures directly from the league endpoint.
    url = f"{THESPORTSDB_BASE}/{api_key}/eventsnextleague.php?id={league_id}"
    data = cached_get_json(url, cache_dir=cache_dir, use_cache=use_cache)
    for ev in data.get("events") or []:
        maybe_add_event(ev)
    if debug:
        print(f"[debug] Fixtures from eventsnextleague: {len(fixtures)}")

    # 2) Supplement by pulling upcoming fixtures for each team.
    # `lookup_all_teams.php?id=<idLeague>` appears inconsistent for this league on TheSportsDB,
    # so we use `search_all_teams.php?l=<league name>` which returns the correct rugby teams.
    teams_url = f"{THESPORTSDB_BASE}/{api_key}/search_all_teams.php?l={urllib.parse.quote_plus(league_name)}"
    teams_data = cached_get_json(teams_url, cache_dir=cache_dir, use_cache=use_cache)
    teams = teams_data.get("teams") or []
    team_ids: List[str] = []
    for t in teams:
        if not isinstance(t, dict):
            continue
        tid = (t.get("idTeam") or "").strip()
        if tid:
            team_ids.append(tid)

    if debug:
        print(f"[debug] Found {len(team_ids)} teams for league '{league_name}'")

    seen_events: set[str] = set(fixtures.keys())
    for tid in team_ids:
        if debug:
            print(f"[debug] Fetching eventsnext for team {tid}...")
        team_events_url = f"{THESPORTSDB_BASE}/{api_key}/eventsnext.php?id={tid}"
        try:
            team_events = cached_get_json(team_events_url, cache_dir=cache_dir, use_cache=use_cache)
        except Exception as e:
            if debug:
                print(f"[debug] eventsnext failed for team {tid}: {e}")
            time.sleep(sleep_s)
            continue
        for ev in team_events.get("events") or []:
            before = str(ev.get("idEvent") or "").strip()
            maybe_add_event(ev)
            if before:
                seen_events.add(before)
        time.sleep(sleep_s)

    result = sorted(fixtures.values(), key=lambda f: f.event_date.isoformat())
    return result


def current_season_start_year(today: dt.date) -> int:
    # URC seasons span two calendar years; assume season starts around Aug/Sep.
    return today.year if today.month >= 8 else today.year - 1


def season_strings(start_year: int, end_year_inclusive: int) -> List[str]:
    # Try multiple formats to accommodate API inconsistencies.
    out: List[str] = []
    for y in range(start_year, end_year_inclusive + 1):
        out.append(f"{y}-{y+1}")
        out.append(f"{y}/{y+1}")
    # De-duplicate while preserving order.
    seen: set[str] = set()
    dedup: List[str] = []
    for s in out:
        if s in seen:
            continue
        seen.add(s)
        dedup.append(s)
    return dedup


def fetch_and_train_elo(
    api_key: str,
    league_id: str,
    train_start_date: dt.date,
    train_end_date_inclusive: dt.date,
    elo_base: float,
    k_factor: float,
    home_adv: float,
    cache_dir: Path,
    use_cache: bool,
    sleep_s: float,
    debug: bool,
) -> Tuple[Dict[str, float], Tuple[float, float, int]]:
    # Elo ratings stored per team, without home advantage (home_adv applied only in expected probability).
    elo: Dict[str, float] = {}

    def get_r(team: str) -> float:
        team = normalize_team_name(team)
        if team not in elo:
            elo[team] = elo_base
        return elo[team]

    today = utc_now_date()
    season_start = max(2021, 0)
    season_end = current_season_start_year(today)
    candidate_seasons = season_strings(season_start, season_end)

    # Collect events across seasons, then train in chronological order.
    all_events: List[Dict[str, Any]] = []
    for s in candidate_seasons:
        url = f"{THESPORTSDB_BASE}/{api_key}/eventsseason.php?id={league_id}&s={urllib.parse.quote_plus(s)}"
        if debug:
            print(f"[debug] Fetching eventsseason for s={s} ...")
        try:
            data = cached_get_json(url, cache_dir=cache_dir, use_cache=use_cache)
        except Exception as e:
            if debug:
                print(f"[debug] eventsseason failed for s={s}: {e}")
            time.sleep(sleep_s)
            continue
        events = data.get("events") or []
        # If the API returns an empty response for that season format, it usually comes back empty.
        for ev in events:
            if not isinstance(ev, dict):
                continue
            # Filter by date early.
            d = parse_date(ev.get("dateEvent") or "")
            if not d:
                continue
            if d < train_start_date or d > train_end_date_inclusive:
                continue
            # We only need events with scores for training.
            hs = parse_float_or_none(ev.get("intHomeScore"))
            as_ = parse_float_or_none(ev.get("intAwayScore"))
            if hs is None or as_ is None:
                continue
            all_events.append(ev)
        time.sleep(sleep_s)

    if debug:
        print(f"[debug] Training events collected: {len(all_events)}")

    def event_date_key(ev: Dict[str, Any]) -> Tuple[str, str]:
        # Sort key: date string then time if present.
        d = parse_date(ev.get("dateEvent") or "")
        date_s = d.isoformat() if d else "9999-99-99"
        time_s = str(ev.get("event_time") or "").strip()
        return (date_s, time_s)

    all_events.sort(key=event_date_key)

    home_margin_total = 0
    matches_used = 0

    # Fit a simple linear model:
    #   margin = a * x + b
    # where:
    #   margin = (home_score - away_score)
    #   x = ((r_home + home_adv) - r_away) / 400
    #
    # This lets us output a predicted winning margin (in points) instead of probability.
    margin_n = 0
    sumx = 0.0
    sumy = 0.0
    sumxx = 0.0
    sumxy = 0.0

    for ev in all_events:
        home = ev.get("strHomeTeam") or ""
        away = ev.get("strAwayTeam") or ""
        if not home or not away:
            continue
        hs = parse_float_or_none(ev.get("intHomeScore"))
        as_ = parse_float_or_none(ev.get("intAwayScore"))
        if hs is None or as_ is None:
            continue

        r_home = get_r(home)
        r_away = get_r(away)

        # Expected home win probability with home advantage.
        adjusted_home = r_home + home_adv
        expected_home = 1.0 / (1.0 + 10 ** ((r_away - adjusted_home) / 400.0))

        if hs > as_:
            score_home = 1.0
        elif hs < as_:
            score_home = 0.0
        else:
            score_home = 0.5

        delta = k_factor * (score_home - expected_home)
        elo[normalize_team_name(home)] = r_home + delta
        elo[normalize_team_name(away)] = r_away - delta

        matches_used += 1
        margin = float(hs - as_)
        home_margin_total += margin

        # Accumulate for margin regression (pre-update ratings, which is what x represents).
        x = ((r_home + home_adv) - r_away) / 400.0
        margin_n += 1
        sumx += x
        sumy += margin
        sumxx += x * x
        sumxy += x * margin

    if debug:
        print(f"[debug] Elo matches used: {matches_used}")
        if matches_used:
            print(f"[debug] Avg score margin (home-away): {home_margin_total / matches_used:.2f}")

    if margin_n >= 2:
        denom = (margin_n * sumxx - sumx * sumx)
        if abs(denom) < 1e-12:
            # Degenerate variance in x; fall back to mean margin.
            a = 0.0
            b = (sumy / margin_n) if margin_n else 0.0
        else:
            a = (margin_n * sumxy - sumx * sumy) / denom
            b = (sumy - a * sumx) / margin_n
    else:
        # Not enough data to fit; fall back to zero slope and average margin.
        a = 0.0
        b = (home_margin_total / matches_used) if matches_used else 0.0

    return elo, (a, b, margin_n)


def predict_fixture(
    elo: Dict[str, float],
    fixture: Fixture,
    elo_base: float,
    home_adv: float,
    margin_a: float,
    margin_b: float,
) -> Tuple[str, float]:
    def get_r(team: str) -> float:
        team = normalize_team_name(team)
        return elo.get(team, elo_base)

    r_home = get_r(fixture.home)
    r_away = get_r(fixture.away)
    # Predicted margin is learned from the linear regression fitted during training.
    # margin = home_score - away_score
    x = ((r_home + home_adv) - r_away) / 400.0
    pred_margin = margin_a * x + margin_b
    winner = fixture.home if pred_margin >= 0 else fixture.away
    return winner, abs(pred_margin)


def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Predict URC match winners + winning margins for the coming week using Elo + TheSportsDB.")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("THESPORTSDB_API_KEY", "123"),
        help="TheSportsDB API key (or env THESPORTSDB_API_KEY). Default: 123 (free).",
    )
    parser.add_argument("--league-name", default="United Rugby Championship", help="League name to search in TheSportsDB.")
    parser.add_argument(
        "--league-id",
        default="4446",
        help="TheSportsDB idLeague for the United Rugby Championship. Default: 4446.",
    )
    parser.add_argument("--window-days", type=int, default=7, help="How many days ahead to include fixtures.")
    parser.add_argument("--start-date", default="", help="Start date (YYYY-MM-DD). Default: today.")
    parser.add_argument("--min-train-date", default="2021-01-01", help="Only train on matches on/after this date.")
    parser.add_argument("--elo-base", type=float, default=1500.0, help="Initial Elo rating for new teams.")
    parser.add_argument("--k-factor", type=float, default=24.0, help="Elo K-factor.")
    parser.add_argument("--home-adv", type=float, default=80.0, help="Home advantage added to home team in probability calculation.")
    parser.add_argument("--output-csv", default="", help="Optional CSV output path.")
    parser.add_argument("--cache-dir", default=".thesportsdb_cache", help="Cache directory for API responses.")
    parser.add_argument("--no-cache", action="store_true", help="Disable caching.")
    parser.add_argument("--sleep-s", type=float, default=0.35, help="Sleep between API calls to be polite.")
    parser.add_argument("--debug", action="store_true", help="Print debug logs.")
    args = parser.parse_args()

    if not args.api_key:
        print("Missing TheSportsDB API key. Set THESPORTSDB_API_KEY env var or pass --api-key.", file=sys.stderr)
        return 2

    start = parse_date(args.start_date) if args.start_date else utc_now_date()
    if not start:
        print("Invalid --start-date. Expected YYYY-MM-DD.", file=sys.stderr)
        return 2
    # Inclusive window: window-days=7 => start day + next 6 days.
    end = start + dt.timedelta(days=args.window_days - 1)

    min_train = parse_date(args.min_train_date)
    if not min_train:
        print("Invalid --min-train-date. Expected YYYY-MM-DD.", file=sys.stderr)
        return 2

    cache_dir = Path(args.cache_dir).resolve()
    use_cache = not args.no_cache

    league_id = str(args.league_id).strip()
    if not league_id:
        print("Missing --league-id.", file=sys.stderr)
        return 2
    if args.debug:
        print(f"[debug] Using league_id={league_id}")

    fixtures = list_fixtures_for_range(
        api_key=args.api_key,
        league_id=league_id,
        league_name=args.league_name,
        start_date=start,
        end_date=end,
        cache_dir=cache_dir,
        use_cache=use_cache,
        sleep_s=args.sleep_s,
        debug=args.debug,
    )

    if not fixtures:
        print("No fixtures found for the coming week window. Try adjusting --start-date or --window-days.", file=sys.stderr)
        return 1

    elo_ratings, (margin_a, margin_b, margin_n) = fetch_and_train_elo(
        api_key=args.api_key,
        league_id=league_id,
        train_start_date=min_train,
        train_end_date_inclusive=start - dt.timedelta(days=1),
        elo_base=args.elo_base,
        k_factor=args.k_factor,
        home_adv=args.home_adv,
        cache_dir=cache_dir,
        use_cache=use_cache,
        sleep_s=args.sleep_s,
        debug=args.debug,
    )

    rows: List[Dict[str, Any]] = []
    if args.debug:
        print(f"[debug] Margin regression: a={margin_a:.6f}, b={margin_b:.3f}, n={margin_n}")
    for f in fixtures:
        winner, pred_margin_abs = predict_fixture(
            elo=elo_ratings,
            fixture=f,
            elo_base=args.elo_base,
            home_adv=args.home_adv,
            margin_a=margin_a,
            margin_b=margin_b,
        )
        rows.append(
            {
                "date": f.event_date.isoformat(),
                "home": f.home,
                "away": f.away,
                "predicted_winner": winner,
                "predicted_winning_margin_points": f"{pred_margin_abs:.1f}",
            }
        )

    # Print a readable table.
    print(f"URC fixtures predictions from {start.isoformat()} to {end.isoformat()} (training: from {min_train.isoformat()} to {start - dt.timedelta(days=1)})")
    print("")
    print("Date        | Home                          | Away                           | Predicted Winner              | Predicted Margin")
    print("-" * 110)
    for r in rows:
        print(
            f"{r['date']} | "
            f"{r['home'][:30]:30} | "
            f"{r['away'][:30]:30} | "
            f"{r['predicted_winner'][:30]:30} | "
            f"{float(r['predicted_winning_margin_points']):>6.1f} pts"
        )

    if args.output_csv:
        out = Path(args.output_csv).resolve()
        write_csv(out, rows)
        if args.debug:
            print(f"[debug] Wrote CSV: {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
