import json
import re
import unicodedata
from pathlib import Path
from urllib.request import Request, urlopen
from datetime import datetime, timezone
from difflib import SequenceMatcher


BASE_URL = "https://fantasy.premierleague.com/api/"
ROOT = Path(__file__).resolve().parents[1]

GAMEWEEK = 1


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
# NAME NORMALISATION
# ============================================================

def normalise(value):
    """
    Convert a name into a comparison-friendly form.

    Examples:

        João Pedro       -> joaopedro
        Joao Pedro       -> joaopedro
        Pascal Groß      -> pascalgross
        Pascal Gross     -> pascalgross
        Dara O'Shea      -> daraoshea
        Dara O’Shea      -> daraoshea
    """

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
    """
    Return normalized individual name parts.
    """

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
# POSITION CONVERSION
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
# PLAYER DESCRIPTION
# ============================================================

def player_full_name(player):
    return (
        str(player.get("first_name", "")).strip()
        + " "
        + str(player.get("second_name", "")).strip()
    ).strip()


def player_names(player):
    """
    Generate all useful names for an official FPL player.
    """

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
    """
    Robustly match an AI squad player to the official FPL player.

    Matching priority:

        1. Exact full name
        2. Exact web_name
        3. Exact normalized name
        4. First + last name
        5. Token matching
        6. Controlled fuzzy matching

    Position is used whenever possible to prevent
    duplicate/common names from selecting the wrong player.
    """

    target = normalise(name)

    if not target:
        raise Exception(
            "Empty player name supplied."
        )

    candidates = []

    # --------------------------------------------------------
    # Build candidate list
    # --------------------------------------------------------

    for player in players:

        if position:
            official_position = fpl_position(player)

            if official_position != position:
                continue

        candidates.append(player)

    # If position filtering produced nothing, search everyone.
    if not candidates:
        candidates = list(players)

    # --------------------------------------------------------
    # 1. Exact normalized full name
    # --------------------------------------------------------

    for player in candidates:

        full = normalise(
            player_full_name(player)
        )

        if target == full:
            return player

    # --------------------------------------------------------
    # 2. Exact normalized web name
    # --------------------------------------------------------

    for player in candidates:

        web = normalise(
            player.get("web_name", "")
        )

        if target == web:
            return player

    # --------------------------------------------------------
    # 3. Exact match against any official name
    # --------------------------------------------------------

    for player in candidates:

        for official_name in player_names(player):

            if target == normalise(
                official_name
            ):
                return player

    # --------------------------------------------------------
    # 4. First + last token matching
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
    # 5. Token-based matching
    # --------------------------------------------------------

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

        target_parts = set(
            name_tokens(name)
        )

        official_parts = set(
            name_tokens(
                player_full_name(player)
            )
        )

        if target_parts and official_parts:

            overlap = len(
                target_parts & official_parts
            )

            score += overlap * 25

        # Strong last-name match
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
    # 6. Controlled fuzzy matching
    # --------------------------------------------------------

    fuzzy = []

    for player in candidates:

        names_to_check = [
            normalise(
                player_full_name(player)
            ),
            normalise(
                player.get(
                    "web_name",
                    ""
                )
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

        # Only accept a strong fuzzy match.
        if best_ratio >= 0.82:

            # Make sure another player isn't equally close.
            if len(fuzzy) == 1:

                return best_player

            second_ratio = fuzzy[1][0]

            if best_ratio - second_ratio >= 0.04:
                return best_player

    # --------------------------------------------------------
    # Failure
    # --------------------------------------------------------

    available = []

    for player in players:

        if position and fpl_position(player) != position:
            continue

        available.append(
            player_full_name(player)
        )

    sample = ", ".join(
        available[:20]
    )

    raise Exception(
        "\nCould not find FPL player: "
        + str(name)
        + "\nNormalized as: "
        + target
        + "\nRequested position: "
        + str(position)
        + "\nExample available players: "
        + sample
    )


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
# LOAD PLAYER
# ============================================================

def load_squad_player(
    players,
    live,
    name,
    position
):

    player = find_player(
        players,
        name,
        position
    )

    stats = live.get(
        player["id"],
        {}
    )

    return {
        "name": name,
        "position": position,
        "official_name": player_full_name(
            player
        ),
        "fpl_id": player["id"],
        "stats": stats,
    }


# ============================================================
# CALCULATE ONE TEAM
# ============================================================

def calculate_team(
    team,
    players,
    live
):

    starting = []
    bench = []

    print(
        "\nLoading starting XI..."
    )

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

        starting.append(
            player
        )

        print(
            "  ✓ "
            + name
            + " -> "
            + player["official_name"]
        )

    # --------------------------------------------------------
    # Bench
    # --------------------------------------------------------

    print(
        "Loading bench..."
    )

    for name, position in team["bench"]:

        player = load_squad_player(
            players,
            live,
            name,
            position
        )

        bench.append(
            player
        )

        print(
            "  ✓ "
            + name
            + " -> "
            + player["official_name"]
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
            "  GK auto-sub: "
            + starting_gk["name"]
            + " -> "
            + bench_gk["name"]
        )

        lineup[0] = bench_gk

    # --------------------------------------------------------
    # Outfield substitutes
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

            test_lineup = list(
                lineup
            )

            test_lineup[index] = (
                substitute
            )

            if valid_formation(
                test_lineup
            ):

                print(
                    "  Auto-sub: "
                    + starter["name"]
                    + " -> "
                    + substitute["name"]
                )

                lineup[index] = (
                    substitute
                )

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
    # CAPTAIN
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

    for player in lineup:

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

    # ========================================================
    # CAPTAIN BONUS
    # ========================================================

    captain_activated = None

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
                "\nCaptain: "
                + captain["name"]
                + " — "
                + str(captain_points)
                + " pts"
            )

            print(
                "Captain bonus: +"
                + str(captain_points)
            )

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
                    vice["name"]
                    + " (VC)"
                )

                print(
                    "\nCaptain did not play."
                )

                print(
                    "Vice-captain activated: "
                    + vice["name"]
                )

                print(
                    "Vice-captain bonus: +"
                    + str(vice_points)
                )

    # ========================================================
    # RETURN RESULT
    # ========================================================

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
        "          GW1 OFFICIAL FPL UPDATE"
    )
    print(
        "=============================================="
    )
    print()

    # ========================================================
    # OFFICIAL FPL DATA
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
    # SQUADS
    # ========================================================

    squads_file = (
        ROOT
        / "data"
        / "squads.json"
    )

    if not squads_file.exists():

        raise Exception(
            "Could not find squads.json at: "
            + str(squads_file)
        )

    with open(
        squads_file,
        "r",
        encoding="utf-8"
    ) as file:

        squads = json.load(file)

    print(
        f"Loaded {len(squads)} AI squads."
    )

    # ========================================================
    # LIVE GW DATA
    # ========================================================

    print()
    print(
        f"Downloading official GW{GAMEWEEK} "
        "player points..."
    )

    live_data = get_api(
        f"event/{GAMEWEEK}/live/"
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

    print()

    # ========================================================
    # CALCULATE TEAMS
    # ========================================================

    results = []

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
            "FINAL GW1 SCORE: "
            + str(result["points"])
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
            "captain": team.get(
                "captain",
                ""
            ),
            "vice": team.get(
                "vice",
                ""
            ),
            "captain_activated": result[
                "captain_activated"
            ],
            "players": result[
                "players"
            ],
        })

    # ========================================================
    # SORT
    # ========================================================

    results.sort(
        key=lambda x: x["points"],
        reverse=True
    )

    # ========================================================
    # LEADERBOARD
    # ========================================================

    leaderboard = []

    for result in results:

        leaderboard.append({
            "id": result["id"],
            "name": result["name"],
            "icon": result["icon"],
            "formation": result["formation"],
            "captain": result["captain"],
            "vice": result["vice"],
            "gw1": result["points"],
            "total": result["points"],
        })

    # ========================================================
    # RESULTS JSON
    # ========================================================

    output = (
        ROOT
        / "data"
        / "results.json"
    )

    results_data = {
        "status":
            "Updated from official FPL data.",

        "updated_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "leaderboard":
            leaderboard,

        "gameweeks": {
            "1": {
                "status":
                    "Official FPL GW1 data.",
                "scores":
                    results
            }
        }
    }

    with open(
        output,
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
    # FINAL DISPLAY
    # ========================================================

    print()
    print(
        "=============================================="
    )
    print(
        "          FINAL GW1 LEADERBOARD"
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
            f"{team['gw1']} pts"
        )

    print()
    print(
        "=============================================="
    )
    print(
        "results.json successfully written."
    )
    print(
        "=============================================="
    )
    print()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
