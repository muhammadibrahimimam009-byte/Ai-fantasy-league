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
        headers={"User-Agent": "AI-Fantasy-League/1.0"}
    )

    with urlopen(request, timeout=30) as response:
        return json.load(response)


def normalise(name):
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode()
    name = name.lower()
    return re.sub(r"[^a-z0-9]", "", name)


def load_squads():
    with open(ROOT / "data" / "squads.json", encoding="utf-8") as f:
        return json.load(f)


def build_player_index(players):
    index = {}

    for player in players:
        names = {
            player.get("web_name", ""),
            player.get("first_name", "") + " " + player.get("second_name", "")
        }

        for name in names:
            key = normalise(name)

            if key:
                index.setdefault(key, []).append(player)

    return index


def find_player(index, name):
    matches = index.get(normalise(name), [])

    if not matches:
        raise KeyError(f"FPL player not found: {name}")

    if len(matches) > 1:
        print(f"WARNING: multiple matches for {name}; using first match")

    return matches[0]


def position_name(element_type):
    return {
        1: "GK",
        2: "DEF",
        3: "MID",
        4: "FWD"
    }.get(element_type)


def valid_formation(lineup):
    counts = {
        "GK": 0,
        "DEF": 0,
        "MID": 0,
        "FWD": 0
    }

    for player in lineup:
        counts[player["position"]] += 1

    return (
        counts["GK"] == 1
        and counts["DEF"] >= 3
        and counts["MID"] >= 2
        and counts["FWD"] >= 1
    )


def calculate_team(team, player_index, live_players):

    starting = []

    for name, position in team["starting"]:

        player = find_player(player_index, name)

        starting.append({
            "name": name,
            "position": position,
            "player": player,
            "live": live_players.get(player["id"], {})
        })

    bench = []

    for name, position in team["bench"]:

        player = find_player(player_index, name)

        bench.append({
            "name": name,
            "position": position,
            "player": player,
            "live": live_players.get(player["id"], {})
        })

    lineup = list(starting)

    # ---------------------------------------------------------
    # AUTOMATIC SUBSTITUTIONS
    # ---------------------------------------------------------

    used_bench = set()

    # Goalkeeper
    if (
        lineup[0]["live"].get("minutes", 0) == 0
        and bench[0]["live"].get("minutes", 0) > 0
    ):
        lineup[0] = bench[0]
        used_bench.add(0)

    # Outfield substitutions
    for bench_index, substitute in enumerate(bench):

        if bench_index in used_bench:
            continue

        if substitute["live"].get("minutes", 0) == 0:
            continue

        for starting_index in range(1, len(lineup)):

            if lineup[starting_index]["live"].get("minutes", 0) != 0:
                continue

            test_lineup = list(lineup)
            test_lineup[starting_index] = substitute

            if valid_formation(test_lineup):
                lineup[starting_index] = substitute
                used_bench.add(bench_index)
                break

    # ---------------------------------------------------------
    # OFFICIAL FPL PLAYER POINTS
    # ---------------------------------------------------------

    total = sum(
        int(player["live"].get("total_points", 0))
        for player in lineup
    )

    # ---------------------------------------------------------
    # CAPTAIN / VICE-CAPTAIN
    # ---------------------------------------------------------

    captain = None
    vice = None

    for player in lineup:

        if player["name"] == team["captain"]:
            captain = player

        if player["name"] == team["vice"]:
            vice = player

    # Captain doubles if they played.
    if captain and captain["live"].get("minutes", 0) > 0:

        total += int(
            captain["live"].get("total_points", 0)
        )

    # Otherwise vice-captain doubles if they played.
    elif vice and vice["live"].get("minutes", 0) > 0:

        total += int(
            vice["live"].get("total_points", 0)
        )

    return total


def main():

    print("Downloading official FPL data...")

    bootstrap = get_api("bootstrap-static/")

    players = bootstrap["elements"]
    events = bootstrap["events"]

    squads = load_squads()

    player_index = build_player_index(players)

    completed_gameweeks = {}

    # ---------------------------------------------------------
    # FIND GAMEWEEKS THAT HAVE ACTUALLY COMPLETED
    # ---------------------------------------------------------

    for event in events:

        gameweek = event["id"]

        # We use the official event status when available.
        finished = event.get("finished", False)

        # Some API versions may not immediately expose
        # every confirmation flag consistently.
        data_checked = event.get("data_checked", True)

        if not finished:
            continue

        if not data_checked:
            continue

        print(f"Processing GW{gameweek}...")

        live_data = get_api(
            f"event/{gameweek}/live/"
        )

        live_players = {
            player["id"]: player["stats"]
            for player in live_data["elements"]
        }

        scores = []

        for manager_id, team in squads.items():

            try:

                points = calculate_team(
                    team,
                    player_index,
                    live_players
                )

                print(
                    f"{team['name']}: "
                    f"{points} points"
                )

            except Exception as error:

                print(
                    f"ERROR calculating "
                    f"{team['name']} GW{gameweek}: "
                    f"{error}"
                )

                points = 0

            scores.append({
                "id": manager_id,
                "name": team["name"],
                "icon": team["icon"],
                "points": points,
                "captain": team["captain"]
            })

        completed_gameweeks[str(gameweek)] = {
            "status": "Official FPL data retrieved.",
            "scores": scores
        }

    # ---------------------------------------------------------
    # TOTAL SEASON SCORES
    # ---------------------------------------------------------

    totals = {
        manager_id: 0
        for manager_id in squads
    }

    for gameweek in completed_gameweeks.values():

        for result in gameweek["scores"]:

            totals[result["id"]] += result["points"]

    leaderboard = []

    for manager_id, team in squads.items():

        gw1_score = 0

        if "1" in completed_gameweeks:

            for result in completed_gameweeks["1"]["scores"]:

                if result["id"] == manager_id:

                    gw1_score = result["points"]

        leaderboard.append({
            "id": manager_id,
            "name": team["name"],
            "icon": team["icon"],
            "formation": team["formation"],
            "captain": team["captain"],
            "gw1": gw1_score,
            "total": totals[manager_id]
        })

    # ---------------------------------------------------------
    # SAVE RESULTS
    # ---------------------------------------------------------

    results = {
        "status": "Updated from official FPL data.",
        "updated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "leaderboard": leaderboard,
        "gameweeks": completed_gameweeks
    }

    with open(
        ROOT / "data" / "results.json",
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            results,
            f,
            indent=2,
            ensure_ascii=False
        )

    print("Results successfully saved.")


if __name__ == "__main__":
    main()
