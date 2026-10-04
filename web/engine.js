// Where the trains are at a given time of day. No map code here: the viewer,
// or anything else, asks for positions and draws them however it likes.

const DAY = 86400;

// Seconds after midnight, and the weekday (0 = Monday), on the railway's local clock.
export function localClock(timeZone, date = new Date()) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-GB", {
      timeZone, hourCycle: "h23", weekday: "short", hour: "2-digit", minute: "2-digit", second: "2-digit",
    }).formatToParts(date).map(p => [p.type, p.value]));
  const seconds = +parts.hour * 3600 + +parts.minute * 60 + +parts.second + date.getMilliseconds() / 1000;
  const weekday = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].indexOf(parts.weekday);
  return { seconds, weekday };
}

const RUNS_ON = {
  daily: () => true,
  weekdays: d => d < 5,
  saturdays: d => d === 5,
  sundays: d => d === 6,
};

export class Railway {
  constructor(data) {
    this.data = data;
    this.stations = data.stations;
    // A line can have several routes (branches); each trip names the one it runs on.
    this.routes = Object.fromEntries((data.routes ?? [{ id: "main", track: data.track, measures: data.trackMeasures }])
      .map(r => [r.id, r]));
    this.mainRoute = (data.routes ?? [{ id: "main" }])[0].id;
  }

  // Metres along a route for a station.
  #m(stationIndex, route) {
    const s = this.stations[stationIndex];
    return s.on?.[route] ?? s.m;
  }

  era(id) {
    return this.data.eras.find(e => e.id === id) ?? this.data.eras[0];
  }

  // [lng, lat] at `m` metres along a route (the main one by default).
  pointAt(m, route = this.mainRoute) {
    const { track, measures: ms } = this.routes[route] ?? this.routes[this.mainRoute];
    let lo = 0, hi = ms.length - 1;
    if (m <= 0) return track[0];
    if (m >= ms[hi]) return track[hi];
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (ms[mid] <= m) lo = mid; else hi = mid;
    }
    const u = (m - ms[lo]) / (ms[hi] - ms[lo] || 1);
    const a = track[lo], b = track[hi];
    return [a[0] + u * (b[0] - a[0]), a[1] + u * (b[1] - a[1])];
  }

  // Every train on the line at `seconds` past midnight.
  // Trips that run past midnight are also checked against the previous day.
  trainsAt(eraId, seconds, weekday = 0) {
    const era = this.era(eraId);
    if (!RUNS_ON[era.days]?.(weekday)) return [];
    const out = [];
    for (const trip of era.trips) {
      for (const t of [seconds, seconds + DAY]) {
        const pos = this.#position(trip, t);
        if (pos) { out.push(pos); break; }
      }
    }
    return out;
  }

  // The next `n` departures from each trip's first station after `seconds`.
  departures(eraId, seconds, n = 5) {
    const era = this.era(eraId);
    return era.trips
      .map(trip => {
        const start = trip.stops[0][1];
        const wait = (start - seconds + DAY) % DAY;
        return { trip, start, wait, from: this.stations[trip.stops[0][0]] };
      })
      .filter(d => d.wait > 0)
      .sort((a, b) => a.wait - b.wait)
      .slice(0, n);
  }

  // Scheduled calls at one station, in time order.
  callsAt(eraId, stationIndex) {
    const calls = [];
    for (const trip of this.era(eraId).trips) {
      const stop = trip.stops.find(s => s[0] === stationIndex);
      if (stop) calls.push({ trip, time: stop[1], flag: stop[2] === 1, kind: stop[3] });
    }
    return calls.sort((a, b) => a.time - b.time);
  }

  // Stops are [station index, arrive, flag, kind, leave]; leave is later
  // than arrive where the card prints a wait.
  #position(trip, t) {
    const stops = trip.stops;
    const leave = s => s[4] ?? s[1];
    const first = stops[0][1], last = stops[stops.length - 1][1];
    if (t < first || t > last) return null;
    let i = 0;
    while (i < stops.length - 2 && stops[i + 1][1] <= t) i++;
    const a = stops[i], b = stops[i + 1];
    const route = trip.route ?? this.mainRoute;
    const ma = this.#m(a[0], route), mb = this.#m(b[0], route);
    let m, waiting = false;
    if (t <= leave(a)) {
      // Standing at the station until its leave time.
      m = ma;
      waiting = t < leave(a);
    } else {
      // Constant speed between leaving one station and reaching the next.
      const span = b[1] - leave(a);
      const u = span > 0 ? Math.min(1, (t - leave(a)) / span) : 1;
      m = ma + u * (mb - ma);
    }
    return {
      trip,
      measure: m,
      waiting,
      lngLat: this.pointAt(m, route),
      previous: { station: this.stations[a[0]], time: leave(a) },
      next: { station: this.stations[b[0]], time: b[1] },
    };
  }
}

export function formatTime(seconds, withSeconds = false) {
  const s = Math.floor(((seconds % DAY) + DAY) % DAY);
  const hh = String(Math.floor(s / 3600)).padStart(2, "0");
  const mm = String(Math.floor(s % 3600 / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return withSeconds ? `${hh}:${mm}:${ss}` : `${hh}:${mm}`;
}
