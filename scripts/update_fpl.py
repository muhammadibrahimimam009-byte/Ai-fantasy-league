import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

BASE = "https://fantasy.premierleague.com/api/"
ROOT = Path(__file__).resolve().parents[1]

POSITION_TYPES = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


def get(path):
    req = Request(BASE + path, headers={"User-Agent": "AI-Fantasy-League/2.0"})
    with urlopen(req, timeout=30) as r:
        return json.load(r)


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]", "", s)
    aliases = {
        "antoninkinsky": "antoninkinsky",
        "cristhianmosquera": "cristhianmosquera",
        "harrymaguire": "harrymaguire",
        "tyrickmitchell": "tyrickmitchell",
        "brunofernandes": "brunofernandes",
        "bryanmbeumo": "bryanmbeumo",
        "pascalgross": "pascalgross",
        "mamadousangare": "mamadousangare",
        "florentino": "florentino",
        "erlinghaaland": "erlinghaaland",
        "joaopedro": "joaopedro",
        "bartverbruggen": "bartverbruggen",
        "bobbythomas": "bobbythomas",
        "jonahkusiasare": "jonahkusiasare",
        "gabriel": "gabriel",
        "christostzolis": "christostzolis",
        "martindubravka": "martindubravka",
        "jacobgreaves": "jacobgreaves",
        "carlosbaleba": "carlosbaleba",
        "elliottanderson": "elliottanderson",
        "sidikicherif": "sidikicherif",
        "markflekken": "markflekken",
        "nobelmendy": "nobelmendy",
        "jeremysarmiento": "jeremysarmiento",
        "riccardocalafiori": "riccardocalafiori",
        "lukeshaw": "lukeshaw",
        "dominikszoboszlai": "dominikszoboszlai",
        "dominickcalvertlewin": "dominickcalvertlewin",
        "kristofferajer": "kristofferajer",
        "daraoshea": "daraoshea",
        "vitalyjanelt": "vitalyjanelt",
        "liamdelap": "liamdelap",
        "eze": "eze",
        "eberechieze": "eberechieze",
    }
    return aliases.get(s, s)


def load_squads():
    return json.loads((ROOT / "data" / "squads.json").read_text(encoding="utf-8"))


def name_tokens(s):
    """Return normalized name tokens while preserving token boundaries."""
    s = unicodedata.normalize("NFKD", str(s)).lower()
    s = s.replace("ß", "ss")
    s = s.encode("ascii", "ignore").decode()
    return re.findall(r"[a-z0-9]+", s)


def initials(token):
    """Return a compact initials form such as 'b' for 'bruno'."""
    t = re.sub(r"[^a-z0-9]", "", token.lower())
    return t[:1] if t else ""


def build_index(players):
    """Build several indexes from official FPL player names.

    FPL's web_name is not guaranteed to equal the human-readable name stored
    in squads.json. Example: FPL may expose Bruno Fernandes as 'B.Fernandes'.
    We therefore index web_name, full name, surname, and token combinations.
    """
    idx = {
        "exact": {},
        "surname": {},
        "full_tokens": {},
        "web_tokens": {},
    }

    for p in players:
        first = str(p.get("first_name", ""))
        second = str(p.get("second_name", ""))
        web = str(p.get("web_name", ""))

        raw_names = [web, f"{first} {second}"]
        for raw in raw_names:
            key = norm(raw)
            if key:
                bucket = idx["exact"].setdefault(key, [])
                if not any(existing["id"] == p["id"] for existing in bucket):
                    bucket.append(p)

        full_toks = name_tokens(f"{first} {second}")
        web_toks = name_tokens(web)
        if full_toks:
            full_bucket = idx["full_tokens"].setdefault(tuple(full_toks), [])
            if not any(existing["id"] == p["id"] for existing in full_bucket):
                full_bucket.append(p)
            surname_bucket = idx["surname"].setdefault(full_toks[-1], [])
            if not any(existing["id"] == p["id"] for existing in surname_bucket):
                surname_bucket.append(p)
        if web_toks:
            web_bucket = idx["web_tokens"].setdefault(tuple(web_toks), [])
            if not any(existing["id"] == p["id"] for existing in web_bucket):
                web_bucket.append(p)

    return idx


