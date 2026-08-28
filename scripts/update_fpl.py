import json
import re
import unicodedata
from pathlib import Path
from urllib.request import Request, urlopen
from datetime import datetime, timezone
from difflib import SequenceMatcher


BASE_URL = "https://fantasy.premierleague.com/api/"
ROOT = Path(__file__).resolve().parents[1]

SQUADS_FILE = ROOT / "data" / "squads.json"
RESULTS_FILE = ROOT / "data" / "results.json"


# ============================================================
# FPL API
# ============================================================

def get_api(path):
    url = BASE_URL + path

    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/120 Safari/537.36"
            ),
            "Accept": "application/json",
        },
    )

    with urlopen(request, timeout=30) as response:
        return json.load(response)


# ============================================================
# GAMEWEEK DETECTION
# ============================================================

def get_latest_finished_gameweek(bootstrap):
    """
    Returns the latest Gameweek whose official FPL status is finished.

    This is safer for the AI league because we only want to score
    a Gameweek after it has actually finished.
    """

    events = bootstrap.get("events", [])

    finished = [
        event
        for event in events
        if event.get("finished") is True
    ]

    if not finished:
        raise Exception(
            "No finished Gameweek found in official FPL data."
        )

    return max(
        finished,
        key=lambda event: event["id"]
    )["id"]


# ============================================================
# NAME NORMALISATION
# ============================================================

def normalise(value):

    if value is None:
        return ""

    value = str(value)

    value = unicodedata.normalize(
        "NFKD",
        value
    )

    value = "".join(
        char
        for char in value
        if not unicodedata.combining(char)
    )

    value = value.lower()

    return re.sub(
        r"[^a-z0-9]",
        "",
        value
    )


def name_tokens(value):

    if value is None:
        return []

    value = unicodedata.normalize(
        "NFKD",
        str(value)
    )

    value = "".join(
        char
        for char in value
        if not unicodedata.combining(char)
    )

    value = value.lower()

    return re.findall(
        r"[a-z0-9]+",
        value
    )


# ============================================================
# POSITIONS
# ============================================================

POSITION_MAP = {
    1: "GK",
    2: "DEF",
    3: "MID",
    4: "FWD",
}


