"""
Source Workers v5
Individual source adapters for:
  - NASA FIRMS (wildfire satellite)
  - GDACS (disaster alerts)
  - USGS (earthquakes)
  - CISA KEV (cyber vulnerabilities)
  - EONET (NASA natural events)

Each worker inherits BaseIngestionWorker and implements fetch_raw().
All HTTP is async via aiohttp.
"""
from __future__ import annotations
import asyncio, json, logging, os, xml.etree.ElementTree as ET
from datetime import datetime, timezone
import aiohttp
import sys
sys.path.insert(0, '/home/claude/v5')
from ingestion.base_worker import BaseIngestionWorker
from schema.event_schema import SourceType, Domain

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=20)
_HEADERS = {"User-Agent": "SovereignIntelligence/5.0"}


# ══════════════════════════════════════════════════════════════════════════════
# NASA FIRMS — active fire detections (satellite)
# ══════════════════════════════════════════════════════════════════════════════

class NASAFIRMSWorker(BaseIngestionWorker):
    """
    NASA FIRMS VIIRS active fire detections.
    API returns CSV; we parse into normalized event dicts.
    Free API key required: firms.modaps.eosdis.nasa.gov/api/
    """
    SOURCE_NAME   = "NASA FIRMS"
    SOURCE_TYPE   = SourceType.SATELLITE
    POLL_INTERVAL = 900   # 15 min

    AREAS = [
        ("Russia",           "50,100,75,180"),
        ("Middle East",      "20,35,40,60"),
        ("Sub-Saharan Africa","5,-20,25,50"),
        ("Southeast Asia",   "-10,95,25,145"),
    ]

    async def fetch_raw(self) -> list[dict]:
        api_key = os.environ.get("FIRMS_API_KEY", "")
        if not api_key:
            logger.debug("[FIRMS] No API key — returning empty")
            return []

        results = []
        async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=_HEADERS) as session:
            for area_name, bbox in self.AREAS:
                url = (
                    f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{api_key}/VIIRS_SNPP_NRT"
                    f"/{bbox}/1/2024-01-01"
                )
                try:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            fires = self._parse_firms_csv(text, area_name)
                            results.extend(fires)
                except Exception as e:
                    logger.warning(f"[FIRMS] {area_name}: {e}")

        return results

    def _parse_firms_csv(self, text: str, area: str) -> list[dict]:
        lines = text.strip().splitlines()
        if len(lines) < 2:
            return []
        # Cluster fires: count detections per 1° grid cell
        clusters: dict[tuple, dict] = {}
        for line in lines[1:]:
            parts = line.split(",")
            if len(parts) < 5:
                continue
            try:
                lat   = round(float(parts[0]), 0)
                lng   = round(float(parts[1]), 0)
                frp   = float(parts[5]) if len(parts) > 5 else 50.0  # fire radiative power
                date  = parts[4] if len(parts) > 4 else ""
                key   = (lat, lng)
                if key not in clusters:
                    clusters[key] = {"lat": lat, "lng": lng, "count": 0, "max_frp": 0, "date": date, "area": area}
                clusters[key]["count"] += 1
                clusters[key]["max_frp"] = max(clusters[key]["max_frp"], frp)
            except (ValueError, IndexError):
                continue

        events = []
        for c in clusters.values():
            if c["count"] < 3:
                continue
            sev = min(92, int(50 + c["count"] * 2 + c["max_frp"] / 10))
            events.append({
                "title":    f"Active wildfire cluster ({c['count']} detections) — {c['area']}",
                "summary":  f"NASA FIRMS VIIRS: {c['count']} fire detections, max FRP={c['max_frp']:.0f} MW",
                "lat":      c["lat"], "lng": c["lng"],
                "region":   c["area"],
                "domain":   Domain.CLIMATE,
                "severity": sev,
                "date":     c["date"],
                "tags":     ["wildfire", "satellite", "firms"],
                "source":   "NASA FIRMS",
            })
        return events


# ══════════════════════════════════════════════════════════════════════════════
# GDACS — Global Disaster Alerting Coordination System
# ══════════════════════════════════════════════════════════════════════════════

class GDACSWorker(BaseIngestionWorker):
    SOURCE_NAME   = "GDACS"
    SOURCE_TYPE   = SourceType.INSTITUTIONAL
    POLL_INTERVAL = 600   # 10 min

    URL = "https://www.gdacs.org/xml/rss.xml"

    async def fetch_raw(self) -> list[dict]:
        async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=_HEADERS) as session:
            async with session.get(self.URL) as resp:
                text = await resp.text()

        root = ET.fromstring(text)
        ns   = {"gdacs": "http://www.gdacs.org"}
        items = root.findall(".//item")
        results = []
        for item in items:
            title   = (item.findtext("title") or "").strip()
            summary = (item.findtext("description") or "").strip()
            pubdate = item.findtext("pubDate") or ""
            link    = item.findtext("link") or ""
            lat = _find_float(item, "geo:lat") or _find_float(item, "{http://www.georss.org/georss}lat")
            lng = _find_float(item, "geo:long") or _find_float(item, "{http://www.georss.org/georss}long")
            # GDACS alert level: green/orange/red
            alertlevel = (item.findtext("gdacs:alertlevel", namespaces=ns) or "").lower()
            sev_map = {"red": 85, "orange": 70, "green": 55}
            severity = sev_map.get(alertlevel, 55)
            country = item.findtext("gdacs:country", namespaces=ns) or ""
            results.append({
                "title": title, "summary": summary,
                "date": pubdate, "source_url": link,
                "lat": lat, "lng": lng,
                "country": country, "region": country,
                "severity": severity,
                "verified": True,
                "tags": ["gdacs", alertlevel, "disaster"],
            })
        return results


