import argparse
import math
import csv
import datetime as dt
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple


THESPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json"

# Approximate home-city coordinates for URC clubs (lat, lon).
TEAM_COORDS: Dict[str, Tuple[float, float]] = {
    "Benetton": (45.6669, 12.2430),          # Treviso
    "Bulls": (-25.7461, 28.1881),            # Pretoria
    "Cardiff Rugby": (51.4816, -3.1791),     # Cardiff
    "Connacht": (53.2707, -9.0568),          # Galway
    "Dragons": (51.5842, -2.9977),           # Newport
    "Edinburgh": (55.9533, -3.1883),         # Edinburgh
    "Glasgow": (55.8642, -4.2518),           # Glasgow
    "Leinster": (53.3498, -6.2603),          # Dublin
    "Lions": (-26.2041, 28.0473),            # Johannesburg
    "Munster": (52.6638, -8.6267),           # Limerick
    "Ospreys": (51.6214, -3.9436),           # Swansea
    "Scarlets": (51.6784, -4.1619),          # Llanelli
    "Stormers": (-33.9249, 18.4241),         # Cape Town
    "The Sharks": (-29.8587, 31.0218),       # Durban
    "Ulster": (54.5973, -5.9301),            # Belfast
    "Zebre": (44.8015, 10.3279),             # Parma
}


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


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def get_travel_km(away_team: str, home_team: str) -> float:
    away = TEAM_COORDS.get(normalize_team_name(away_team))
    home = TEAM_COORDS.get(normalize_team_name(home_team))
    if not away or not home:
        return 0.0
    return haversine_km(away[0], away[1], home[0], home[1])


def mean_or_zero(vals: Deque[float]) -> float:
    if not vals:
        return 0.0
    return sum(vals) / float(len(vals))


def normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def solve_linear_system(a: List[List[float]], b: List[float]) -> List[float]:
    n = len(a)
    aug = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = col
        max_abs = abs(aug[col][col])
        for r in range(col + 1, n):
            if abs(aug[r][col]) > max_abs:
                max_abs = abs(aug[r][col])
                pivot = r
        if max_abs < 1e-12:
            continue
        if pivot != col:
            aug[col], aug[pivot] = aug[pivot], aug[col]
        pivot_val = aug[col][col]
        for c in range(col, n + 1):
            aug[col][c] /= pivot_val
        for r in range(n):
            if r == col:
                continue
            factor = aug[r][col]
            if abs(factor) < 1e-12:
                continue
            for c in range(col, n + 1):
                aug[r][c] -= factor * aug[col][c]
    return [aug[i][n] for i in range(n)]
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
    max_retries: int = 4,
) -> Dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = cache_key_for_url(url)
    cache_file = cache_dir / f"{key}.json"
    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    req = urllib.request.Request(url, headers={"User-Agent": "urc-predictor/1.0"})
    raw = ""
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                raw = resp.read().decode("utf-8")
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries:
                # Respect free-tier throttling by backing off.
                time.sleep(1.5 * (2 ** attempt))
                continue
            raise
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
    for idx, tid in enumerate(team_ids):
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


