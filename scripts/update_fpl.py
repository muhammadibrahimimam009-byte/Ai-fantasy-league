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


def build_index(players):
    idx = {}
    for p in players:
        names = {
            norm(p.get("web_name", "")),
            norm(f'{p.get("first_name", "")} {p.get("second_name", "")}'),
        }
        for name in names:
            if name:
                idx.setdefault(name, []).append(p)
    return idx


def find_player(idx, name):
    hits = idx.get(norm(name), [])
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise KeyError(f"Ambiguous FPL player name: {name}")
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
    refs = [find_player(idx, name) for name, _ in all_pairs]
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


def fpl_position(player):
    return POSITION_MAP.get(player.get("element_type"))


# ============================================================
# PLAYER NAMES
# ============================================================

def player_full_name(player):
    return (
        str(player.get("first_name", "")).strip()
        + " "
        + str(player.get("second_name", "")).strip()
    ).strip()


def player_names(player):
    names = set()

    first = str(player.get("first_name", "")).strip()
    second = str(player.get("second_name", "")).strip()
    web = str(player.get("web_name", "")).strip()

    if first:
        names.add(first)

    if second:
        names.add(second)

    if first and second:
        names.add(first + " " + second)

    if web:
        names.add(web)

    return names


# ============================================================
# PLAYER LOOKUP
# ============================================================

def find_player(players, name, position=None):

    target = normalise(name)

    if not target:
        raise Exception("Empty player name supplied.")

    candidates = []

    for player in players:

        if position:
            if fpl_position(player) != position:
                continue

        candidates.append(player)

    if not candidates:
        candidates = list(players)

    # --------------------------------------------------------
    # Exact full name
    # --------------------------------------------------------

    for player in candidates:
        if target == normalise(player_full_name(player)):
            return player

    # --------------------------------------------------------
    # Exact web name
    # --------------------------------------------------------

    for player in candidates:
        if target == normalise(player.get("web_name", "")):
            return player

    # --------------------------------------------------------
    # Exact official name
    # --------------------------------------------------------

    for player in candidates:

        for official_name in player_names(player):

            if target == normalise(official_name):
                return player

    # --------------------------------------------------------
    # Token matching
    # --------------------------------------------------------

    target_parts = set(name_tokens(name))

    scored = []

    for player in candidates:

        official_full = normalise(
            player_full_name(player)
        )

        official_web = normalise(
            player.get("web_name", "")
        )

        score = 0

        if target in official_full:
            score += 60

        if target in official_web:
            score += 60

        official_parts = set(
            name_tokens(player_full_name(player))
        )

        overlap = len(
            target_parts & official_parts
        )

        score += overlap * 25

        target_tokens = name_tokens(name)

        official_tokens = name_tokens(
            player_full_name(player)
        )

        if target_tokens and official_tokens:

            if target_tokens[-1] == official_tokens[-1]:
                score += 40

        if score > 0:

            scored.append(
                (score, player)
            )

    if scored:

        scored.sort(
            key=lambda x: x[0],
            reverse=True
        )

        best_score = scored[0][0]

        best = [
            player
            for score, player in scored
            if score == best_score
        ]

        if len(best) == 1:
            return best[0]

    # --------------------------------------------------------
    # Controlled fuzzy matching
    # --------------------------------------------------------

    fuzzy = []

    for player in candidates:

        names_to_check = [
            normalise(
                player_full_name(player)
            ),
            normalise(
                player.get("web_name", "")
            ),
        ]

        best_ratio = 0

        for official_name in names_to_check:

            if not official_name:
                continue

            ratio = SequenceMatcher(
                None,
                target,
                official_name
            ).ratio()

            best_ratio = max(
                best_ratio,
                ratio
            )

        fuzzy.append(
            (best_ratio, player)
        )

    fuzzy.sort(
        key=lambda x: x[0],
        reverse=True
    )

    if fuzzy:

        best_ratio, best_player = fuzzy[0]

        if best_ratio >= 0.82:

            if len(fuzzy) == 1:
                return best_player

            second_ratio = fuzzy[1][0]

            if best_ratio - second_ratio >= 0.04:
                return best_player

    raise Exception(
        f"Could not find FPL player: {name}"
    )


