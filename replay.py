"""Render a 2-D animated replay of a PUBG match as GIF."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from maps import (
    get_map_meta, load_map_image, game_to_pixel, pixel_radius, RENDER_SIZE,
)
from telemetry import MatchTelemetry

OUTPUT_DIR = Path(__file__).parent / "output"
FRAME_INTERVAL_S = 8        # one frame per N game-seconds
GIF_FPS = 8
TRAIL_COLOR = (0, 220, 255)
ENEMY_COLOR = (255, 60, 60)
DAMAGE_COLOR = (255, 200, 0)
DEATH_COLOR = (255, 0, 0)
SAFE_ZONE_COLOR = (255, 255, 255)
BLUE_ZONE_COLOR = (50, 120, 255)
KILL_COLOR = (0, 255, 100)
CLOCK_BG = (0, 0, 0, 160)


def _try_font(size: int):
    for path in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _draw_circle_outline(draw: ImageDraw.Draw, cx, cy, r, color, width=2):
    if r < 1:
        return
    bbox = [cx - r, cy - r, cx + r, cy + r]
    draw.ellipse(bbox, outline=color, width=width)


def _lerp_circle(circles, t):
    """Interpolate circle state at time t."""
    if not circles:
        return None
    # Find the two bracketing entries
    prev = circles[0]
    for c in circles:
        if c[0] > t:
            break
        prev = c
    return prev  # (t, sx, sy, sr, bx, by, br)


def _player_pos_at(positions, t):
    """Interpolate player position at time t."""
    if not positions:
        return None
    prev = positions[0]
    for p in positions:
        if p[0] > t:
            break
        prev = p
    return (prev[1], prev[2])


def _format_time(seconds: float) -> str:
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m:02d}:{s:02d}"


def _alive_at(alive_counts, t):
    if not alive_counts:
        return "?"
    prev = alive_counts[0]
    for ac in alive_counts:
        if ac[0] > t:
            break
        prev = ac
    return str(prev[1])


def render_replay(
    telem: MatchTelemetry,
    match_data: dict,
    player_name: str,
) -> str:
    """Create an animated GIF replay. Returns the output path."""
    OUTPUT_DIR.mkdir(exist_ok=True)

    map_name = match_data["map"]
    meta = get_map_meta(map_name)
    map_size = meta["size"]
    base_img = load_map_image(map_name)

    font_big = _try_font(22)
    font_sm = _try_font(16)

    # Build time range
    end_time = telem.match_elapsed
    if end_time <= 0:
        end_time = match_data.get("duration", 600)
    times = np.arange(0, end_time + FRAME_INTERVAL_S, FRAME_INTERVAL_S)

    # Pre-compute pixel positions for my trail
    trail_px = [
        game_to_pixel(x, y, map_size) for (_, x, y) in telem.positions
    ]
    trail_times = [t for (t, _, _) in telem.positions]

    # Enemies in last 60s: group by name for display
    death_time = telem.death["t"] if telem.death else end_time
    enemy_window_start = max(0, death_time - 60)

    # Pre-compute damage / kill marker pixel positions
    dmg_markers = []
    for (t, attacker, weapon, dmg, ax, ay, vx, vy) in telem.damage_taken:
        vpx = game_to_pixel(vx, vy, map_size)
        apx = game_to_pixel(ax, ay, map_size) if ax and ay else None
        dmg_markers.append((t, vpx, apx, weapon, dmg))

    kill_markers = []
    for (t, victim, weapon) in telem.kills_by_me:
        # Find victim position from enemy_positions near that time
        # (approximate: use my position as fallback)
        pos = _player_pos_at(telem.positions, t)
        if pos:
            kill_markers.append((t, game_to_pixel(pos[0], pos[1], map_size)))

    death_marker = None
    if telem.death:
        d = telem.death
        if d["my_pos"]:
            death_marker = (d["t"], game_to_pixel(d["my_pos"][0], d["my_pos"][1], map_size))

    frames: list[Image.Image] = []
    print(f"  Rendering {len(times)} frames...")

    for frame_i, t in enumerate(times):
        frame = base_img.copy()
        draw = ImageDraw.Draw(frame, "RGBA")

        # --- Circles ---
        cs = _lerp_circle(telem.circles, t)
        if cs:
            _, sx, sy, sr, bx, by, br = cs
            # Blue zone (current safe boundary)
            scx, scy = game_to_pixel(sx, sy, map_size)
            spr = pixel_radius(sr, map_size)
            _draw_circle_outline(draw, scx, scy, spr, BLUE_ZONE_COLOR, width=3)
            # White zone (next zone)
            bcx, bcy = game_to_pixel(bx, by, map_size)
            bpr = pixel_radius(br, map_size)
            _draw_circle_outline(draw, bcx, bcy, bpr, SAFE_ZONE_COLOR, width=2)

        # --- My trail up to current time ---
        pts_so_far = []
        for i, pt in enumerate(trail_times):
            if pt > t:
                break
            pts_so_far.append(trail_px[i])
        if len(pts_so_far) >= 2:
            flat = [(int(p[0]), int(p[1])) for p in pts_so_far]
            draw.line(flat, fill=TRAIL_COLOR, width=2)

        # Current position
        cur_pos = _player_pos_at(telem.positions, t)
        if cur_pos:
            cpx, cpy = game_to_pixel(cur_pos[0], cur_pos[1], map_size)
            r = 5
            draw.ellipse([cpx - r, cpy - r, cpx + r, cpy + r], fill=TRAIL_COLOR)

        # --- Enemies near me (last 60s window) ---
        if t >= enemy_window_start:
            my_pos = _player_pos_at(telem.positions, t)
            if my_pos:
                mx, my_ = my_pos
                for (et, ename, ex, ey) in telem.enemy_positions:
                    if abs(et - t) > FRAME_INTERVAL_S * 2:
                        continue
                    dist = math.sqrt((ex - mx)**2 + (ey - my_)**2)
                    if dist < 50000:  # within ~500m
                        epx, epy = game_to_pixel(ex, ey, map_size)
                        r = 4
                        draw.ellipse(
                            [epx - r, epy - r, epx + r, epy + r],
                            fill=ENEMY_COLOR,
                        )

        # --- Damage markers (show for 10s after they happen) ---
        for (dt, vpx, apx, weapon, dmg) in dmg_markers:
            if dt <= t <= dt + 10:
                x, y = int(vpx[0]), int(vpx[1])
                s = 6
                draw.line([(x - s, y - s), (x + s, y + s)], fill=DAMAGE_COLOR, width=2)
                draw.line([(x - s, y + s), (x + s, y - s)], fill=DAMAGE_COLOR, width=2)
                if apx:
                    ax, ay = int(apx[0]), int(apx[1])
                    draw.line([(ax, ay), (x, y)], fill=(255, 200, 0, 120), width=1)

        # --- Kill markers (my kills, show for 10s) ---
        for (kt, kpx) in kill_markers:
            if kt <= t <= kt + 10:
                x, y = int(kpx[0]), int(kpx[1])
                r = 6
                draw.ellipse([x - r, y - r, x + r, y + r], outline=KILL_COLOR, width=2)

        # --- Death marker ---
        if death_marker and t >= death_marker[0]:
            x, y = int(death_marker[1][0]), int(death_marker[1][1])
            s = 10
            draw.line([(x - s, y - s), (x + s, y + s)], fill=DEATH_COLOR, width=3)
            draw.line([(x - s, y + s), (x + s, y - s)], fill=DEATH_COLOR, width=3)
            # skull text
            draw.text((x + 12, y - 10), "DEAD", fill=DEATH_COLOR, font=font_sm)

        # --- Landing marker ---
        if telem.landing:
            lx, ly, lt = telem.landing
            lpx, lpy = game_to_pixel(lx, ly, map_size)
            if t >= lt:
                draw.rectangle(
                    [lpx - 4, lpy - 4, lpx + 4, lpy + 4],
                    outline=(0, 255, 180), width=2,
                )

        # --- HUD: clock, alive count, map name ---
        draw.rectangle([0, 0, 220, 36], fill=CLOCK_BG)
        clock_text = f"  {_format_time(t)}   Alive: {_alive_at(telem.alive_counts, t)}"
        draw.text((6, 8), clock_text, fill=(255, 255, 255), font=font_sm)

        draw.rectangle([0, RENDER_SIZE - 30, 320, RENDER_SIZE], fill=CLOCK_BG)
        label = f"  {meta['label']}  |  pubg-coach"
        draw.text((6, RENDER_SIZE - 24), label, fill=(200, 200, 200), font=font_sm)

        frames.append(frame.convert("RGB").quantize(colors=128, dither=0))

    # Hold last frame a bit longer
    for _ in range(GIF_FPS * 2):
        frames.append(frames[-1])

    out_name = f"replay_{match_data['match_id'][-8:]}.gif"
    out_path = OUTPUT_DIR / out_name
    print(f"  Saving GIF ({len(frames)} frames)...")

    duration_ms = int(1000 / GIF_FPS)
    frames[0].save(
        str(out_path),
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=0,
        optimize=True,
    )

    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"  GIF: {size_mb:.1f} MB")
    return str(out_path)
