#!/usr/bin/env python3
"""Pro-style breakdown of one match from an export_v3.py file.

    python breakdown.py export/last_match_v3.json

Writes breakdown/report.md and four images:
    endgame.png  the last minute on real terrain: who could see you, from how high
    stops.png    every stop of 20 s or more and how many teams had a line on you
    timing.png   when you and the top 3 teams got into each new circle
    hp.png       your HP, heals and boosts over the match
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import requests
from PIL import Image

import terrain as T

Image.MAX_IMAGE_PIXELS = None
BASE = Path(__file__).parent
from maps import MAP_META
COL = {"me": "#ffaa22", "killer": "#ff3b3b", "near": "#ff6bf0", "top": ["#3dff86", "#38d6ff", "#c78bff"]}


def hires_map(map_id: str) -> Image.Image:
    name = MAP_META.get(map_id, {}).get("asset", map_id)
    path = BASE / "maps" / f"{name}_High_Res.png"
    path.parent.mkdir(exist_ok=True)
    if not path.exists():
        url = f"https://media.githubusercontent.com/media/pubg/api-assets/master/Assets/Maps/{name}_High_Res.png"
        print(f"downloading {name} high-res map (large file, once)...")
        r = requests.get(url, timeout=300)
        r.raise_for_status()
        path.write_bytes(r.content)
    return Image.open(path).convert("RGB")


class Match:
    def __init__(self, d: dict):
        self.d = d
        self.P = {k: sorted(v, key=lambda p: p["s"]) for k, v in d["positions"].items()}
        self.team = {k: v["team"] for k, v in d["players"].items()}
        self.dead = {k["victim"]: k["s"] for k in d["kills"]}
        self.my_team = d["meta"]["my_team"]
        self.map_m = MAP_META.get(d["meta"]["map_id"], {}).get("size", 816000) / 100

    def at(self, pid, s, maxgap=12):
        ps = [p for p in self.P.get(pid, []) if p["s"] <= s and s - p["s"] <= maxgap]
        return ps[-1] if ps else None

    def interp(self, pid, s):
        ps = self.P.get(pid, [])
        if not ps or s < ps[0]["s"] or s > ps[-1]["s"] + 10 or self.dead.get(pid, 1e9) < s - 0.5:
            return None
        tt = [p["s"] for p in ps]
        i = int(np.searchsorted(tt, s))
        if i == 0 or i >= len(ps):
            p = ps[min(i, len(ps) - 1)]
            return (p["x"] / 100, p["y"] / 100, p["z"] / 100)
        a, b = ps[i - 1], ps[i]
        f = (s - a["s"]) / max(1e-6, b["s"] - a["s"])
        return tuple((a[c] + (b[c] - a[c]) * f) / 100 for c in ("x", "y", "z"))

    def enemies(self):
        return [p for p in self.P if self.team.get(p) != self.my_team]

    def circle_at(self, s, key="white"):
        c = None
        for x in sorted(self.d["circles"], key=lambda c: c["s"]):
            if x["s"] <= s:
                c = x[key]
        return c

    def phases(self):
        out, last = [], None
        for c in sorted(self.d["circles"], key=lambda c: c["s"]):
            w = c["white"]
            key = (round(w["cx"]), round(w["cy"]), round(w["r"]))
            if w["r"] > 0 and key != last:
                out.append((c["s"], w)); last = key
        return out

    def first_in(self, pid, w, s_from):
        for p in self.P.get(pid, []):
            if p["s"] >= s_from and np.hypot(p["x"] - w["cx"], p["y"] - w["cy"]) <= w["r"]:
                return p["s"]
        return None


def mmss(s):
    s = int(round(s)); return f"{s // 60}:{s % 60:02d}"


# ------------------------------------------------------------------ analysis
def analyse(m: Match):
    d = m.d
    death = next((k for k in d["kills"] if k["victim"] == "me"), None)
    if not death:
        raise SystemExit("no death in this match (did you win?)")
    ds = death["s"]
    kp, vp = death["killer_pos"], death["victim_pos"]
    DX, DY, DZ = vp["x"] / 100, vp["y"] / 100, vp["z"] / 100
    kteam = m.team[death["killer"]]
    teams = d["teams"]
    top = [t for t, _ in sorted(teams.items(), key=lambda kv: kv[1]["rank"]) if t != m.my_team][:3]

    samples = T.ground_samples(d)
    R = 650
    g = T.build_grid(samples, (DX - R, DX + R, DY - R, DY + R), 16)
    ok, tot = T.validate(g, d)
    E, Eh = T.exposure(g)
    xs, ys = g.centres()
    j, i = int((DY - g.y0) / g.cell), int((DX - g.x0) / g.cell)
    pct = float((Eh < Eh[j, i]).mean() * 100)

    # killer team: seconds with line of sight in the last minute
    kmembers = teams[kteam]["members"]
    seen = []
    for s in range(int(ds) - 60, int(ds) + 1):
        me = m.interp("me", s)
        if not me:
            seen.append(0); continue
        v = 0
        for mbr in kmembers:
            p = m.interp(mbr, s)
            if p and g.inside(*p[:2]) and np.hypot(p[0] - me[0], p[1] - me[1]) < 450 and T.line_of_sight(g, p, me):
                v = 1
        seen.append(v)
    first_seen = next((int(ds) - 60 + k for k, v in enumerate(seen) if v), None)
    # stationary before death
    still = 0
    for p in reversed([p for p in m.P["me"] if p["s"] <= ds]):
        if np.hypot(p["x"] / 100 - DX, p["y"] / 100 - DY) < 6:
            still = ds - p["s"]
        else:
            break
    # enemies with a line on me at death
    near = []
    for pid in m.enemies():
        p = m.interp(pid, ds - 2)
        if p and g.inside(*p[:2]) and np.hypot(p[0] - DX, p[1] - DY) < 450 and T.line_of_sight(g, p, (DX, DY, DZ)):
            near.append(pid)
    # safest spot: inside the current white circle, within 500 m, unseen by anyone near in the last 45 s
    w = m.circle_at(ds)
    obs = []
    for pid in m.enemies():
        for s in range(int(ds) - 45, int(ds) + 1, 3):
            p = m.interp(pid, s)
            if p and g.inside(*p[:2]) and np.hypot(p[0] - DX, p[1] - DY) < 450:
                obs.append((pid, p))
    def search(observers):
        best = None
        for jj in range(g.shape[0]):
            for ii in range(g.shape[1]):
                x, y, z = xs[ii], ys[jj], g.z[jj, ii]
                dm = np.hypot(x - DX, y - DY)
                if dm > 500 or (w and np.hypot(x * 100 - w["cx"], y * 100 - w["cy"]) > w["r"] - 3000):
                    continue
                if Eh[jj, ii] > np.percentile(Eh, 30):
                    continue
                n = sum(1 for o in observers if np.hypot(o[0] - x, o[1] - y) < 450 and T.line_of_sight(g, o, (x, y, z)))
                key = (n, dm)
                if n == 0 and (best is None or key < best[0]):
                    best = (key, (x, y, z))
        return best
    best = search([o for _, o in obs])
    best_k = search([o for pid, o in obs if m.team[pid] == kteam])
    out_of_circle = (np.hypot(DX * 100 - w["cx"], DY * 100 - w["cy"]) - w["r"]) / 100 if w else None
    return dict(death=death, ds=ds, DX=DX, DY=DY, DZ=DZ, kteam=kteam, krank=teams[kteam]["rank"], top=top,
                g=g, Eh=Eh, pct=pct, valid=(ok, tot), seen=seen, first_seen=first_seen, still=still,
                near=near, best=best, best_k=best_k, w=w, out=out_of_circle, rise=kp["z"] / 100 - DZ,
                dist=float(np.hypot(kp["x"] / 100 - DX, kp["y"] / 100 - DY)))


def stops(m: Match):
    me = [p for p in m.P["me"] if p["s"] >= 80]
    xs = [p["x"] / 100 for p in me if not p["in_vehicle"]]; ys = [p["y"] / 100 for p in me if not p["in_vehicle"]]
    g = T.build_grid(T.ground_samples(m.d), (min(xs) - 450, max(xs) + 450, min(ys) - 450, max(ys) + 450), 20)
    res, i = [], 0
    while i < len(me):
        j = i
        while j + 1 < len(me) and not me[j + 1]["in_vehicle"] and np.hypot(me[j + 1]["x"] - me[i]["x"], me[j + 1]["y"] - me[i]["y"]) / 100 < 30:
            j += 1
        if me[j]["s"] - me[i]["s"] >= 20 and not me[i]["in_vehicle"]:
            seen = {}
            for k in range(i, j + 1):
                M = (me[k]["x"] / 100, me[k]["y"] / 100, me[k]["z"] / 100); s = me[k]["s"]
                for pid in m.enemies():
                    if m.dead.get(pid, 1e9) <= s:
                        continue
                    q = m.at(pid, s)
                    if not q or q["in_vehicle"]:
                        continue
                    Q = (q["x"] / 100, q["y"] / 100, q["z"] / 100)
                    D = np.hypot(Q[0] - M[0], Q[1] - M[1])
                    if D < 400 and g.inside(*Q[:2]) and T.line_of_sight(g, Q, M):
                        seen.setdefault(m.team[pid], []).append(D)
            dmg = sum(h["damage"] for h in m.d["hits"] if h["victim"] == "me" and me[i]["s"] <= h["s"] <= me[j]["s"] + 10)
            res.append(dict(s0=me[i]["s"], s1=me[j]["s"], x=np.mean([p["x"] / 100 for p in me[i:j + 1]]),
                            y=np.mean([p["y"] / 100 for p in me[i:j + 1]]),
                            teams={t: round(min(v)) for t, v in seen.items()}, dmg=round(dmg)))
            i = j + 1
        else:
            i += 1
    return res


def timing(m: Match, top):
    rows = []
    for s, w in m.phases():
        rs = [(c["s"], c["blue"]["r"]) for c in sorted(m.d["circles"], key=lambda c: c["s"]) if c["s"] >= s]
        shrink = next((rs[k][0] for k in range(1, len(rs)) if rs[k][1] < rs[k - 1][1] - 1), None)
        me_in = m.first_in("me", w, s)
        if m.dead.get("me", 1e9) < s:
            break
        tops = {}
        for t in top:
            ins = [x for x in (m.first_in(p, w, s) for p in m.d["teams"][t]["members"]) if x is not None]
            tops[t] = min(ins) if ins else None
        rows.append(dict(reveal=s, shrink=shrink, me_in=me_in if (me_in or 1e9) < m.dead.get("me", 1e9) else None, tops=tops, r=w["r"] / 100))
    return rows


# ------------------------------------------------------------------ drawing
def crop_map(img, m, x0, x1, y0, y1, w=1200):
    k = img.width / m.map_m
    c = img.crop((int(x0 * k), int(y0 * k), int(x1 * k), int(y1 * k)))
    return np.asarray(c.resize((w, int(w * (y1 - y0) / (x1 - x0)))))


def draw_endgame(m, A, img, path):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle
    g, ds = A["g"], A["ds"]
    ny, nx = g.shape
    x0, x1, y0, y1 = g.x0, g.x0 + nx * g.cell, g.y0, g.y0 + ny * g.cell
    fig = plt.figure(figsize=(12, 12 * (y1 - y0) / (x1 - x0)), dpi=110); ax = fig.add_axes([0, 0, 1, 1])
    ax.imshow(crop_map(img, m, x0, x1, y0, y1), extent=(x0, x1, y1, y0))
    from scipy.ndimage import distance_transform_edt
    far = distance_transform_edt(~g.known) * g.cell > 120   # no real height sample within 120 m: do not guess
    ov = np.ma.masked_where((A["Eh"] / A["Eh"].max() < 0.25) | far, A["Eh"] / A["Eh"].max())
    ax.imshow(ov, extent=(x0, x1, y1, y0), cmap="Reds", alpha=.35, vmin=0, vmax=1, interpolation="bilinear")
    w = A["w"]
    if w:
        ax.add_patch(Circle((w["cx"] / 100, w["cy"] / 100), w["r"] / 100, fill=False, ec="w", lw=2, ls="--"))
    tr = [(p["x"] / 100, p["y"] / 100) for p in m.P["me"] if ds - 180 <= p["s"] <= ds]
    ax.plot(*zip(*tr), color=COL["me"], lw=3.5)
    for mbr in m.d["teams"][A["kteam"]]["members"]:
        t2 = [m.interp(mbr, s) for s in range(int(ds) - 60, int(ds) + 1)]
        t2 = [p for p in t2 if p]
        if t2:
            ax.plot([p[0] for p in t2], [p[1] for p in t2], color=COL["killer"], lw=2.2)
            ax.plot(t2[-1][0], t2[-1][1], "o", ms=8, mfc=COL["killer"], mec="k")
    for pid in A["near"]:
        if m.team[pid] != A["kteam"]:
            p = m.interp(pid, ds - 2)
            ax.plot(p[0], p[1], "o", ms=8, mfc=COL["near"], mec="k")
    kp = A["death"]["killer_pos"]
    ax.plot([kp["x"] / 100, A["DX"]], [kp["y"] / 100, A["DY"]], color="#ffee88", lw=2)
    ax.plot(A["DX"], A["DY"], "o", ms=14, mfc=COL["me"], mec="k", mew=2)
    box = dict(boxstyle="round,pad=.35", fc="black", ec="none", alpha=.78)
    ax.text(A["DX"] + 25, A["DY"] + 60, f"you, {mmss(ds)}\n{A['still']:.0f} s without moving", color=COL["me"], fontsize=12, fontweight="bold", bbox=box)
    ax.text(kp["x"] / 100 + 20, kp["y"] / 100 - 40, f"{A['krank']}st place" if A['krank'] == 1 else f"#{A['krank']} team", color=COL["killer"], fontsize=12, fontweight="bold", bbox=box)
    for key, lab, col in (("best_k", "hidden from the team that killed you", "#40ff80"), ("best", "hidden from every nearby enemy", "#38d6ff")):
        if A[key]:
            bx, by, _ = A[key][1]
            ax.add_patch(Circle((bx, by), 22, fill=False, ec=col, lw=4))
            ax.text(bx - 160, by + 70, f"{lab}, {np.hypot(bx - A['DX'], by - A['DY']):.0f} m", color=col, fontsize=11, fontweight="bold", bbox=box)
    ax.set_xlim(x0, x1); ax.set_ylim(y1, y0); ax.axis("off")
    fig.savefig(path); plt.close(fig)


def draw_stops(m, S, img, path):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle
    me = [p for p in m.P["me"] if p["s"] >= 80]
    xs = [p["x"] / 100 for p in me]; ys = [p["y"] / 100 for p in me]
    x0, x1, y0, y1 = min(xs) - 250, max(xs) + 250, min(ys) - 250, max(ys) + 250
    fig = plt.figure(figsize=(12, 12 * (y1 - y0) / (x1 - x0)), dpi=110); ax = fig.add_axes([0, 0, 1, 1])
    ax.imshow(crop_map(img, m, x0, x1, y0, y1, 1400), extent=(x0, x1, y1, y0), alpha=.9)
    for a, b in zip(me, me[1:]):
        ax.plot([a["x"] / 100, b["x"] / 100], [a["y"] / 100, b["y"] / 100], color="#7fd0ff" if b["in_vehicle"] else COL["me"],
                lw=3, ls="--" if b["in_vehicle"] else "-")
    lines = []
    for k, s in enumerate(S, 1):
        n = len(s["teams"])
        col = "#ff3b3b" if n >= 3 or s["dmg"] > 0 else ("#ffb000" if n == 2 else "#40ff80")
        ax.add_patch(Circle((s["x"], s["y"]), 14 + (s["s1"] - s["s0"]) * .6, fc=col, ec="k", lw=1.5, alpha=.85))
        ax.text(s["x"], s["y"], str(k), color="k", fontsize=10, fontweight="bold", ha="center", va="center")
        lines.append(f"{k:>2}  {mmss(s['s0']):>5}  {s['s1'] - s['s0']:>3.0f} s  {n} team{'s' if n != 1 else ' '}" + (f"  -{s['dmg']} HP" if s["dmg"] else ""))
    ax.text(0.012, 0.985, "stop  time   still  had a line on you\n" + "\n".join(lines), transform=ax.transAxes, va="top", ha="left",
            family="monospace", fontsize=10, color="w", bbox=dict(boxstyle="round,pad=.5", fc="black", ec="none", alpha=.8))
    ax.set_xlim(x0, x1); ax.set_ylim(y1, y0); ax.axis("off")
    fig.savefig(path); plt.close(fig)


def draw_timing(m, rows, top, path):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"axes.facecolor": "#111", "figure.facecolor": "#111", "text.color": "w", "xtick.color": "w",
                         "ytick.color": "w", "axes.edgecolor": "#555"})
    fig, ax = plt.subplots(figsize=(12, 1.4 + 1.1 * len(rows)), dpi=110)
    marks = ["o", "s", "D"]
    for k, r in enumerate(rows):
        y = len(rows) - k
        if r["shrink"]:
            ax.barh(y, r["shrink"] - r["reveal"], left=r["reveal"], color="#333", height=.5)
            ax.plot([r["shrink"]], [y], "|", ms=26, color="#5aa0ff", mew=3)
        ax.text(r["reveal"], y + .33, f"circle {k + 1}: shown {mmss(r['reveal'])}", fontsize=10)
        for n, t in enumerate(top):
            if r["tops"].get(t):
                ax.plot(r["tops"][t], y - .05, marks[n], ms=9, color=COL["top"][n])
        if r["me_in"]:
            ax.plot(r["me_in"], y - .05, "*", ms=18, color=COL["me"], mec="k")
    ax.plot([], [], "*", color=COL["me"], ms=14, label="you")
    for n, t in enumerate(top):
        ax.plot([], [], marks[n], color=COL["top"][n], label=f"#{m.d['teams'][t]['rank']} team")
    ax.plot([], [], "|", color="#5aa0ff", ms=14, mew=3, label="zone starts moving")
    ax.legend(loc="upper center", bbox_to_anchor=(.5, -.12), ncol=5, facecolor="#222", edgecolor="#444", labelcolor="w", fontsize=9)
    ax.set_yticks([])
    xt = ax.get_xticks(); ax.set_xticks(xt); ax.set_xticklabels([mmss(x) for x in xt])
    ax.set_title("When each team got into the new circle", loc="left", color="w")
    plt.tight_layout(); fig.savefig(path); plt.close(fig)


def draw_hp(m, path):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    me = [p for p in m.P["me"] if p["s"] >= 60]
    fig, ax = plt.subplots(figsize=(12, 4.6), dpi=110)
    ax.plot([p["s"] for p in me], [p["hp"] for p in me], color=COL["me"], lw=2.5)
    ax.axhline(75, color="#aaa", ls=":", lw=1); ax.text(me[0]["s"], 71, "first aid stops at 75", color="#aaa", fontsize=9)
    for e in m.d["my_inventory"]:
        if e["action"] in ("use", "pickup") and any(k in e["item"] for k in ("first aid", "bandage", "energy", "pain", "med", "adren")):
            boost = any(k in e["item"] for k in ("energy", "pain", "adren"))
            hp = np.interp(e["s"], [p["s"] for p in me], [p["hp"] for p in me])
            if e["action"] == "use":
                ax.plot(e["s"], hp, "o", color="#40ff80" if boost else "w", ms=7)
                ax.text(e["s"], hp - 12, e["item"], fontsize=8, ha="center", color="#40ff80" if boost else "w")
            elif boost:
                ax.plot(e["s"], 106, "v", color="#40ff80", ms=7)
    ax.text(me[0]["s"], 110, "green triangle = boost picked up", color="#40ff80", fontsize=9)
    ax.set_ylim(-5, 118)
    xt = ax.get_xticks(); ax.set_xticks(xt); ax.set_xticklabels([mmss(x) for x in xt])
    ax.set_title("Your HP, heals and boosts", loc="left", color="w")
    plt.tight_layout(); fig.savefig(path); plt.close(fig)


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "breakdown"); out.mkdir(exist_ok=True)
    m = Match(T.load_export(sys.argv[1]))
    img = hires_map(m.d["meta"]["map_id"])
    print("analysing the last minute...")
    A = analyse(m)
    print("checking every stop...")
    S = stops(m)
    rows = timing(m, A["top"])
    draw_endgame(m, A, img, out / "endgame.png")
    draw_stops(m, S, img, out / "stops.png")
    draw_timing(m, rows, A["top"], out / "timing.png")
    draw_hp(m, out / "hp.png")
    k = A["death"]
    mapname = MAP_META.get(m.d["meta"]["map_id"], {}).get("name", m.d["meta"]["map_id"])
    L = [f"# Match breakdown: {mapname} · place {m.d['meta']['my_placement']}", "",
         f"Terrain grid checked against {A['valid'][1]} real hits over 40 m: {A['valid'][0]} agree.", "",
         "## The death", "",
         f"- {mmss(A['ds'])}: {k['weapon']} from {A['dist']:.0f} m, shooter {A['rise']:+.0f} m above you, team placed #{A['krank']}.",
         f"- Their team had a terrain line on you from {mmss(A['first_seen']) if A['first_seen'] else 'n/a'}.",
         f"- You were still for {A['still']:.0f} s before the shot.",
         f"- Exposure to higher ground at your spot: worse than {A['pct']:.0f}% of the area.",
         f"- Enemies with a terrain line on you at the end: {len(A['near'])}.",
         f"- Distance outside the current circle: {A['out']:.0f} m." if A["out"] and A["out"] > 0 else "- You were inside the current circle.",
         (f"- Nearest low spot inside the circle the team that killed you could not see: {np.hypot(A['best_k'][1][0] - A['DX'], A['best_k'][1][1] - A['DY']):.0f} m away." if A["best_k"] else ""),
         (f"- Nearest low spot inside the circle no nearby enemy could see: {np.hypot(A['best'][1][0] - A['DX'], A['best'][1][1] - A['DY']):.0f} m away." if A["best"] else "- No spot within 500 m was hidden from every nearby enemy."),
         "", "## Stops of 20 s or more", ""]
    for s in S:
        n = len(s['teams'])
        L.append(f"- {mmss(s['s0'])}, {s['s1'] - s['s0']:.0f} s: {n} team{'s' if n != 1 else ''} with a line" + (f", took {s['dmg']} damage" if s["dmg"] else ""))
    L += ["", "## Circles", ""]
    for n, r in enumerate(rows):
        tops = ", ".join(f"#{m.d['teams'][t]['rank']} in at {mmss(v)}" for t, v in r["tops"].items() if v)
        L.append(f"- Circle {n + 1} shown {mmss(r['reveal'])}: you in at {mmss(r['me_in']) if r['me_in'] else 'never'}; {tops}")
    L += ["", "Terrain only. Buildings, walls and trees are not in the grid."]
    (out / "report.md").write_text("\n".join(L), encoding="utf-8")
    print(f"done: {out}/")


if __name__ == "__main__":
    main()