# ============================================================
# FORMATION VALIDATION
# ============================================================

def valid_formation(lineup):

    gk = sum(
        p["position"] == "GK"
        for p in lineup
    )

    defenders = sum(
        p["position"] == "DEF"
        for p in lineup
    )

    midfielders = sum(
        p["position"] == "MID"
        for p in lineup
    )

    forwards = sum(
        p["position"] == "FWD"
        for p in lineup
    )

    return (
        gk == 1
        and defenders >= 3
        and midfielders >= 2
        and forwards >= 1
    )


# ============================================================
# SQUAD STRUCTURE VALIDATION
# ============================================================

def validate_squad(team):

    all_players = (
        team.get("starting", [])
        + team.get("bench", [])
    )

    if len(all_players) != 15:

        raise Exception(
            f"{team.get('name', 'Team')} has "
            f"{len(all_players)} players instead of 15."
        )

    positions = {
        "GK": 0,
        "DEF": 0,
        "MID": 0,
        "FWD": 0,
    }

    for name, position in all_players:

        if position not in positions:

            raise Exception(
                f"Invalid position for {name}: {position}"
            )

        positions[position] += 1

    expected = {
        "GK": 2,
        "DEF": 5,
        "MID": 5,
        "FWD": 3,
    }

    if positions != expected:

        raise Exception(
            f"{team.get('name', 'Team')} has invalid "
            f"squad structure: {positions}"
        )


# ============================================================
# TRANSFER VALIDATION
# ============================================================

def validate_transfer(previous_team, current_team):

    previous = {
        normalise(name): position
        for name, position in (
            previous_team.get("starting", [])
            + previous_team.get("bench", [])
        )
    }

    current = {
        normalise(name): position
        for name, position in (
            current_team.get("starting", [])
            + current_team.get("bench", [])
        )
    }

    outgoing = [
        name
        for name in previous
        if name not in current
    ]

    incoming = [
        name
        for name in current
        if name not in previous
    ]

    declared_out = normalise(
        current_team.get("transfer_out")
    )

    declared_in = normalise(
        current_team.get("transfer_in")
    )

    if len(outgoing) != len(incoming):

        raise Exception(
            f"{current_team.get('name')} has an invalid "
            f"GW transfer: {len(outgoing)} OUT / "
            f"{len(incoming)} IN."
        )

    used = int(
        current_team.get(
            "free_transfers_used",
            0
        )
    )

    hit = int(
        current_team.get(
            "hit",
            0
        )
    )

    if used > 1:

        raise Exception(
            f"{current_team.get('name')} used "
            f"{used} free transfers. Maximum is 1."
        )

    if hit < 0:

        raise Exception(
            f"{current_team.get('name')} has invalid hit."
        )

    if outgoing:

        if normalise(outgoing[0]) != declared_out:

            raise Exception(
                f"{current_team.get('name')} transfer OUT "
                f"does not match squad change."
            )

        if normalise(incoming[0]) != declared_in:

            raise Exception(
                f"{current_team.get('name')} transfer IN "
                f"does not match squad change."
            )

    else:

        if declared_out or declared_in:

            raise Exception(
                f"{current_team.get('name')} declares a "
                f"transfer but the squad did not change."
            )

    print(
        f"  ✓ Transfer check: "
        f"{current_team.get('name')} "
        f"{outgoing} -> {incoming}"
    )


# ============================================================
# LOAD PLAYER
# ============================================================

