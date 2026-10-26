"""Terrain from telemetry.

PUBG telemetry stores x, y and z for every player position, hit and kill.
Ninety-nine players walking a map for twenty minutes leave thousands of
height samples. This module turns them into a height grid, then answers
three questions a coach asks:

  - who could see this spot (line of sight over the terrain)
  - how exposed is this spot to higher ground around it
  - where was the nearest spot that nobody could see

Buildings, walls and trees are not in the grid. Results are terrain only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np

CM = 100.0


def load_export(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def ground_samples(d: dict) -> np.ndarray:
    """(x, y, z) in metres from on-foot positions and combat events."""
    pts = []
    for ps in d["positions"].values():
        for p in ps:
            # skip vehicles and the plane / parachute phase
            if p.get("in_vehicle") or p["s"] < 120 or p["z"] > 22000:
                continue
            pts.append((p["x"] / CM, p["y"] / CM, p["z"] / CM))
    for ev in d.get("hits", []) + d.get("knocks", []) + d.get("kills", []):
        for k in ("attacker_pos", "killer_pos", "victim_pos"):
            q = ev.get(k)
            if q and q["z"] <= 22000:
                pts.append((q["x"] / CM, q["y"] / CM, q["z"] / CM))
    return np.array(pts, dtype=float)


@dataclass
class Grid:
    z: np.ndarray          # character height surface, metres
    known: np.ndarray      # cells that hold a real sample
    x0: float
    y0: float
    cell: float

    @property
    def shape(self):
        return self.z.shape

    def inside(self, x, y) -> bool:
        ny, nx = self.z.shape
        return self.x0 <= x < self.x0 + nx * self.cell and self.y0 <= y < self.y0 + ny * self.cell

    def height(self, x, y):
        ny, nx = self.z.shape
        fx = np.clip((np.asarray(x) - self.x0) / self.cell - 0.5, 0, nx - 1.001)
        fy = np.clip((np.asarray(y) - self.y0) / self.cell - 0.5, 0, ny - 1.001)
        i = fx.astype(int); j = fy.astype(int); ax = fx - i; ay = fy - j
        Z = self.z
        return (Z[j, i] * (1 - ax) * (1 - ay) + Z[j, i + 1] * ax * (1 - ay)
                + Z[j + 1, i] * (1 - ax) * ay + Z[j + 1, i + 1] * ax * ay)

    def centres(self):
        ny, nx = self.z.shape
        return (self.x0 + self.cell * (np.arange(nx) + 0.5),
                self.y0 + self.cell * (np.arange(ny) + 0.5))


def build_grid(samples: np.ndarray, bbox, cell: float = 16.0) -> Grid:
    """Lowest sample per cell, roofs and parachutes filtered, gaps interpolated."""
    from scipy.interpolate import griddata
    from scipy.ndimage import median_filter

    x0, x1, y0, y1 = bbox
    nx, ny = int(np.ceil((x1 - x0) / cell)), int(np.ceil((y1 - y0) / cell))
    Z = np.full((ny, nx), np.nan)
    m = (samples[:, 0] >= x0) & (samples[:, 0] < x1) & (samples[:, 1] >= y0) & (samples[:, 1] < y1)
    P = samples[m]
    ix = np.minimum(((P[:, 0] - x0) / cell).astype(int), nx - 1)
    iy = np.minimum(((P[:, 1] - y0) / cell).astype(int), ny - 1)
    for a, b, z in zip(iy, ix, P[:, 2]):
        if np.isnan(Z[a, b]) or z < Z[a, b]:
            Z[a, b] = z
    G = np.mgrid[0:ny, 0:nx]

    def fill(Zc, known, method):
        yy, xx = np.nonzero(known)
        return griddata((xx, yy), Zc[known], (G[1], G[0]), method=method)

    known = ~np.isnan(Z)
    if known.sum() < 4:
        raise ValueError("not enough height samples in this area")
    for _ in range(3):  # cells far above their neighbours are roofs or parachutes
        med = median_filter(fill(Z, known, "nearest"), size=7)
        bad = known & (Z - med > 12)
        if not bad.any():
            break
        Z[bad] = np.nan
        known = ~np.isnan(Z)
    lin = fill(Z, known, "linear")
    near = fill(Z, known, "nearest")
    return Grid(np.where(np.isnan(lin), near, lin), known, x0, y0, cell)


def line_of_sight(g: Grid, a, b, margin: float = 1.5, skip: float = 8.0) -> bool:
    """True if terrain does not block the line between two character positions."""
    a = np.asarray(a, float); b = np.asarray(b, float)
    L = float(np.hypot(*(b[:2] - a[:2])))
    if L < 2 * skip:
        return True
    t = np.linspace(skip / L, 1 - skip / L, int(L / 4) + 2)
    P = a[None, :] + (b - a)[None, :] * t[:, None]
    return not bool((g.height(P[:, 0], P[:, 1]) - P[:, 2] > margin).any())


def exposure(g: Grid, radius: float = 300.0, higher: float = 5.0):
    """For every cell: how many cells within `radius` see it, and how many of
    those are more than `higher` metres above it."""
    xs, ys = g.centres()
    GX, GY = np.meshgrid(xs, ys)
    ny, nx = g.shape
    r = int(radius / g.cell)
    E = np.zeros_like(g.z); Eh = np.zeros_like(g.z)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            L = np.hypot(dx, dy) * g.cell
            if L == 0 or L > radius:
                continue
            ox = GX + dx * g.cell; oy = GY + dy * g.cell
            valid = (ox > g.x0) & (ox < g.x0 + nx * g.cell) & (oy > g.y0) & (oy < g.y0 + ny * g.cell)
            oz = g.height(ox, oy)
            vis = np.ones_like(g.z, bool)
            n = max(3, int(L / 6))
            for t in np.linspace(8 / L if L > 16 else .5, 1 - 8 / L if L > 16 else .5, n):
                px = GX + (ox - GX) * t; py = GY + (oy - GY) * t; pz = g.z + (oz - g.z) * t
                vis &= ~(g.height(px, py) - pz > 1.5)
            vis &= valid
            E += vis
            Eh += vis & (oz - g.z > higher)
    return E, Eh


def validate(g: Grid, d: dict, min_dist: float = 40.0):
    """Every real hit is a proven line of sight. Count how many the grid agrees with."""
    ok = total = 0
    for h in d["hits"]:
        if "zone" in h["weapon"].lower():
            continue
        a, v = h["attacker_pos"], h["victim_pos"]
        A = (a["x"] / CM, a["y"] / CM, a["z"] / CM); V = (v["x"] / CM, v["y"] / CM, v["z"] / CM)
        if not (g.inside(*A[:2]) and g.inside(*V[:2])) or np.hypot(A[0] - V[0], A[1] - V[1]) < min_dist:
            continue
        total += 1
        ok += line_of_sight(g, A, V)
    return ok, total
