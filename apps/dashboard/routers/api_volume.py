"""GET /api/debug/api-volume — API call-volume and cache-hit observability (issue #1787)."""
from fastapi import APIRouter

router = APIRouter(tags=["debug"])


@router.get("/api/debug/api-volume")
def get_api_volume(n: int = 20):
    """Return in-process call-volume counters and cache hit/miss stats.

    Query params:
      n — number of top paths to return (default 20)
    """
    import api_volume as _av  # noqa: PLC0415
    return _av.get_snapshot(top_n=n)