def find_player(idx, name, expected_pos=None):
    """Resolve a submitted name to exactly one FPL player.

    expected_pos is used only to break genuine same-name collisions, such as
    duplicate human-readable names in the FPL player pool. It never changes a
    player's actual FPL position; it only narrows otherwise-valid candidates.
    """
    raw = str(name).strip()
    key = norm(raw)

    def narrow(candidates):
        if expected_pos is None:
            return candidates
        wanted_type = {v: k for k, v in POSITION_TYPES.items()}.get(expected_pos)
        if wanted_type is None:
            raise ValueError(f"Unknown submitted position for {name}: {expected_pos}")
        filtered = [p for p in candidates if p.get("element_type") == wanted_type]
        return filtered

    exact_hits = narrow(idx["exact"].get(key, []))
    if len(exact_hits) == 1:
        return exact_hits[0]
    if len(exact_hits) > 1:
        raise KeyError(f"Ambiguous FPL player name: {name}")

    submitted_tokens = name_tokens(raw)
    if not submitted_tokens:
        raise KeyError(f"FPL player not found: {name}")

    # Full token sequence, ignoring punctuation/case/diacritics.
    full_hits = narrow(idx["full_tokens"].get(tuple(submitted_tokens), []))
    if len(full_hits) == 1:
        return full_hits[0]
    if len(full_hits) > 1:
        raise KeyError(f"Ambiguous FPL player name: {name}")

    # Common FPL display form: first initial + surname, e.g. B.Fernandes.
    if len(submitted_tokens) >= 2:
        sub_surname = submitted_tokens[-1]
        sub_initial = initials(submitted_tokens[0])
        candidates = []
        for p in idx["surname"].get(sub_surname, []):
            first = name_tokens(p.get("first_name", ""))
            if first and initials(first[0]) == sub_initial:
                candidates.append(p)
        candidates = narrow(candidates)
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise KeyError(f"Ambiguous FPL player name: {name}")

    # Match a supplied full human-readable name to an abbreviated web_name.
    # Example: submitted 'Bruno Fernandes'; official web_name 'B.Fernandes'.
    if len(submitted_tokens) >= 2:
        surname = submitted_tokens[-1]
        first = submitted_tokens[0]
        candidates = []
        for p in idx["surname"].get(surname, []):
            p_first = name_tokens(p.get("first_name", ""))
            p_web = name_tokens(p.get("web_name", ""))
            if not p_first:
                continue
            same_first = p_first[0] == first
            abbreviated = bool(p_web) and (
                len(p_web) == 2 and p_web[0] == first[:1] and p_web[-1] == surname
            )
            if same_first or abbreviated:
                candidates.append(p)
        candidates = narrow(candidates)
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise KeyError(f"Ambiguous FPL player name: {name}")

    # Surname-only is accepted only when that surname is unique in the FPL API.
    surname_hits = narrow(idx["surname"].get(submitted_tokens[-1], []))
    if len(submitted_tokens) == 1 and len(surname_hits) == 1:
        return surname_hits[0]

    raise KeyError(f"FPL player not found: {name}")


def submitted_teams_for_gw(squads_data, gw):
    """Return only the four manager entries for the requested GW.

    Supports the current structure:
        {"current_gameweek": 4, "gameweeks": {"1": {...}, "2": {...}}}

    Also supports the older flat structure where manager IDs are at the top level.
    """
    gameweeks = squads_data.get("gameweeks")
    if isinstance(gameweeks, dict):
        gw_data = gameweeks.get(str(gw), {})
        if not isinstance(gw_data, dict):
            return {}
        managers = {}
        for sid, team in gw_data.items():
            if isinstance(team, dict) and "starting" in team and "bench" in team:
                managers[sid] = team
        return managers

    # Backward compatibility with the older file format.
    managers = {}
    for sid, team in squads_data.items():
        if isinstance(team, dict) and "starting" in team and "bench" in team:
            managers[sid] = team
    return managers


def valid_formation(players):
    """Validate a standard FPL starting XI formation."""
    if len(players) != 11:
        return False
    counts = {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0}
    for p in players:
        pos = p["pos"]
        if pos not in counts:
            raise ValueError(f"Unknown position in submitted team: {pos}")
        counts[pos] += 1
    return (
        counts["GK"] == 1
        and 3 <= counts["DEF"] <= 5
        and 2 <= counts["MID"] <= 5
        and 1 <= counts["FWD"] <= 3
    )


