#!/usr/bin/env python3
"""Capital Seasons Map daily builder.

Usage: python3 update.py TEMPLATE_HTML TOPO_JSON OUT_HTML HISTORY_IN HISTORY_OUT [PREDLOG_IN PREDLOG_OUT
       [CITIES_IN CITIES_OUT POINTS_JSON [WATCHLOG_IN WATCHLOG_OUT]]]
With the cities files, also refreshes the extra front cities and builds the smooth season-front surfaces.
Loads the cached observed history, refreshes the last few weeks (forecast API
past days + archive reanalysis) and pulls a 10-day forecast for every state capital
in two requests total,
applies the season rules (a season is skipped if its window closes unmet), predicts upcoming
changes with a confidence level, and writes a standalone HTML page.
Exits non-zero without writing if any capital's data could not be fetched.
"""
import json, re, sys, time, datetime as dt, urllib.request
from zoneinfo import ZoneInfo

HORIZON = 10          # forecast days, including today
MARGIN = 3.0          # degrees F for likely / possible
NEAR_DAYS = 3         # a change more than this many days out is at most "possible"
DATA_START = dt.date(2021, 9, 1)

CAPS = [
("01","AL","Montgomery",32.3668,-86.3000),("02","AK","Juneau",58.3019,-134.4197),("04","AZ","Phoenix",33.4484,-112.0740),
("05","AR","Little Rock",34.7465,-92.2896),("06","CA","Sacramento",38.5816,-121.4944),("08","CO","Denver",39.7392,-104.9903),
("09","CT","Hartford",41.7658,-72.6734),("10","DE","Dover",39.1582,-75.5244),("11","DC","Washington",38.9072,-77.0369),
("12","FL","Tallahassee",30.4383,-84.2807),("13","GA","Atlanta",33.7490,-84.3880),("15","HI","Honolulu",21.3069,-157.8583),
("16","ID","Boise",43.6150,-116.2023),("17","IL","Springfield",39.7817,-89.6501),("18","IN","Indianapolis",39.7684,-86.1581),
("19","IA","Des Moines",41.5868,-93.6250),("20","KS","Topeka",39.0473,-95.6752),("21","KY","Frankfort",38.2009,-84.8733),
("22","LA","Baton Rouge",30.4515,-91.1871),("23","ME","Augusta",44.3106,-69.7795),("24","MD","Annapolis",38.9784,-76.4922),
("25","MA","Boston",42.3601,-71.0589),("26","MI","Lansing",42.7325,-84.5555),("27","MN","Saint Paul",44.9537,-93.0900),
("28","MS","Jackson",32.2988,-90.1848),("29","MO","Jefferson City",38.5767,-92.1735),("30","MT","Helena",46.5891,-112.0391),
("31","NE","Lincoln",40.8136,-96.7026),("32","NV","Carson City",39.1638,-119.7674),("33","NH","Concord",43.2081,-71.5376),
("34","NJ","Trenton",40.2206,-74.7597),("35","NM","Santa Fe",35.6870,-105.9378),("36","NY","Albany",42.6526,-73.7562),
("37","NC","Raleigh",35.7796,-78.6382),("38","ND","Bismarck",46.8083,-100.7837),("39","OH","Columbus",39.9612,-82.9988),
("40","OK","Oklahoma City",35.4676,-97.5164),("41","OR","Salem",44.9429,-123.0351),("42","PA","Harrisburg",40.2732,-76.8867),
("44","RI","Providence",41.8240,-71.4128),("45","SC","Columbia",34.0007,-81.0348),("46","SD","Pierre",44.3683,-100.3510),
("47","TN","Nashville",36.1627,-86.7816),("48","TX","Austin",30.2672,-97.7431),("49","UT","Salt Lake City",40.7608,-111.8910),
("50","VT","Montpelier",44.2601,-72.5754),("51","VA","Richmond",37.5407,-77.4360),("53","WA","Olympia",47.0379,-122.9007),
("54","WV","Charleston",38.3498,-81.6326),("55","WI","Madison",43.0731,-89.4012),("56","WY","Cheyenne",41.1400,-104.8202)]

def get(url, tries=6):
    for a in range(tries):
        try:
            return json.load(urllib.request.urlopen(url, timeout=60))
        except Exception as e:
            print("retry", a + 1, e, file=sys.stderr); time.sleep(5 * (a + 1))
    raise SystemExit("fetch failed: " + url)

def feb_end(y):
    return dt.date(y, 2, 29) if (y % 4 == 0 and (y % 100 or y % 400 == 0)) else dt.date(y, 2, 28)

SEASON_ORDER = ["spring", "summer", "autumn", "winter"]

def windows(y):
    # season, opening day, search limit, run length, test(hi, lo, shift), margin(hi, lo); shift>0 makes the test stricter.
    # A streak can only begin on or after the opening day; the season starts the day the streak is complete.
    # There is no deadline: the search continues until the condition is met or a later season starts first
    # (then this season is skipped). The limit is just a safety stop one year out.
    rules = [
        ("spring", dt.date(y, 3, 1), 3, lambda h, l, s: h >= 55 + s, lambda h, l: h - 55),
        ("summer", dt.date(y, 5, 15), 5, lambda h, l, s: h >= 75 + s, lambda h, l: h - 75),
        ("autumn", dt.date(y, 9, 1), 1, lambda h, l, s: l < 50 - s, lambda h, l: 50 - l),
        ("winter", dt.date(y, 11, 1), 1, lambda h, l, s: l < 28 - s, lambda h, l: 28 - l),
    ]
    return [(n, w0, w0.replace(year=y + 1) - dt.timedelta(days=1), run, t, m) for n, w0, run, t, m in rules]

def slot_index(name, y):
    return y * 4 + SEASON_ORDER.index(name)

def first_run(days, run, test, w0, w1, shift=0.0):
    """days: list of (date, hi, lo). Return index of the first run start inside [w0, w1] that fully qualifies."""
    for i, (d, h, l) in enumerate(days):
        if d < w0 or d > w1: continue
        if i + run > len(days): return None
        if all(test(days[i + k][1], days[i + k][2], shift) for k in range(run)):
            return i
    return None

def season_events(days, y_last):
    """Official season starts from observed days. Each season's first qualifying day counts only if it comes
    before the next season that actually starts; otherwise that season is skipped that year."""
    return season_events_from(days, y_last, DATA_START, {})

def season_events_from(days, y_last, start, preset):
    """Like season_events, for a series that begins on `start`. preset {(name, y): date or None} supplies season
    starts from before the series (None = not met by then; the search continues from `start`)."""
    last_obs = days[-1][0]
    found = []
    for y in range(DATA_START.year, y_last + 1):
        for name, w0, w1, run, test, marg in windows(y):
            if w0 > last_obs: continue
            if (name, y) in preset and preset[(name, y)]:
                found.append((slot_index(name, y), name, y, preset[(name, y)])); continue
            if w0 < start and (name, y) not in preset: continue
            i = first_run(days, run, test, max(w0, start), w1)
            found.append((slot_index(name, y), name, y, days[i + run - 1][0] if i is not None else None))
    found.sort()
    events, nxt = [], None
    for idx, name, y, d in reversed(found):
        if d is not None and (nxt is None or d < nxt):
            events.append([d.isoformat(), name, y, 0]); nxt = d
    events.sort()
    return events

def predict(allday, last_obs, today, fc_end, known):
    """known: events already observed by last_obs. Looks for season starts that reach into the forecast."""
    done = {(e[1], e[2]) for e in known}
    latest = max((slot_index(e[1], e[2]) for e in known), default=-1)
    cands = []
    for y in range(last_obs.year - 1, last_obs.year + 2):
        for name, w0, w1, run, test, marg in windows(y):
            if (name, y) in done or slot_index(name, y) < latest or w0 > fc_end or w1 < last_obs: continue
            lo_start = max(w0, last_obs - dt.timedelta(days=run - 2))   # streak must reach into the forecast
            i = first_run(allday, run, test, lo_start, w1)
            if i is not None:
                seg = allday[i:i + run]
                fseg = [s for s in seg if s[0] > last_obs]
                margin = min(marg(s[1], s[2]) for s in fseg) if fseg else 99
                start = seg[-1][0]   # the day the streak completes is the season's start
                out = (start - today).days
                conf = "likely" if (margin >= MARGIN and out <= NEAR_DAYS) else "possible"
                cands.append(dict(season=name, date=start.isoformat(), conf=conf, via="forecast", year=y,
                                  margin=round(margin, 1)))
                continue
            j = first_run(allday, run, test, lo_start, w1, -MARGIN)
            if j is not None:
                cands.append(dict(season=name, date=allday[j + run - 1][0].isoformat(), conf="possible", via="near-miss", year=y))
    cur = None
    for e in known:
        if e[0] <= last_obs.isoformat(): cur = e[1]
    cands = sorted((c for c in cands if c["season"] != cur), key=lambda c: c["date"])
    return cands[0] if cands else None

