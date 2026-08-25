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
            "User-Agent": "Mozilla/5.0"
        }
    )

    with urlopen(request, timeout=30) as response:
        return json.load(response)


# ============================================================
# NAME NORMALISATION
# ============================================================

def normalise(name):
    """
    Makes names easier to compare.

    Example:
        João Pedro -> joaopedro
        Sangaré -> sangare
        Groß -> gross
    """

    name = unicodedata.normalize(
        "NFKD",
        name
    )

    name = name.encode(
        "ascii",
        "ignore"
    ).decode()

    return re.sub(
        r"[^a-z0-9]",
        "",
        name.lower()
    )


# ============================================================
# PLAYER LOOKUP
# ============================================================

def find_player(players, name):
    """
    Match an AI squad name with an official FPL player.

    Handles differences such as:

        Elliott Anderson -> Anderson
        Pascal Gross -> Gross / Groß
        Mamadou Sangare -> Sangaré
        Joao Pedro -> João Pedro
        Bruno Fernandes -> Fernandes
    """

    target = normalise(name)

    # Known differences between our squad names
    # and FPL's web_name.
    aliases = {
        "elliottanderson": "anderson",
        "pascalgross": "gross",
        "mamadousangare": "sangare",
        "joaopedro": "joaopedro",
        "cristhianmosquera": "mosquera",
        "cristianmosquera": "mosquera",
        "dominicalvertdlewin": "calvertdlewin",
        "nobelmendy": "mendy",
        "jeremysarmiento": "sarmiento",
        "sidikicherif": "cherif",
        "christostzolis": "tzolis",
        "jonahkusiasare": "kusiasare",
        "jacobgreaves": "greaves",
        "bobbythomas": "thomas",
        "markflekken": "flekken",
        "martindubravka": "dubravka",
        "antoninkinsky": "kinsky",
        "bartverbruggen": "verbruggen",
        "riccardocalafiori": "calafiori",
        "lukeshaw": "shaw",
        "dominikszoboszlai": "szoboszlai",
        "kristofferajer": "ajer",
        "daraoshea": "oshea",
        "brunofernandes": "fernandes",
        "bryanmbeumo": "mbeumo",
        "carlosbaleba": "baleba",
        "erlinghaaland": "haaland",
        "harrymaguire": "maguire",
        "gabriel": "gabriel",
        "tyrickmitchell": "mitchell",
    }

    search_name = aliases.get(
        target,
        target
    )

    # --------------------------------------------------------
    # 1. Exact official web_name
    # --------------------------------------------------------

    for player in players:

        web_name = normalise(
            player.get("web_name", "")
        )

        if search_name == web_name:
            return player

    # --------------------------------------------------------
    # 2. Exact full name
    # --------------------------------------------------------

    for player in players:

        full_name = (
            player.get("first_name", "")
            + " "
            + player.get("second_name", "")
        )

        if target == normalise(full_name):
            return player

    # --------------------------------------------------------
    # 3. Last-name match
    # --------------------------------------------------------

    target_words = re.findall(
        r"[a-z0-9]+",
        target
    )

    if target_words:

        last_name = target_words[-1]

        for player in players:

            second_name = normalise(
                player.get(
                    "second_name",
                    ""
                )
            )

            web_name = normalise(
                player.get(
                    "web_name",
                    ""
                )
            )

            if (
                last_name == second_name
                or last_name == web_name
                or search_name == second_name
                or search_name == web_name
            ):
                return player

    # --------------------------------------------------------
    # 4. Partial match
    # --------------------------------------------------------

    for player in players:

        full_name = normalise(
            player.get("first_name", "")
            + " "
            + player.get("second_name", "")
        )

        web_name = normalise(
            player.get("web_name", "")
        )

        if (
            search_name in full_name
            or search_name in web_name
            or target in full_name
        ):
            return player

    # --------------------------------------------------------
    # 5. Fail with useful information
    # --------------------------------------------------------

    raise Exception(
        "Could not find FPL player: "
        + name
        + " | searched as: "
        + search_name
    )


# ============================================================
# FORMATION CHECK
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
# CALCULATE ONE AI TEAM
# ============================================================

