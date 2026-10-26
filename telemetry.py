"""Parse PUBG telemetry events into structured data for replay and analysis."""

from __future__ import annotations
import math
from dataclasses import dataclass, field


WEAPON_NAMES = {
    "WeapAK47_C":            "AKM",
    "WeapM416_C":            "M416",
    "WeapSCAR-L_C":          "SCAR-L",
    "WeapGroza_C":           "Groza",
    "WeapAUG_C":             "AUG",
    "WeapBerylM762_C":       "Beryl M762",
    "WeapMk47Mutant_C":      "Mk47 Mutant",
    "WeapG36C_C":            "G36C",
    "WeapQBZ95_C":           "QBZ95",
    "WeapACE32_C":           "ACE32",
    "WeapK2_C":              "K2",
    "WeapM16A4_C":           "M16A4",
    "WeapMini14_C":          "Mini 14",
    "WeapSKS_C":             "SKS",
    "WeapSLR_C":             "SLR",
    "WeapMk14_C":            "Mk14",
    "WeapQBU88_C":           "QBU",
    "WeapVSS_C":             "VSS",
    "WeapKar98k_C":          "Kar98k",
    "WeapM24_C":             "M24",
    "WeapAWM_C":             "AWM",
    "WeapWin94_C":           "Win94",
    "WeapMosinNagant_C":     "Mosin",
    "WeapLynx_C":            "Lynx AMR",
    "WeapDP28_C":            "DP-28",
    "WeapM249_C":            "M249",
    "WeapMG3_C":             "MG3",
    "WeapUMP_C":             "UMP45",
    "WeapVector_C":          "Vector",
    "WeapThompson_C":        "Tommy Gun",
    "WeapBizonPP19_C":       "Bizon",
    "WeapMP5K_C":            "MP5K",
    "WeapUZI_C":             "Micro UZI",
    "WeapP90_C":             "P90",
    "WeapJS9_C":             "JS9",
    "WeapSaiga12_C":         "S12K",
    "WeapBeretta686_C":      "S686",
    "WeapWinchester_C":      "S1897",
    "WeapDP12_C":            "DBS",
    "WeapDesertEagle_C":     "Deagle",
    "WeapM1911_C":           "P1911",
    "WeapM9_C":              "P92",
    "WeapNagantR1895_C":     "R1895",
    "WeapRhino_C":           "R45",
    "WeapSawnoff_C":         "Sawed-Off",
    "WeapScorpion_C":        "Skorpion",
    "WeapP18C_C":            "P18C",
    "WeapCrossbow_1_C":      "Crossbow",
    "WeapPan_C":             "Pan",
    "WeapMachete_C":         "Machete",
    "WeapCowbar_C":          "Crowbar",
    "WeapSickle_C":          "Sickle",
    "ProjGrenade_C":         "Frag grenade",
    "ProjMolotov_C":         "Molotov",
    "ProjC4_C":              "C4",
    "ProjStickyGrenade_C":   "Sticky bomb",
    "RedZoneBomb_C":         "Red zone",
    "Bluezone":              "Blue zone",
    "BattleRoyaleFlareGun_C":"Flare gun",
    "WeapMortar_C":          "Mortar",
}


def _weapon(raw: str) -> str:
    if not raw:
        return "unknown"
    return WEAPON_NAMES.get(raw, raw.replace("Weap", "").replace("_C", ""))


def _loc(obj: dict | None) -> tuple[float, float] | None:
    if not obj:
        return None
    loc = obj.get("location")
    if not loc:
        return None
    return (loc.get("x", 0.0), loc.get("y", 0.0))


def _elapsed(event: dict) -> float:
    return event.get("elapsedTime", 0.0)


# ------------------------------------------------------------------

@dataclass
class MatchTelemetry:
    player_name: str
    positions: list = field(default_factory=list)       # (t, x, y)
    circles: list = field(default_factory=list)          # (t, sx, sy, sr, bx, by, br)
    damage_taken: list = field(default_factory=list)     # (t, attacker, weapon, dmg, ax, ay, vx, vy)
    damage_dealt: list = field(default_factory=list)     # (t, victim, weapon, dmg)
    kills_by_me: list = field(default_factory=list)      # (t, victim, weapon)
    death: dict | None = None                            # {t, killer, weapon, distance, killer_pos, my_pos}
    landing: tuple | None = None                         # (x, y, t)
    enemy_positions: list = field(default_factory=list)  # (t, name, x, y)
    alive_counts: list = field(default_factory=list)     # (t, count)
    match_elapsed: float = 0.0
    zone_damage_taken: float = 0.0
    total_heals: int = 0
    total_boosts: int = 0
    heal_items: set = field(default_factory=set)         # itemIds of heals used
    heal_log: list = field(default_factory=list)          # (t, itemId)
    boost_times: list = field(default_factory=list)       # t of every boost used
    items_picked: list = field(default_factory=list)


