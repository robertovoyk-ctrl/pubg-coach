#!/usr/bin/env python3
"""
PUBG Post-Match Coach: analyse your recent matches.

Usage:
  python coach.py last          breakdown of your last match + replay GIF
  python coach.py last 5        summary of your last 5 matches
"""

import sys
import os
from pathlib import Path

from dotenv import load_dotenv

from api import PubgApi, PubgApiError
from telemetry import parse_telemetry
from replay import render_replay
from analysis import analyze_match, analyze_multiple, print_report, print_summary


ENV_PATH = Path(__file__).parent / ".env"


def setup_env() -> tuple[str, str, str]:
    """Load or create .env with API key, player name, platform."""
    load_dotenv(ENV_PATH)

    api_key = os.getenv("PUBG_API_KEY", "").strip()
    player_name = os.getenv("PUBG_PLAYER_NAME", "").strip()
    platform = os.getenv("PUBG_PLATFORM", "steam").strip()

    changed = False
    if not api_key:
        api_key = input("Enter your PUBG API key: ").strip()
        changed = True
    if not player_name:
        player_name = input("Enter your in-game name: ").strip()
        changed = True

    plat = input(f"Platform [{platform}]: ").strip() if changed else ""
    if plat:
        platform = plat

    if changed:
        with open(ENV_PATH, "w") as f:
            f.write(f"PUBG_API_KEY={api_key}\n")
            f.write(f"PUBG_PLAYER_NAME={player_name}\n")
            f.write(f"PUBG_PLATFORM={platform}\n")
        print(f"Settings saved to {ENV_PATH}\n")

    return api_key, player_name, platform


def cmd_last(api: PubgApi, player_name: str, platform: str, count: int = 1):
    print(f"Loading player \"{player_name}\" ({platform})...")
    try:
        player = api.get_player(player_name, platform)
    except PubgApiError as e:
        print(f"Error: {e}")
        sys.exit(1)

    available = len(player["matches"])
    if available == 0:
        print("No recent matches for this player.")
        sys.exit(0)

    count = min(count, available)
    match_ids = player["matches"][:count]

    if count == 1:
        _analyze_single(api, platform, match_ids[0], player_name)
    else:
        _analyze_multi(api, platform, match_ids, player_name, count)


def _analyze_single(api, platform, match_id, player_name):
    print(f"Loading match {match_id[:16]}...")
    try:
        match_data = api.get_match(match_id, platform)
    except PubgApiError as e:
        print(f"Could not load match: {e}")
        sys.exit(1)

    print("Downloading telemetry (10-30 s)...")
    try:
        telemetry_raw = api.get_telemetry(match_data["telemetry_url"])
    except Exception as e:
        print(f"Could not load telemetry: {e}")
        sys.exit(1)

    print(f"Parsing telemetry ({len(telemetry_raw)} events)...")
    telem = parse_telemetry(telemetry_raw, player_name)

    print("Rendering 2D replay...")
    try:
        output_path = render_replay(telem, match_data, player_name)
        print(f"Replay saved: {output_path}")
    except Exception as e:
        print(f"Could not render replay: {e}")

    report = analyze_match(telem, match_data, player_name)
    print_report(report)


def _analyze_multi(api, platform, match_ids, player_name, count):
    reports = []
    for i, mid in enumerate(match_ids, 1):
        print(f"\nMatch {i}/{count}: {mid[:16]}...")
        try:
            match_data = api.get_match(mid, platform)
            telemetry_raw = api.get_telemetry(match_data["telemetry_url"])
            telem = parse_telemetry(telemetry_raw, player_name)
            report = analyze_match(telem, match_data, player_name)
            reports.append(report)

            p = report["placement"]
            k = report["kills"]
            d = report["damage"]
            print(f"  #{p} | {k} kills | {d:.0f} damage | {report['map_label']}")
        except Exception as e:
            print(f"  Error: {e}")

    if reports:
        summary = analyze_multiple(reports)
        print_summary(summary, len(reports))
    else:
        print("Could not load any match.")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        sys.exit(0)

    api_key, player_name, platform = setup_env()
    api = PubgApi(api_key)

    cmd = sys.argv[1]
    if cmd == "last":
        count = 1
        if len(sys.argv) > 2:
            try:
                count = int(sys.argv[2])
            except ValueError:
                print(f"Invalid count: {sys.argv[2]}")
                sys.exit(1)
        cmd_last(api, player_name, platform, count)
    else:
        print(f"Unknown command: \"{cmd}\"\n")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
