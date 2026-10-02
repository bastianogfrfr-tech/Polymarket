"""Live station observations + forecasts -> probability for each temperature bucket.

Idea: late in the local day most of the daily high is already observed. The final
high is max(observed so far, high of the remaining hours). The remaining part comes
from weather models, corrected by how far the station currently deviates from them.
Daily lows work the same way on negated temperatures: min(x) = -max(-x).
"""
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev
from zoneinfo import ZoneInfo

import requests
from timezonefinder import TimezoneFinder

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "polybot/0.1 (personal research bot)"
_TF = TimezoneFinder()

OPEN_METEO_MODELS = "ecmwf_ifs025,icon_seamless,gfs_seamless,ukmo_seamless,meteofrance_seamless"


@dataclass
class Observation:
    station: str
    lat: float
    lon: float
    tz: ZoneInfo
    local_now: datetime
    obs_max_c: float        # highest reading so far on the local day
    obs_min_c: float        # lowest reading so far on the local day
    last_temp_c: float
    last_time: datetime     # UTC
    n_obs: int


@dataclass
class Estimate:
    obs: Observation
    kind: str               # "high" or "low"
    mu_c: float             # expected extreme of the remaining hours, sign-flipped for
                            # "low" (-inf if the day is over)
    sigma_c: float
    remaining_hours: float
    hours_to_peak: float
    bias_c: float
    source: str

    @property
    def sign(self):
        return 1 if self.kind == "high" else -1

    def prob(self, lo, hi, unit):
        """P(rounded daily high/low in [lo, hi]) in the market's unit."""
        s = self.sign
        if s < 0:  # work on negated temperatures
            lo, hi = -hi, -lo
        conv = (lambda c: c * 9 / 5 + 32 * s) if unit == "F" else (lambda c: c)
        scale = 9 / 5 if unit == "F" else 1.0
        floor = conv(s * (self.obs.obs_max_c if s > 0 else self.obs.obs_min_c))
        mu = conv(self.mu_c) if self.mu_c != float("-inf") else float("-inf")
        sigma = self.sigma_c * scale

        def cdf(v):  # P(X < v), X = max(floor, Y)
            if v <= floor:
                return 0.0
            if mu == float("-inf") or v == float("inf"):
                return 1.0
            return 0.5 * (1 + math.erf((v - mu) / (sigma * math.sqrt(2))))

        return max(0.0, cdf(hi + 0.5) - cdf(lo - 0.5))


def observe(station, day):
    """METAR readings of the station for the local calendar day `day`."""
    r = SESSION.get("https://aviationweather.gov/api/data/metar",
                    params={"ids": station, "hours": 36, "format": "json"}, timeout=30)
    r.raise_for_status()
    rows = [x for x in r.json() if x.get("temp") is not None]
    if not rows:
        return None
    lat, lon = rows[0]["lat"], rows[0]["lon"]
    tz = ZoneInfo(_TF.timezone_at(lat=lat, lng=lon) or "UTC")
    today = []
    for x in rows:
        t = datetime.fromtimestamp(x["obsTime"], timezone.utc)
        if t.astimezone(tz).date() == day:
            today.append((t, float(x["temp"])))
    if not today:
        return None
    today.sort()
    return Observation(station, lat, lon, tz, datetime.now(tz),
                       max(v for _, v in today), min(v for _, v in today),
                       today[-1][1], today[-1][0], len(today))


def _forecast_open_meteo(lat, lon):
    r = SESSION.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": lat, "longitude": lon, "hourly": "temperature_2m",
        "models": OPEN_METEO_MODELS, "timezone": "GMT", "forecast_days": 3}, timeout=30)
    r.raise_for_status()
    h = r.json()["hourly"]
    times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in h["time"]]
    series = [v for k, v in h.items() if k.startswith("temperature_2m")]
    out = {}
    for i, t in enumerate(times):
        vals = [s[i] for s in series if s[i] is not None]
        if vals:
            out[t] = vals
    return out


def _forecast_met_no(lat, lon):
    r = SESSION.get("https://api.met.no/weatherapi/locationforecast/2.0/compact",
                    params={"lat": round(lat, 3), "lon": round(lon, 3)}, timeout=30)
    r.raise_for_status()
    out = {}
    for ts in r.json()["properties"]["timeseries"]:
        t = datetime.fromisoformat(ts["time"].replace("Z", "+00:00"))
        out[t] = [ts["data"]["instant"]["details"]["air_temperature"]]
    return out


_FC_CACHE = {}
FC_TTL = 3600


def forecast(lat, lon):
    """{utc_hour: [model temps]}; Open-Meteo multi-model, MET Norway as fallback.
    Cached for an hour so frequent scans stay within the free API limits."""
    key = (round(lat, 2), round(lon, 2))
    hit = _FC_CACHE.get(key)
    if hit and time.time() - hit[0] < FC_TTL:
        return hit[1], hit[2]
    try:
        fc, source = _forecast_open_meteo(lat, lon), "open-meteo"
    except Exception:
        fc = {}
    if not fc:
        fc, source = _forecast_met_no(lat, lon), "met.no"
    _FC_CACHE[key] = (time.time(), fc, source)
    return fc, source


def estimate(obs, day, kind="high"):
    fc, source = forecast(obs.lat, obs.lon)
    if not fc:
        return None
    s = 1 if kind == "high" else -1
    fc = {t: [s * v for v in vals] for t, vals in fc.items()}

    # Station vs. model at the latest observation.
    nearest = min(fc, key=lambda t: abs(t - obs.last_time))
    bias = 0.0
    if abs(nearest - obs.last_time) <= timedelta(hours=1):
        bias = max(-3.0, min(3.0, s * obs.last_temp_c - mean(fc[nearest])))

    now = datetime.now(timezone.utc)
    end_of_day = datetime.combine(day + timedelta(days=1), datetime.min.time(), obs.tz)
    remaining = sorted(t for t in fc if now < t < end_of_day)
    rem_hours = max(0.0, (end_of_day - now).total_seconds() / 3600)

    if not remaining:
        return Estimate(obs, kind, float("-inf"), 0.3, rem_hours, 0.0, bias, source)

    def corrected(t, v):  # bias fades out over ~6 hours
        w = 0.8 * math.exp(-(t - now).total_seconds() / 3600 / 6)
        return v + w * bias

    path = [(t, corrected(t, mean(fc[t]))) for t in remaining]
    peak_t, mu = max(path, key=lambda p: p[1])
    hours_to_peak = max(0.0, (peak_t - now).total_seconds() / 3600)
    n_models = min(len(fc[t]) for t in remaining)
    per_model = [max(corrected(t, fc[t][i]) for t in remaining) for i in range(n_models)]
    spread = pstdev(per_model) if len(per_model) > 1 else 0.8
    sigma = math.sqrt(min(3.0, 0.5 + 0.3 * hours_to_peak) ** 2 + spread ** 2)
    return Estimate(obs, kind, mu, sigma, rem_hours, hours_to_peak, bias, source)
