"""PUBG map metadata, image caching, and coordinate helpers."""

import os
from pathlib import Path

import numpy as np
import requests
from PIL import Image

BASE_DIR = Path(__file__).parent

# telemetry map id -> name, size in cm, file name in pubg/api-assets/Assets/Maps
MAP_META = {
    "Baltic_Main":     {"name": "Erangel",     "size": 816000, "label": "Erangel",     "asset": "Erangel_Main"},
    "Erangel_Main":    {"name": "Erangel",     "size": 816000, "label": "Erangel",     "asset": "Erangel_Main"},
    "Desert_Main":     {"name": "Miramar",     "size": 816000, "label": "Miramar",     "asset": "Miramar_Main"},
    "Savage_Main":     {"name": "Sanhok",      "size": 408000, "label": "Sanhok",      "asset": "Sanhok_Main"},
    "DihorOtok_Main":  {"name": "Vikendi",     "size": 816000, "label": "Vikendi",     "asset": "Vikendi_Main"},
    "Tiger_Main":      {"name": "Taego",       "size": 816000, "label": "Taego",       "asset": "Taego_Main"},
    "Chimera_Main":    {"name": "Paramo",      "size": 306000, "label": "Paramo",      "asset": "Paramo_Main"},
    "Kiki_Main":       {"name": "Deston",      "size": 816000, "label": "Deston",      "asset": "Deston_Main"},
    "Neon_Main":       {"name": "Rondo",       "size": 816000, "label": "Rondo",       "asset": "Rondo_Main"},
    "Summerland_Main": {"name": "Karakin",     "size": 204000, "label": "Karakin",     "asset": "Karakin_Main"},
    "Heaven_Main":     {"name": "Haven",       "size": 102000, "label": "Haven",       "asset": "Haven_Main"},
    "Range_Main":      {"name": "Camp Jackal", "size": 204000, "label": "Camp Jackal", "asset": "Camp_Jackal_Main"},
}

_IMAGE_BASE = (
    "https://raw.githubusercontent.com/pubg/api-assets/master/Assets/Maps"
)
MAP_IMAGE_URLS = {k: f"{_IMAGE_BASE}/{v['asset']}_Low_Res.png" for k, v in MAP_META.items()}

# Named places are not listed: positions are described by compass area instead,
# which is always correct for any map version.
NAMED_LOCATIONS: dict = {}

RENDER_SIZE = 512


def get_map_meta(map_name: str) -> dict:
    return MAP_META.get(map_name, {"name": map_name, "size": 816000, "label": map_name})


def game_to_pixel(x: float, y: float, map_size: float, img_size: int = RENDER_SIZE):
    px = x / map_size * img_size
    py = y / map_size * img_size
    return px, py


def pixel_radius(r: float, map_size: float, img_size: int = RENDER_SIZE):
    return r / map_size * img_size


def nearest_location(map_name: str, x: float, y: float) -> str:
    locations = NAMED_LOCATIONS.get(map_name, [])
    meta = get_map_meta(map_name)
    map_size = meta["size"]

    best_name, best_dist = None, float("inf")
    for name, lx, ly in locations:
        d = ((x - lx) ** 2 + (y - ly) ** 2) ** 0.5
        if d < best_dist:
            best_dist = d
            best_name = name

    if best_name and best_dist < map_size * 0.08:
        return best_name

    col = int(x / map_size * 3)
    row = int(y / map_size * 3)
    col = min(col, 2)
    row = min(row, 2)
    grid = [
        ["north-west", "north", "north-east"],
        ["west", "centre", "east"],
        ["south-west", "south", "south-east"],
    ]
    return grid[row][col] + " of the map"


def load_map_image(map_name: str) -> Image.Image:
    cache_dir = BASE_DIR / "maps"
    cache_dir.mkdir(exist_ok=True)
    cache_path = cache_dir / f"{map_name}.png"

    if cache_path.exists():
        img = Image.open(cache_path).convert("RGB")
        return img.resize((RENDER_SIZE, RENDER_SIZE), Image.LANCZOS)

    url = MAP_IMAGE_URLS.get(map_name)
    if url:
        try:
            print(f"  Downloading map {map_name}...")
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            cache_path.write_bytes(resp.content)
            img = Image.open(cache_path).convert("RGB")
            return img.resize((RENDER_SIZE, RENDER_SIZE), Image.LANCZOS)
        except Exception as exc:
            print(f"  Could not download map: {exc}")

    img = Image.new("RGB", (RENDER_SIZE, RENDER_SIZE), (25, 30, 35))
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    step = RENDER_SIZE // 8
    for i in range(1, 8):
        c = i * step
        draw.line([(c, 0), (c, RENDER_SIZE)], fill=(50, 55, 60), width=1)
        draw.line([(0, c), (RENDER_SIZE, c)], fill=(50, 55, 60), width=1)
    return img
