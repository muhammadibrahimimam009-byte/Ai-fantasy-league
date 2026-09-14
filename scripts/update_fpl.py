import json
import re
import unicodedata
from pathlib import Path
from urllib.request import Request, urlopen
from datetime import datetime, timezone
from difflib import SequenceMatcher

BASE_URL = "https://fantasy.premierleague.com/api/"
ROOT = Path(__file__).resolve().parents[1]

POSITION_MAP = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


# ============================================================
# FPL API
# ============================================================

def get_api(path):
    request = Request(
        BASE_URL + path,
        headers={
            "User-Agent": "AI-Fantasy-League/3.0",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


# ============================================================
# NAME NORMALISATION
# ============================================================

def normalise(value):
    if value is None:
        return ""
    value = unicodedata.normalize("NFKD", str(value))
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = value.lower()
    return re.sub(r"[^a-z0-9]", "", value)


def name_tokens(value):
    if value is None:
        return []
    value = unicodedata.normalize("NFKD", str(value))
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", value.lower())


# ============================================================
# PLAYER DATA
# ============================================================

def fpl_position(player):
    return POSITION_MAP.get(player.get("element_type"))


def player_full_name(player):
    return (
        f"{str(player.get('first_name', '')).strip()} "
        f"{str(player.get('second_name', '')).strip()}"
    ).strip()


def player_name_variants(player):
    first = str(player.get("first_name", "")).strip()
    second = str(player.get("second_name", "")).strip()
    web = str(player.get("web_name", "")).strip()
    known = str(player.get("known_name", "")).strip()

    variants = set()
    for value in (
        first,
        second,
        web,
        known,
        f"{first} {second}".strip(),
        f"{first} {known}".strip(),
    ):
        if value:
            variants.add(value)

    return variants


def unique_player(candidates):
    by_id = {}
    for player in candidates:
        by_id[player["id"]] = player
    return by_id[next(iter(by_id))] if len(by_id) == 1 else None


def find_player(players, name, position=None):
    """Resolve a submitted human name to the current official FPL player list."""
    target = normalise(name)
    if not target:
        raise KeyError("Empty player name supplied")

    candidates = [
        p for p in players
        if not position or fpl_position(p) == position
    ]

    if not candidates:
        candidates = list(players)

    # 1. Exact full/web/known-name match.
    exact = []
    for player in candidates:
        for variant in player_name_variants(player):
            if normalise(variant) == target:
                exact.append(player)
                break
    found = unique_player(exact)
    if found:
        return found

    # 2. First name + surname match, including FPL display surname.
    target_parts = name_tokens(name)
    if len(target_parts) >= 2:
        first_target = target_parts[0]
        last_target = target_parts[-1]
        token_matches = []
        for player in candidates:
            first = normalise(player.get("first_name", ""))
            second = normalise(player.get("second_name", ""))
            web = normalise(player.get("web_name", ""))
            if first == first_target and (
                last_target == second
                or last_target == web
                or second.endswith(last_target)
                or web.endswith(last_target)
            ):
                token_matches.append(player)
        found = unique_player(token_matches)
        if found:
            return found

    # 3. Initial + surname: B. Fernandes / B Fernandes.
    if len(target_parts) >= 2:
        first_initial = target_parts[0][0]
        surname = target_parts[-1]
        initial_matches = []
        for player in candidates:
            first = normalise(player.get("first_name", ""))
            second = normalise(player.get("second_name", ""))
            web = normalise(player.get("web_name", ""))
            if first.startswith(first_initial) and surname in {second, web}:
                initial_matches.append(player)
        found = unique_player(initial_matches)
        if found:
            return found

    # 4. Conservative token scoring.
    target_set = set(target_parts)
    scored = []
    for player in candidates:
        full_tokens = set(name_tokens(player_full_name(player)))
        web_tokens = set(name_tokens(player.get("web_name", "")))
        score = 0
        score += 50 * len(target_set & full_tokens)
        score += 35 * len(target_set & web_tokens)
        full = normalise(player_full_name(player))
        web = normalise(player.get("web_name", ""))
        if target and target in full:
            score += 35
        if target and target in web:
            score += 35
        if target_parts and name_tokens(player_full_name(player)):
            if target_parts[-1] == name_tokens(player_full_name(player))[-1]:
                score += 25
        if score:
            scored.append((score, player))

    scored.sort(key=lambda item: item[0], reverse=True)
    if scored:
        if len(scored) == 1 or scored[0][0] - scored[1][0] >= 15:
            return scored[0][1]

    # 5. Very conservative fuzzy fallback.
    fuzzy = []
    for player in candidates:
        ratio = max(
            (
                SequenceMatcher(None, target, normalise(variant)).ratio()
                for variant in player_name_variants(player)
                if normalise(variant)
            ),
            default=0.0,
        )
        fuzzy.append((ratio, player))

    fuzzy.sort(key=lambda item: item[0], reverse=True)
    if fuzzy and fuzzy[0][0] >= 0.90:
        if len(fuzzy) == 1 or fuzzy[0][0] - fuzzy[1][0] >= 0.04:
            return fuzzy[0][1]

    raise KeyError(f"FPL player not found: {name} (position={position})")


# ============================================================
# SQUAD / GAMEWEEK STRUCTURE
# ============================================================

def is_manager_entry(value):
    return (
        isinstance(value, dict)
        and isinstance(value.get("starting"), list)
        and isinstance(value.get("bench"), list)
    )


def get_gameweek_teams(squad_data, gw):
    gameweeks = squad_data.get("gameweeks", {})
    if not isinstance(gameweeks, dict):
        raise RuntimeError("squads.json must contain a 'gameweeks' object.")

    raw = gameweeks.get(str(gw), {})
    if not isinstance(raw, dict):
        return {}

    return {
        manager_id: team
        for manager_id, team in raw.items()
        if is_manager_entry(team)
    }


def get_available_squad_gameweeks(squad_data):
    gameweeks = squad_data.get("gameweeks", {})
    numbers = []
    for key, value in gameweeks.items():
        try:
            gw = int(key)
        except (TypeError, ValueError):
            continue
        if gw > 0 and isinstance(value, dict):
            if any(is_manager_entry(v) for v in value.values()):
                numbers.append(gw)
    return sorted(set(numbers))


# ============================================================
# VALIDATION
# ============================================================

def validate_squad(team):
    starting = team.get("starting", [])
    bench = team.get("bench", [])
    all_players = starting + bench

    if len(starting) != 11:
        raise RuntimeError(f"{team.get('name', 'Team')} must have exactly 11 starters.")
    if len(bench) != 4:
        raise RuntimeError(f"{team.get('name', 'Team')} must have exactly 4 bench players.")

    if len(all_players) != 15:
        raise RuntimeError(f"{team.get('name', 'Team')} has {len(all_players)} players instead of 15.")

    seen = set()
    positions = {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0}
    for name, position in all_players:
        if position not in positions:
            raise RuntimeError(f"Invalid position for {name}: {position}")
        key = normalise(name)
        if key in seen:
            raise RuntimeError(f"Duplicate player in {team.get('name', 'Team')}: {name}")
        seen.add(key)
        positions[position] += 1

    if positions != {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}:
        raise RuntimeError(
            f"{team.get('name', 'Team')} has invalid squad structure: {positions}"
        )

    if sum(1 for _, pos in starting if pos == "GK") != 1:
        raise RuntimeError("Starting XI must contain exactly one goalkeeper.")
    if sum(1 for _, pos in bench if pos == "GK") != 1:
        raise RuntimeError("Bench must contain exactly one goalkeeper.")

    starting_gk_index = next(i for i, (_, pos) in enumerate(starting) if pos == "GK")
    if starting_gk_index != 0:
        raise RuntimeError("Starting goalkeeper must be the first player in starting order.")

    captain = normalise(team.get("captain", ""))
    vice = normalise(team.get("vice", ""))
    starting_names = {normalise(name) for name, _ in starting}
    if captain not in starting_names:
        raise RuntimeError(f"Captain is not in the starting XI: {team.get('captain')}")
    if vice not in starting_names:
        raise RuntimeError(f"Vice-captain is not in the starting XI: {team.get('vice')}")
    if captain == vice:
        raise RuntimeError("Captain and vice-captain must be different players.")

    if not valid_formation([{ "position": pos } for _, pos in starting]):
        raise RuntimeError(f"Invalid starting formation for {team.get('name', 'Team')}")


def validate_transfer(previous_team, current_team):
    previous = {
        normalise(name): position
        for name, position in previous_team.get("starting", []) + previous_team.get("bench", [])
    }
    current = {
        normalise(name): position
        for name, position in current_team.get("starting", []) + current_team.get("bench", [])
    }

    outgoing = [name for name in previous if name not in current]
    incoming = [name for name in current if name not in previous]

    declared_out = normalise(current_team.get("transfer_out"))
    declared_in = normalise(current_team.get("transfer_in"))
    chip = str(current_team.get("chip", "")).strip().lower().replace("_", " ")

    # A Wildcard/Free Hit can legitimately change more than one player without a hit.
    if "wildcard" in chip or "free hit" in chip:
        return

    if len(outgoing) != len(incoming):
        raise RuntimeError(
            f"{current_team.get('name')} has an invalid GW transfer: "
            f"{len(outgoing)} OUT / {len(incoming)} IN."
        )

    if outgoing:
        if len(outgoing) == 1:
            if normalise(outgoing[0]) != declared_out or normalise(incoming[0]) != declared_in:
                raise RuntimeError(f"{current_team.get('name')} declared transfer does not match squad change.")
        else:
            # Multiple-transfer weeks should still have enough metadata to explain them.
            if not int(current_team.get("free_transfers_used", 0) or 0) and not int(current_team.get("hit", 0) or 0):
                raise RuntimeError(f"{current_team.get('name')} changed multiple players without transfer metadata.")
    elif declared_out or declared_in:
        raise RuntimeError(f"{current_team.get('name')} declares a transfer but the squad did not change.")

    used = int(current_team.get("free_transfers_used", 0) or 0)
    hit = int(current_team.get("hit", 0) or 0)
    if used < 0 or hit < 0:
        raise RuntimeError(f"{current_team.get('name')} has invalid transfer metadata.")


# ============================================================
# GAMEWEEK / MATCH STATUS
# ============================================================

def player_played(stats):
    """FPL treatment for substitutions/captaincy: appearance OR yellow/red card counts as played."""
    minutes = int(stats.get("minutes", 0) or 0)
    yellow = int(stats.get("yellow_cards", 0) or 0)
    red = int(stats.get("red_cards", 0) or 0)
    return minutes > 0 or yellow > 0 or red > 0


def valid_formation(lineup):
    gk = sum(p["position"] == "GK" for p in lineup)
    defenders = sum(p["position"] == "DEF" for p in lineup)
    midfielders = sum(p["position"] == "MID" for p in lineup)
    forwards = sum(p["position"] == "FWD" for p in lineup)

    return (
        len(lineup) == 11
        and gk == 1
        and 3 <= defenders <= 5
        and 2 <= midfielders <= 5
        and 1 <= forwards <= 3
    )


def normalise_chip(value):
    return str(value or "").strip().lower().replace("_", " ").replace("-", " ")


# ============================================================
# PLAYER LOADING
# ============================================================

def load_player_strict(players, live, name, position):
    player = find_player(players, name, position)
    stats = live.get(player["id"])
    if stats is None:
        raise RuntimeError(f"No live FPL data for {name} (ID {player['id']}).")
    return {
        "name": name,
        "position": position,
        "official_name": player_full_name(player),
        "fpl_id": player["id"],
        "stats": stats,
    }


def try_load_player(players, live, name, position):
    try:
        return load_player_strict(players, live, name, position)
    except Exception as exc:
        print(f"  ⚠️ Bench player unavailable in current FPL pool: {name} ({exc})")
        return None


# ============================================================
# TEAM SCORING
# ============================================================

def calculate_team(team, players, live, allow_autosubs):
    starting = [
        load_player_strict(players, live, name, position)
        for name, position in team["starting"]
    ]

    # Keep bench raw so an old transferred-out bench player cannot break scoring.
    bench_raw = list(team["bench"])
    bench_cache = {}

    def bench_player(index):
        if index not in bench_cache:
            name, position = bench_raw[index]
            bench_cache[index] = try_load_player(players, live, name, position)
        return bench_cache[index]

    original_starting = list(starting)
    lineup = list(starting)
    used_bench = set()
    auto_subs = []

    chip = normalise_chip(team.get("chip", ""))
    bench_boost = chip == "bench boost"

    if allow_autosubs and not bench_boost:
        # Goalkeeper substitution: find the actual bench GK, not blindly bench[0].
        starting_gk_idx = next(i for i, p in enumerate(lineup) if p["position"] == "GK")
        bench_gk_idx = next(i for i, (_, pos) in enumerate(bench_raw) if pos == "GK")

        if not player_played(lineup[starting_gk_idx]["stats"]):
            incoming = bench_player(bench_gk_idx)
            if incoming is not None and player_played(incoming["stats"]):
                outgoing = lineup[starting_gk_idx]
                lineup[starting_gk_idx] = incoming
                used_bench.add(bench_gk_idx)
                auto_subs.append({"out": outgoing["name"], "in": incoming["name"]})
                print(f"  GK auto-sub: {outgoing['name']} -> {incoming['name']}")

        # Outfield substitutions follow bench priority.
        original_nonplaying = [
            i for i, p in enumerate(original_starting)
            if p["position"] != "GK" and not player_played(p["stats"])
        ]

        for bench_index in range(4):
            if bench_index in used_bench:
                continue
            if bench_raw[bench_index][1] == "GK":
                continue

            incoming = bench_player(bench_index)
            if incoming is None or not player_played(incoming["stats"]):
                continue

            for starter_index in original_nonplaying:
                # Do not replace a slot that has already been filled by another sub.
                if lineup[starter_index]["name"] != original_starting[starter_index]["name"]:
                    continue

                trial = list(lineup)
                trial[starter_index] = incoming
                if valid_formation(trial):
                    outgoing = lineup[starter_index]
                    lineup[starter_index] = incoming
                    used_bench.add(bench_index)
                    auto_subs.append({"out": outgoing["name"], "in": incoming["name"]})
                    print(f"  Auto-sub: {outgoing['name']} -> {incoming['name']}")
                    break

    # Base points from the final XI.
    total = sum(int(p["stats"].get("total_points", 0) or 0) for p in lineup)

    # Bench Boost counts all four bench players as well; no auto-subs are needed for scoring.
    if bench_boost:
        for bench_index in range(4):
            player = bench_player(bench_index)
            if player is None:
                raise RuntimeError(
                    f"Bench Boost cannot be scored because bench player "
                    f"'{bench_raw[bench_index][0]}' is unavailable."
                )
            total += int(player["stats"].get("total_points", 0) or 0)

    # Captain / vice are ALWAYS evaluated from the original submitted XI.
    captain = next((p for p in original_starting if normalise(p["name"]) == normalise(team.get("captain", ""))), None)
    vice = next((p for p in original_starting if normalise(p["name"]) == normalise(team.get("vice", ""))), None)

    captain_activated = None
    if captain is not None and player_played(captain["stats"]):
        cap_points = int(captain["stats"].get("total_points", 0) or 0)
        if chip == "triple captain":
            total += cap_points * 2
            captain_activated = f"{captain['name']} (TC)"
        else:
            total += cap_points
            captain_activated = captain["name"]
    elif vice is not None and player_played(vice["stats"]):
        vice_points = int(vice["stats"].get("total_points", 0) or 0)
        total += vice_points
        captain_activated = f"{vice['name']} (VC)"

    # Transfer hit applies after player points, except on Free Hit/Wildcard.
    hit = int(team.get("hit", 0) or 0)
    raw_points_before_hit = total
    if chip not in {"free hit", "wildcard"}:
        total -= hit

    breakdown = []
    for p in lineup:
        breakdown.append({
            "name": p["name"],
            "position": p["position"],
            "points": int(p["stats"].get("total_points", 0) or 0),
            "minutes": int(p["stats"].get("minutes", 0) or 0),
        })

    return {
        "points": total,
        "raw_points_before_hit": raw_points_before_hit,
        "hit": 0 if chip in {"free hit", "wildcard"} else hit,
        "captain": team.get("captain", ""),
        "vice": team.get("vice", ""),
        "captain_activated": captain_activated,
        "chip": team.get("chip", "None"),
        "auto_substitutions": auto_subs,
        "starting": breakdown,
    }


# ============================================================
# MAIN
# ============================================================

def main():
    print("\n" + "=" * 58)
    print("            AI FANTASY LEAGUE — FPL UPDATE")
    print("=" * 58 + "\n")

    print("Downloading official FPL data...")
    bootstrap = get_api("bootstrap-static/")
    players = bootstrap.get("elements", [])
    events = bootstrap.get("events", [])
    if not players:
        raise RuntimeError("Official FPL player data was empty.")

    squads_path = ROOT / "data" / "squads.json"
    if not squads_path.exists():
        raise FileNotFoundError(f"squads.json not found: {squads_path}")
    with squads_path.open("r", encoding="utf-8") as f:
        squad_data = json.load(f)

    available_gws = get_available_squad_gameweeks(squad_data)
    if not available_gws:
        raise RuntimeError("No Gameweek squad submissions were found in squads.json.")

    finished_gws = {
        int(ev["id"]): ev
        for ev in events
        if ev.get("finished") and ev.get("data_checked")
    }

    target_gws = sorted(gw for gw in available_gws if gw in finished_gws)
    if not target_gws:
        raise RuntimeError(
            "No Gameweek in squads.json is both finished and data-checked by FPL yet."
        )

    print(f"Locked squad GWs available: {available_gws}")
    print(f"Finished/data-checked GWs to score: {target_gws}")

    history = {}

    # Recalculate every completed locked GW from official FPL data.
    # This guarantees that fixing the updater also repairs any earlier wrong result.
    for gw in target_gws:
        print("\n" + "=" * 58)
        print(f"GW{gw}")
        print("=" * 58)

        teams = get_gameweek_teams(squad_data, gw)
        if not teams:
            continue

        for manager_id, team in teams.items():
            validate_squad(team)

        if gw > 1:
            previous_teams = get_gameweek_teams(squad_data, gw - 1)
            for manager_id, team in teams.items():
                if manager_id in previous_teams:
                    validate_transfer(previous_teams[manager_id], team)

        print(f"Downloading official GW{gw} live data...")
        live_data = get_api(f"event/{gw}/live/")
        live = {item["id"]: item["stats"] for item in live_data.get("elements", [])}

        scores = []
        for manager_id, team in teams.items():
            print(f"\n{team.get('name', manager_id)}")
            result = calculate_team(
                team,
                players,
                live,
                allow_autosubs=True,
            )
            scores.append({
                "id": manager_id,
                "name": team.get("name", manager_id),
                "icon": team.get("icon", ""),
                "formation": team.get("formation", ""),
                "points": result["points"],
                "raw_points_before_hit": result["raw_points_before_hit"],
                "hit": result["hit"],
                "captain": result["captain"],
                "vice": result["vice"],
                "captain_activated": result["captain_activated"],
                "chip": result["chip"],
                "auto_substitutions": result["auto_substitutions"],
                "starting": result["starting"],
            })
            print(f"GW{gw} points: {result['points']}")

        scores.sort(key=lambda row: row["points"], reverse=True)
        history[str(gw)] = {
            "status": "Official FPL data marked finished and checked.",
            "scores": scores,
        }

    if not history:
        raise RuntimeError("No completed Gameweeks could be scored.")

    # Cumulative totals across all completed locked GWs.
    totals = {}
    for gw_data in history.values():
        for row in gw_data["scores"]:
            totals[row["id"]] = totals.get(row["id"], 0) + int(row["points"])

    latest_gw = max(int(gw) for gw in history)
    latest_teams = get_gameweek_teams(squad_data, latest_gw)

    leaderboard = []
    all_manager_ids = set(totals) | set(latest_teams)
    historical_gws = sorted(int(gw) for gw in history)

    for manager_id in all_manager_ids:
        team = latest_teams.get(manager_id, {})
        entry = {
            "id": manager_id,
            "name": team.get("name", manager_id),
            "icon": team.get("icon", ""),
            "formation": team.get("formation", ""),
            "captain": team.get("captain", ""),
            "vice": team.get("vice", ""),
        }
        for gw in historical_gws:
            points = 0
            for row in history[str(gw)]["scores"]:
                if row["id"] == manager_id:
                    points = int(row["points"])
                    break
            entry[f"gw{gw}"] = points
        entry["total"] = totals.get(manager_id, 0)
        leaderboard.append(entry)

    leaderboard.sort(key=lambda row: row["total"], reverse=True)

    output = {
        "status": "Updated from official FPL data.",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "current_gameweek": latest_gw,
        "leaderboard": leaderboard,
        "gameweeks": history,
    }

    results_path = ROOT / "data" / "results.json"
    with results_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 58)
    print("FINAL LEADERBOARD")
    print("=" * 58)
    for rank, row in enumerate(leaderboard, 1):
        print(f"{rank}. {row['icon']} {row['name']} — TOTAL: {row['total']}")
    print("\nresults.json successfully written.\n")


if __name__ == "__main__":
    main()
