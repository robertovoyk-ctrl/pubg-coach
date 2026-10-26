#!/usr/bin/env python3
"""Export v3: complete match — all players, all events, raw cm coordinates.

Usage:
    python export_v3.py                        # last match → export/last_match_v3.json
    python export_v3.py --match-id <id>        # specific match
    python export_v3.py --match-id <id> -o export/erangel_v3.json
"""

import argparse
import gzip
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv
from api import PubgApi
from maps import get_map_meta
from telemetry import _weapon

EXPORT_DIR = Path(__file__).parent / "export"

HEAL_ITEMS = {
    "Item_Heal_Bandage_C":            "bandage",
    "Item_Heal_FirstAid_C":           "first aid",
    "Item_Heal_MedKit_C":             "medkit",
    "Item_Boost_EnergyDrink_C":       "energy drink",
    "Item_Boost_PainKiller_C":        "painkiller",
    "Item_Boost_AdrenalineSyringe_C": "adrenaline",
}


def _item_name(item_id):
    if not item_id:
        return ""
    if item_id in HEAL_ITEMS:
        return HEAL_ITEMS[item_id]
    n = item_id
    for pfx in ("Item_Weapon_", "Item_Ammo_", "Item_Attach_Weapon_",
                 "Item_Armor_", "Item_Back_", "Item_Head_",
                 "Item_Heal_", "Item_Boost_", "Item_Ghillie_",
                 "Item_JerryCan_", "Item_Misc_"):
        n = n.replace(pfx, "")
    return n.replace("_C", "").replace("_", " ").strip()


def _loc(obj):
    if not obj:
        return None
    loc = obj.get("location")
    if not loc:
        return None
    return {
        "x": round(loc.get("x", 0), 1),
        "y": round(loc.get("y", 0), 1),
        "z": round(loc.get("z", 0), 1),
    }


