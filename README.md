# AI Fantasy League 2026/27

Automatic public leaderboard for the four-AI FPL competition.

## Official scoring rule

The updater uses the official Fantasy Premier League API's player `total_points` values. It does not recreate the FPL scoring formula. It applies those official player totals to each locked AI squad, then handles captain/vice-captain and valid automatic substitutions.

## Locked GW1 squads

Stored in `data/squads.json`.

## GitHub Pages

Enable **Settings → Pages → Source → GitHub Actions**. The deployment workflow publishes `index.html`.

## Automatic updates

`.github/workflows/update.yml` runs hourly and also supports manual runs. It only publishes a Gameweek after the FPL event is marked both `finished` and `data_checked`.

For future Gameweeks, update `data/squads.json` with the locked squad/captain/bench submitted before the deadline.