def fetch_and_train_model(
    api_key: str,
    league_id: str,
    league_name: str,
    train_start_date: dt.date,
    train_end_date_inclusive: dt.date,
    elo_base: float,
    k_factor: float,
    home_adv: float,
    cache_dir: Path,
    use_cache: bool,
    sleep_s: float,
    debug: bool,
) -> Tuple[Dict[str, float], List[float], float, int, Dict[str, Deque[float]], Dict[Tuple[str, str], Deque[float]]]:
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
    candidate_seasons = [s for s in candidate_seasons if "-" in s]

    all_events: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    # Seed with recent results per team (few calls, robust on free-tier).
    teams_url = f"{THESPORTSDB_BASE}/{api_key}/search_all_teams.php?l={urllib.parse.quote_plus(league_name)}"
    try:
        teams_data = cached_get_json(teams_url, cache_dir=cache_dir, use_cache=use_cache)
        team_ids = [str(t.get("idTeam") or "").strip() for t in (teams_data.get("teams") or []) if isinstance(t, dict)]
        team_ids = [tid for tid in team_ids if tid]
    except Exception:
        team_ids = []
    if debug:
        print(f"[debug] Training seed team count: {len(team_ids)}")
    for tid in team_ids:
        url = f"{THESPORTSDB_BASE}/{api_key}/eventslast.php?id={tid}"
        try:
            # Always refresh `eventslast` so stale cached null responses do not zero-out training.
            data = cached_get_json(url, cache_dir=cache_dir, use_cache=False)
        except Exception as e:
            if debug:
                print(f"[debug] eventslast failed for team {tid}: {e}")
            time.sleep(sleep_s)
            continue
        for ev in data.get("results") or data.get("events") or []:
            if not isinstance(ev, dict):
                continue
            if str(ev.get("idLeague") or "").strip() != str(league_id):
                continue
            event_id = str(ev.get("idEvent") or "").strip()
            if event_id in seen_ids:
                continue
            d = parse_date(ev.get("dateEvent") or "")
            hs = parse_float_or_none(ev.get("intHomeScore"))
            as_ = parse_float_or_none(ev.get("intAwayScore"))
            if not d or hs is None or as_ is None:
                continue
            if d < train_start_date or d > train_end_date_inclusive:
                continue
            seen_ids.add(event_id)
            all_events.append(ev)
        time.sleep(sleep_s)
    if debug:
        print(f"[debug] Seed events from eventslast: {len(all_events)}")

    # Then augment with season results as available.
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
        for ev in events:
            if not isinstance(ev, dict):
                continue
            if str(ev.get("idLeague") or "").strip() != str(league_id):
                continue
            event_id = str(ev.get("idEvent") or "").strip()
            if event_id and event_id in seen_ids:
                continue
            d = parse_date(ev.get("dateEvent") or "")
            hs = parse_float_or_none(ev.get("intHomeScore"))
            as_ = parse_float_or_none(ev.get("intAwayScore"))
            if not d or hs is None or as_ is None:
                continue
            if d < train_start_date or d > train_end_date_inclusive:
                continue
            if event_id:
                seen_ids.add(event_id)
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

    # Features for probabilistic margin model:
    # [1, elo_diff_scaled, recent_form_diff, h2h_margin, away_travel_1000km]
    x_rows: List[List[float]] = []
    y_vals: List[float] = []

    form_points: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=5))
    h2h_margins: Dict[Tuple[str, str], Deque[float]] = defaultdict(lambda: deque(maxlen=5))

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
        home_n = normalize_team_name(home)
        away_n = normalize_team_name(away)

        elo_diff_scaled = ((r_home + home_adv) - r_away) / 400.0
        form_diff = mean_or_zero(form_points[home_n]) - mean_or_zero(form_points[away_n])
        h2h_key = (home_n, away_n)
        h2h_diff = mean_or_zero(h2h_margins[h2h_key])
        away_travel_1000 = get_travel_km(away_n, home_n) / 1000.0

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
        elo[home_n] = r_home + delta
        elo[away_n] = r_away - delta

        matches_used += 1
        margin = float(hs - as_)
        home_margin_total += margin
        x_rows.append([1.0, elo_diff_scaled, form_diff, h2h_diff, away_travel_1000])
        y_vals.append(margin)

        # Update state for next games.
        if hs > as_:
            home_pts, away_pts = 1.0, 0.0
        elif hs < as_:
            home_pts, away_pts = 0.0, 1.0
        else:
            home_pts, away_pts = 0.5, 0.5
        form_points[home_n].append(home_pts)
        form_points[away_n].append(away_pts)
        h2h_margins[(home_n, away_n)].append(margin)
        h2h_margins[(away_n, home_n)].append(-margin)

    if debug:
        print(f"[debug] Elo matches used: {matches_used}")
        if matches_used:
            print(f"[debug] Avg score margin (home-away): {home_margin_total / matches_used:.2f}")

    n = len(x_rows)
    if n >= 8:
        k = len(x_rows[0])
        xtx = [[0.0 for _ in range(k)] for _ in range(k)]
        xty = [0.0 for _ in range(k)]
        for row, y in zip(x_rows, y_vals):
            for i in range(k):
                xty[i] += row[i] * y
                for j in range(k):
                    xtx[i][j] += row[i] * row[j]
        # Small ridge term for numerical stability.
        for i in range(k):
            xtx[i][i] += 1e-6
        beta = solve_linear_system(xtx, xty)
    else:
        avg_margin = (home_margin_total / matches_used) if matches_used else 0.0
        beta = [avg_margin, 0.0, 0.0, 0.0, 0.0]

    # Residual standard deviation -> win probability via normal CDF.
    if n >= 2:
        rss = 0.0
        for row, y in zip(x_rows, y_vals):
            yhat = sum(b * x for b, x in zip(beta, row))
            rss += (y - yhat) ** 2
        sigma = math.sqrt(max(rss / max(1, n - len(beta)), 1e-6))
    else:
        sigma = 10.0

    return elo, beta, sigma, n, form_points, h2h_margins