def main():
    parser = argparse.ArgumentParser(description="Export PUBG match to JSON")
    parser.add_argument("--match-id", default=None, help="Match ID (default: last match)")
    parser.add_argument("-o", "--output", default=None, help="Output path (default: export/last_match_v3.json)")
    args = parser.parse_args()

    load_dotenv(Path(__file__).parent / ".env")
    api = PubgApi(os.getenv("PUBG_API_KEY"))
    player_name = os.getenv("PUBG_PLAYER_NAME", "").strip()
    platform = os.getenv("PUBG_PLATFORM", "steam")
    if not player_name:
        print("ERROR: PUBG_PLAYER_NAME not set in .env")
        sys.exit(1)
    pname = player_name.lower()

    print("Fetching player + match…")
    player = api.get_player(player_name, platform)
    match_id = args.match_id or player["matches"][0]
    md = api.get_match(match_id, platform)
    map_name = md["map"]
    meta = get_map_meta(map_name)

    print("Downloading telemetry…")
    events = api.get_telemetry(md["telemetry_url"])
    print(f"  {len(events)} events")

    # ── match start time for elapsed() ───────────────────────────
    _mstart = None
    for ev in events:
        if ev.get("_T") == "LogMatchStart":
            try:
                _mstart = datetime.fromisoformat(ev["_D"].replace("Z", "+00:00"))
            except Exception:
                pass
            break
    if not _mstart:
        for ev in events:
            if "_D" in ev:
                try:
                    _mstart = datetime.fromisoformat(ev["_D"].replace("Z", "+00:00"))
                except Exception:
                    pass
                break

    def elapsed(ev):
        t = ev.get("elapsedTime")
        if t and t > 0:
            return t
        if _mstart and "_D" in ev:
            try:
                dt = datetime.fromisoformat(ev["_D"].replace("Z", "+00:00"))
                return max(0, (dt - _mstart).total_seconds())
            except Exception:
                pass
        return 0

    # ── player → teamId from telemetry ───────────────────────────
    player_team = {}
    name_case = {}
    for ev in events:
        if ev.get("_T") != "LogPlayerPosition":
            continue
        ch = ev.get("character") or {}
        n = ch.get("name", "")
        if not n:
            continue
        nl = n.lower()
        if nl not in player_team:
            tid = ch.get("teamId")
            if tid is not None:
                player_team[nl] = tid
                name_case[nl] = n

    team_members = defaultdict(set)
    for nl, tid in player_team.items():
        team_members[tid].add(nl)

    my_team_id = player_team.get(pname)
    my_team_names = set()
    if my_team_id is not None:
        for nl in team_members[my_team_id]:
            if nl != pname:
                my_team_names.add(nl)

    # ── anonymizers ──────────────────────────────────────────────
    mate_sorted = sorted(my_team_names)
    mate_map = {n: f"mate{i}" for i, n in enumerate(mate_sorted, 1)}

    _ecount = [0]
    _emap = {}

    def anon(name):
        if not name:
            return "unknown"
        nl = name.lower()
        if nl == pname:
            return "me"
        if nl in mate_map:
            return mate_map[nl]
        if nl not in _emap:
            _ecount[0] += 1
            _emap[nl] = f"enemy{_ecount[0]}"
        return _emap[nl]

    _tcount = [0]
    _tmap = {}
    if my_team_id is not None:
        _tcount[0] = 1
        _tmap[my_team_id] = "T1"

    def anon_team(tid):
        if tid is None:
            return "T0"
        if tid not in _tmap:
            _tcount[0] += 1
            _tmap[tid] = f"T{_tcount[0]}"
        return _tmap[tid]

    for tid in sorted(team_members.keys()):
        anon_team(tid)

    # ── roster ranks ─────────────────────────────────────────────
    pid_to_name = {}
    for p in md["participants"]:
        pid_to_name[p["id"]] = p["name"].lower()

    roster_rank = {}
    for r in md["rosters"]:
        for pid in r["participant_ids"]:
            nl = pid_to_name.get(pid, "")
            if nl in player_team:
                roster_rank[player_team[nl]] = r["rank"]
                break

    # ── single pass: collect everything ──────────────────────────
    positions = defaultdict(list)
    all_hits = []
    all_knocks = []
    all_kills = []
    vehicle_events = []
    raw_heals = defaultdict(list)
    raw_item_uses = []
    my_inventory = []
    circles = []
    my_shots = []

    for ev in events:
        et = ev.get("_T", "")

        if et == "LogPlayerPosition":
            ch = ev.get("character") or {}
            n = ch.get("name", "")
            if not n:
                continue
            loc = (ch.get("location") or {})
            veh = ev.get("vehicle")
            positions[n.lower()].append({
                "s": round(elapsed(ev), 1),
                "x": round(loc.get("x", 0), 1),
                "y": round(loc.get("y", 0), 1),
                "z": round(loc.get("z", 0), 1),
                "hp": round(ch.get("health", 0), 1),
                "in_vehicle": bool(veh),
            })

        elif et == "LogPlayerTakeDamage":
            a = (ev.get("attacker") or {})
            v = (ev.get("victim") or {})
            an = a.get("name", "")
            vn = v.get("name", "")
            if not vn or not an:
                continue
            cause = ev.get("damageTypeCategory", "")
            if "BlueZone" in cause or "Bluezone" in cause or "RedZone" in cause:
                continue
            t = elapsed(ev)
            al = _loc(a)
            vl = _loc(v)
            dist = 0
            if al and vl:
                dist = round(((al["x"] - vl["x"])**2 + (al["y"] - vl["y"])**2)**0.5 / 100, 1)
            all_hits.append({
                "s": round(t, 1),
                "attacker": anon(an),
                "victim": anon(vn),
                "weapon": _weapon(ev.get("damageCauserName", "")),
                "distance_m": dist,
                "body_part": ev.get("damageReason", ""),
                "damage": round(ev.get("damage", 0), 1),
                "attacker_pos": al,
                "victim_pos": vl,
            })

        elif et == "LogPlayerMakeGroggy":
            a = (ev.get("attacker") or ev.get("instigator") or {})
            v = (ev.get("victim") or {})
            an = a.get("name", "")
            vn = v.get("name", "")
            if not vn:
                continue
            t = elapsed(ev)
            al = _loc(a)
            vl = _loc(v)
            dist = 0
            if al and vl:
                dist = round(((al["x"] - vl["x"])**2 + (al["y"] - vl["y"])**2)**0.5 / 100, 1)
            all_knocks.append({
                "s": round(t, 1),
                "attacker": anon(an),
                "victim": anon(vn),
                "weapon": _weapon(ev.get("damageCauserName", "")),
                "distance_m": dist,
                "attacker_pos": al,
                "victim_pos": vl,
            })

        elif et in ("LogPlayerKillV2", "LogPlayerKill"):
            k = ev.get("killer") or {}
            f = ev.get("finisher") or {}
            v = (ev.get("victim") or {})
            vn = v.get("name", "")
            kn = k.get("name", "") or f.get("name", "")
            if not vn:
                continue
            t = elapsed(ev)
            kdi = ev.get("killerDamageInfo") or ev.get("finishDamageInfo") or {}
            wr = kdi.get("damageCauserName") or ev.get("damageCauserName", "")
            killer_obj = k if k.get("name") else f
            al = _loc(killer_obj)
            vl = _loc(v)
            dist = 0
            if al and vl:
                dist = round(((al["x"] - vl["x"])**2 + (al["y"] - vl["y"])**2)**0.5 / 100, 1)
            all_kills.append({
                "s": round(t, 1),
                "killer": anon(kn) if kn else "unknown",
                "victim": anon(vn),
                "weapon": _weapon(wr),
                "distance_m": dist,
                "killer_pos": al,
                "victim_pos": vl,
            })

        elif et in ("LogVehicleRide", "LogVehicleLeave"):
            ch = ev.get("character") or {}
            n = ch.get("name", "")
            veh = ev.get("vehicle") or {}
            vehicle_events.append({
                "s": round(elapsed(ev), 1),
                "event": "ride" if et == "LogVehicleRide" else "leave",
                "player": anon(n),
                "vehicle": veh.get("vehicleType", "unknown"),
                "pos": _loc(ch),
            })

        elif et == "LogHeal":
            ch = (ev.get("character") or {})
            n = ch.get("name", "")
            if not n:
                continue
            item = (ev.get("item") or {})
            iid = item.get("itemId", "")
            iname = HEAL_ITEMS.get(iid, _item_name(iid))
            raw_heals[n.lower()].append((elapsed(ev), iname, ev.get("healAmount", 0)))

        elif et == "LogItemUse":
            ch = (ev.get("character") or {})
            n = ch.get("name", "")
            if not n:
                continue
            item = (ev.get("item") or {})
            iid = item.get("itemId", "")
            if iid in HEAL_ITEMS:
                raw_item_uses.append((elapsed(ev), n.lower(), HEAL_ITEMS[iid]))
            if n.lower() == pname:
                my_inventory.append({
                    "s": round(elapsed(ev), 1),
                    "action": "use",
                    "item": _item_name(iid),
                })

        elif et == "LogItemPickup":
            ch = (ev.get("character") or {})
            if ch.get("name", "").lower() != pname:
                continue
            item = (ev.get("item") or {})
            iid = item.get("itemId", "")
            sc = item.get("stackCount", 1)
            entry = {"s": round(elapsed(ev), 1), "action": "pickup", "item": _item_name(iid)}
            if sc and sc > 1:
                entry["count"] = sc
            my_inventory.append(entry)

        elif et == "LogItemDrop":
            ch = (ev.get("character") or {})
            if ch.get("name", "").lower() != pname:
                continue
            item = (ev.get("item") or {})
            iid = item.get("itemId", "")
            sc = item.get("stackCount", 1)
            entry = {"s": round(elapsed(ev), 1), "action": "drop", "item": _item_name(iid)}
            if sc and sc > 1:
                entry["count"] = sc
            my_inventory.append(entry)

        elif et == "LogGameStatePeriodic":
            gs = ev.get("gameState") or {}
            t = gs.get("elapsedTime", 0)
            sz = gs.get("safetyZonePosition") or {}
            sr = gs.get("safetyZoneRadius", 0)
            pw = gs.get("poisonGasWarningPosition") or {}
            pr_ = gs.get("poisonGasWarningRadius", 0)
            rz = gs.get("redZonePosition") or {}
            rr = gs.get("redZoneRadius", 0)
            circles.append({
                "s": round(t, 1),
                "blue": {
                    "cx": round(sz.get("x", 0), 1),
                    "cy": round(sz.get("y", 0), 1),
                    "r": round(sr, 1),
                },
                "white": {
                    "cx": round(pw.get("x", 0), 1),
                    "cy": round(pw.get("y", 0), 1),
                    "r": round(pr_, 1),
                },
                "red": {
                    "cx": round(rz.get("x", 0), 1),
                    "cy": round(rz.get("y", 0), 1),
                    "r": round(rr, 1),
                },
                "alive": gs.get("numAlivePlayers", 0),
            })

        elif et == "LogPlayerAttack":
            a = ev.get("attacker") or {}
            if a.get("name", "").lower() != pname:
                continue
            t = elapsed(ev)
            w = ev.get("weapon") or {}
            pos = _loc(a)
            entry = {
                "s": round(t, 1),
                "pos": pos,
                "weapon": _item_name(w.get("itemId", "")),
                "attack_type": ev.get("attackType", ""),
            }
            for dkey in ("fireDirection", "direction", "aimDirection"):
                d = ev.get(dkey)
                if d and isinstance(d, dict):
                    entry["direction"] = {
                        "x": round(d.get("x", 0), 4),
                        "y": round(d.get("y", 0), 4),
                        "z": round(d.get("z", 0), 4),
                    }
                    break
            my_shots.append(entry)

    # ── group heals per player (LogHeal ticks → single uses) ─────
    heals_export = []
    for nl, raw in raw_heals.items():
        raw.sort()
        i = 0
        while i < len(raw):
            t0, iname, total_hp = raw[i]
            j = i + 1
            while j < len(raw) and raw[j][1] == iname and raw[j][0] - t0 < 10:
                total_hp += raw[j][2]
                j += 1
            heals_export.append({
                "s": round(t0, 1),
                "player": anon(nl),
                "item": iname if iname else "unknown",
                "hp": round(total_hp, 1),
            })
            i = j

    heal_keys = {(h["s"], h["player"]) for h in heals_export}
    for t, nl, iname in raw_item_uses:
        alias = anon(nl)
        rt = round(t, 1)
        if not any(abs(rt - hs) < 2 and alias == hp for hs, hp in heal_keys):
            heals_export.append({"s": rt, "player": alias, "item": iname, "hp": 0})
    heals_export.sort(key=lambda x: x["s"])

    # ── build players + teams ────────────────────────────────────
    players_export = {}
    for nl in player_team:
        players_export[anon(nl)] = {"team": anon_team(player_team[nl])}

    teams_export = {}
    for tid, members in team_members.items():
        ta = anon_team(tid)
        teams_export[ta] = {
            "rank": roster_rank.get(tid, 0),
            "members": sorted(anon(nl) for nl in members),
        }

    # ── anonymize position keys ──────────────────────────────────
    pos_export = {}
    for nl, pts in positions.items():
        pos_export[anon(nl)] = pts

    # ── finalise my inventory ────────────────────────────────────
    my_inventory.sort(key=lambda x: x["s"])
    my_inventory = [e for e in my_inventory if e.get("item")]

    # ── my stats ─────────────────────────────────────────────────
    my_stats = None
    for p in md["participants"]:
        if p["name"].lower() == pname:
            my_stats = p
            break

    my_team_alias = anon_team(my_team_id) if my_team_id is not None else None
    my_team_rank = roster_rank.get(my_team_id, 0) if my_team_id is not None else 0

    # ── assemble ─────────────────────────────────────────────────
    export = {
        "meta": {
            "map": meta["label"],
            "map_id": map_name,
            "game_mode": md["game_mode"],
            "duration_s": md["duration"],
            "my_placement": my_team_rank,
            "my_team": my_team_alias,
            "coordinate_unit": "cm",
        },
        "players": players_export,
        "teams": teams_export,
        "positions": pos_export,
        "hits": all_hits,
        "knocks": all_knocks,
        "kills": all_kills,
        "vehicles": vehicle_events,
        "heals": heals_export,
        "my_inventory": my_inventory,
        "circles": circles,
        "my_shots": my_shots,
    }

    # ── write ────────────────────────────────────────────────────
    EXPORT_DIR.mkdir(exist_ok=True)
    out = Path(args.output) if args.output else EXPORT_DIR / "last_match_v3.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(export, f, ensure_ascii=False, indent=1)

    sz = out.stat().st_size
    print(f"\nExported: {out}")
    print(f"  Size: {sz / 1024 / 1024:.2f} MB ({sz / 1024:.0f} KB)")

    if sz > 20 * 1024 * 1024:
        gz = EXPORT_DIR / "last_match_v3.json.gz"
        with open(out, "rb") as fi:
            with gzip.open(gz, "wb") as fo:
                fo.write(fi.read())
        print(f"  Gzipped: {gz} ({gz.stat().st_size / 1024 / 1024:.1f} MB)")

    # ── verify anonymization ─────────────────────────────────────
    print("\nVerifying anonymization…")
    raw_json = json.dumps(export).lower()
    leaked = []
    if pname in raw_json:
        leaked.append(player_name)
    for rn in my_team_names:
        if rn in raw_json:
            leaked.append(name_case.get(rn, rn))
    api_key = os.getenv("PUBG_API_KEY", "")
    if api_key and api_key[:12].lower() in raw_json:
        leaked.append("API_KEY")
    if "account." in raw_json:
        leaked.append("accountId")

    if leaked:
        print(f"  *** WARNING: leaked: {leaked} ***")
    else:
        print(f"  OK — 0 matches for my nick, teammates, API key, accountId")

    # ── stats ────────────────────────────────────────────────────
    total_pos = sum(len(v) for v in pos_export.values())
    total_ev = (len(all_hits) + len(all_knocks) + len(all_kills)
                + len(vehicle_events) + len(heals_export)
                + len(my_inventory) + len(my_shots))
    print(f"\n  Players:    {len(pos_export)}")
    print(f"  Positions:  {total_pos}")
    print(f"  Events:     {total_ev}")
    print(f"    hits={len(all_hits)}  knocks={len(all_knocks)}  kills={len(all_kills)}")
    print(f"    vehicles={len(vehicle_events)}  heals={len(heals_export)}")
    print(f"    my_inventory={len(my_inventory)}  my_shots={len(my_shots)}")
    print(f"  Circles:    {len(circles)}")
    print(f"  Teams:      {len(teams_export)}")


if __name__ == "__main__":
    main()