def calculate_team(team, players, live):

    starting = []
    bench = []

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
    # Goalkeeper
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
            "  GK auto-sub: "
            + lineup[0]["name"]
            + " -> "
            + bench[0]["name"]
        )

        lineup[0] = bench[0]

    # --------------------------------------------------------
    # Outfield players
    # --------------------------------------------------------

    for substitute in bench[1:]:

        # Bench player must have played.
        if substitute["stats"].get(
            "minutes",
            0
        ) == 0:
            continue

        # Look for a non-playing starter.
        for index in range(
            1,
            len(lineup)
        ):

            if lineup[index][
                "stats"
            ].get(
                "minutes",
                0
            ) > 0:
                continue

            test_lineup = list(lineup)

            test_lineup[index] = substitute

            # FPL cannot leave an invalid formation.
            if valid_formation(
                test_lineup
            ):

                print(
                    "  Auto-sub: "
                    + lineup[index]["name"]
                    + " -> "
                    + substitute["name"]
                )

                lineup[index] = substitute
                break

    # ========================================================
    # PLAYER POINTS
    # ========================================================

    total = 0

    print(
        "  Starting XI points:"
    )

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

        print(
            f"    {player['name']}: "
            f"{points} pts "
            f"({minutes} min)"
        )

        total += points

    # ========================================================
    # CAPTAIN
    # ========================================================

    captain = None

    for player in lineup:

        if (
            player["name"]
            == team["captain"]
        ):

            captain = player
            break

    # ========================================================
    # VICE CAPTAIN
    # ========================================================

    vice = None

    for player in lineup:

        if (
            player["name"]
            == team["vice"]
        ):

            vice = player
            break

    # ========================================================
    # CAPTAIN MULTIPLIER
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

        # Captain played: double his points.
        if captain_minutes > 0:

            total += captain_points

            print(
                "  Captain bonus: "
                + captain["name"]
                + " +"
                + str(captain_points)
            )

        # Captain didn't play: vice gets the multiplier.
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

                print(
                    "  Vice-captain activated: "
                    + vice["name"]
                    + " +"
                    + str(vice_points)
                )

    return total


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print(
        "========================================"
    )
    print(
        "       AI FANTASY LEAGUE"
    )
    print(
        "       GW1 OFFICIAL FPL UPDATE"
    )
    print(
        "========================================"
    )
    print()

    # ========================================================
    # DOWNLOAD OFFICIAL FPL DATA
    # ========================================================

    print(
        "Downloading official FPL data..."
    )

    bootstrap = get_api(
        "bootstrap-static/"
    )

    players = bootstrap[
        "elements"
    ]

    print(
        f"Loaded {len(players)} players."
    )

    # ========================================================
    # LOAD LOCKED AI SQUADS
    # ========================================================

    squads_file = (
        ROOT
        / "data"
        / "squads.json"
    )

    with open(
        squads_file,
        encoding="utf-8"
    ) as file:

        squads = json.load(file)

    print(
        f"Loaded {len(squads)} AI squads."
    )

    print()

    # ========================================================
    # GAMEWEEK 1
    # ========================================================

    gameweek = 1

    print(
        "Downloading official GW1 "
        "player points..."
    )

    live_data = get_api(
        f"event/{gameweek}/live/"
    )

    live = {
        item["id"]: item["stats"]
        for item in live_data["elements"]
    }

    print(
        f"Loaded live data for "
        f"{len(live)} players."
    )

    print()

    # ========================================================
    # CALCULATE EVERY AI
    # ========================================================

    scores = []

    for manager_id, team in squads.items():

        print(
            "========================================"
        )

        print(
            team["name"]
        )

        print(
            "========================================"
        )

        score = calculate_team(
            team,
            players,
            live
        )

        print()

        print(
            "FINAL GW1 SCORE: "
            + str(score)
        )

        print()

        scores.append({
            "id": manager_id,
            "name": team["name"],
            "icon": team["icon"],
            "points": score,
            "captain": team["captain"]
        })

    # ========================================================
    # SORT LEADERBOARD
    # ========================================================

    scores.sort(
        key=lambda x: x["points"],
        reverse=True
    )

    leaderboard = []

    for result in scores:

        team = squads[
            result["id"]
        ]

        leaderboard.append({
            "id": result["id"],
            "name": result["name"],
            "icon": result["icon"],
            "formation": team["formation"],
            "captain": team["captain"],
            "gw1": result["points"],
            "total": result["points"]
        })

    # ========================================================
    # CREATE RESULTS.JSON
    # ========================================================

    results = {
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
                    scores
            }
        }
    }

    output = (
        ROOT
        / "data"
        / "results.json"
    )

    with open(
        output,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            results,
            file,
            indent=2,
            ensure_ascii=False
        )

    # ========================================================
    # PRINT FINAL LEADERBOARD
    # ========================================================

    print()
    print(
        "========================================"
    )
    print(
        "       FINAL GW1 LEADERBOARD"
    )
    print(
        "========================================"
    )

    for position, team in enumerate(
        leaderboard,
        1
    ):

        print(
            f"{position}. "
            f"{team['name']} — "
            f"{team['gw1']} pts"
        )

    print()
    print(
        "========================================"
    )
    print(
        "results.json successfully written."
    )
    print(
        "========================================"
    )
    print()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