def predict_fixture(
    elo: Dict[str, float],
    fixture: Fixture,
    elo_base: float,
    home_adv: float,
    beta: List[float],
    sigma: float,
    form_points: Dict[str, Deque[float]],
    h2h_margins: Dict[Tuple[str, str], Deque[float]],
) -> Tuple[str, float, float]:
    def get_r(team: str) -> float:
        team = normalize_team_name(team)
        return elo.get(team, elo_base)

    home_n = normalize_team_name(fixture.home)
    away_n = normalize_team_name(fixture.away)
    r_home = get_r(home_n)
    r_away = get_r(away_n)

    recent_form_diff_last5 = mean_or_zero(form_points.get(home_n, deque())) - mean_or_zero(form_points.get(away_n, deque()))
    head_to_head_margin_last5 = mean_or_zero(h2h_margins.get((home_n, away_n), deque()))
    row = [
        1.0,
        ((r_home + home_adv) - r_away) / 400.0,
        recent_form_diff_last5,
        head_to_head_margin_last5,
        get_travel_km(away_n, home_n) / 1000.0,
    ]
    expected_margin = sum(b * x for b, x in zip(beta, row))  # home - away
    if sigma <= 1e-6:
        win_prob_home = 1.0 if expected_margin > 0 else 0.5
    else:
        win_prob_home = normal_cdf(expected_margin / sigma)
    winner = fixture.home if expected_margin >= 0 else fixture.away
    winner_prob = win_prob_home if expected_margin >= 0 else (1.0 - win_prob_home)
    return winner, winner_prob * 100.0, expected_margin


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

    elo_ratings, beta, sigma, train_n, form_points, h2h_margins = fetch_and_train_model(
        api_key=args.api_key,
        league_id=league_id,
        league_name=args.league_name,
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
        print(f"[debug] Probabilistic margin model: beta={beta}, sigma={sigma:.3f}, n={train_n}")
    for f in fixtures:
        winner, win_prob_pct, expected_margin = predict_fixture(
            elo=elo_ratings,
            fixture=f,
            elo_base=args.elo_base,
            home_adv=args.home_adv,
            beta=beta,
            sigma=sigma,
            form_points=form_points,
            h2h_margins=h2h_margins,
        )
        rows.append(
            {
                "date": f.event_date.isoformat(),
                "home": f.home,
                "away": f.away,
                "predicted_winner": winner,
                "win_probability_percent": f"{win_prob_pct:.1f}",
                "expected_margin_points_home_minus_away": f"{expected_margin:.1f}",
            }
        )

    # Print a readable table.
    print(f"URC fixtures predictions from {start.isoformat()} to {end.isoformat()} (training: from {min_train.isoformat()} to {start - dt.timedelta(days=1)})")
    print("")
    print("Date        | Home                          | Away                           | Predicted Winner              | Win Prob | Exp Margin (H-A)")
    print("-" * 140)
    for r in rows:
        print(
            f"{r['date']} | "
            f"{r['home'][:30]:30} | "
            f"{r['away'][:30]:30} | "
            f"{r['predicted_winner'][:30]:30} | "
            f"{float(r['win_probability_percent']):>6.1f}% | "
            f"{float(r['expected_margin_points_home_minus_away']):>8.1f} pts"
        )

    if args.output_csv:
        out = Path(args.output_csv).resolve()
        write_csv(out, rows)
        if args.debug:
            print(f"[debug] Wrote CSV: {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

