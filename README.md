# AHA → BenchApp sync

An AWS Lambda that copies [AHA Hockey](https://www.ahahockey.com) box scores into [BenchApp](https://www.benchapp.com)'s scorekeeper, so your team's record and player stats fill in without anyone typing them.

Once a day it reads your AHA team page and opens each completed game's box score. It finds the matching BenchApp game by date and opponent, enters the game, and checks that BenchApp's final score matches AHA's.

BenchApp has no public API. The sync signs in as a team manager and calls the same private GraphQL API the BenchApp website uses. If BenchApp changes that API, the sync will fail and email you until it's updated. [docs/benchapp-api-notes.md](docs/benchapp-api-notes.md) records what the sync relies on.

## What it enters

- **Goals and assists**, with the period, time, and strength (even strength, power play, short-handed or empty net). Overtime goals go in BenchApp's OT period.
- **Penalties**, with the player, infraction and minutes. This includes 10-minute misconducts and bench penalties.
- **Shots per period** for both teams, so BenchApp's totals equal AHA's.
- **Goalies** for both teams.
- **The final period**, so BenchApp shows the game as finished.

**Your players are matched by jersey number only**, because BenchApp names are often nicknames. If two rostered players share a number, the last name breaks the tie. Anyone who can't be matched, like a sub goalie, is entered as a temporary player, and the email tells you. **Opponents** are added as temporary players on their side of the sheet. On later games against the same team, those players are reused.

## What it won't do

- **It never deletes or edits anything in BenchApp.** A game that already has any goals, penalties or shots is skipped, so a game you scored by hand is left alone, and the sync can't enter a game twice.
- **Shootouts and forfeits** aren't entered. AHA only uses shootouts in the playoffs; the sync emails you to enter those games by hand.
- If the score in BenchApp doesn't match AHA's once the goals are in, the sync stops on that game and emails you rather than guessing.

## Install

You need:
- **An AWS account,** with the [AWS CLI](https://aws.amazon.com/cli/) and [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) signed in to it.
- **Python 3.12.**
- **A BenchApp login** that's a manager on the team.

It runs comfortably within the AWS free tier: one Lambda run a day, a small DynamoDB table, and an SNS topic.

**1. Store the BenchApp login** in Secrets Manager. Leave the password out of your shell history by putting the JSON in a file you then delete:

```sh
aws secretsmanager create-secret --name aha-benchapp/credentials \
  --secret-string file://credentials.json   # {"email": "...", "password": "..."}
```

**2. Find your two team IDs.**
- **AHA:** the AHA team ID is the number at the end of your team page's URL, for example `https://www.ahahockey.com/team/les-etoiles-du-nord-c1/308`.
- **BenchApp:** for the BenchApp team ID, open the team on benchapp.com and take the number from the URL.

**3. Configure the stack:**

```sh
cp samconfig.example.toml samconfig.toml
```

In `samconfig.toml`, under `[default.deploy.parameters.parameter_overrides]`:
- Set `NotifyEmail`.
- Add `AhaTeamUrl` and `BenchAppTeamId`.
- Set `DryRun = "true"` for now.

`samconfig.toml` is git-ignored, so your email stays out of the repo.

**4. Deploy:**

```sh
sam build && sam deploy
```

AWS emails `NotifyEmail` a subscription link. Click it, or you won't get the sync's emails.

**5. Try a dry run.** With `DryRun` on, the sync logs every write it would make instead of sending it. It also logs its emails instead of sending them, and saves no state:

```sh
aws lambda invoke --function-name "$(aws cloudformation describe-stacks --stack-name aha-benchapp-sync \
  --query "Stacks[0].Outputs[?OutputKey=='SyncFunctionName'].OutputValue" --output text)" /dev/stdout
```

Read the log in CloudWatch, in the stack's log group, and check that the games, players and penalties look right.

**6. Go live.** Set `DryRun = "false"`, then run `sam build && sam deploy` again.

From then on, it runs every day at 8:00 a.m. Central. A game usually shows up in BenchApp the morning after it's posted on AHA.

The first live run enters every completed game that's still empty in BenchApp. Games you've already scored are skipped. A run enters at most 20 games, so a long season catches up over a few days.

## Adding another team

Each team gets its own stack, with its own schedule, state table and emails. All the stacks can share one BenchApp login.

`samconfig.example.toml` has an example second team under `[b3]`. In your `samconfig.toml`, copy that section, then give it:
- a new `stack_name`
- the team's `AhaTeamUrl` and `BenchAppTeamId`
- your `NotifyEmail`

Then deploy it the same way, with a dry run first:

```sh
sam build && sam deploy --config-env b3
```

## Running it locally

You can also run the sync from your own machine. This is handy for trying a single game or checking a fix before you deploy it.

```sh
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
mkdir -p ~/.config/aha-benchapp   # then save {"email": "...", "password": "..."} as credentials.json there

AHA_TEAM_URL=https://www.ahahockey.com/team/les-etoiles-du-nord-c1/308 BENCHAPP_TEAM_ID=1247980 \
  PYTHONPATH=src .venv/bin/python -m aha_benchapp --dry-run [--game AHA_GAME_ID]
```

To pass the login another way, set `BENCHAPP_EMAIL` and `BENCHAPP_PASSWORD` instead of using the credentials file.

A local run saves no state, and it prints its emails instead of sending them. Leave off `--dry-run` to write to BenchApp for real. That's safe to repeat, because a game that already has scorekeeping is skipped.

If requests fail with `CERTIFICATE_VERIFY_FAILED` on a network that inspects TLS, like a corporate VPN, point `REQUESTS_CA_BUNDLE` at a bundle that includes that network's root certificate.

## Emails

The sync only emails you when a game needs a look, and only once per problem. A game that enters cleanly sends nothing.

| Result in the email | What it means | What to do |
|---|---|---|
| `synced-with-warnings` | Entered, but something was off: a temp player was added for your team, or a penalty, shot or goalie couldn't be entered. | Check the warnings and fix that part in BenchApp. |
| `failed-manual-entry` | A shootout or forfeit, which the sync doesn't enter. | Enter the game by hand. |
| `needs-cleanup` | It stopped partway through a game, or the score didn't match AHA's once the goals were in. The email lists the goals it created. | Fix or remove the game's entries in BenchApp. |
| `failed-no-event`, `failed-ambiguous-event` | It couldn't find the BenchApp game, or found more than one, for that date and opponent. | Fix the game's date or opponent in BenchApp. |
| `failed-parse-error`, `failed-api-error` | AHA's page or BenchApp's API didn't look as expected. | Usually nothing. If it keeps failing, the code needs updating. |

The `failed-*` results are retried on every run until they succeed. `needs-cleanup` and `failed-manual-entry` aren't, so the sync never touches a game twice.

To have the sync try a game again after you've emptied it in BenchApp, delete that game's item from the stack's DynamoDB table. The item's key is the AHA game ID.

If the whole run fails, for example because the BenchApp login stopped working, you get an "AHA → BenchApp sync failed" email.

## Development

```sh
.venv/bin/pytest
```

The tests run offline, against saved AHA pages and BenchApp responses in `tests/fixtures/`.

The code is in `src/aha_benchapp/`:

| File | What it does |
|---|---|
| `aha.py` | Reads AHA's team page and box scores. |
| `benchapp.py` | The BenchApp API client. Its GraphQL queries are in `graphql/`, copied from the website. |
| `matching.py` | Finds the BenchApp game, and matches players. |
| `enums.py` | Maps AHA's penalties and periods to BenchApp's. |
| `sync.py` | Enters one game, and runs the whole sync. |
| `state.py` | Keeps each game's result in DynamoDB. |
| `notify.py` | Writes and sends the emails. |
| `handler.py` | The Lambda handler. |
| `__main__.py` | The local runner. |

The design is in `docs/superpowers/specs/`.
