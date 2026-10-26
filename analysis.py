"""Match analysis and the console report."""

from __future__ import annotations

import math

from maps import get_map_meta, nearest_location
from telemetry import MatchTelemetry


# ── helpers ──────────────────────────────────────────────────────────

def _direction_label(from_xy, to_xy) -> str:
    """Compass direction from `from_xy` to `to_xy`."""
    if not from_xy or not to_xy:
        return ""
    dx = to_xy[0] - from_xy[0]
    dy = to_xy[1] - from_xy[1]
    angle = math.degrees(math.atan2(-dy, dx)) % 360
    dirs = [
        (0,   "from the east"),
        (45,  "from the north-east"),
        (90,  "from the north"),
        (135, "from the north-west"),
        (180, "from the west"),
        (225, "from the south-west"),
        (270, "from the south"),
        (315, "from the south-east"),
    ]
    best = min(dirs, key=lambda d: min(abs(angle - d[0]), 360 - abs(angle - d[0])))
    return best[1]


def _rotation_summary(positions, map_name) -> str:
    """Describe how the player rotated across the map."""
    if len(positions) < 3:
        return "Not enough movement data."

    # Sample ~5 waypoints
    n = len(positions)
    indices = [0, n // 4, n // 2, 3 * n // 4, n - 1]
    waypoints = [positions[i] for i in indices]

    total_dist = 0
    for i in range(1, len(positions)):
        dx = positions[i][1] - positions[i - 1][1]
        dy = positions[i][2] - positions[i - 1][2]
        total_dist += math.sqrt(dx * dx + dy * dy)

    dist_m = total_dist / 100
    locations_visited = []
    seen = set()
    for _, x, y in waypoints:
        loc = nearest_location(map_name, x, y)
        if loc not in seen:
            seen.add(loc)
            locations_visited.append(loc)

    route = " → ".join(locations_visited)
    return f"Route: {route}. Total distance on foot and in vehicles: ~{dist_m:.0f} m."


# ── main analysis ────────────────────────────────────────────────────

def analyze_match(
    telem: MatchTelemetry,
    match_data: dict,
    player_name: str,
) -> dict:
    """Produce a report dict from one match."""
    map_name = match_data["map"]
    meta = get_map_meta(map_name)
    pname_lower = player_name.lower()

    # Find my participant stats
    my_stats = None
    for p in match_data["participants"]:
        if p["name"].lower() == pname_lower:
            my_stats = p
            break

    placement = my_stats["win_place"] if my_stats else "?"
    kills = my_stats["kills"] if my_stats else 0
    damage = my_stats["damage_dealt"] if my_stats else 0
    survived = my_stats["time_survived"] if my_stats else 0
    walk_dist = my_stats["walk_distance"] if my_stats else 0
    ride_dist = my_stats["ride_distance"] if my_stats else 0
    dbnos = my_stats["dbnos"] if my_stats else 0
    assists = my_stats["assists"] if my_stats else 0

    # Landing
    if telem.landing:
        lx, ly, lt = telem.landing
        landing_loc = nearest_location(map_name, lx, ly)
    else:
        landing_loc = "unknown"

    # Rotation
    rotation = _rotation_summary(telem.positions, map_name)

    # Death
    death_info = None
    if telem.death:
        d = telem.death
        dist_m = d["distance"]
        if dist_m > 1000:
            dist_m /= 100  # some API versions report in cm
        direction = ""
        if d.get("killer_pos") and d.get("my_pos"):
            direction = _direction_label(d["my_pos"], d["killer_pos"])
        death_info = {
            "killer": d["killer"] or "unknown",
            "weapon": d["weapon"],
            "distance": dist_m,
            "direction": direction,
        }

    # Tips — context-sensitive
    tips = _generate_tips(telem, match_data, my_stats, death_info)

    return {
        "match_id": match_data["match_id"],
        "map_name": map_name,
        "map_label": meta["label"],
        "game_mode": match_data["game_mode"],
        "duration": match_data["duration"],
        "placement": placement,
        "kills": kills,
        "damage": damage,
        "survived": survived,
        "walk_distance": walk_dist,
        "ride_distance": ride_dist,
        "dbnos": dbnos,
        "assists": assists,
        "landing_loc": landing_loc,
        "rotation": rotation,
        "death_info": death_info,
        "tips": tips,
        "zone_damage": telem.zone_damage_taken,
        "heals": telem.total_heals,
        "boosts": telem.total_boosts,
        "kills_detail": telem.kills_by_me,
        "death_type": my_stats["death_type"] if my_stats else "",
    }


def _generate_tips(telem, match_data, my_stats, death_info) -> list[str]:
    tips = []

    # 1. Zone damage
    if telem.zone_damage_taken > 50:
        tips.append(
            f"You took {telem.zone_damage_taken:.0f} HP from the zone. "
            "Rotate earlier: be inside the next circle before it starts "
            "moving, not when it reaches you."
        )

    # 2. Death analysis
    if death_info:
        dist = death_info["distance"]
        weapon = death_info["weapon"]
        direction = death_info["direction"]

        if dist > 100:
            tips.append(
                f"Killed from {dist:.0f} m ({weapon}, {direction}). "
                "At this range open ground kills you. Move cover to cover, "
                "use ridges and smokes, and never stand still in the open."
            )
        elif dist > 30:
            tips.append(
                f"Killed from {dist:.0f} m ({weapon}, {direction}). "
                "At mid range position decides the fight. Take cover and "
                "high ground before you start shooting."
            )
        else:
            tips.append(
                f"Killed up close ({dist:.0f} m, {weapon}). "
                "Clear buildings before you walk in, listen for footsteps "
                "and keep a close-range weapon ready."
            )

    # 2b. Heal cap: bandages and first aid stop at 75 HP. Only boosts take you
    #     higher, and a boost wears off after about two minutes.
    capped = {"Item_Heal_Bandage_C", "Item_Heal_FirstAid_C"}
    if death_info and telem.death:
        td = telem.death["t"]
        last_boost = max(telem.boost_times) if telem.boost_times else None
        recent_cap = [t for t, iid in telem.heal_log if iid in capped and td - 120 <= t <= td]
        if recent_cap and (last_boost is None or td - last_boost > 150):
            last_hit = telem.damage_taken[-1][3] if telem.damage_taken else 0
            hit = (f" The last shot did {last_hit:.0f}."
                   if 0 < last_hit < 100 else "")
            tips.append(
                "Your last heals were bandages or first aid, which stop at 75 HP, "
                "and you had no boost running." + hit + " Save an energy drink "
                "for the end game: 75 HP is not full HP."
            )

    # 3. Low damage / kills
    if my_stats:
        kills = my_stats["kills"]
        damage = my_stats["damage_dealt"]
        placement = my_stats["win_place"]

        if kills == 0 and damage < 50:
            tips.append(
                "You barely did any damage. Land somewhere busier and take "
                "early fights to get your aim warmed up."
            )
        elif placement <= 10 and kills <= 1:
            tips.append(
                f"Top {placement} with only {kills} kills and {damage:.0f} damage: "
                "too passive. Late game, a push from a good position wins "
                "when you know where they are and they do not."
            )
        elif kills >= 3 and placement > 30:
            tips.append(
                f"{kills} kills but placed #{placement}: aggressive without a "
                "position. After a kill, move right away and think about "
                "your next cover."
            )

    # 4. Healing / boosts
    if telem.total_heals == 0 and telem.total_boosts == 0 and my_stats:
        if my_stats["time_survived"] > 120:
            tips.append(
                "You used no heals or boosts. Pick them up from the start: "
                "boosts also give you run speed, which matters on rotations."
            )

    # 5. Short survival
    if my_stats and my_stats["time_survived"] < 120 and my_stats["win_place"] > 50:
        tips.append(
            "You lived under 2 minutes. Land further from the flight path, "
            "or on a rooftop where you find a gun first."
        )

    # 6. Generic positioning tip if we don't have 3 yet
    if len(tips) < 3:
        tips.append(
            "Pick your next position before you leave the current one. "
            "Ask yourself where you run if you get shot now. "
            "If there is no answer, you are standing in the wrong place."
        )
    if len(tips) < 3:
        tips.append(
            "Use free look (Alt) while running to scan around you "
            "without slowing down."
        )
    if len(tips) < 3:
        tips.append(
            "Before every fight check what you have: height, cover, surprise. "
            "If you have none, skip the fight and find a better spot."
        )

    return tips[:3]


# ── printing ─────────────────────────────────────────────────────────

def print_report(report: dict):
    """Print a single-match report."""
    sep = "─" * 56

    print(f"\n{sep}")
    print(f"  MATCH REPORT: {report['map_label']}")
    print(f"  Mode: {report['game_mode']}  |  Length: {report['duration'] // 60} min")
    print(sep)

    print(f"\n  📊 Result:")
    print(f"     Place: #{report['placement']}")
    print(f"     Kills: {report['kills']}  |  Damage: {report['damage']:.0f}")
    print(f"     Knocks: {report['dbnos']}  |  Assists: {report['assists']}")
    print(f"     Survived: {report['survived'] // 60} min {report['survived'] % 60} s")
    print(f"     On foot: {report['walk_distance']:.0f} m  |  Vehicle: {report['ride_distance']:.0f} m")

    print(f"\n  🪂 Landing: {report['landing_loc']}")

    print(f"\n  🗺️  Rotation:")
    print(f"     {report['rotation']}")

    if report["death_info"]:
        d = report["death_info"]
        print(f"\n  💀 Death:")
        print(f"     Killer: {d['killer']}")
        print(f"     Weapon: {d['weapon']}")
        print(f"     Distance: {d['distance']:.0f} m, {d['direction']}")
    elif report.get("death_type") == "alive":
        print(f"\n  🏆 Chicken dinner!")
    else:
        print(f"\n  💀 Death: no details found")

    if report["zone_damage"] > 0:
        print(f"     Zone damage: {report['zone_damage']:.0f}")

    print(f"\n  💡 Tips:")
    for i, tip in enumerate(report["tips"], 1):
        print(f"     {i}. {tip}")

    print(f"\n{sep}\n")


# ── multi-match summary ──────────────────────────────────────────────

def analyze_multiple(reports: list[dict]) -> dict:
    n = len(reports)
    placements = [r["placement"] for r in reports if isinstance(r["placement"], (int, float))]
    kills_list = [r["kills"] for r in reports]
    damage_list = [r["damage"] for r in reports]
    survived_list = [r["survived"] for r in reports]

    death_causes: dict[str, int] = {}
    for r in reports:
        if r["death_info"]:
            w = r["death_info"]["weapon"]
            death_causes[w] = death_causes.get(w, 0) + 1

    top_death = max(death_causes.items(), key=lambda x: x[1]) if death_causes else ("—", 0)

    top10 = sum(1 for p in placements if p <= 10)
    wins = sum(1 for p in placements if p == 1)

    return {
        "count": n,
        "avg_placement": sum(placements) / len(placements) if placements else 0,
        "avg_kills": sum(kills_list) / n if n else 0,
        "avg_damage": sum(damage_list) / n if n else 0,
        "avg_survived": sum(survived_list) / n if n else 0,
        "top10": top10,
        "wins": wins,
        "top_death_weapon": top_death[0],
        "top_death_count": top_death[1],
        "maps_played": list({r["map_label"] for r in reports}),
    }


def print_summary(summary: dict, count: int):
    sep = "─" * 56
    print(f"\n{sep}")
    print(f"  SUMMARY OF YOUR LAST {count} MATCHES")
    print(sep)
    print(f"\n  📊 Averages:")
    print(f"     Place: #{summary['avg_placement']:.1f}")
    print(f"     Kills: {summary['avg_kills']:.1f}")
    print(f"     Damage: {summary['avg_damage']:.0f}")
    print(f"     Survived: {summary['avg_survived'] / 60:.1f} min")
    print(f"\n  🏆 Top 10: {summary['top10']} of {count}  |  Wins: {summary['wins']}")
    print(f"  🗺️  Maps: {', '.join(summary['maps_played'])}")
    if summary["top_death_weapon"] != "—":
        print(
            f"  💀 Most common killer weapon: {summary['top_death_weapon']} "
            f"({summary['top_death_count']}×)"
        )
    print(f"\n{sep}\n")
