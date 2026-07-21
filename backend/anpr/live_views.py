"""Authenticated HTTP access to the newest live ANPR frame."""

from __future__ import annotations

from typing import Any

from django.http import HttpResponse, HttpResponseNotModified
from django.utils.cache import patch_cache_control, patch_vary_headers
from rest_framework import permissions
from rest_framework.views import APIView

from access_management.models import Gate
from anpr.live_transport import get_live_transport


EXPOSED_FRAME_HEADERS = (
    "ETag, X-ANPR-Frame-Sequence, X-ANPR-Published-At, "
    "X-ANPR-FPS, X-ANPR-Vehicle-Count, X-ANPR-Tracked-Count"
)


class GateLiveFrameView(APIView):
    """Return the latest annotated JPEG for one active campus gate.

    The endpoint is intentionally a latest-value read rather than an MJPEG
    connection. Normal JWT authentication therefore protects every request,
    and slow clients cannot create a frame backlog in Django or Redis.
    """

    permission_classes = [permissions.IsAuthenticated]
    authentication_classes = APIView.authentication_classes
    renderer_classes = APIView.renderer_classes

    def get(self, request, gate_id: int):
        if not Gate.objects.filter(pk=gate_id, is_active=True).exists():
            return self._empty_response(status_code=404)

        snapshot = get_live_transport().get_latest_frame(gate_id)
        if snapshot is None:
            return self._empty_response(status_code=204)

        etag = f'"{snapshot.sequence}"'
        if request.headers.get("If-None-Match") == etag:
            response = HttpResponseNotModified()
        else:
            response = HttpResponse(
                snapshot.jpeg,
                content_type="image/jpeg",
                status=200,
            )
            response["Content-Length"] = str(len(snapshot.jpeg))

        response["ETag"] = etag
        response["X-ANPR-Frame-Sequence"] = str(snapshot.sequence)
        response["X-ANPR-Published-At"] = snapshot.published_at
        self._copy_numeric_header(
            response,
            "X-ANPR-FPS",
            snapshot.metadata.get("fps"),
        )
        self._copy_numeric_header(
            response,
            "X-ANPR-Vehicle-Count",
            snapshot.metadata.get("vehicle_count"),
        )
        self._copy_numeric_header(
            response,
            "X-ANPR-Tracked-Count",
            snapshot.metadata.get("tracked_count"),
        )
        return self._secure_response(response)

    @classmethod
    def _empty_response(cls, *, status_code: int) -> HttpResponse:
        response = HttpResponse(status=status_code)
        return cls._secure_response(response)

    @staticmethod
    def _copy_numeric_header(
        response: HttpResponse,
        header: str,
        value: Any,
    ) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        response[header] = str(value)

    @staticmethod
    def _secure_response(response: HttpResponse) -> HttpResponse:
        patch_cache_control(
            response,
            private=True,
            no_store=True,
            no_cache=True,
            must_revalidate=True,
        )
        patch_vary_headers(response, ("Authorization",))
        response["Pragma"] = "no-cache"
        response["X-Content-Type-Options"] = "nosniff"
        response["Access-Control-Expose-Headers"] = EXPOSED_FRAME_HEADERS
        return response