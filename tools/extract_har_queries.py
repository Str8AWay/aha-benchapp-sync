"""Extract the BenchApp GraphQL queries the sync uses from a recorded HAR.

Usage: python -I tools/extract_har_queries.py HAR OUT_DIR

Writes OUT_DIR/<operationName>.graphql (first-seen query text, verbatim) and,
next to the test fixtures, the recorded variables for one point and one penalty.
"""
import json
import re
import sys
from pathlib import Path

OPERATIONS = {
    "login",
    "eventListScreenContent",
    "eventDetailsScreenContent",
    "scorekeeperEvent",
    "scorekeeperRostersPage",
    "scorekeeperPointsAndPenalties",
    "scorekeeperCreateRosterPlayer",
    "scorekeeperSetAttendance",
    "scorekeeperCreateEventPoint",
    "scorekeeperCreateEventPenalty",
    "scorekeeperCreateEventShot",
    "scorekeeperSetPeriod",
    "manageGoaliesCreateEventGoalie",
    "getTeamRecord",
}
# HAR entry index -> fixture name, for request-shape tests
VARIABLE_FIXTURES = {133: "har_point_vars.json", 565: "har_penalty_vars.json"}
FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
TERMINAL_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def main(har_path: str, out_dir: str) -> None:
    text = TERMINAL_ESCAPE.sub("", Path(har_path).read_text(encoding="utf-8"))
    entries = json.loads(text)["log"]["entries"]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = set()
    for index, entry in enumerate(entries):
        request = entry["request"]
        if "graphql.benchapp.com" not in request["url"]:
            continue
        body = json.loads(request["postData"]["text"])
        name = body.get("operationName")
        if name in OPERATIONS and name not in written:
            (out / f"{name}.graphql").write_text(body["query"] + "\n", encoding="utf-8")
            written.add(name)
        if index in VARIABLE_FIXTURES:
            (FIXTURE_DIR / VARIABLE_FIXTURES[index]).write_text(
                json.dumps(body["variables"], indent=2) + "\n", encoding="utf-8"
            )
    missing = OPERATIONS - written
    print(f"wrote {len(written)} queries to {out}")
    if missing:
        sys.exit(f"missing operations: {sorted(missing)}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