def parse_telemetry(events: list, player_name: str) -> MatchTelemetry:
    """Walk through raw telemetry events and build a MatchTelemetry."""
    mt = MatchTelemetry(player_name=player_name)
    pname_lower = player_name.lower()

    # First pass: collect my positions and death time so we know the
    # "last 60 seconds" window for enemy display.
    death_time: float | None = None
    my_positions_raw: list[tuple[float, float, float]] = []

    for ev in events:
        etype = ev.get("_T", "")

        if etype == "LogPlayerPosition":
            char = ev.get("character", {})
            if char.get("name", "").lower() == pname_lower:
                t = _elapsed(ev)
                loc = _loc(char)
                if loc:
                    my_positions_raw.append((t, loc[0], loc[1]))

        elif etype in ("LogPlayerKillV2", "LogPlayerKill"):
            victim = ev.get("victim", {})
            if victim.get("name", "").lower() == pname_lower:
                death_time = _elapsed(ev)

    if my_positions_raw:
        # Sort by time just in case
        my_positions_raw.sort()
        mt.positions = my_positions_raw

    end_time = death_time if death_time else (my_positions_raw[-1][0] if my_positions_raw else 0)
    enemy_window_start = max(0, end_time - 60)

    # Second pass: everything else
    for ev in events:
        etype = ev.get("_T", "")

        if etype == "LogGameStatePeriodic":
            gs = ev.get("gameState", {})
            t = gs.get("elapsedTime", 0)
            sz = gs.get("safetyZonePosition", {})
            sr = gs.get("safetyZoneRadius", 0)
            bz = gs.get("poisonGasWarningPosition", {})
            br = gs.get("poisonGasWarningRadius", 0)
            mt.circles.append((
                t,
                sz.get("x", 0), sz.get("y", 0), sr,
                bz.get("x", 0), bz.get("y", 0), br,
            ))
            mt.alive_counts.append((t, gs.get("numAlivePlayers", 0)))

        elif etype == "LogPlayerTakeDamage":
            victim = ev.get("victim") or {}
            attacker = ev.get("attacker") or {}
            if victim.get("name", "").lower() == pname_lower:
                t = _elapsed(ev)
                aloc = _loc(attacker)
                vloc = _loc(victim)
                weapon = _weapon(ev.get("damageCauserName", ""))
                dmg = ev.get("damage", 0)
                cause = ev.get("damageTypeCategory", "")
                if "BlueZone" in cause or "Bluezone" in cause:
                    mt.zone_damage_taken += dmg
                mt.damage_taken.append((
                    t,
                    attacker.get("name", ""),
                    weapon,
                    dmg,
                    aloc[0] if aloc else 0, aloc[1] if aloc else 0,
                    vloc[0] if vloc else 0, vloc[1] if vloc else 0,
                ))

        elif etype == "LogPlayerTakeDamage":
            pass  # handled above

        elif etype in ("LogPlayerKillV2", "LogPlayerKill"):
            victim = ev.get("victim") or {}
            killer = ev.get("killer") or ev.get("attacker") or {}
            t = _elapsed(ev)

            # LogPlayerKillV2 stores weapon in nested damage info
            kill_dmg = ev.get("killerDamageInfo") or ev.get("finishDamageInfo") or {}
            weapon_raw = (
                kill_dmg.get("damageCauserName")
                or ev.get("damageCauserName", "")
            )
            dist_raw = kill_dmg.get("distance", ev.get("distance", 0))

            if victim.get("name", "").lower() == pname_lower:
                kloc = _loc(killer) or _loc(ev.get("finisher"))
                vloc = _loc(victim)
                dist = dist_raw / 100 if dist_raw > 1000 else dist_raw
                if dist == 0 and kloc and vloc:
                    dist = math.sqrt((kloc[0] - vloc[0])**2 + (kloc[1] - vloc[1])**2) / 100
                mt.death = {
                    "t": t,
                    "killer": killer.get("name") or (ev.get("finisher") or {}).get("name", ""),
                    "weapon": _weapon(weapon_raw),
                    "distance": dist,
                    "killer_pos": kloc,
                    "my_pos": vloc,
                }
            elif killer.get("name", "").lower() == pname_lower:
                mt.kills_by_me.append((
                    t,
                    victim.get("name", ""),
                    _weapon(weapon_raw),
                ))

        elif etype == "LogParachuteLanding":
            char = ev.get("character", {})
            if char.get("name", "").lower() == pname_lower:
                loc = _loc(char)
                if loc:
                    mt.landing = (loc[0], loc[1], _elapsed(ev))

        elif etype == "LogPlayerPosition":
            char = ev.get("character", {})
            name = char.get("name", "")
            if name.lower() != pname_lower:
                t = _elapsed(ev)
                if enemy_window_start <= t <= end_time:
                    loc = _loc(char)
                    if loc:
                        mt.enemy_positions.append((t, name, loc[0], loc[1]))

        elif etype == "LogHeal":
            char = ev.get("character", {})
            if char.get("name", "").lower() == pname_lower:
                mt.total_heals += 1
                iid = (ev.get("item") or {}).get("itemId", "")
                if iid:
                    mt.heal_items.add(iid)
                    mt.heal_log.append((_elapsed(ev), iid))

        elif etype == "LogItemUse":
            char = ev.get("character", {})
            item = ev.get("item", {})
            if char.get("name", "").lower() == pname_lower:
                cat = item.get("category", "")
                if cat == "Boost":
                    mt.total_boosts += 1
                    mt.boost_times.append(_elapsed(ev))

    mt.circles.sort()
    mt.alive_counts.sort()
    mt.enemy_positions.sort()
    mt.match_elapsed = end_time
    return mt
