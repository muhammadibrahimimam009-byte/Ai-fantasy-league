import json
import re
import unicodedata
from pathlib import Path
from urllib.request import Request, urlopen
from datetime import datetime, timezone

BASE_URL = "https://fantasy.premierleague.com/api/"
ROOT = Path(__file__).resolve().parents[1]


# ============================================================
# FPL API
# ============================================================

def get_api(path):
    request = Request(
        BASE_URL + path,
        headers={
            "User-Agent": "AI-Fantasy-League/1.0"
        }
    )

    with urlopen(request, timeout=30) as response:
        return json.load(response)


# ============================================================
# NAME NORMALISATION
# ============================================================

def normalise(name):
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode()
    name = name.lower()

    return re.sub(r"[^a-z0-9]", "", name)


def words(name):
    return set(
        re.findall(
            r"[a-z0-9]+",
            name.lower()
        )
    )


# ============================================================
# FIND FPL PLAYER
# ============================================================

def find_player(players, name):

    target = normalise(name)
    target_words = words(name)

    matches = []

    for player in players:

        web_name = player.get(
            "web_name",
            ""
        )

        first_name = player.get(
            "first_name",
            ""
        )

        second_name = player.get(
            "second_name",
            ""
        )

        full_name = (
            first_name
            + " "
            + second_name
        )

        # ----------------------------------------------------
        # Exact web name
        # ----------------------------------------------------

        if target == normalise(web_name):
            matches.append(player)
            continue

        # ----------------------------------------------------
        # Exact full name
        # ----------------------------------------------------

        if target == normalise(full_name):
            matches.append(player)
            continue

        # ----------------------------------------------------
        # Flexible full-name matching
        #
        # Example:
        # "Bruno Fernandes"
        #
        # can match:
        # "Bruno Miguel Borges Fernandes"
        # ----------------------------------------------------

        full_words = words(full_name)

        if target_words and target_words.issubset(
            full_words
        ):
            matches.append(player)

    if not matches:

        raise Exception(
            "Could not find FPL player: "
            + name
        )

    if len(matches) > 1:

        print(
            "WARNING: multiple matches for "
            + name
            + ". Using first match."
        )

    return matches[0]


# ============================================================
# FORMATION VALIDATION
# ============================================================

def valid_formation(lineup):

    goalkeeper = sum(
        player["position"] == "GK"
        for player in lineup
    )

    defenders = sum(
        player["position"] == "DEF"
        for player in lineup
    )

    midfielders = sum(
        player["position"] == "MID"
        for player in lineup
    )

    forwards = sum(
        player["position"] == "FWD"
        for player in lineup
    )

    return (
        goalkeeper == 1
        and defenders >= 3
        and midfielders >= 2
        and forwards >= 1
    )


# ============================================================
# CALCULATE ONE AI TEAM
# ============================================================

def calculate_team(
    team,
    players,
    live
):

    starting = []

    # --------------------------------------------------------
    # Load starting XI
    # --------------------------------------------------------

    for name, position in team["starting"]:

        player = find_player(
            players,
            name
        )

        stats = live.get(
            player["id"],
            {}
        )

        starting.append({
            "name": name,
            "position": position,
            "stats": stats
        })

    # --------------------------------------------------------
    # Load bench
    # --------------------------------------------------------

    bench = []

    for name, position in team["bench"]:

        player = find_player(
            players,
            name
        )

        stats = live.get(
            player["id"],
            {}
        )

        bench.append({
            "name": name,
            "position": position,
            "stats": stats
        })

    lineup = list(starting)

    # ========================================================
    # AUTOMATIC SUBSTITUTIONS
    # ========================================================

    # --------------------------------------------------------
    # Goalkeeper substitution
    # --------------------------------------------------------

    if (
        lineup[0]["stats"].get(
            "minutes",
            0
        ) == 0

        and bench[0]["stats"].get(
            "minutes",
            0
        ) > 0
    ):

        print(
            f"  Auto-sub GK: "
            f"{lineup[0]['name']} -> "
            f"{bench[0]['name']}"
        )

        lineup[0] = bench[0]

    # --------------------------------------------------------
    # Outfield substitutions
    # --------------------------------------------------------

    for bench_index in range(
        1,
        len(bench)
    ):

        substitute = bench[
            bench_index
        ]

        # Player didn't play
        if (
            substitute["stats"].get(
                "minutes",
                0
            ) == 0
        ):
            continue

        for starting_index in range(
            1,
            len(lineup)
        ):

            starter = lineup[
                starting_index
            ]

            # Starter played
            if (
                starter["stats"].get(
                    "minutes",
                    0
                ) > 0
            ):
                continue

            # Test the formation
            test_lineup = list(
                lineup
            )

            test_lineup[
                starting_index
            ] = substitute

            if valid_formation(
                test_lineup
            ):

                print(
                    f"  Auto-sub: "
                    f"{starter['name']} -> "
                    f"{substitute['name']}"
                )

                lineup[
                    starting_index
                ] = substitute

                break

    # ========================================================
    # OFFICIAL FPL PLAYER POINTS
    # ========================================================

    points = 0

    for player in lineup:

        player_points = int(
            player["stats"].get(
                "total_points",
                0
            )
        )

        points += player_points

    # ========================================================
    # CAPTAIN
    # ========================================================

   