# ══════════════════════════════════════════════════════════════════════════════
# USGS — Earthquakes M4.5+ last 7 days
# ══════════════════════════════════════════════════════════════════════════════

class USGSWorker(BaseIngestionWorker):
    SOURCE_NAME   = "USGS"
    SOURCE_TYPE   = SourceType.SCIENTIFIC
    POLL_INTERVAL = 600

    URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson"

    async def fetch_raw(self) -> list[dict]:
        async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=_HEADERS) as session:
            async with session.get(self.URL) as resp:
                data = await resp.json(content_type=None)

        results = []
        for feature in data.get("features", []):
            p   = feature.get("properties", {})
            geo = feature.get("geometry", {})
            coords = geo.get("coordinates", [None, None])
            mag   = p.get("mag", 0) or 0
            place = p.get("place", "") or ""
            ts    = p.get("time", 0)
            title = f"Earthquake M{mag:.1f} — {place}"
            sev   = min(95, int(40 + mag * 8))
            results.append({
                "title": title,
                "summary": f"USGS: Magnitude {mag}, depth {p.get('dmin','?')}km, {place}",
                "timestamp": ts,
                "lat": coords[1], "lng": coords[0],
                "region": place,
                "severity": sev,
                "magnitude": mag,
                "domain": Domain.CLIMATE,
                "verified": True,
                "tags": ["earthquake", "seismic", "usgs"],
            })
        return results


# ══════════════════════════════════════════════════════════════════════════════
# CISA KEV — Known Exploited Vulnerabilities
# ══════════════════════════════════════════════════════════════════════════════

class CISAKEVWorker(BaseIngestionWorker):
    SOURCE_NAME   = "CISA KEV"
    SOURCE_TYPE   = SourceType.GOVERNMENT
    POLL_INTERVAL = 3600   # 1 hour (catalog updates daily)

    URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"

    _last_seen: set[str] = set()

    async def fetch_raw(self) -> list[dict]:
        async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=_HEADERS) as session:
            async with session.get(self.URL) as resp:
                data = await resp.json(content_type=None)

        vulns = data.get("vulnerabilities", [])
        # Only new entries since last poll
        new_vulns = [v for v in vulns if v.get("cveID") not in self._last_seen]
        self._last_seen.update(v.get("cveID","") for v in vulns)

        results = []
        for v in new_vulns[-50:]:   # max 50 per cycle
            cve     = v.get("cveID", "")
            vendor  = v.get("vendorProject", "")
            product = v.get("product", "")
            due     = v.get("dueDate", "")
            desc    = v.get("shortDescription", "")
            results.append({
                "title":   f"CISA KEV: {cve} — {vendor} {product}",
                "summary": desc,
                "date":    v.get("dateAdded", ""),
                "domain":  Domain.TECHNOLOGY,
                "severity": 82,
                "verified": True,
                "tags":    ["cve", cve, vendor, "cisa", "vulnerability"],
            })
        return results


# ══════════════════════════════════════════════════════════════════════════════
# NASA EONET — Natural events
# ══════════════════════════════════════════════════════════════════════════════

class EONETWorker(BaseIngestionWorker):
    SOURCE_NAME   = "NASA EONET"
    SOURCE_TYPE   = SourceType.SATELLITE
    POLL_INTERVAL = 1800   # 30 min

    URL = "https://eonet.gsfc.nasa.gov/api/v3/events?status=open&limit=50"

    async def fetch_raw(self) -> list[dict]:
        async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=_HEADERS) as session:
            async with session.get(self.URL) as resp:
                data = await resp.json(content_type=None)

        results = []
        for event in data.get("events", []):
            title    = event.get("title", "")
            ev_date  = ""
            lat = lng = None
            geoms = event.get("geometry", [])
            if geoms:
                latest = geoms[-1]
                ev_date = latest.get("date", "")
                coords  = latest.get("coordinates", [])
                if len(coords) >= 2:
                    lng, lat = coords[0], coords[1]

            category = (event.get("categories") or [{}])[0].get("title", "")
            results.append({
                "title":   f"EONET: {title}",
                "summary": f"NASA EONET natural event: {category}",
                "date":    ev_date,
                "lat":     lat, "lng": lng,
                "domain":  Domain.CLIMATE,
                "severity": 65,
                "tags":    ["eonet", category.lower(), "natural"],
            })
        return results


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_float(elem, tag: str) -> float | None:
    text = elem.findtext(tag)
    if text:
        try:
            return float(text)
        except ValueError:
            pass
    return None


# ── Worker registry ───────────────────────────────────────────────────────────

ALL_WORKERS: list[type[BaseIngestionWorker]] = [
    NASAFIRMSWorker,
    GDACSWorker,
    USGSWorker,
    CISAKEVWorker,
    EONETWorker,
]
