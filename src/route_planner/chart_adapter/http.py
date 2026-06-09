"""chart-server HTTP source (httpx, sync) for targeted checks.

Wraps the aiohttp/nginx API at ``chart_server_url`` (docs/CHART-ADAPTER.md §2): identify / radius /
bbox / depth / grounding-line / catalog / layers. identify/radius/bbox return GeoJSON
FeatureCollections (normalized to ChartFeatures); depth returns a DepthInfo; the rest return their
raw JSON dicts. A custom ``httpx.Client`` (e.g. with MockTransport) can be injected for tests.
"""

from __future__ import annotations

from . import layers as L


class ChartServerHttp:
    """Sync client for the chart-server query API."""

    def __init__(self, base_url: str, client=None, timeout: float = 15.0) -> None:
        import httpx

        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def _get(self, path: str, params: dict) -> dict:
        resp = self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _features(fc: dict) -> list:
        return [L.feature_from_geojson(f) for f in (fc.get("features") or [])]

    def identify(self, lat: float, lon: float, radius: float = 50.0) -> list:
        return self._features(self._get("/api/identify", {"lat": lat, "lon": lon, "radius": radius}))

    def radius(self, lat: float, lon: float, meters: float) -> list:
        return self._features(self._get("/api/radius", {"lat": lat, "lon": lon, "radius": meters}))

    def bbox(self, bbox) -> list:
        """bbox = (west, south, east, north); the API takes s/w/n/e."""
        west, south, east, north = bbox
        fc = self._get("/api/bbox", {"s": south, "w": west, "n": north, "e": east})
        return self._features(fc)

    def depth(self, lat: float, lon: float) -> L.DepthInfo:
        return L.DepthInfo.from_api(self._get("/api/depth", {"lat": lat, "lon": lon}))

    def grounding_line(self, lat: float, lon: float, draft: float, ukc: float = 0.5) -> dict:
        return self._get(
            "/api/grounding-line", {"lat": lat, "lon": lon, "draft": draft, "ukc": ukc}
        )

    def catalog(self) -> dict:
        return self._get("/api/catalog", {})

    def layers(self) -> dict:
        return self._get("/api/layers", {})

    def close(self) -> None:
        self._client.close()