def fpl_position(player):
    return POSITION_MAP.get(
        player.get("element_type")
    )


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

    first = str(
        player.get("first_name", "")
    ).strip()

    second = str(
        player.get("second_name", "")
    ).strip()

    web = str(
        player.get("web_name", "")
    ).strip()

    names = set()

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
        raise Exception(
            "Empty player name supplied."
        )

    candidates = []

    for player in players:

        if position:

            official_position = fpl_position(
                player
            )

            if official_position != position:
                continue

        candidates.append(player)

    if not candidates:
        candidates = list(players)

    # --------------------------------------------------------
    # Exact full name
    # --------------------------------------------------------

    for player in candidates:

        if target == normalise(
            player_full_name(player)
        ):
            return player

    # --------------------------------------------------------
    # Exact web name
    # --------------------------------------------------------

    for player in candidates:

        if target == normalise(
            player.get("web_name", "")
        ):
            return player

    # --------------------------------------------------------
    # Any official name
    # --------------------------------------------------------

    for player in candidates:

        for official_name in player_names(player):

            if target == normalise(
                official_name
            ):
                return player

    # --------------------------------------------------------
    # First + last token
    # --------------------------------------------------------

    target_parts = name_tokens(name)

    if len(target_parts) >= 2:

        first_target = target_parts[0]
        last_target = target_parts[-1]

        possible = []

        for player in candidates:

            first = normalise(
                player.get("first_name", "")
            )

            second = normalise(
                player.get("second_name", "")
            )

            web = normalise(
                player.get("web_name", "")
            )

            if (
                first == first_target
                and (
                    last_target == second
                    or last_target == web
                    or second.endswith(last_target)
                )
            ):
                possible.append(player)

        if len(possible) == 1:
            return possible[0]

    # --------------------------------------------------------
    # Token matching
    # --------------------------------------------------------

    scored = []

    target_parts = set(
        name_tokens(name)
    )

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
            name_tokens(
                player_full_name(player)
            )
        )

        overlap = len(
            target_parts & official_parts
        )

        score += overlap * 25

        if (
            target_parts
            and official_parts
            and list(target_parts)[-1]
            == list(official_parts)[-1]
        ):
            score += 20

        if score > 0:
            scored.append(
                (score, player)
            )

    if scored:

        scored.sort(
            key=lambda item: item[0],
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
        key=lambda item: item[0],
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
# SQUAD STRUCTURE VALIDATION
# ============================================================

def validate_squad(team):

    starting = team.get("starting", [])
    bench = team.get("bench", [])

    if len(starting) != 11:
        raise Exception(
            f"{team.get('name')} has "
            f"{len(starting)} starting players. "
            "Exactly 11 are required."
        )

    if len(bench) != 4:
        raise Exception(
            f"{team.get('name')} has "
            f"{len(bench)} bench players. "
            "Exactly 4 are required."
        )

    all_players = starting + bench

    names = [
        normalise(player[0])
        for player in all_players
    ]

    if len(names) != len(set(names)):
        raise Exception(
            f"{team.get('name')} has duplicate players."
        )

    counts = {
        "GK": 0,
        "DEF": 0,
        "MID": 0,
        "FWD": 0
    }

    for name, position in all_players:

        if position not in counts:
            raise Exception(
                f"{team.get('name')}: invalid position "
                f"{position} for {name}"
            )

        counts[position] += 1

    if counts != {
        "GK": 2,
        "DEF": 5,
        "MID": 5,
        "FWD": 3
    }:
        raise Exception(
            f"{team.get('name')} has invalid squad structure: "
            f"{counts}. "
            "Required: 2 GK / 5 DEF / 5 MID / 3 FWD."
        )

    # Starting XI formation
    starting_counts = {
        "GK": 0,
        "DEF": 0,
        "MID": 0,
        "FWD": 0
    }

    for _, position in starting:
        starting_counts[position] += 1

    if starting_counts["GK"] != 1:
        raise Exception(
            f"{team.get('name')} must start exactly 1 GK."
        )

    if starting_counts["DEF"] < 3:
        raise Exception(
            f"{team.get('name')} must start at least 3 DEF."
        )

    if starting_counts["MID"] < 2:
        raise Exception(
            f"{team.get('name')} must start at least 2 MID."
        )

    if starting_counts["FWD"] < 1:
        raise Exception(
            f"{team.get('name')} must start at least 1 FWD."
        )

    print(
        f"  ✓ Squad structure valid: "
        f"2 GK / 5 DEF / 5 MID / 3 FWD"
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
            f"  ⚠️ WARNING: '{name}' not found "
            f"in official FPL database. "
            "Assigned 0 points."
        )

        official_name = (
            f"{name} (Unregistered)"
        )

        fpl_id = 0

        stats = {
            "total_points": 0,
            "minutes": 0
        }

    return {
        "name": name,
        "position": position,
        "official_name": official_name,
        "fpl_id": fpl_id,
        "stats": stats,
    }


# ============================================================
# VALID FORMATION
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
# CALCULATE ONE TEAM
# ============================================================

def calculate_team(
    team,
    players,
    live
):

    validate_squad(team)

    starting = []
    bench = []

    print("\nLoading starting XI...")

    for name, position in team["starting"]:

        player = load_squad_player(
            players,
            live,
            name,
            position
        )

        starting.append(player)

        print(
            f"  ✓ {name} -> "
            f"{player['official_name']}"
        )

    print("\nLoading bench...")

    for name, position in team["bench"]:

        player = load_squad_player(
            players,
            live,
            name,
            position
        )

        bench.append(player)

        print(
            f"  ✓ {name} -> "
            f"{player['official_name']}"
        )

    lineup = list(starting)

    # ========================================================
    # AUTOMATIC SUBSTITUTIONS
    # ========================================================

    print(
        "\nChecking automatic substitutions..."
    )

    # --------------------------------------------------------
    # Goalkeeper
    # --------------------------------------------------------

    starting_gk = lineup[0]
    bench_gk = bench[0]

    if (
        int(
            starting_gk["stats"].get(
                "minutes",
                0
            )
        ) == 0
        and int(
            bench_gk["stats"].get(
                "minutes",
                0
            )
        ) > 0
    ):

        print(
            f"  GK auto-sub: "
            f"{starting_gk['name']} -> "
            f"{bench_gk['name']}"
        )

        lineup[0] = bench_gk

    # --------------------------------------------------------
    # Outfield bench players in submitted order
    # --------------------------------------------------------

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

                print(
                    f"  Auto-sub: "
                    f"{starter['name']} -> "
                    f"{substitute['name']}"
                )

                lineup[index] = substitute

                break

    # ========================================================
    # PLAYER POINTS
    # ========================================================

    print(
        "\nStarting XI points:"
    )

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

        print(
            f"  {player['name']}: "
            f"{points} pts "
            f"({minutes} min)"
        )

    # ========================================================
    # CAPTAIN / VICE
    # ========================================================

    captain_name = team.get(
        "captain",
        ""
    )

    vice_name = team.get(
        "vice",
        ""
    )

    captain = None
    vice = None

    # Important:
    # captain/vice are checked against the ORIGINAL
    # submitted XI, as in FPL.

    for player in starting:

        if normalise(
            player["name"]
        ) == normalise(
            captain_name
        ):
            captain = player

        if normalise(
            player["name"]
        ) == normalise(
            vice_name
        ):
            vice = player

    captain_activated = None

    # --------------------------------------------------------
    # Captain played
    # --------------------------------------------------------

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

            total += captain_points

            captain_activated = (
                captain["name"]
            )

            print(
                f"\nCaptain: "
                f"{captain['name']} — "
                f"{captain_points} pts"
            )

            print(
                f"Captain bonus: "
                f"+{captain_points}"
            )

        # ----------------------------------------------------
        # Captain did not play -> Vice Captain
        # ----------------------------------------------------

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

                total += vice_points

                captain_activated = (
                    f"{vice['name']} (VC)"
                )

                print(
                    "\nCaptain did not play."
                )

                print(
                    f"Vice-captain activated: "
                    f"{vice['name']}"
                )

                print(
                    f"Vice-captain bonus: "
                    f"+{vice_points}"
                )

    return {
        "points": total,
        "captain": captain_name,
        "vice": vice_name,
        "captain_activated": captain_activated,
        "players": player_breakdown,
    }


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print(
        "=============================================="
    )
    print(
        "          AI FANTASY LEAGUE"
    )
    print(
        "          OFFICIAL FPL UPDATE"
    )
    print(
        "=============================================="
    )
    print()

    # ========================================================
    # DOWNLOAD BOOTSTRAP
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

    print(
        f"Loaded {len(players)} players."
    )

    # ========================================================
    # DETERMINE LATEST FINISHED GW
    # ========================================================

    gameweek = get_latest_finished_gameweek(
        bootstrap
    )

    print()
    print(
        f"Latest finished Gameweek: GW{gameweek}"
    )

    # ========================================================
    # LOAD SQUADS
    # ========================================================

    if not SQUADS_FILE.exists():

        raise Exception(
            f"Could not find squads.json at:\n"
            f"{SQUADS_FILE}"
        )

    with open(
        SQUADS_FILE,
        "r",
        encoding="utf-8"
    ) as file:

        squads = json.load(file)

    print(
        f"Loaded {len(squads)} AI squads."
    )

    # ========================================================
    # LOAD OLD RESULTS
    # ========================================================

    if RESULTS_FILE.exists():

        with open(
            RESULTS_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            old_results = json.load(file)

    else:

        old_results = {}

    old_gameweeks = old_results.get(
        "gameweeks",
        {}
    )

    # ========================================================
    # SAFETY CHECK
    # ========================================================

    if str(gameweek) in old_gameweeks:

        print()
        print(
            f"⚠️ GW{gameweek} already exists "
            "in results.json."
        )

        print(
            "This script will RECALCULATE and "
            "replace that Gameweek."
        )

    # ========================================================
    # DOWNLOAD LIVE GW DATA
    # ========================================================

    print()
    print(
        f"Downloading official GW{gameweek} "
        "player points..."
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
    # CALCULATE ALL TEAMS
    # ========================================================

    current_results = []

    for manager_id, team in squads.items():

        print()
        print(
            "=============================================="
        )

        print(
            team.get(
                "name",
                manager_id
            )
        )

        print(
            "=============================================="
        )

        result = calculate_team(
            team,
            players,
            live
        )

        print()
        print(
            f"FINAL GW{gameweek} SCORE: "
            f"{result['points']}"
        )

        current_results.append({
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
            "captain": team.get(
                "captain",
                ""
            ),
            "vice": team.get(
                "vice",
                ""
            ),
            "captain_activated":
                result[
                    "captain_activated"
                ],
            "players":
                result["players"],
        })

    # ========================================================
    # SAVE CURRENT GAMEWEEK
    # ========================================================

    updated_gameweeks = dict(
        old_gameweeks
    )

    updated_gameweeks[
        str(gameweek)
    ] = {
        "status":
            f"Official FPL GW{gameweek} data.",
        "scores":
            current_results
    }

    # ========================================================
    # CALCULATE CUMULATIVE TOTALS
    # ========================================================

    manager_totals = {}

    for manager_id in squads:

        manager_totals[
            manager_id
        ] = 0

    for gw_key, gw_data in updated_gameweeks.items():

        for result in gw_data.get(
            "scores",
            []
        ):

            manager_id = result.get(
                "id"
            )

            points = int(
                result.get(
                    "points",
                    0
                )
            )

            if manager_id in manager_totals:

                manager_totals[
                    manager_id
                ] += points

    # ========================================================
    # CREATE LEADERBOARD
    # ========================================================

    leaderboard = []

    for manager_id, team in squads.items():

        # Find this team's current GW score
        current_score = 0

        for result in current_results:

            if result["id"] == manager_id:

                current_score = result[
                    "points"
                ]

                break

        leaderboard.append({
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
            "captain": team.get(
                "captain",
                ""
            ),
            "vice": team.get(
                "vice",
                ""
            ),

            # Dynamic GW field
            f"gw{gameweek}":
                current_score,

            # Backwards-compatible field
            "current_gw":
                current_score,

            # Historical cumulative total
            "total":
                manager_totals.get(
                    manager_id,
                    0
                ),
        })

    leaderboard.sort(
        key=lambda x: x["total"],
        reverse=True
    )

    # ========================================================
    # BUILD FINAL RESULTS.JSON
    # ========================================================

    results_data = {
        "status":
            "Updated from official FPL data.",

        "updated_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "current_gameweek":
            gameweek,

        "leaderboard":
            leaderboard,

        "gameweeks":
            updated_gameweeks
    }

    # ========================================================
    # WRITE RESULTS
    # ========================================================

    with open(
        RESULTS_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            results_data,
            file,
            indent=2,
            ensure_ascii=False
        )

    # ========================================================
    # FINAL OUTPUT
    # ========================================================

    print()
    print(
        "=============================================="
    )

    print(
        f"       FINAL GW{gameweek} LEADERBOARD"
    )

    print(
        "=============================================="
    )

    for position, team in enumerate(
        leaderboard,
        1
    ):

        print(
            f"{position}. "
            f"{team['icon']} "
            f"{team['name']} — "
            f"GW{gameweek}: "
            f"{team[f'gw{gameweek}']} pts | "
            f"TOTAL: "
            f"{team['total']} pts"
        )

    print()
    print(
        "=============================================="
    )

    print(
        "results.json successfully written."
    )

    print(
        f"GW{gameweek} added without deleting "
        "previous Gameweeks."
    )

    print(
        "=============================================="
    )

    print()


if __name__ == "__main__":
    main()