def load_squad_player(
    players,
    live,
    name,
    position
):

    try:

        player = find_player(
            players,
            name,
            position
        )

        stats = live.get(
            player["id"],
            {}
        )

        official_name = player_full_name(
            player
        )

        fpl_id = player["id"]

    except Exception:

        print(
            f"  ⚠️ WARNING: "
            f"Could not match '{name}'. "
            f"Using 0 points."
        )

        official_name = (
            f"{name} (Unmatched)"
        )

        fpl_id = 0

        stats = {
            "total_points": 0,
            "minutes": 0,
        }

    return {
        "name": name,
        "position": position,
        "official_name": official_name,
        "fpl_id": fpl_id,
        "stats": stats,
    }


# ============================================================
# CALCULATE TEAM
# ============================================================

def calculate_team(
    team,
    players,
    live,
    allow_autosubs
):

    starting = []
    bench = []

    # --------------------------------------------------------
    # Starting XI
    # --------------------------------------------------------

    for name, position in team["starting"]:

        player = load_squad_player(
            players,
            live,
            name,
            position
        )

        starting.append(player)

    # --------------------------------------------------------
    # Bench
    # --------------------------------------------------------

    for name, position in team["bench"]:

        player = load_squad_player(
            players,
            live,
            name,
            position
        )

        bench.append(player)

    # Keep the original starting XI separately.
    # This is important for captain/vice-captain logic.
    original_starting = list(starting)

    lineup = list(starting)

    # ========================================================
    # AUTOMATIC SUBSTITUTIONS
    # ========================================================

    if allow_autosubs:

        # ----------------------------------------------------
        # Goalkeeper substitution
        # ----------------------------------------------------

        starting_gk = lineup[0]
        bench_gk = bench[0]

        starting_gk_minutes = int(
            starting_gk["stats"].get(
                "minutes",
                0
            )
        )

        bench_gk_minutes = int(
            bench_gk["stats"].get(
                "minutes",
                0
            )
        )

        if (
            starting_gk_minutes == 0
            and
            bench_gk_minutes > 0
        ):

            lineup[0] = bench_gk

            print(
                f"  GK auto-sub: "
                f"{starting_gk['name']} -> "
                f"{bench_gk['name']}"
            )

        # ----------------------------------------------------
        # Outfield substitutions
        # ----------------------------------------------------

        for substitute in bench[1:]:

            substitute_minutes = int(
                substitute["stats"].get(
                    "minutes",
                    0
                )
            )

            if substitute_minutes <= 0:
                continue

            for index in range(
                1,
                len(lineup)
            ):

                starter = lineup[index]

                starter_minutes = int(
                    starter["stats"].get(
                        "minutes",
                        0
                    )
                )

                if starter_minutes > 0:
                    continue

                test_lineup = list(lineup)

                test_lineup[index] = substitute

                if valid_formation(
                    test_lineup
                ):

                    lineup[index] = substitute

                    print(
                        f"  Auto-sub: "
                        f"{starter['name']} -> "
                        f"{substitute['name']}"
                    )

                    break

    # ========================================================
    # PLAYER POINTS
    # ========================================================

    total = 0
    player_breakdown = []

    for player in lineup:

        points = int(
            player["stats"].get(
                "total_points",
                0
            )
        )

        minutes = int(
            player["stats"].get(
                "minutes",
                0
            )
        )

        total += points

        player_breakdown.append({
            "name": player["name"],
            "position": player["position"],
            "points": points,
            "minutes": minutes,
        })

    # ========================================================
    # CAPTAIN / VICE-CAPTAIN
    # ========================================================

    captain_name = team.get(
        "captain",
        ""
    )

    vice_name = team.get(
        "vice",
        ""
    )

    chip = str(
        team.get(
            "chip",
            ""
        )
    ).strip().lower()

    captain = None
    vice = None

    # Search the ORIGINAL starting XI.
    # A captain who gets auto-subbed because he played 0
    # minutes still causes the vice-captain to activate.
    for player in original_starting:

        if (
            normalise(player["name"])
            ==
            normalise(captain_name)
        ):

            captain = player

        if (
            normalise(player["name"])
            ==
            normalise(vice_name)
        ):

            vice = player

    captain_activated = None

    # ========================================================
    # CAPTAIN PLAYED
    # ========================================================

    if captain:

        captain_minutes = int(
            captain["stats"].get(
                "minutes",
                0
            )
        )

        captain_points = int(
            captain["stats"].get(
                "total_points",
                0
            )
        )

        if captain_minutes > 0:

            # Normal captain:
            # base points already counted once,
            # so add another copy for 2x.
            #
            # Triple Captain:
            # base points already counted once,
            # so add TWO additional copies for 3x.
            if chip == "triple captain":

                total += captain_points * 2

                captain_activated = (
                    f"{captain['name']} (TC)"
                )

                print(
                    f"  Triple Captain: "
                    f"{captain['name']} "
                    f"+{captain_points * 2} "
                    f"(3x total)"
                )

            else:

                total += captain_points

                captain_activated = (
                    captain["name"]
                )

                print(
                    f"  Captain: "
                    f"{captain['name']} "
                    f"+{captain_points}"
                )

        # ====================================================
        # CAPTAIN DID NOT PLAY
        # ====================================================

        elif vice:

            vice_minutes = int(
                vice["stats"].get(
                    "minutes",
                    0
                )
            )

            vice_points = int(
                vice["stats"].get(
                    "total_points",
                    0
                )
            )

            if vice_minutes > 0:

                # Vice gets normal 2x.
                # Triple Captain does NOT transfer.
                total += vice_points

                captain_activated = (
                    f"{vice['name']} (VC)"
                )

                print(
                    "  Captain did not play."
                )

                print(
                    f"  Vice-captain activated: "
                    f"{vice['name']} "
                    f"+{vice_points}"
                )

    return {
        "points": total,
        "captain": captain_name,
        "vice": vice_name,
        "captain_activated": captain_activated,
        "chip": team.get("chip", "None"),
        "players": player_breakdown,
    }


