import json
import re
import unicodedata
from pathlib import Path
from urllib.request import Request, urlopen
from datetime import datetime, timezone

BASE_URL = "https://fantasy.premierleague.com/api/"
ROOT = Path(__file__).resolve().parents[1]


def get_api(path):
    request = Request(
        BASE_URL + path,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    with urlopen(request, timeout=30) as response:
        return json.load(response)


def normalise(name):
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


def words(name):
    return set(
        re.findall(
            r"[a-z0-9]+",
            name.lower()
        )
    )


def find_player(players, name):

    target = normalise(name)
    target_words = words(name)

    # First: exact web_name
    for player in players:

        if target == normalise(
            player.get(
                "web_name",
                ""
            )
        ):
            return player

    # Second: exact full name
    for player in players:

        full_name = (
            player.get("first_name", "")
            + " "
            + player.get("second_name", "")
        )

        if target == normalise(
            full_name
        ):
            return player

    # Third: flexible word matching
    matches = []

    for player in players:

        full_name = (
            player.get("first_name", "")
            + " "
            + player.get("second_name", "")
        )

        if target_words.issubset(
            words(full_name)
        ):
            matches.append(player)

    if not matches:

        raise Exception(
            "Could not find FPL player: "
            + name
        )

    return matches[0]


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


def calculate_team(
    team,
    players,
    live
):

    starting = []
    bench = []

    # -------------------------------
    # Starting XI
    # -------------------------------

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

    # -------------------------------
    # Bench
    # -------------------------------

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

    # -------------------------------
    # Goalkeeper substitution
    # -------------------------------

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

    # -------------------------------
    # Outfield substitutions
    # -------------------------------

    for substitute in bench[1:]:

        if substitute["stats"].get(
            "minutes",
            0
        ) == 0:
            continue

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

            test = list(lineup)

            test[index] = substitute

            if valid_formation(test):

                print(
                    "  Auto-sub: "
                    + lineup[index]["name"]
                    + " -> "
                    + substitute["name"]
                )

                lineup[index] = substitute
                break

    # -------------------------------
    # Calculate player points
    # -------------------------------

    total = 0

    print("  Starting XI points:")

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

    # -------------------------------
    # Captain
    # -------------------------------

    captain = None

    for player in lineup:

        if (
            player["name"]
            == team["captain"]
        ):
            captain = player
            break

    # -------------------------------
    # Vice
    # -------------------------------

    vice = None

    for player in lineup:

        if (
            player["name"]
            == team["vice"]
        ):
            vice = player
            break

    # -------------------------------
    # Captain gets second copy
    # -------------------------------

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

            print(
                "  Captain bonus: "
                + captain["name"]
                + " +"
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

                print(
                    "  Vice-captain activated: "
                    + vice["name"]
                    + " +"
                    + str(vice_points)
                )

    return total


def main():

    print()
    print(
        "===================================="
    )
    print(
        "      AI FANTASY LEAGUE"
    )
    print(
        "      GW1 OFFICIAL FPL UPDATE"
    )
    print(
        "===================================="
    )
    print()

    # -------------------------------
    # Official FPL player data
    # -------------------------------

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

    # -------------------------------
    # Locked squads
    # -------------------------------

    with open(
        ROOT / "data" / "squads.json",
        encoding="utf-8"
    ) as file:

        squads = json.load(file)

    print(
        f"Loaded {len(squads)} AI squads."
    )

    print()

    # -------------------------------
    # GW1 ONLY
    # -------------------------------

    gameweek = 1

    print(
        "Downloading official GW1 "
        "player points..."
    )

    live_data = get_api(
        "event/1/live/"
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

    scores = []

    # -------------------------------
    # Calculate all AI teams
    # -------------------------------

    for manager_id, team in squads.items():

        print(
            "================================"
        )

        print(
            team["name"]
        )

        print(
            "================================"
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

    # -------------------------------
    # Sort
    # -------------------------------

    scores.sort(
        key=lambda x: x["points"],
        reverse=True
    )

    # -------------------------------
    # Leaderboard
    # -------------------------------

    leaderboard = []

    for result in scores:

        leaderboard.append({
            "id": result["id"],
            "name": result["name"],
            "icon": result["icon"],
            "formation":
                squads[
                    result["id"]
                ]["formation"],
            "captain":
                result["captain"],
            "gw1":
                result["points"],
            "total":
                result["points"]
        })

    # -------------------------------
    # Results
    # -------------------------------

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

    print()
    print(
        "===================================="
    )
    print(
        "       FINAL GW1 LEADERBOARD"
    )
    print(
        "===================================="
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
        "results.json successfully written."
    )


if __name__ == "__main__":
    main()
