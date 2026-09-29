# Abandoned Railways

Timetables of defunct railways, replayed against today's clock: where would the trains be right now if they still ran?

Each railway lives in `railways/<id>/`:

| File | What it holds |
|---|---|
| `railway.yaml` | Name, dates, timezone, colour, eras and sources |
| `track.geojson` | The traced line |
| `stations.geojson` | Stations, with alternate names |
| `timetables/<era>-<direction>.csv` | The printed timetable as a grid: stations down, train numbers across, 24-hour times |

Timetable cells: a blank cell means the train stops but no time was printed (the build estimates one from distance); `-` means the train doesn't serve that station; `~HH:MM` means the time was reconstructed (the reason is noted in `railway.yaml`). The `stop` column is `S` for regular stops as printed; every other station is flag-only.

A `mile` column is optional and kept as printed. Old mileposts rarely match real distance, so they are never used to place anything; all distances come from the traced track.

## Build

```bash
pip install -r requirements.txt
python -m pipeline            # or: python -m pipeline wvs
```

This checks every railway and writes `dist/feeds/<railway>-<era>.zip` (GTFS) and `dist/data/<railway>.json` for the viewer. Errors stop the build: unknown station names, unreadable times, times running backwards, or stations out of order along the track. Warnings are printed and the build carries on: stations more than 50 m off the track, or implied speeds between stations outside 5–60 mph.

Each feed's calendar runs from 2000 to 2099, so any GTFS tool shows the trains running today.

## Railways

- **Willamette Valley Southern** (1915–1933): Oregon City – Mt. Angel electric interurban.

## License

MIT
