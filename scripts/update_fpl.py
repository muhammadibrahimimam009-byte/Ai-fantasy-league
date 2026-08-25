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
        headers={"User-Agent": "Mozilla/5.0"}
    )

    with urlopen(request, timeout=30) as response:
        return json.load(response)


def normalise(name):
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode()
    name = name.lower()
    return re.sub(r"[^a-z0-9]", "", name)


def find_player(players, name):
    target = normalise(name)

    matches = []

    for player in players:
        web_name = normalise(player.get("web_name", ""))
        full_name = normalise(
            player.get("first_name", "")
            + player.get("second_name", "")
        )

        if target == web_name or target == full_name:
            matches.append(player)

    if not matches:
        raise Exception("Could not find FPL player: " + name)

    return matches[0]


def valid_formation(lineup):
    gk = sum(x["position"] == "GK" for x in lineup)
    df = sum(x["position"] == "DEF" for x in lineup)
    md = sum(x["position"] == "MID" for x in lineup)
    fw = sum(x["position"] == "FWD" for x in lineup)

    return (
        gk == 1
        and df >= 3
        and md >= 2
        and fw >= 1
    )


def calculate_team(team, players, live):

    starting = []

    for name, position in team["starting"]:

        player = find_player(players, name)

        stats = live[player["id"]]

        starting.append({
            "name": name,
            "position": position,
            "stats": stats
        })

    bench = []

    for name, position in team["bench"]:

        player = find_player(players, name)

        stats = live[player["id"]]

        bench.append({
            "name": name,
            "position": position,
            "stats": stats
        })

    lineup = list(starting)

    # Goalkeeper automatic substitution
    if (
        lineup[0]["stats"]["minutes"] == 0
        and bench[0]["stats"]["minutes"] > 0
    ):
        lineup[0] = bench[0]

    # Outfield automatic substitutions
    used = set()

    for bench_index, substitute in enumerate(bench):

        if bench_index == 0:
            continue

        if substitute["stats"]["minutes"] == 0:
            continue

        for index in range(1, len(lineup)):

            if lineup[index]["stats"]["minutes"] != 0:
                continue

            test = list(lineup)
            test[index] = substitute

            if valid_formation(test):
                lineup[index] = substitute
                used.add(bench_index)
                break

    # Official FPL player points
    points = sum(
        int(player["stats"]["total_points"])
        for player in lineup
    )

    # Captain
    captain = next(
        (
            player for player in lineup
            if player["name"] == team["captain"]
        ),
        None
    )

    # Vice captain
    vice = next(
        (
            player for player in lineup
            if player["name"] == team["vice"]
        ),
        None
    )

    if captain and captain["stats"]["minutes"] > 0:

        points += int(
            captain["stats"]["total_points"]
        )

    elif vice and vice["stats"]["minutes"] > 0:

        points += int(
            vice["stats"]["total_points"]
        )

    return points


def main():

    print("================================")
    print("AI FANTASY LEAGUE FPL UPDATER")
    print("================================")

    bootstrap = get_api("bootstrap-static/")

    players = bootstrap["elements"]
    events = bootstrap["events"]

    with open(
        ROOT / "data" / "squads.json",
        encoding="utf-8"
    ) as f:
        squads = json.load(f)

    completed = {}

    # --------------------------------------------------
    # CHECK EVERY GAMEWEEK
    # --------------------------------------------------

    for event in events:

        gw = event["id"]

        print(
            f"Checking Gameweek {gw}..."
        )

        try:

            fixtures = get_api(
                f"fixtures/?event={gw}"
            )

        except Exception as error:

            print(
                f"Could not load GW{gw} fixtures: {error}"
            )

            continue

        # A Gameweek is complete only when ALL
        # its fixtures are finished.
        if not fixtures:
            continue

        all_finished = all(
            fixture.get("finished", False)
            for fixture in fixtures
        )

        if not all_finished:

            print(
                f"GW{gw}: not finished yet."
            )

            continue

        print(
            f"GW{gw}: COMPLETE. Getting official points..."
        )

        live_data = get_api(
            f"event/{gw}/live/"
        )

        live = {
            element["id"]: element["stats"]
            for element in live_data["elements"]
        }

        scores = []

        for manager_id, team in squads.items():

            try:

                score = calculate_team(
                    team,
                    players,
                    live
                )

                print(
                    f"  {team['name']}: {score} points"
                )

            except Exception as error:

                print(
                    f"  ERROR — {team['name']}: {error}"
                )

                raise

            scores.append({
                "id": manager_id,
                "name": team["name"],
                "icon": team["icon"],
                "points": score,
                "captain": team["captain"]
            })

        completed[str(gw)] = {
            "status": "Official FPL fixtures completed.",
            "scores": scores
        }

    # --------------------------------------------------
    # CALCULATE TOTALS
    # --------------------------------------------------

    totals = {
        manager_id: 0
        for manager_id in squads
    }

    for gw in completed.values():

        for score in gw["scores"]:

            totals[score["id"]] += score["points"]

    leaderboard = []

    for manager_id, team in squads.items():

        gw1 = 0

        if "1" in completed:

            for score in completed["1"]["scores"]:

                if score["id"] == manager_id:
                    gw1 = score["points"]

        leaderboard.append({
            "id": manager_id,
            "name": team["name"],
            "icon": team["icon"],
            "formation": team["formation"],
            "captain": team["captain"],
            "gw1": gw1,
            "total": totals[manager_id]
        })

    # --------------------------------------------------
    # SAVE
    # --------------------------------------------------

    results = {
        "status": "Updated from official FPL data.",
        "updated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "leaderboard": leaderboard,
        "gameweeks": completed
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

    print("================================")
    print("UPDATE COMPLETE")
    print("================================")


if __name__ == "__main__":
    main()
