"""Thin PUBG API client (no external wrapper libraries)."""

import time
import requests

API_BASE = "https://api.pubg.com"
HEADERS_ACCEPT = "application/vnd.api+json"


class PubgApiError(Exception):
    pass


class PubgApi:
    def __init__(self, api_key: str):
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Accept": HEADERS_ACCEPT,
        })

    def _get(self, url: str, retries: int = 3):
        for attempt in range(retries):
            resp = self._session.get(url, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 6))
                print(f"  Rate limit, waiting {wait} s...")
                time.sleep(wait)
                continue
            if resp.status_code == 404:
                raise PubgApiError(
                    "404: player or match not found. Check the name and platform."
                )
            raise PubgApiError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        raise PubgApiError("Too many retries")

    def get_player(self, name: str, platform: str = "steam") -> dict:
        url = f"{API_BASE}/shards/{platform}/players?filter[playerNames]={name}"
        data = self._get(url)
        players = data.get("data", [])
        if not players:
            raise PubgApiError(f"Player \"{name}\" not found on {platform}")
        player = players[0]
        match_ids = [
            m["id"] for m in player.get("relationships", {})
                                    .get("matches", {})
                                    .get("data", [])
        ]
        return {
            "account_id": player["id"],
            "name": player["attributes"]["name"],
            "matches": match_ids,
        }

    def get_match(self, match_id: str, platform: str = "steam") -> dict:
        url = f"{API_BASE}/shards/{platform}/matches/{match_id}"
        data = self._get(url)
        attrs = data["data"]["attributes"]
        included = data.get("included", [])
        telemetry_url = None
        participants = []
        rosters = []
        for item in included:
            if item["type"] == "asset" and item["attributes"].get("name") == "telemetry":
                telemetry_url = item["attributes"]["URL"]
            elif item["type"] == "participant":
                stats = item["attributes"]["stats"]
                participants.append({
                    "id": item["id"],
                    "player_id": stats.get("playerId", ""),
                    "name": stats.get("name", ""),
                    "kills": stats.get("kills", 0),
                    "damage_dealt": stats.get("damageDealt", 0),
                    "win_place": stats.get("winPlace", 0),
                    "time_survived": stats.get("timeSurvived", 0),
                    "death_type": stats.get("deathType", ""),
                    "walk_distance": stats.get("walkDistance", 0),
                    "ride_distance": stats.get("rideDistance", 0),
                    "dbnos": stats.get("DBNOs", 0),
                    "assists": stats.get("assists", 0),
                    "heals": stats.get("heals", 0),
                    "boosts": stats.get("boosts", 0),
                })
            elif item["type"] == "roster":
                r_attrs = item["attributes"]
                r_participants = [
                    p["id"]
                    for p in item.get("relationships", {})
                                 .get("participants", {})
                                 .get("data", [])
                ]
                rosters.append({
                    "id": item["id"],
                    "rank": r_attrs.get("stats", {}).get("rank", 0),
                    "participant_ids": r_participants,
                })
        if not telemetry_url:
            raise PubgApiError("No telemetry for this match")
        return {
            "match_id": match_id,
            "map": attrs.get("mapName", "Unknown"),
            "game_mode": attrs.get("gameMode", ""),
            "duration": attrs.get("duration", 0),
            "shard": attrs.get("shardId", platform),
            "created_at": attrs.get("createdAt", ""),
            "telemetry_url": telemetry_url,
            "participants": participants,
            "rosters": rosters,
        }

    def get_telemetry(self, url: str) -> list:
        resp = self._session.get(url, timeout=60)
        resp.raise_for_status()
        return resp.json()