# ============================================================
# GET HISTORICAL GAMEWEEK POINTS
# ============================================================

def get_gameweek_points(
    old_gameweeks,
    gameweek,
    manager_id
):

    gw_data = old_gameweeks.get(
        str(gameweek),
        {}
    )

    if not isinstance(gw_data, dict):
        return 0

    scores = gw_data.get(
        "scores",
        []
    )

    if not isinstance(scores, list):
        return 0

    for result in scores:

        if not isinstance(result, dict):
            continue

        if result.get("id") == manager_id:

            return int(
                result.get(
                    "points",
                    0
                )
            )

    return 0


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 50)
    print("          AI FANTASY LEAGUE")
    print("          OFFICIAL FPL UPDATE")
    print("=" * 50)
    print()

    # ========================================================
    # LOAD OFFICIAL FPL DATA
    # ========================================================

    print(
        "Downloading official FPL data..."
    )

    bootstrap = get_api(
        "bootstrap-static/"
    )

    players = bootstrap.get(
        "elements",
        []
    )

    events = bootstrap.get(
        "events",
        []
    )

    print(
        f"Loaded {len(players)} players."
    )

    # ========================================================
    # DETERMINE CURRENT GAMEWEEK
    # ========================================================

    current_event = None

    # First: currently active GW
    for event in events:

        if event.get("is_current"):

            current_event = event
            break

    # Second: if there is a next GW,
    # use the previous one
    if current_event is None:

        for event in events:

            if event.get("is_next"):

                previous_id = (
                    event["id"] - 1
                )

                current_event = next(
                    (
                        e for e in events
                        if e["id"] == previous_id
                    ),
                    None
                )

                break

    # Third: latest finished GW
    if current_event is None:

        finished = [
            e for e in events
            if e.get("finished")
        ]

        if finished:
            current_event = finished[-1]

    if current_event is None:

        raise Exception(
            "Could not determine current Gameweek."
        )

    gameweek = current_event["id"]

    print(
        f"FPL current Gameweek: GW{gameweek}"
    )

    # ========================================================
    # LOAD SQUADS.JSON
    # ========================================================

    squads_file = (
        ROOT
        / "data"
        / "squads.json"
    )

    if not squads_file.exists():

        raise Exception(
            f"Could not find squads.json: "
            f"{squads_file}"
        )

    with open(
        squads_file,
        "r",
        encoding="utf-8"
    ) as file:

        squad_data = json.load(file)

    if "gameweeks" not in squad_data:

        raise Exception(
            "squads.json is using the old format. "
            "Expected a 'gameweeks' object."
        )

    gameweeks = squad_data["gameweeks"]

    gw_key = str(gameweek)

    if gw_key not in gameweeks:

        raise Exception(
            f"No squad submission exists for GW{gameweek}."
        )

    raw_current_squads = gameweeks[gw_key]

    # ========================================================
    # REMOVE METADATA
    # ========================================================

    current_squads = {
        manager_id: team
        for manager_id, team
        in raw_current_squads.items()
        if isinstance(team, dict)
        and "starting" in team
        and "bench" in team
    }

    print(
        f"Loaded {len(current_squads)} AI squads "
        f"for GW{gameweek}."
    )

    if not current_squads:

        raise Exception(
            f"No valid AI squads found for GW{gameweek}."
        )

    # ========================================================
    # PREVIOUS GAMEWEEK
    # ========================================================

    previous_squads = gameweeks.get(
        str(gameweek - 1),
        {}
    )

    if isinstance(previous_squads, dict):

        previous_squads = {
            manager_id: team
            for manager_id, team
            in previous_squads.items()
            if isinstance(team, dict)
            and "starting" in team
            and "bench" in team
        }

    # ========================================================
    # VALIDATE CURRENT SQUADS
    # ========================================================

    print()
    print(
        "Validating AI squads..."
    )

    for manager_id, team in current_squads.items():

        print()
        print(
            f"Checking "
            f"{team.get('name', manager_id)}..."
        )

        validate_squad(team)

        if gameweek > 1:

            if manager_id not in previous_squads:

                raise Exception(
                    f"{manager_id} has no previous "
                    f"Gameweek squad."
                )

            validate_transfer(
                previous_squads[manager_id],
                team
            )

        print(
            "  ✓ Squad structure valid"
        )

    # ========================================================
    # DOWNLOAD OFFICIAL GAMEWEEK LIVE DATA
    # ========================================================

    print()
    print(
        f"Downloading official GW{gameweek} "
        f"player points..."
    )

    live_data = get_api(
        f"event/{gameweek}/live/"
    )

    live = {
        item["id"]: item["stats"]
        for item in live_data.get(
            "elements",
            []
        )
    }

    print(
        f"Loaded live data for "
        f"{len(live)} players."
    )

    # ========================================================
    # CALCULATE SCORES
    # ========================================================

    results = []

    gw_finished = bool(
        current_event.get("finished")
    )

    for manager_id, team in current_squads.items():

        print()
        print("=" * 50)
        print(
            team.get(
                "name",
                manager_id
            )
        )
        print("=" * 50)

        result = calculate_team(
            team,
            players,
            live,
            allow_autosubs=gw_finished
        )

        print(
            f"GW{gameweek} points: "
            f"{result['points']}"
        )

        results.append({
            "id": manager_id,
            "name": team.get(
                "name",
                manager_id
            ),
            "icon": team.get(
                "icon",
                ""
            ),
            "formation": team.get(
                "formation",
                ""
            ),
            "points": result["points"],
            "captain": result["captain"],
            "vice": result["vice"],
            "captain_activated": (
                result["captain_activated"]
            ),
            "chip": result["chip"],
            "players": result["players"],
        })

    results.sort(
        key=lambda x: x["points"],
        reverse=True
    )

    # ========================================================
    # LOAD EXISTING RESULTS
    # ========================================================

    results_file = (
        ROOT
        / "data"
        / "results.json"
    )

    old_results = {}

    if results_file.exists():

        try:

            with open(
                results_file,
                "r",
                encoding="utf-8"
            ) as file:

                old_results = json.load(file)

        except Exception:

            old_results = {}

    old_gameweeks = old_results.get(
        "gameweeks",
        {}
    )

    if not isinstance(old_gameweeks, dict):
        old_gameweeks = {}

    # ========================================================
    # REPLACE ONLY CURRENT GAMEWEEK
    # ========================================================

    old_gameweeks[gw_key] = {
        "status": (
            f"Official FPL GW{gameweek} "
            + (
                "data."
                if gw_finished
                else "live data."
            )
        ),
        "scores": results,
    }

    # ========================================================
    # CALCULATE CUMULATIVE TOTALS
    # ========================================================

    cumulative = {}

    for gw_number, gw_data in old_gameweeks.items():

        if not isinstance(gw_data, dict):
            continue

        scores = gw_data.get(
            "scores",
            []
        )

        if not isinstance(scores, list):
            continue

        for result in scores:

            if not isinstance(result, dict):
                continue

            manager_id = result.get(
                "id"
            )

            if manager_id is None:
                continue

            cumulative[manager_id] = (
                cumulative.get(
                    manager_id,
                    0
                )
                + int(
                    result.get(
                        "points",
                        0
                    )
                )
            )

    # ========================================================
    # BUILD DYNAMIC LEADERBOARD
    #
    # This now automatically creates:
    #
    # gw1
    # gw2
    # gw3
    # gw4
    # ...
    #
    # We will NOT need to edit this Python file every
    # Gameweek.
    # ========================================================

    leaderboard = []

    # Find every numeric Gameweek that exists
    # in the historical results.
    historical_gws = []

    for gw_number in old_gameweeks.keys():

        try:

            number = int(gw_number)

            if number > 0:
                historical_gws.append(number)

        except (TypeError, ValueError):

            continue

    historical_gws = sorted(
        set(historical_gws)
    )

    for result in results:

        manager_id = result["id"]

        entry = {
            "id": manager_id,
            "name": result["name"],
            "icon": result["icon"],
            "formation": result["formation"],
        }

        # Add every Gameweek dynamically.
        for historical_gw in historical_gws:

            entry[
                f"gw{historical_gw}"
            ] = get_gameweek_points(
                old_gameweeks,
                historical_gw,
                manager_id
            )

        entry["total"] = cumulative.get(
            manager_id,
            0
        )

        leaderboard.append(
            entry
        )

    leaderboard.sort(
        key=lambda x: x["total"],
        reverse=True
    )

    # ========================================================
    # WRITE RESULTS.JSON
    # ========================================================

    output = {
        "status": (
            f"Official FPL GW{gameweek} "
            + (
                "data."
                if gw_finished
                else "live data."
            )
        ),
        "updated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "current_gameweek": gameweek,
        "leaderboard": leaderboard,
        "gameweeks": old_gameweeks,
    }

    with open(
        results_file,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            output,
            file,
            indent=2,
            ensure_ascii=False
        )

    # ========================================================
    # FINAL OUTPUT
    # ========================================================

    print()
    print("=" * 50)
    print(
        f"          GW{gameweek} LEADERBOARD"
    )
    print("=" * 50)

    for position, team in enumerate(
        leaderboard,
        1
    ):

        print(
            f"{position}. "
            f"{team['icon']} "
            f"{team['name']} — "
            f"GW{gameweek}: "
            f"{team.get(f'gw{gameweek}', 0)} "
            f"| TOTAL: "
            f"{team['total']}"
        )

    print()
    print(
        "results.json successfully written."
    )
    print()


if __name__ == "__main__":
    main()
