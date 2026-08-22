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
        names = [
            player["web_name"],
            player["first_name"] + " " + player["second_name"]
        ]

        for name in names:
            key = normalise(name)
            index.setdefault(key, []).append(player)

    return index


def find_player(index, name):
    matches = index.get(normalise(name), [])

    if len(matches) == 1:
        return matches[0]

    if matches:
        return matches[0]

    raise KeyError("FPL player not found: " + name)


def formation_is_valid(players):
    counts = {
        "GK": 0,
        "DEF": 0,
        "MID": 0,
        "FWD": 0
    }

    for player in players:
        counts[player["pos"]] += 1

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
            "live": live_players[player["id"]]
        })

    bench = []

    for name, position in team["bench"]:
        player = find_player(player_index, name)

        bench.append({
            "name": name,
            "position": position,
            "player": player,
            "live": live_players[player["id"]]
        })

    lineup = list(starting)
    used_bench = set()

    # Goalkeeper substitution
    if (
        lineup[0]["live"]["minutes"] == 0
        and bench[0]["live"]["minutes"] > 0
    ):
        lineup[0] = bench[0]
        used_bench.add(0)

    # Outfield automatic substitutions
    for bench_index, substitute in enumerate(bench):

        if bench_index in used_bench:
            continue

        if substitute["live"]["minutes"] == 0:
            continue

        for starting_index in range(1, len(lineup)):

            if lineup[starting_index]["live"]["minutes"] != 0:
                continue

            test_positions = [
                player["position"]
                for player in lineup
            ]

            test_positions[starting_index] = substitute["position"]

            test_players = [
                {"pos": position}
                for position in test_positions
            ]

            if formation_is_valid(test_players):
                lineup[starting_index] = substitute
                used_bench.add(bench_index)
                break

    # Official FPL total_points for each player
    total = sum(
        int(player["live"]["total_points"])
        for player in lineup
    )

    # Captain gets the official FPL captain multiplier.
    captain = None
    vice_captain = None

    for player in lineup:

        if player["name"] == team["captain"]:
            captain = player

        if player["name"] == team["vice"]:
            vice_captain = player

    if captain and captain["live"]["minutes"] > 0:
        total += int(captain["live"]["total_points"])

    elif vice_captain and vice_captain["live"]["minutes"] > 0:
        total += int(vice_captain["live"]["total_points"])

    return total


def main():

    bootstrap = get_api("bootstrap-static/")

    players = bootstrap["elements"]
    events = bootstrap["events"]

    squads = load_squads()

    player_index = build_player_index(players)

    completed_gameweeks = {}

    for event in events:

        gameweek = event["id"]

        # Only use Gameweeks officially marked finished
        # AND data checked by FPL.
        if not event.get("finished"):
            continue

        if not event.get("data_checked"):
            continue

        live_data = get_api(
            f"event/{gameweek}/live/"
        )

        live_players = {
            player["id"]: player
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
            "status": "Official FPL data marked finished and checked.",
            "scores": scores
        }

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


if __name__ == "__main__":
    main()
