# Erangel, 10th place: the breakdown

One squad match. 6 kills, 835 damage, killed at 16:48 by an AWM from 201 m.
Everything below comes from the match telemetry of all 99 players, run through `breakdown.py`.
The raw, anonymised export is in [`examples/erangel_v3.json`](../examples/erangel_v3.json).

Video of the last minute on rebuilt terrain: [`assets/pubg_map.mp4`](../assets/pubg_map.mp4)

## How the terrain was built

![terrain in 3D](img/terrain3d.jpg)


Telemetry stores x, y and z for every player position, hit and kill.
That is about 11,000 data points for this match, 10,200 of them usable as ground height.
The lowest point in each 16 m cell becomes the ground, roofs and parachutes are filtered out, gaps are interpolated.

Check: every hit is a proven line of sight, because the shooter saw the target.
Around the death spot there were 99 hits over 40 m. The terrain grid lets all 99 through, so it does not invent walls where shots really landed. That is a check against false cover, not proof that a spot is hidden.

Limits: buildings, walls and trees are not in the grid. Results are terrain only.
They are strongest on hills and open slopes, weakest in towns and forests.

## 1. The last minute

![endgame](img/endgame.jpg)

- **16:03.** Circle 4 appears. The #2 team is inside 5 s later, the #3 team 2 s later. I am 100 m outside, on the east edge, where the ground rises toward a ridge.
- **15:53 to 16:07.** I knock a player at 22 m and take 58 from an M24 in the same second. First aid to 75. No boosts left.
- **From 16:09.** The team that went on to win comes over the ridge. They are 100 m above me and the terrain no longer hides me.
- **16:26 to 16:48.** I stay in the same spot behind a waist-high wall for 22 s.
- **16:48.** AWM, 201 m, shooter 58 m above me. 75 damage. I had exactly 75.

My spot was more exposed to higher ground than 99% of the area around it.
At the end, 7 enemies had a terrain line on me: 4 on the ridge, 3 in the same compound.

The nearest low spot inside the circle that the winning team could not see was a gully 260 m west, by the road.
No spot within 500 m was hidden from every enemy around. The gully was hidden from the ridge.

## 2. Every stop

![stops](img/stops.jpg)

Eleven stops of 20 seconds or more. The colour is how many teams had a terrain line on me during the stop.

The pattern: I stop where a fight or the loot ends, not where the ground covers me.
At 13:57 five teams had a line on me for 20 seconds. Nobody fired. At 15:46 two did.

Near the river and in towns the terrain is flat, so buildings matter more than the grid shows. Treat the early stops as an upper bound.

## 3. Timing

![timing](img/timing.png)

- Circle 2: shown at 10:05, I was 1.3 km out and got in 2:12 after the zone started moving.
- Circle 3: I was in at 13:37, earlier than the #1 and #2 teams.
- Then I moved to the east edge. When circle 4 appeared, the #2 and #3 teams were already sitting where it landed.

The lesson is not "the zone will hurt you". It is that the team that arrives first picks the cover, and the team that arrives late takes what is left.

## 4. HP and boosts

![hp](img/hp.png)

- The last boost I picked up was at 4:50. Twelve minutes without another one.
- Both energy drinks went into the blue zone at 11:32 and 12:26.
- At 16:07 I had 3 first aids and no boosts. First aid stops at 75.
- Under a boost I regenerated about 3 HP every 12 s. One energy drink at 16:07 would have put me near 84 HP by 16:48. The AWM would have left about 9. This is an estimate.

## Run it on your own match

```
python export_v3.py --match-id <id> -o export/my_match.json
python breakdown.py export/my_match.json
```

The first run downloads the official high-res map once.