def validate_team_structure(team, refs):
    """Reject malformed locked submissions before any scoring happens."""
    starting = team.get("starting", [])
    bench = team.get("bench", [])
    if len(starting) != 11 or len(bench) != 4:
        raise ValueError("Each manager must have exactly 11 starters and 4 bench players")

    names = [name for name, _ in starting + bench]
    ids = [p["id"] for p in refs]
    if len(set(ids)) != 15:
        raise ValueError("Locked squad contains duplicate players")

    for (name, submitted_pos), player in zip(starting + bench, refs):
        actual_pos = POSITION_TYPES.get(player.get("element_type"))
        if actual_pos is None:
            raise ValueError(f"Unknown FPL position for {name}")
        if submitted_pos != actual_pos:
            raise ValueError(
                f"Position mismatch for {name}: submitted {submitted_pos}, FPL says {actual_pos}"
            )

    if sum(1 for _, pos in starting if pos == "GK") != 1:
        raise ValueError("Starting XI must contain exactly one goalkeeper")
    if sum(1 for _, pos in bench if pos == "GK") != 1:
        raise ValueError("Bench must contain exactly one goalkeeper")
    if team.get("captain") not in {name for name, _ in starting}:
        raise ValueError("Captain must be in the starting XI")
    if team.get("vice") not in {name for name, _ in starting}:
        raise ValueError("Vice-captain must be in the starting XI")
    if team.get("captain") == team.get("vice"):
        raise ValueError("Captain and vice-captain must be different players")


def played_in_gameweek(live):
    """FPL considers a player to have played if they appeared OR received a yellow/red card."""
    minutes = int(live.get("minutes", 0) or 0)
    yellow = int(live.get("yellow_cards", 0) or 0)
    red = int(live.get("red_cards", 0) or 0)
    return minutes > 0 or yellow > 0 or red > 0


def build_player_record(name_pos, player, live_by_id):
    name, pos = name_pos
    if player["id"] not in live_by_id:
        raise KeyError(f"No live FPL data for {name} (ID {player['id']})")
    return {
        "name": name,
        "pos": pos,
        "p": player,
        "live": live_by_id[player["id"]],
    }


def apply_auto_substitutions(starters, bench):
    """Apply FPL-style automatic substitutions to a locked starting XI.

    Order:
      1) Replace a non-playing GK with the replacement GK if that GK played.
      2) Process non-playing outfield starters in their submitted order.
         For each, use the highest-priority unused outfield substitute who played
         and leaves the XI in a valid formation.
    """
    current = list(starters)
    used_bench_indices = set()
    substitutions = []

    # Goalkeeper substitution: the one bench GK is the replacement GK.
    starting_gk_index = next((i for i, p in enumerate(current) if p["pos"] == "GK"), None)
    bench_gk_index = next((i for i, p in enumerate(bench) if p["pos"] == "GK"), None)

    if starting_gk_index is None:
        raise ValueError("Starting XI has no goalkeeper")

    if not played_in_gameweek(current[starting_gk_index]["live"]):
        if bench_gk_index is not None and played_in_gameweek(bench[bench_gk_index]["live"]):
            outgoing = current[starting_gk_index]
            incoming = bench[bench_gk_index]
            current[starting_gk_index] = incoming
            used_bench_indices.add(bench_gk_index)
            substitutions.append({"out": outgoing["name"], "in": incoming["name"]})

    # Outfield substitutions: original starting order is retained for the
    # players who failed to play. Bench priority is retained independently.
    for sidx in range(len(starters)):
        # Only the original outfield starters can be replaced here.
        if starters[sidx]["pos"] == "GK":
            continue
        if played_in_gameweek(starters[sidx]["live"]):
            continue

        # A starter that didn't play should currently still occupy this slot.
        # Search bench from highest priority to lowest, ignoring the GK slot.
        for bidx, candidate in enumerate(bench):
            if bidx in used_bench_indices:
                continue
            if candidate["pos"] == "GK":
                continue
            if not played_in_gameweek(candidate["live"]):
                continue

            trial = list(current)
            trial[sidx] = candidate
            if not valid_formation(trial):
                continue

            outgoing = current[sidx]
            current[sidx] = candidate
            used_bench_indices.add(bidx)
            substitutions.append({"out": outgoing["name"], "in": candidate["name"]})
            break

    return current, substitutions


def raw_points(record):
    return int(record["live"].get("total_points", 0) or 0)