# ---------------- season fronts: smooth arrival surfaces over the lower 48 ----------------
FRONT_STEP, FRONT_W, FRONT_H = 8, 975, 610
FRONT_POW, FRONT_SOFT, FRONT_BLUR = 1.5, 16.0, False   # weight = (dist^2 + soft)^-pow
CAP_DAYS = 3000            # stored value for "skipped" / "not yet": far beyond any date shown

def front_surfaces(points, slots, obs_end):
    """points: [(x, y, {(name, year): date or 'skip' or 'pending'})]; slots: [(name, year, open_date)].
    Returns per-slot dicts with an inverse-distance-blended grid of arrival days (tenths, int16, base64)."""
    import base64, struct
    nx, ny = -(-FRONT_W // FRONT_STEP), -(-FRONT_H // FRONT_STEP)
    out = []
    try:
        import numpy as np
    except ImportError:
        np = None
    for name, y, w0 in slots:
        real, other, first, last, pending = [], [], None, None, False
        for x, yy, ev in points:
            v = ev.get((name, y))
            if v is None: continue                       # point has no data for this season
            if v in ("pending", "skip"):
                other.append((x, yy, v)); pending = pending or v == "pending"; continue
            real.append((x, yy, (v - w0).days))
            first = v if first is None or v < first else first
            last = v if last is None or v > last else last
        if not real and not other: continue
        # Places without a start get a value just past the real ones, so they hold the front back without
        # warping the whole surface: "skipped" = 30 days after the last real start; "not yet" = 20 days past today.
        latest_real = max((r[2] for r in real), default=0)
        today_rel = (obs_end - w0).days
        vals = real + [(x, yy, (today_rel + 20) if v == "pending" else (latest_real + 30)) for x, yy, v in other]
        if np is not None:
            gx = (np.arange(nx) + 0.5) * FRONT_STEP; gy = (np.arange(ny) + 0.5) * FRONT_STEP
            X, Y = np.meshgrid(gx, gy)
            P = np.array(vals, dtype=float)
            d2 = (X.ravel()[:, None] - P[:, 0]) ** 2 + (Y.ravel()[:, None] - P[:, 1]) ** 2 + FRONT_SOFT
            w = d2 ** -FRONT_POW
            g = ((w * P[:, 2]).sum(1) / w.sum(1)).reshape(ny, nx)
            if FRONT_BLUR:
                pad = np.pad(g, 1, mode="edge")
                g = sum(pad[1 + dj:1 + dj + ny, 1 + di:1 + di + nx] for dj in (-1, 0, 1) for di in (-1, 0, 1)) / 9.0
            flat = [int(round(min(v, CAP_DAYS) * 10)) for v in g.ravel()]
        else:
            flat = []
            for j in range(ny):
                for i in range(nx):
                    cx, cy = (i + 0.5) * FRONT_STEP, (j + 0.5) * FRONT_STEP
                    num = den = 0.0
                    for px, py, pv in vals:
                        wt = ((px - cx) ** 2 + (py - cy) ** 2 + FRONT_SOFT) ** -FRONT_POW
                        num += wt * pv; den += wt
                    flat.append(int(round(min(num / den, CAP_DAYS) * 10)))
        b64 = base64.b64encode(struct.pack("<%dh" % len(flat), *flat)).decode()
        out.append(dict(s=name, y=y, open=w0.isoformat(), first=first.isoformat() if first else None,
                        last=None if pending or not last else last.isoformat(), g=b64))
    return dict(step=FRONT_STEP, nx=nx, ny=ny, slots=out)

def point_slot_values(events, covered_from, obs_end, slots):
    """For one point: {(name, y): date | 'skip' | 'pending'} for each display slot its data covers."""
    have = {(e[1], e[2]): dt.date.fromisoformat(e[0]) for e in events}
    latest = max((slot_index(e[1], e[2]) for e in events), default=-1)
    res = {}
    for name, y, w0 in slots:
        if w0 < covered_from or w0 > obs_end: continue
        if (name, y) in have: res[(name, y)] = have[(name, y)]
        elif slot_index(name, y) < latest: res[(name, y)] = "skip"
        else: res[(name, y)] = "pending"
    return res

def compute(days, fdays, today, fc_end, obs_end):
    """days: observed (date, hi, lo) through the last observed day; fdays: forecast days after it."""
    last_obs = days[-1][0]
    events = season_events(days, obs_end.year)
    i0 = max(0, len(days) - 6)
    pred = predict(days[i0:] + fdays, last_obs, today, fc_end, events)

    return events, pred

def multi(base, extra):
    lats = ",".join(str(c[3]) for c in CAPS); lons = ",".join(str(c[4]) for c in CAPS)
    r = get(base + "?latitude=%s&longitude=%s&daily=temperature_2m_max,temperature_2m_min"
            "&temperature_unit=fahrenheit&timezone=auto%s" % (lats, lons, extra))
    return r if isinstance(r, list) else [r]

def daily(block):
    d = block["daily"]
    return {t: [h, l] for t, h, l in zip(d["time"], d["temperature_2m_max"], d["temperature_2m_min"])
            if h is not None and l is not None}

# Weather fronts: a day-by-day season for each point that follows the weather with no calendar windows.
# Seasons can advance and fall back:
#   winter -> mild    : last 3 highs all >= 55 and no low < 28 in those 3 days
#   mild   -> winter  : a low < 28 in the last 3 days
#   mild   -> summer  : last 5 highs all >= 75 (while cooling, also no low < 50 in the last 3 days)
#   summer -> mild    : a low < 50 in the last 3 days once the 5-day 75-degree streak is broken
# "Mild" is spring or autumn per place: always spring from Jan 1 to Jul 14 (a place dropping out of summer in June
# or early July is the spring front pulling back). From Jul 15 a place turns to autumn when it leaves summer, or,
# if it never got summer, once its temperatures have peaked (3-week average 3F below its warmest of the year).
# Once autumn it stays autumn until Jan 1, when every mild place is spring again. The relabel never triggers a watch.
REGIME_CODE ={"winter": "w", "spring": "s", "summer": "u", "autumn": "a"}

LATE_SUMMER = (7, 15)     # from this date a place can turn to autumn
PEAK_DROP = 3.0           # degrees below the year's warmest 3-week average that marks "temperatures have peaked"

def _mild_label(d):
    """Calendar half used only inside the weather rules (warming vs cooling half of the year)."""
    return "spring" if (d.month, d.day) < LATE_SUMMER else "autumn"

class Labeler:
    """Turns weather tiers (winter / mild / summer) into seasons, deciding spring vs autumn per place."""
    def __init__(self):
        self.year = None; self.autumn = False; self.best = None; self.win = []; self.prev = None
    def copy(self):
        c = Labeler(); c.year, c.autumn, c.best, c.win, c.prev = self.year, self.autumn, self.best, list(self.win), self.prev
        return c
    def step(self, d, h, l, tier):
        if d.year != self.year: self.year, self.autumn, self.best = d.year, False, None
        self.win.append((h + l) / 2)
        if len(self.win) > 21: self.win.pop(0)
        m = sum(self.win) / len(self.win)
        if d.month >= 5: self.best = m if self.best is None else max(self.best, m)
        if (d.month, d.day) >= LATE_SUMMER:
            if self.prev == "summer" and tier == "mild": self.autumn = True
            if self.best is not None and len(self.win) == 21 and m <= self.best - PEAK_DROP: self.autumn = True
        self.prev = tier
        return tier if tier != "mild" else ("autumn" if self.autumn else "spring")
    def peek(self, tier):
        return tier if tier != "mild" else ("autumn" if self.autumn else "spring")

def label_days(days, tiers):
    """Season per day for a place, plus the labeler state after the last day."""
    lab = Labeler()
    return [lab.step(d, h, l, t) for (d, h, l), t in zip(days, tiers)], lab

def regime_next(tier, win, d, shift=0.0):
    """One day of the weather-season rules. win: up to the last 5 (hi, lo), newest last. shift > 0 makes a change
    harder (forecast must clear the rule by that many degrees), shift < 0 easier (near misses count)."""
    hs = [x[0] for x in win][::-1]; ls = [x[1] for x in win][-3:]
    h3 = len(hs) >= 3 and all(x >= 55 + shift for x in hs[:3])
    h5 = len(hs) >= 5 and all(x >= 75 + shift for x in hs[:5])
    c3 = any(x < 28 - shift for x in ls)
    a3 = any(x < 50 - shift for x in ls)
    hold_c3 = any(x < 28 for x in ls)
    warming = _mild_label(d) == "spring"
    if tier == "winter":
        if h3 and not hold_c3: return "mild"
    elif tier == "mild":
        if c3: return "winter"
        if h5 and (warming or not any(x < 50 for x in ls)): return "summer"
    elif tier == "summer":
        if a3 and not (len(hs) >= 5 and all(x >= 75 for x in hs[:5])): return "mild"
    return tier

def regime_tiers(days, seed):
    tier = "winter" if seed == "winter" else "summer" if seed == "summer" else "mild"
    out = []
    for i, (d, h, l) in enumerate(days):
        tier = regime_next(tier, [(x[1], x[2]) for x in days[max(0, i - 4):i + 1]], d)
        out.append(tier)
    return out

def regime_runs(days, seed, d0):
    """days: [(date, hi, lo)]; seed: season on the first day. Returns runs [offset_from_d0, length, code]."""
    runs, cur, start = [], None, None
    labels, _ = label_days(days, regime_tiers(days, seed))
    for (d, h, l), season in zip(days, labels):
        code = REGIME_CODE[season]
        if code != cur:
            if cur: runs.append([(start - d0).days, (d - start).days, cur])
            cur, start = code, d
    if cur: runs.append([(start - d0).days, (days[-1][0] - start).days + 1, cur])
    return runs

# ---------------- watches & warnings ----------------
WATCH_DAYS, WARN_DAYS, WATCH_MARGIN, GROUP_DIST = 5, 3, 3.0, 130

NEXT_SEASON = {"winter": "spring", "spring": "summer", "summer": "autumn", "autumn": "winter"}
# Backward moves never get alerts, with two exceptions (warnings only, after the season has held >= EXC_HELD days,
# clearing the rule by WATCH_MARGIN within WARN_DAYS): a late freeze (spring -> winter) and summer returning (autumn -> summer).
EXCEPTIONS = {("spring", "winter"): "latefreeze", ("autumn", "summer"): "summerreturns"}
EXC_HELD = 7
EXC_MARGIN = 1.5      # degrees the forecast must clear the rule by for the two exceptions
EXC_WINDOWS = {"latefreeze": ((3, 1), (5, 31)), "summerreturns": ((8, 15), (10, 31))}

def held_days(labels):
    """How many days in a row the place has been in its current season."""
    n = 0
    for s in reversed(labels):
        if s != labels[-1]: break
        n += 1
    return n

def forecast_change(tail, tier0, fdays, shift, lab=None, backs=()):
    """tail: last observed (date, hi, lo) (up to 5); fdays: forecast days; lab: the place's Labeler after the last
    observed day. First season change in the forecast: (date, new_season, key_value) or None."""
    sim = list(tail) + list(fdays); start = len(tail); tier = tier0
    lab = lab.copy() if lab else Labeler()
    before = lab.peek(tier0) if tail else None
    for i in range(start, len(sim)):
        win = [(x[1], x[2]) for x in sim[max(0, i - 4):i + 1]]
        d = sim[i][0]
        nt = regime_next(tier, win, d, shift)
        new = lab.step(d, sim[i][1], sim[i][2], nt)
        if nt != tier and NEXT_SEASON.get(before) != new and (before, new) not in backs:   # backward moves are skipped
            tier = nt; continue
        if nt != tier:
            if nt == "winter" or (tier == "summer" and nt == "mild"): val = min(x[1] for x in win[-3:])
            elif nt == "summer": val = min(x[0] for x in win[-5:])
            else: val = min(x[0] for x in win[-3:])
            if new != before: return (d, new, round(val))
        tier = nt
    return None

def capital_watch(tail, tier0, fdays, issue, lab=None, held=0):
    """Level for one place: ('warning'|'watch', season, date, value[, exception kind]) or None."""
    fdays = [f for f in fdays if 1 <= (f[0] - issue).days <= WATCH_DAYS]
    if not tail or not fdays: return None
    normal = forecast_change(tail, tier0, fdays, 0.0, lab)
    if normal:
        strict = forecast_change(tail, tier0, fdays, WATCH_MARGIN, lab)
        if strict and strict[1] == normal[1] and (strict[0] - issue).days <= WARN_DAYS:
            return ("warning", strict[1], strict[0], strict[2])
        return ("watch",) + normal[1:2] + (normal[0], normal[2])
    before = (lab.peek(tier0) if lab else Labeler().peek(tier0)) if tail else None
    if held >= EXC_HELD and any(k[0] == before for k in EXCEPTIONS):
        backs = {k for k in EXCEPTIONS if k[0] == before}
        ex = forecast_change(tail, tier0, fdays, EXC_MARGIN, lab, backs)
        if ex and (before, ex[1]) in backs and (ex[0] - issue).days <= WARN_DAYS:
            kind = EXCEPTIONS[(before, ex[1])]; w0, w1 = EXC_WINDOWS[kind]
            md = (ex[0].month, ex[0].day)
            if w0 <= md <= w1:
                return ("warning", ex[1], ex[0], ex[2], kind)
            # before its window it is a plain winter / summer warning (e.g. a January freeze after an early spring in the South)
            if md < w0 and (kind != "latefreeze" or ex[0].month <= 2) and (kind != "summerreturns" or ex[0].month >= 7):
                return ("warning", ex[1], ex[0], ex[2], "earlyfreeze" if kind == "latefreeze" else "earlysummer")
    return None     # near misses (forecast within 3F but not meeting the rule) no longer get watches

# ---------------- rare advisories: extreme heat / cold, false spring / false summer ----------------
RARE_PCT = 0.02           # extreme = hottest / coldest 2% of that place's own daily history
FALSE_DAYS = 7            # a new season that the forecast reverses within this many days is "false"
# each false season only counts when it starts in its classic part of the year (month, day) .. (month, day)
FALSE_WINDOWS = {"falsespring": ((2, 1), (4, 30)), "falsesummer": ((4, 1), (6, 30)),
                 "falseautumn": ((8, 1), (10, 31)), "falsewinter": ((10, 15), (12, 31))}

def extremes(days):
    """(98th-percentile high, 2nd-percentile low) of a place's daily history, or None if too short."""
    if len(days) < 300: return None
    hs = sorted(x[1] for x in days); ls = sorted(x[2] for x in days)
    k = int(len(hs) * RARE_PCT)
    return (hs[-1 - k], ls[k])

def rare_items(days, tiers, fdays, issue, thr, labels=None, lab=None):
    """days: observed (date, hi, lo) through `issue`, tiers: their weather tiers, labels/lab: their seasons and the
    Labeler after the last day (computed if not given). Returns
    [[type, start, value, end], ...] for the next WATCH_DAYS days."""
    out = []
    fd = [f for f in fdays if 1 <= (f[0] - issue).days <= WATCH_DAYS]
    if not fd or not days: return out
    # season sequence: last FALSE_DAYS observed days, then the forecast simulated with the same rules
    if labels is None or lab is None: labels, lab = label_days(days, tiers)
    k0 = max(0, len(days) - FALSE_DAYS)
    seq = [(days[i][0], labels[i]) for i in range(k0, len(days))]
    sim = list(days[-5:]) + fd; tier = tiers[-1]; lab = lab.copy()
    for i in range(min(5, len(days)), len(sim)):
        tier = regime_next(tier, [(x[1], x[2]) for x in sim[max(0, i - 4):i + 1]], sim[i][0])
        seq.append((sim[i][0], lab.step(sim[i][0], sim[i][1], sim[i][2], tier)))
    hl = {x[0]: (x[1], x[2]) for x in list(days[-10:]) + fd}       # highs and lows by date (observed + forecast)
    found = set()
    for e in range(1, len(seq)):
        prev, cur = seq[e - 1][1], seq[e][1]
        start = seq[e][0]
        kind = None
        if cur == "spring" and prev == "winter": kind = "falsespring"
        elif cur == "summer" and prev == "spring": kind = "falsesummer"
        elif cur == "autumn" and prev == "summer": kind = "falseautumn"
        elif cur == "winter" and prev in ("autumn", "spring"): kind = "falsewinter"
        if not kind or kind in found: continue
        w0, w1 = FALSE_WINDOWS[kind]
        if not (w0 <= (start.month, start.day) <= w1): continue
        for r in range(e + 1, len(seq)):
            when = seq[r][0]
            if (when - start).days > FALSE_DAYS: break
            if seq[r][1] != prev or when <= issue: continue
            # the switch back must clear its rule by WATCH_MARGIN (3F), judged on the forecast days that cause it
            fw = [hl[when - dt.timedelta(days=k)] for k in range(5) if (when - dt.timedelta(days=k)) in hl]
            lows3 = [v[1] for v in fw[:3]]; highs3 = [v[0] for v in fw[:3]]; highs5 = [v[0] for v in fw[:5]]
            if kind == "falsespring": ok, val = bool(lows3) and min(lows3) < 28 - WATCH_MARGIN, min(lows3 or [0])
            elif kind == "falsesummer": ok, val = bool(lows3) and min(lows3) < 50 - WATCH_MARGIN, min(lows3 or [0])
            elif kind == "falseautumn": ok, val = len(highs5) == 5 and min(highs5) >= 75 + WATCH_MARGIN, min(highs5 or [0])
            else: ok, val = len(highs3) == 3 and min(highs3) >= 55 + WATCH_MARGIN and min(lows3) >= 28, min(highs3 or [0])
            if ok:
                out.append([kind, start.isoformat(), round(val), when.isoformat()])
                found.add(kind)
            break
    return out

# ---------------- rare temperatures: each place against its own history ----------------
RARE_T1, RARE_T2 = 0.01, 0.0025   # tier 1: coldest / hottest 1% of that place's days; tier 2: 0.25%
RARE_EXIT = (8.0, 5.0)            # staying power, like the Arctic / Heat fronts: degrees back past the tier-1 line to leave
RARE_SLOPE = 6.0                  # index units per degree outside the tier band (for smooth contours between places)

def rare_thresholds(days):
    """{c1, c2, h1, h2}: the lows at the coldest 1% / 0.25% and the highs at the hottest 1% / 0.25% of a place's
    own days, or None if the history is too short."""
    if len(days) < 300: return None
    hs = sorted(x[1] for x in days); ls = sorted(x[2] for x in days); n = len(hs)
    k1, k2 = int(n * RARE_T1), int(n * RARE_T2)
    return dict(c1=round(ls[k1], 1), c2=round(ls[k2], 1), h1=round(hs[-1 - k1], 1), h2=round(hs[-1 - k2], 1))

def rare_city_thresholds(cities, cap_thr, cap_days):
    """Rare-temperature thresholds for the extra cities: their own when they have the full history (Alaska), otherwise
    their state capital's, shifted by how much warmer or colder the city ran than the capital on the days both have."""
    D = dt.date.fromisoformat
    capmap = {ab: {x[0].isoformat(): (x[1], x[2]) for x in d} for ab, d in cap_days.items()}
    out = {}
    for c in cities["cities"]:
        key = c["name"] + ", " + c["st"]
        if not c.get("days"): continue
        if c.get("full"):
            own = rare_thresholds([(D(t), v[0], v[1]) for t, v in c["days"].items() if t >= DATA_START.isoformat()])
            if own: out[key] = own; continue
        thr = cap_thr.get(c["st"]); cm = capmap.get(c["st"], {})
        if not thr: continue
        both = [(v, cm[t]) for t, v in c["days"].items() if t in cm]
        if len(both) < 60: continue
        # the shift is judged on the capital's hottest / coldest tenth of the shared days, since extremes can differ
        # more than averages do (Miami's hot days run cooler than Tallahassee's, though their averages are close)
        k = max(6, len(both) // 10)
        hot = sorted(both, key=lambda p: -p[1][0])[:k]; cold = sorted(both, key=lambda p: p[1][1])[:k]
        dh = sum(v[0] - w[0] for v, w in hot) / k; dl = sum(v[1] - w[1] for v, w in cold) / k
        out[key] = dict(c1=round(thr["c1"] + dl, 1), c2=round(thr["c2"] + dl, 1),
                        h1=round(thr["h1"] + dh, 1), h2=round(thr["h2"] + dh, 1))
    return out

def rare_index(v, t1, t2, cold):
    """How rare a value is for a place: 1 at its tier-1 line, 2 at its tier-2 line, falling off by RARE_SLOPE
    degrees per unit outside that band (0 or less = ordinary), capped at 3."""
    x = (t1 - v) if cold else (v - t1)                 # degrees past the tier-1 line
    gap = max(abs(t1 - t2), 1.0)
    if x <= 0: return 1 + x / RARE_SLOPE
    if x <= gap: return 1 + x / gap
    return min(3.0, 2 + (x - gap) / RARE_SLOPE)

def rare_sparse(days, d0, thr):
    """Per-day rarity index for the Fronts view as {"c": [[off, idx*20]]} (only days above 0 are kept).
    Same staying power as the Arctic / Heat fronts: once a place reaches tier 1 it stays in (held just past the line)
    until two days in a row clear the exit line (RARE_EXIT degrees back on the ordinary side)."""
    if not thr: return None
    out = {"c": []}                     # cold only: heat fronts are off the map (rare heat still gets forecast alerts)
    for key, t1, t2, cold, ext in (("c", thr["c1"], thr["c2"], True, RARE_EXIT[0]),):
        on, miss = False, 0
        for d, hi, lo in days:
            v = lo if cold else hi
            if v is None: continue
            idx = rare_index(v, t1, t2, cold)
            if idx >= 1: on, miss = True, 0
            elif on:
                clear = (v >= t1 + ext) if cold else (v <= t1 - ext)
                miss = miss + 1 if clear else 0
                if miss >= 2: on = False
            if on: idx = max(idx, 1.05)
            if idx > 0: out[key].append([(d - d0).days, int(round(idx * 20))])
    return out

def rare_temp_items(fdays, issue, thr):
    """Forecast rare cold / heat in the next WATCH_DAYS days:
    [[type, first day, most extreme value, last day, tier, tier-1 line, tier-2 line], ...]."""
    out = []
    if not thr: return out
    fd = [f for f in fdays if 1 <= (f[0] - issue).days <= WATCH_DAYS]
    for kind, t1, t2, cold in (("rarecold", thr["c1"], thr["c2"], True), ("rareheat", thr["h1"], thr["h2"], False)):
        hits = [f for f in fd if (f[2] <= t1 if cold else f[1] >= t1)]
        if not hits: continue
        val = min(f[2] for f in hits) if cold else max(f[1] for f in hits)
        tier = 2 if (val <= t2 if cold else val >= t2) else 1
        out.append([kind, hits[0][0].isoformat(), round(val), hits[-1][0].isoformat(), tier, round(t1), round(t2)])
    return out

RARE_LOG_START = dt.date(2026, 10, 25)   # first morning whose rare cold / heat alerts come from that morning's forecast

def backfill_rare(wlog, series, rare_thr, obs_end):
    """Past watch-log days from before RARE_LOG_START get rare cold / heat alerts rebuilt from what actually happened
    over their next WATCH_DAYS days (a perfect forecast), once that whole window has been observed. Done once per day
    (marked rb=1). series: {key: {date: (hi, lo)}}; days that only checked the capitals get capitals only."""
    n = 0
    for d, entry in wlog.items():
        if not isinstance(entry, dict) or entry.get("rb"): continue
        day = dt.date.fromisoformat(d)
        if day >= RARE_LOG_START or day + dt.timedelta(days=WATCH_DAYS) > obs_end: continue
        add = []
        for key, thr in rare_thr.items():
            if not entry.get("all") and not re.fullmatch(r"[A-Z]{2}", key): continue
            s = series.get(key)
            if not s or not thr: continue
            fd = [(day + dt.timedelta(days=k),) + tuple(s[day + dt.timedelta(days=k)])
                  for k in range(1, WATCH_DAYS + 1) if (day + dt.timedelta(days=k)) in s]
            if len(fd) < WATCH_DAYS: continue
            add += [[key] + it for it in rare_temp_items(fd, day, thr)]
        entry["r"] = [x for x in entry.get("r", []) if x[1] not in ("rarecold", "rareheat")] + sorted(add)
        entry["rb"] = 1; n += 1
    return n

def city_thresholds(cities, cap_days):
    """Extreme thresholds for the extra cities: their state capital's, shifted by how much warmer or colder the
    city ran than the capital on the days both have."""
    caps = {ab: extremes([x for x in d if x[0] >= DATA_START]) for ab, d in cap_days.items()}
    capmap = {ab: {x[0].isoformat(): (x[1], x[2]) for x in d} for ab, d in cap_days.items()}
    out = {}
    for c in cities["cities"]:
        thr = caps.get(c["st"]); cm = capmap.get(c["st"], {})
        if not thr or not c.get("days"): continue
        both = [(v, cm[t]) for t, v in c["days"].items() if t in cm]
        if len(both) < 60: continue
        dh = sum(v[0] - w[0] for v, w in both) / len(both); dl = sum(v[1] - w[1] for v, w in both) / len(both)
        out[c["name"] + ", " + c["st"]] = (thr[0] + dh, thr[1] + dl)
    return out

def persist(prev, cur, issue, cur_season):
    """Keep an alert steady: today's forecast wins; if it drops the alert, keep yesterday's one more day (marked
    held) unless its date has passed or the place is already in that season. A second miss in a row drops it."""
    if cur: return cur
    if not prev: return None
    when = dt.date.fromisoformat(prev[3])
    if when <= issue or cur_season == prev[2]: return None
    if len(prev) > 6 and prev[6]: return None
    return (prev[1], prev[2], when, prev[4], prev[5] if len(prev) > 5 else None, 1)

def apply_persistence(items, prev_day, issue, seasons):
    """items: {key: tuple} today; prev_day: yesterday's log entry ({'e': [...]}) or None; seasons: {key: season now}."""
    prev = {e[0]: e for e in (prev_day or {}).get("e", [])}
    out = {}
    for k in set(items) | set(prev):
        v = persist(prev.get(k), items.get(k), issue, seasons.get(k))
        if v: out[k] = v
    return out

def watch_entries(items, all_points, rare=None):
    """Per-point log entries [key, level, season, date, value]; the page turns them into fitted areas.
    all_points: whether the extra cities were checked too (False = capitals only, e.g. rebuilt history).
    rare: {key: [[type, start, value, end], ...]} advisories."""
    return dict(all=all_points, e=[[k, v[0], v[1], v[2].isoformat(), v[3]] + list(v[4:]) for k, v in sorted(items.items())
                                   if v],
                r=[[k] + x for k, xs in sorted((rare or {}).items()) for x in xs])

def group_watches(items, points):
    """items: {AB: (level, season, date, value)} -> regional groups with a tilted box (map coords)."""
    import math
    groups = []
    for level in ("warning", "watch"):
        for season in ("winter", "spring", "summer", "autumn"):
            mem = [ab for ab, v in items.items() if v[0] == level and v[1] == season and ab in points["capitals"]]
            left = set(mem)
            while left:
                seed = left.pop(); comp, stack = [seed], [seed]
                while stack:
                    a = stack.pop(); ax, ay = points["capitals"][a]
                    for b in list(left):
                        bx, by = points["capitals"][b]
                        if (ax - bx) ** 2 + (ay - by) ** 2 <= GROUP_DIST ** 2:
                            left.remove(b); comp.append(b); stack.append(b)
                xs = [points["capitals"][a][0] for a in comp]; ys = [points["capitals"][a][1] for a in comp]
                mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
                if len(comp) >= 2:
                    sxx = sum((x - mx) ** 2 for x in xs); syy = sum((y - my) ** 2 for y in ys); sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
                    ang = 0.5 * math.atan2(2 * sxy, sxx - syy)
                else:
                    ang = -0.26                                  # a single capital gets a slightly tilted square
                ca, sa = math.cos(ang), math.sin(ang)
                us = [(x - mx) * ca + (y - my) * sa for x, y in zip(xs, ys)]
                vs = [-(x - mx) * sa + (y - my) * ca for x, y in zip(xs, ys)]
                pad = 30
                u0, u1 = min(us) - pad, max(us) + pad; v0, v1 = min(vs) - pad, max(vs) + pad
                if u1 - u0 < 70: c = (u0 + u1) / 2; u0, u1 = c - 35, c + 35
                if v1 - v0 < 60: c = (v0 + v1) / 2; v0, v1 = c - 30, c + 30
                box = [[round(mx + u * ca - v * sa), round(my + u * sa + v * ca)] for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]
                comp.sort(key=lambda a: (items[a][2], a))
                groups.append(dict(s=season, lv=level, box=box,
                                   m=[[a, items[a][2].isoformat(), items[a][3]] for a in comp]))
    return groups

def temps_b64(days, d0):
    """Daily mean temperature for one point as bytes (deg F + 60, 0-255), base64, starting at its first day."""
    import base64
    first, last = days[0][0], days[-1][0]
    by = {d: (h + l) / 2 for d, h, l in days}
    out, prev = bytearray(), (days[0][1] + days[0][2]) / 2
    d = first
    while d <= last:
        v = by.get(d, prev); prev = v
        out.append(max(0, min(255, int(round(v + 60)))))
        d += dt.timedelta(days=1)
    return base64.b64encode(bytes(out)).decode()

def extremes_sparse(days, d0, lo_cap=5.0, hi_cap=95.0):
    """Days in Arctic air (low < 0) or extreme heat (high > 100), as {"l": [[off, low]], "h": [[off, high]]}.
    With staying power: once in, a place stays in until two days in a row clear the exit line (low 8 or higher,
    high under 95), so one milder night does not make the area blink out. Held days count as just past the line.
    Only days past the caps are kept; everything else counts as the cap on the page."""
    ARC_IN, ARC_OUT, HEAT_IN, HEAT_OUT = 0.0, 8.0, 100.0, 95.0
    l, h = [], []
    arc = heat = False; arc_miss = heat_miss = 0
    for d, hi, lo in days:
        off = (d - d0).days
        if lo is not None:
            if lo < ARC_IN: arc, arc_miss = True, 0
            elif arc:
                arc_miss = arc_miss + 1 if lo >= ARC_OUT else 0
                if arc_miss >= 2: arc = False
            if arc: l.append([off, int(round(min(lo, -2.0)))])
            elif lo < lo_cap: l.append([off, int(round(lo))])
        if hi is not None:
            if hi > HEAT_IN: heat, heat_miss = True, 0
            elif heat:
                heat_miss = heat_miss + 1 if hi < HEAT_OUT else 0
                if heat_miss >= 2: heat = False
            if heat: h.append([off, int(round(max(hi, 102.0)))])
            elif hi > hi_cap: h.append([off, int(round(hi))])
    return {"l": l, "h": h}

def official_on(events, d, fallback):
    cur = fallback
    for e in events:
        if dt.date.fromisoformat(e[0]) <= d: cur = e[1]
        else: break
    return cur

def outlook_item(days_all, seed, obs_end):
    """The first season change the outlook shows OUTLOOK_FROM..OUTLOOK_TO days after obs_end (next in the cycle after
    wherever the 5-day watch window leaves off): (season, date, value) or None."""
    if not days_all or days_all[-1][0] < obs_end + dt.timedelta(days=OUTLOOK_FROM): return None
    labels, _ = label_days(days_all, regime_tiers(days_all, seed))
    pos = {d[0]: i for i, d in enumerate(days_all)}
    w_end = obs_end + dt.timedelta(days=OUTLOOK_FROM - 1)
    if w_end not in pos: return None
    tgt = NEXT_SEASON[labels[pos[w_end]]]
    for i in range(pos[w_end] + 1, len(days_all)):
        d, hi, lo = days_all[i]
        if (d - obs_end).days > OUTLOOK_TO: break
        if labels[i] == tgt:
            return (tgt, d, round(lo if tgt in ("autumn", "winter") else hi))
    return None

def fronts_from(cap_days, cities, points, obs_end, rare_thr=None, fut=None, outlook_out=None):
    """Per-point day-by-day weather seasons for the Fronts view (capitals + extra cities; Alaska and Hawaii are their own regions).
    rare_thr: {point key: rare-temperature thresholds}; adds each point's rarity index as its 9th field.
    fut: {point key ("AB" or "Name, ST"): [(date, hi, lo)] after obs_end} — the forecast then the CFS outlook, appended
    so the view can run into the coming months. outlook_out (dict) gets each point's outlook season change."""
    fut = fut or {}
    D = dt.date.fromisoformat
    D0 = DATA_START
    rare_thr = rare_thr or {}
    pts = []
    for ab, days in cap_days.items():
        if ab not in points["capitals"] or not days: continue
        days = [x for x in days if D0 <= x[0] <= obs_end]
        if not days: continue
        days = days + [f for f in fut.get(ab, []) if f[0] > days[-1][0]]
        if outlook_out is not None and fut.get(ab):
            it = outlook_item(days, "summer", obs_end)
            if it: outlook_out[ab] = it
        x, y = points["capitals"][ab]
        pts.append([x, y, (days[0][0] - D0).days, (days[-1][0] - D0).days, regime_runs(days, "summer", D0), temps_b64(days, D0), ab,
                    extremes_sparse(days, D0), rare_sparse(days, D0, rare_thr.get(ab))])
    start = D(cities["start"])
    for c in cities["cities"]:
        key = c["name"] + "|" + c["st"]
        if key not in points["cities"] or not c.get("days"): continue
        days = sorted((D(t), v[0], v[1]) for t, v in c["days"].items() if t <= obs_end.isoformat())
        if not days: continue
        if c.get("full"):        # full history from D0 (the Alaska cities): run like a capital
            days = [x for x in days if x[0] >= D0]; seed = "summer"
        else:
            preset = {("autumn", 2025): D(c["autumn"]) if c.get("autumn") else None,
                      ("winter", 2025): D(c["winter"]) if c.get("winter") else None}
            seed = official_on(season_events_from(days, obs_end.year, start, preset), days[0][0], "winter")
        ck = c["name"] + ", " + c["st"]
        days = days + [f for f in fut.get(ck, []) if f[0] > days[-1][0]]
        if outlook_out is not None and fut.get(ck):
            it = outlook_item(days, seed, obs_end)
            if it: outlook_out[ck] = it
        x, y = points["cities"][key]
        pts.append([x, y, (days[0][0] - D0).days, (days[-1][0] - D0).days, regime_runs(days, seed, D0), temps_b64(days, D0),
                    c["name"] + ", " + c["st"], extremes_sparse(days, D0), rare_sparse(days, D0, rare_thr.get(c["name"] + ", " + c["st"]))])
    return dict(d0=D0.isoformat(), points=len(pts), pts=pts, obsOff=(obs_end - D0).days)

def city_watch_items(cities, obs_end, thresholds=None, rare_out=None, seasons_out=None, rare_thr=None):
    """Watch items for the extra cities from their short forecast: {"Name, ST": (level, season, date, value)}.
    Rare advisories go into rare_out when given (with rare cold / heat when rare_thr is given)."""
    D = dt.date.fromisoformat
    start = D(cities["start"]); out = {}
    for c in cities["cities"]:
        fc = c.get("fc") or {}
        if not fc or not c.get("days"): continue
        days = sorted((D(t), v[0], v[1]) for t, v in c["days"].items() if t <= obs_end.isoformat())
        if not days or days[-1][0] != obs_end: continue
        preset = {("autumn", 2025): D(c["autumn"]) if c.get("autumn") else None,
                  ("winter", 2025): D(c["winter"]) if c.get("winter") else None}
        seed = official_on(season_events_from(days, obs_end.year, start, preset), days[0][0], "winter")
        tiers = regime_tiers(days, seed); tier = tiers[-1]
        labels, lab = label_days(days, tiers)
        fdays = sorted((D(t), v[0], v[1]) for t, v in fc.items())
        key = c["name"] + ", " + c["st"]
        if seasons_out is not None: seasons_out[key] = labels[-1]
        w = capital_watch(days[-5:], tier, fdays, obs_end, lab, held_days(labels))
        if w: out[key] = w
        if rare_out is not None:
            r = rare_items(days, tiers, fdays, obs_end, (thresholds or {}).get(key), labels, lab)
            r += rare_temp_items(fdays, obs_end, (rare_thr or {}).get(key))
            if r: rare_out[key] = r
    return out

def refresh_cities(cities, obs_end):
    """One request: the last week of observed highs/lows for every front city. Failure is non-fatal."""
    lst = cities["cities"]
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&daily=temperature_2m_max,temperature_2m_min"
           "&temperature_unit=fahrenheit&timezone=auto&past_days=7&forecast_days=%d"
           % (",".join(str(c["lat"]) for c in lst), ",".join(str(c["lon"]) for c in lst), WATCH_DAYS + 1))
    try:
        r = get(url, tries=4)
    except SystemExit:
        print("city refresh skipped", file=sys.stderr); return
    r = r if isinstance(r, list) else [r]
    for c, blk in zip(lst, r):
        fc = {}
        for t, v in daily(blk).items():
            if t <= obs_end.isoformat():
                if t >= cities["start"]: c.setdefault("days", {})[t] = v
            else: fc[t] = v
        c["fc"] = fc

# ---------------- CFS long-range outlook (NOAA Climate Forecast System, version 2) ----------------
# Each morning: the four most recent 9-month runs (member 01 of the 00/06/12/18Z cycles), daily highs and lows for
# every map point, averaged across the runs, then shifted per point so its first days match the regular forecast.
CFS_BASE = "https://noaa-cfs-pds.s3.amazonaws.com"
CFS_DAYS = 183            # how far the outlook reaches (about 6 months)
CFS_RUNS = 4
CFS_SPREAD = 1.0         # far out, the 4 runs no longer agree, so their average swings about half as much as real weather: double it back (0 = none)
OUTLOOK_FROM, OUTLOOK_TO = WATCH_DAYS + 1, 30   # outlook areas: season changes the outlook shows in these days

def _cfs_eccodes():
    try:
        import eccodes
        return eccodes
    except ImportError:
        import subprocess
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--break-system-packages", "eccodes"],
                           check=True, timeout=300, capture_output=True)
            import eccodes
            return eccodes
        except Exception as e:
            print("CFS outlook skipped: no GRIB decoder (%s)" % e, file=sys.stderr); return None

def _cfs_text(url):
    try:
        with urllib.request.urlopen(url, timeout=60) as r: return r.read().decode()
    except Exception:
        return None

def _cfs_range(url, last):
    for k in range(4):
        try:
            req = urllib.request.Request(url, headers={"Range": "bytes=0-%d" % last})
            with urllib.request.urlopen(req, timeout=300) as r: return r.read()
        except Exception as e:
            print("CFS retry", k, e, file=sys.stderr); time.sleep(10 * (k + 1))
    return None

def cfs_outlook(locs, today):
    """locs: [(key, lat, lon)]. Returns ({key: {date: [hi, lo]}} averaged over the runs, run labels) or (None, [])."""
    ec = _cfs_eccodes()
    if ec is None: return None, []
    import numpy as np, tempfile, os
    cycles = []
    for back in range(0, 3):
        d = today - dt.timedelta(days=back)
        for hh in ("18", "12", "06", "00"):
            if len(cycles) >= CFS_RUNS: break
            stamp = d.strftime("%Y%m%d") + hh
            base = "%s/cfs.%s/%s/time_grib_01/" % (CFS_BASE, d.strftime("%Y%m%d"), hh)
            idx = {v: _cfs_text(base + "%s.01.%s.daily.grb2.idx" % (v, stamp)) for v in ("tmax", "tmin")}
            if all(idx.values()) and idx["tmax"].count("\n") > 700:
                cycles.append((dt.datetime.strptime(stamp, "%Y%m%d%H"), base, stamp, idx))
    if not cycles:
        print("CFS outlook skipped: no recent runs found", file=sys.stderr); return None, []
    end = today + dt.timedelta(days=CFS_DAYS)
    keys = [k for k, la, lo in locs]
    acc = {}                                  # (key, date) -> [sum_hi, n_hi, sum_lo, n_lo]
    geo = None
    for t0, base, stamp, idx in cycles:
        need_h = int(((dt.datetime.combine(end + dt.timedelta(days=1), dt.time(6)) - t0).total_seconds()) // 3600)
        per = {}
        for var in ("tmax", "tmin"):
            lines = [l.split(":") for l in idx[var].strip().split("\n")]
            offs = [int(l[1]) for l in lines]
            hrs = [int(re.search(r"(\d+) hour", ":".join(l)).group(1)) if re.search(r"(\d+) hour", ":".join(l)) else 0 for l in lines]
            cut = next((i for i, h in enumerate(hrs) if h > need_h), len(lines))
            last = offs[cut] - 1 if cut < len(lines) else ""
            blob = _cfs_range(base + "%s.01.%s.daily.grb2" % (var, stamp), last if last != "" else 10**10)
            if not blob: print("CFS run", stamp, var, "failed", file=sys.stderr); per = None; break
            fd, path = tempfile.mkstemp(suffix=".grb2"); os.write(fd, blob); os.close(fd)
            daily_v = {}
            with open(path, "rb") as fh:
                while True:
                    g = ec.codes_grib_new_from_file(fh)
                    if g is None: break
                    try:
                        if geo is None:
                            ni, nj = ec.codes_get(g, "Ni"), ec.codes_get(g, "Nj")
                            lats = np.array(ec.codes_get_array(g, "distinctLatitudes"))[::-1] if ec.codes_get(g, "jScansPositively") == 0 else np.array(ec.codes_get_array(g, "distinctLatitudes"))
                            lats = np.array(ec.codes_get_array(g, "distinctLatitudes"))
                            north_first = ec.codes_get(g, "jScansPositively") == 0
                            if north_first: lats = np.sort(lats)[::-1]
                            dlon = 360.0 / ni
                            sel = []
                            for k, la, lo in locs:
                                x = (lo % 360) / dlon; i0 = int(np.floor(x)) % ni; i1 = (i0 + 1) % ni; fx = x - np.floor(x)
                                if north_first: j1 = int(np.searchsorted(-lats, -la)); j0 = max(j1 - 1, 0)
                                else: j1 = int(np.searchsorted(lats, la)); j0 = max(j1 - 1, 0)
                                j1 = min(j1, nj - 1)
                                fy = 0.0 if j1 == j0 else (la - lats[j0]) / (lats[j1] - lats[j0])
                                sel.append((j0 * ni + i0, j0 * ni + i1, j1 * ni + i0, j1 * ni + i1, fx, fy))
                            geo = (ni, nj, np.array([s[:4] for s in sel]), np.array([s[4] for s in sel]), np.array([s[5] for s in sel]))
                        endh = ec.codes_get(g, "endStep")
                        vals = ec.codes_get_values(g)
                    finally:
                        ec.codes_release(g)
                    _, _, ix, fx, fy = geo
                    v = vals[ix]
                    pt = (v[:, 0] * (1 - fx) + v[:, 1] * fx) * (1 - fy) + (v[:, 2] * (1 - fx) + v[:, 3] * fx) * fy
                    pt = (pt - 273.15) * 9 / 5 + 32
                    ve = t0 + dt.timedelta(hours=endh)
                    day = ve.date() if ve.hour in (12, 18) else ve.date() - dt.timedelta(days=1)   # US local day ~ 06Z-06Z
                    cur = daily_v.setdefault(day, [None, 0])
                    cur[0] = pt if cur[0] is None else (np.maximum(cur[0], pt) if var == "tmax" else np.minimum(cur[0], pt))
                    cur[1] += 1
            os.remove(path)
            per[var] = {d: v for d, (v, n) in daily_v.items() if n == 4 and today <= d <= end}
        if per is None: continue
        for d in per["tmax"]:
            if d not in per["tmin"]: continue
            hi, lo = per["tmax"][d], per["tmin"][d]
            for j, k in enumerate(keys):
                a = acc.setdefault((k, d), [0.0, 0, 0.0, 0])
                a[0] += float(hi[j]); a[1] += 1; a[2] += float(lo[j]); a[3] += 1
        print("CFS run", stamp, "read", file=sys.stderr)
    out = {}
    for (k, d), a in acc.items():
        if a[1] and a[3]: out.setdefault(k, {})[d] = [a[0] / a[1], a[2] / a[3]]
    return (out or None), [c[2] for c in cycles]

def climate_of(series):
    """A place's typical high / low by day of year from its own history ({date: [hi, lo]}): the average of every
    year's days within a week either side. Returns {(month, day): (hi, lo)} or None if under 2 years of data."""
    if len(series) < 700: return None
    by = {}
    for t, v in series.items():
        d = t if isinstance(t, dt.date) else dt.date.fromisoformat(t)
        if v[0] is None or v[1] is None: continue
        by.setdefault(d.timetuple().tm_yday, []).append(v)
    out = {}
    for doy in range(1, 367):
        vals = [v for k in range(-7, 8) for v in by.get((doy + k - 1) % 366 + 1, [])]
        if not vals: continue
        md = (dt.date(2024, 1, 1) + dt.timedelta(days=doy - 1))
        out[(md.month, md.day)] = (sum(v[0] for v in vals) / len(vals), sum(v[1] for v in vals) / len(vals))
    return out

def cfs_future(cfs_pt, fc_days, after, clim=None):
    """One point's days after `after` for the outlook: the regular forecast where it has days, then CFS.
    CFS is corrected two ways: right after the forecast it is shifted to meet the forecast (that gap fades within
    about a week, since much of it is just this week's weather); further out it is shifted so its average over the
    outlook matches the place's own climate for those dates (the model's own bias at that spot). fc_days: [(date, hi, lo)]."""
    import math
    fc_days = [x for x in fc_days if x[0] > after]
    if not cfs_pt: return fc_days
    both = [(x, cfs_pt[x[0]]) for x in fc_days if x[0] in cfs_pt]
    sh = sum(x[1] - c[0] for x, c in both) / len(both) if len(both) >= 3 else 0.0
    sl = sum(x[2] - c[1] for x, c in both) / len(both) if len(both) >= 3 else 0.0
    last = max([x[0] for x in fc_days], default=after)
    tail = sorted((d, v) for d, v in cfs_pt.items() if d > last)
    lh = ll = 0.0
    if clim:
        pairs = [(clim[(d.month, d.day)], v) for d, v in tail if (d.month, d.day) in clim]
        if len(pairs) >= 30:
            lh = sum(c[0] - v[0] for c, v in pairs) / len(pairs); ll = sum(c[1] - v[1] for c, v in pairs) / len(pairs)
    cap = lambda x: max(-15.0, min(15.0, x))
    sh, sl, lh, ll = cap(sh), cap(sl), cap(lh), cap(ll)
    out = list(fc_days)
    for d, v in tail:
        k = (d - last).days
        w = math.exp(-k / 7.0)
        hi, lo = v[0] + w * sh + (1 - w) * lh, v[1] + w * sl + (1 - w) * ll
        c = clim.get((d.month, d.day)) if clim else None
        if c:
            # averaging several runs flattens the day-to-day swings further out; widen them back around the
            # place's normal so single cold nights / hot days happen about as often as they really do
            f = 1 + CFS_SPREAD * (1 - math.exp(-k / 10.0))
            hi, lo = c[0] + (hi - c[0]) * f, c[1] + (lo - c[1]) * f
        out.append((d, hi, lo))
    res = []
    for x in out:                         # no gaps: stop at the first missing day
        if res and (x[0] - res[-1][0]).days != 1: break
        if not res and (x[0] - after).days != 1: break
        res.append(x)
    return res

def main(tpl_path, topo_path, out_path, hist_in, hist_out, log_in=None, log_out=None,
         cities_in=None, cities_out=None, points_path=None, watch_in=None, watch_out=None):
    et = ZoneInfo("America/New_York")
    now = dt.datetime.now(et)
    today = now.date()
    obs_end = today - dt.timedelta(days=1)
    fc_end = today + dt.timedelta(days=HORIZON - 1)

    # history cache: {AB: {date: [hi, lo]}} observed days only
    hist = json.load(open(hist_in))
    if "AL" in hist and "time" in hist["AL"]:          # bootstrap from the original raw archive dump
        hist = {ab: {t: [h, l] for t, h, l in zip(r["time"], r["hi"], r["lo"]) if h is not None and l is not None}
                for ab, r in hist.items()}

    # 1) forecast API: last 14 days (fills the archive's ~5-day lag) + the outlook. One request for all capitals.
    fc = multi("https://api.open-meteo.com/v1/forecast", "&past_days=14&forecast_days=%d" % (HORIZON + 1))
    # 2) archive API: last 3 weeks of settled reanalysis, overwrites model values once available. Optional.
    try:
        arc = multi("https://archive-api.open-meteo.com/v1/archive", "&start_date=%s&end_date=%s"
                    % ((obs_end - dt.timedelta(days=21)).isoformat(), obs_end.isoformat()))
    except SystemExit:
        arc = None; print("archive refresh skipped", file=sys.stderr)

    # 3) CFS outlook for every map point (capitals + front cities). Optional: the map works without it.
    cfs, cfs_runs = None, []
    try:
        locs = [(ab, la, lo) for fips, ab, city, la, lo in CAPS]
        if cities_in:
            locs += [(c["name"] + ", " + c["st"], c["lat"], c["lon"]) for c in json.load(open(cities_in))["cities"]]
        cfs, cfs_runs = cfs_outlook(locs, today)
    except Exception as e:
        print("CFS outlook skipped:", e, file=sys.stderr); cfs = None
    fut_all, clim_cap = {}, {}

    states, watch_items, rare_items_all, season_now, cap_thr = {}, {}, {}, {}, {}
    for idx, (fips, ab, city, lat, lon) in enumerate(CAPS):
        series = hist.setdefault(ab, {})
        fseries = daily(fc[idx])
        for t, v in fseries.items():
            if t <= obs_end.isoformat(): series[t] = v
        if arc:
            for t, v in daily(arc[idx]).items(): series[t] = v
        series = {t: v for t, v in series.items() if t <= obs_end.isoformat()}
        hist[ab] = series
        days = sorted((dt.date.fromisoformat(t), v[0], v[1]) for t, v in series.items())
        days = [d for d in days if d[0] <= obs_end]
        if not days or days[-1][0] < obs_end - dt.timedelta(days=2):
            raise SystemExit("observed data too old for " + ab)
        last_obs = days[-1][0]
        fdays = [(dt.date.fromisoformat(t), v[0], v[1]) for t, v in sorted(fseries.items())
                 if last_obs < dt.date.fromisoformat(t) <= fc_end]
        events, pred = compute(days, fdays, today, fc_end, obs_end)
        outlook = []
        if cfs:
            clim_cap[ab] = climate_of(series)
            fut = cfs_future(cfs.get(ab), fdays, last_obs, clim_cap[ab])
            fut_all[ab] = fut
            if fut:
                full = season_events(days + fut, fut[-1][0].year)
                outlook = [e for e in full if e[0] > obs_end.isoformat()]
        if True:                      # every capital, Alaska and Hawaii included
            dd = [x for x in days if x[0] >= DATA_START]
            tiers = regime_tiers(dd, "summer"); tier = tiers[-1]
            labels, lab = label_days(dd, tiers)
            w = capital_watch(days[-5:], tier, fdays, obs_end, lab, held_days(labels))
            if w: watch_items[ab] = w
            season_now[ab] = labels[-1]
            r = rare_items(dd, tiers, fdays, obs_end, extremes(dd), labels, lab)
            cap_thr[ab] = rare_thresholds(dd)
            r += rare_temp_items(fdays, obs_end, cap_thr[ab])
            if r: rare_items_all[ab] = r
        states[ab] = dict(fips=fips, city=city, events=events, pred=pred, outlook=outlook,
                          fc=[[d.isoformat(), round(h), round(l)] for d, h, l in fdays])

    # prediction log: {map date (last observed day): {AB: pred}} — keeps each morning's outlines for scrolling back
    log = {}
    if log_in:
        try: log = json.load(open(log_in))
        except (OSError, ValueError): log = {}
    log[obs_end.isoformat()] = {ab: {k: s["pred"][k] for k in ("season", "date", "conf", "via", "year")}
                                for ab, s in states.items() if s["pred"]}
    fronts, cities_checked = None, False
    rare_thr = {ab: t for ab, t in cap_thr.items() if t}
    series = {ab: {dt.date.fromisoformat(t): v for t, v in s.items()} for ab, s in hist.items()}
    if cities_in and points_path:
        cities = json.load(open(cities_in)); points = json.load(open(points_path))
        refresh_cities(cities, obs_end)
        cap_days = {ab: sorted((dt.date.fromisoformat(t), v[0], v[1]) for t, v in hist[ab].items()) for ab in hist}
        rare_thr.update(rare_city_thresholds(cities, rare_thr, cap_days))
        if cfs:
            for c in cities["cities"]:
                ck = c["name"] + ", " + c["st"]
                fcd = sorted((dt.date.fromisoformat(t), v[0], v[1]) for t, v in (c.get("fc") or {}).items())
                last = max((dt.date.fromisoformat(t) for t in c.get("days", {}) if t <= obs_end.isoformat()), default=obs_end)
                cd = c.get("days", {})
                clim = climate_of(cd)
                capc, caph = clim_cap.get(c["st"]), hist.get(c["st"], {})
                if clim is None and capc:
                    both = [(cd[t], caph[t]) for t in cd if t in caph]
                    if len(both) >= 60:
                        dh = sum(a[0] - b[0] for a, b in both) / len(both); dl = sum(a[1] - b[1] for a, b in both) / len(both)
                        clim = {k: (v[0] + dh, v[1] + dl) for k, v in capc.items()}
                fut_all[ck] = cfs_future(cfs.get(ck), fcd, last, clim)
        outlook_items = {}
        fronts = fronts_from(cap_days, cities, points, obs_end, rare_thr, fut_all, outlook_items)
        city_items = city_watch_items(cities, obs_end, city_thresholds(cities, cap_days), rare_items_all, season_now, rare_thr)
        cities_checked = any(c.get("fc") for c in cities["cities"])
        watch_items.update(city_items)
        for c in cities["cities"]:
            c.pop("fc", None)
            if c.get("days"): series[c["name"] + ", " + c["st"]] = {dt.date.fromisoformat(t): v for t, v in c["days"].items()}
        if cities_out: json.dump(cities, open(cities_out, "w"), separators=(",", ":"))
    # watches & warnings log: {map date: [groups]}; today's comes from this morning's forecast
    wlog = {}
    if watch_in:
        try: wlog = json.load(open(watch_in))
        except (OSError, ValueError): wlog = {}
    backfill_rare(wlog, series, rare_thr, obs_end)
    watch_items = apply_persistence(watch_items, wlog.get((obs_end - dt.timedelta(days=1)).isoformat()), obs_end, season_now)
    wlog[obs_end.isoformat()] = watch_entries(watch_items, bool(cities_in and points_path and cities_checked), rare_items_all)
    if cities_in and points_path and outlook_items:
        # outlook areas: [key, season, date, value] — season changes the CFS outlook shows in weeks 2-4
        wlog[obs_end.isoformat()]["o"] = [[k, s, d.isoformat(), v] for k, (s, d, v) in sorted(outlook_items.items())]
    data = dict(updated=now.strftime("%Y-%m-%dT%H:%M"), today=today.isoformat(), obsEnd=obs_end.isoformat(),
                fcEnd=fc_end.isoformat(), horizon=HORIZON, margin=MARGIN, nearDays=NEAR_DAYS, states=states,
                predLog=log, fronts=fronts, watchLog=wlog, watchDays=WATCH_DAYS, warnDays=WARN_DAYS,
                outlookEnd=max((f[-1][0] for f in fut_all.values() if f), default=fc_end).isoformat() if cfs else None,
                cfsRuns=cfs_runs, outlookFrom=OUTLOOK_FROM, outlookTo=OUTLOOK_TO)
    import os
    cpath = os.path.join(os.path.dirname(os.path.abspath(topo_path)), "counties-lite.json")   # county map for watches
    counties_json = open(cpath).read() if os.path.exists(cpath) else "null"
    html = open(tpl_path).read().replace("__TOPO__", open(topo_path).read()).replace(
        "__SEASONS__", json.dumps(data, separators=(",", ":"))).replace("__COUNTIES__", counties_json)
    open(out_path, "w").write(html)
    json.dump(hist, open(hist_out, "w"), separators=(",", ":"))
    if log_out: json.dump(log, open(log_out, "w"), separators=(",", ":"))
    if watch_out: json.dump(wlog, open(watch_out, "w"), separators=(",", ":"))
    print("wrote", out_path, "predictions:", sum(1 for s in states.values() if s["pred"]), file=sys.stderr)

if __name__ == "__main__":
    main(*sys.argv[1:13])