def score_team(team, idx, live_by_id):
    starter_pairs = team["starting"]
    bench_pairs = team["bench"]
    all_pairs = starter_pairs + bench_pairs
    refs = [find_player(idx, name, pos) for name, pos in all_pairs]
    validate_team_structure(team, refs)

    starters = [
        build_player_record(pair, player, live_by_id)
        for pair, player in zip(starter_pairs, refs[:11])
    ]
    bench = [
        build_player_record(pair, player, live_by_id)
        for pair, player in zip(bench_pairs, refs[11:])
    ]

    if not valid_formation(starters):
        raise ValueError("Submitted starting XI is not a valid FPL formation")

    chip = str(team.get("chip", "") or "").strip().lower()
    bench_boost = chip in {"bench boost", "bench_boost", "benchboost"}

    # Bench Boost: all four bench players score, so no auto-substitution is used.
    if bench_boost:
        scoring_players = starters + bench
        substitutions = []
    else:
        scoring_players, substitutions = apply_auto_substitutions(starters, bench)

    total = sum(raw_points(p) for p in scoring_players)

    # Captaincy is based on the ORIGINAL locked starting XI.
    # A vice-captain who gets auto-subbed in does NOT inherit the armband.
    original_by_name = {p["name"]: p for p in starters}
    cap = original_by_name.get(team.get("captain"))
    vice = original_by_name.get(team.get("vice"))

    captain_multiplier = 1
    captain_name_used = None

    if cap and played_in_gameweek(cap["live"]):
        captain_multiplier = 3 if chip in {"triple captain", "triple_captain", "triplecaptain"} else 2
        captain_name_used = cap["name"]
        total += raw_points(cap) * (captain_multiplier - 1)
    elif vice and played_in_gameweek(vice["live"]):
        captain_multiplier = 2
        captain_name_used = vice["name"]
        total += raw_points(vice)

    hit = max(0, int(team.get("hit", team.get("points_hit", 0)) or 0))
    if chip in {"wildcard", "free hit", "free_hit", "freehit"}:
        hit = 0
    total -= hit

    return {
        "points": total,
        "substitutions": substitutions,
        "captain_doubled_or_tripled": captain_name_used,
        "captain_multiplier": captain_multiplier if captain_name_used else 1,
        "points_hit": hit,
        "scoring_players": [p["name"] for p in scoring_players],
    }


def main():
    squads_data = load_squads()
    boot = get("bootstrap-static/")
    idx = build_index(boot["elements"])

    history = {}
    manager_ids = set()

    for event in boot["events"]:
        gw = int(event["id"])
        if not event.get("finished") or not event.get("data_checked"):
            continue

        teams = submitted_teams_for_gw(squads_data, gw)
        if not teams:
            continue

        live = get(f"event/{gw}/live/")
        live_by_id = {x["id"]: x for x in live["elements"]}

        scores = []
        for sid, team in teams.items():
            try:
                result = score_team(team, idx, live_by_id)
            except Exception as exc:
                raise RuntimeError(f"GW{gw} manager {sid} ({team.get('name', sid)}) failed validation/scoring: {exc}") from exc
            manager_ids.add(sid)
            scores.append({
                "id": sid,
                "name": team.get("name", sid),
                "icon": team.get("icon", ""),
                "points": result["points"],
                "captain": team.get("captain"),
                "vice": team.get("vice"),
                "formation": team.get("formation"),
                "chip": team.get("chip"),
                "points_hit": result["points_hit"],
                "auto_substitutions": result["substitutions"],
                "scoring_players": result["scoring_players"],
                "captain_multiplier": result["captain_multiplier"],
                "captain_used": result["captain_doubled_or_tripled"],
            })

        history[str(gw)] = {
            "status": "Official FPL data marked finished and checked.",
            "scores": scores,
        }

    # Build totals from completed Gameweeks represented in history.
    totals = {sid: 0 for sid in manager_ids}
    for gw_data in history.values():
        for row in gw_data["scores"]:
            totals[row["id"]] = totals.get(row["id"], 0) + row["points"]

    # Prefer the latest available GW's metadata for each manager.
    latest_team = {}
    if isinstance(squads_data.get("gameweeks"), dict):
        for gw in sorted(squads_data["gameweeks"].keys(), key=lambda x: int(x)):
            for sid, team in squads_data["gameweeks"][gw].items():
                if isinstance(team, dict) and "starting" in team:
                    latest_team[sid] = team
    else:
        latest_team = {
            sid: team for sid, team in squads_data.items()
            if isinstance(team, dict) and "starting" in team
        }

    leaderboard = []
    for sid, total in totals.items():
        team = latest_team.get(sid, {})
        row = {
            "id": sid,
            "name": team.get("name", sid),
            "icon": team.get("icon", ""),
            "formation": team.get("formation"),
            "captain": team.get("captain"),
            "total": total,
        }
        for gw, gw_data in history.items():
            score = next((x["points"] for x in gw_data["scores"] if x["id"] == sid), None)
            if score is not None:
                row[f"gw{gw}"] = score
        leaderboard.append(row)

    leaderboard.sort(key=lambda x: (-x["total"], x["name"]))
    for rank, row in enumerate(leaderboard, start=1):
        row["rank"] = rank

    out = {
        "status": "Updated from official FPL data.",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "leaderboard": leaderboard,
        "gameweeks": history,
    }

    (ROOT / "data" / "results.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
