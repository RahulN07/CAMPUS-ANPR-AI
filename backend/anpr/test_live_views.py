"""API tests for authenticated latest-frame delivery."""

from __future__ import annotations

from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from access_management.models import Gate
from accounts.models import User
from anpr.live_transport import LiveFrameSnapshot
from anpr.live_views import GateLiveFrameView


class LiveFrameCorsRegressionTests(SimpleTestCase):
    def test_preflight_allows_etag_conditional_request_header(self):
        response = self.client.options(
            "/api/anpr/gates/1/live-frame/",
            HTTP_ORIGIN="http://localhost:5173",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS=(
                "authorization, content-type, if-none-match"
            ),
        )

        self.assertEqual(response.status_code, 200)
        allowed_headers = response.get(
            "Access-Control-Allow-Headers",
            "",
        ).lower()
        self.assertIn("authorization", allowed_headers)
        self.assertIn("if-none-match", allowed_headers)


class FakeFrameTransport:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot
        self.requested_gate_ids = []

    def get_latest_frame(self, gate_id):
        self.requested_gate_ids.append(gate_id)
        return self.snapshot


class GateLiveFrameViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="live-viewer",
            password="StrongPassword123!",
            role=User.Role.VIEWER,
        )
        cls.gate = Gate.objects.create(
            name="Live Main Gate",
            gate_type=Gate.GateType.ENTRY,
            is_active=True,
        )
        cls.inactive_gate = Gate.objects.create(
            name="Inactive Gate",
            gate_type=Gate.GateType.EXIT,
            is_active=False,
        )

    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = GateLiveFrameView.as_view()

    def authenticated_request(self, *, etag=None):
        headers = {}
        if etag is not None:
            headers["HTTP_IF_NONE_MATCH"] = etag
        request = self.factory.get("/api/anpr/live/", **headers)
        force_authenticate(request, user=self.user)
        return request

    @staticmethod
    def snapshot(**metadata_overrides):
        metadata = {
            "fps": 9.75,
            "vehicle_count": 3,
            "tracked_count": 2,
        }
        metadata.update(metadata_overrides)
        return LiveFrameSnapshot(
            gate_id=1,
            sequence=1784569885413315101,
            published_at="2026-07-21T08:30:00+00:00",
            jpeg=b"\xff\xd8test-jpeg\xff\xd9",
            metadata=metadata,
        )

    def test_authentication_is_required(self):
        request = self.factory.get("/api/anpr/live/")

        response = self.view(request, gate_id=self.gate.id)

        self.assertEqual(response.status_code, 401)

    @patch("anpr.live_views.get_live_transport")
    def test_active_gate_returns_latest_jpeg_and_metadata_headers(
        self,
        get_transport,
    ):
        transport = FakeFrameTransport(self.snapshot())
        get_transport.return_value = transport

        response = self.view(
            self.authenticated_request(),
            gate_id=self.gate.id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"\xff\xd8test-jpeg\xff\xd9")
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertEqual(response["Content-Length"], str(len(response.content)))
        self.assertEqual(response["ETag"], '"1784569885413315101"')
        self.assertEqual(
            response["X-ANPR-Frame-Sequence"],
            "1784569885413315101",
        )
        self.assertEqual(response["X-ANPR-FPS"], "9.75")
        self.assertEqual(response["X-ANPR-Vehicle-Count"], "3")
        self.assertEqual(response["X-ANPR-Tracked-Count"], "2")
        self.assertEqual(transport.requested_gate_ids, [self.gate.id])

        cache_control = response["Cache-Control"]
        self.assertIn("private", cache_control)
        self.assertIn("no-store", cache_control)
        self.assertIn("no-cache", cache_control)
        self.assertIn("must-revalidate", cache_control)
        self.assertIn("Authorization", response["Vary"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    @patch("anpr.live_views.get_live_transport")
    def test_matching_etag_returns_not_modified_without_jpeg_body(
        self,
        get_transport,
    ):
        get_transport.return_value = FakeFrameTransport(self.snapshot())
        etag = '"1784569885413315101"'

        response = self.view(
            self.authenticated_request(etag=etag),
            gate_id=self.gate.id,
        )

        self.assertEqual(response.status_code, 304)
        self.assertEqual(response.content, b"")
        self.assertEqual(response["ETag"], etag)
        self.assertIn("no-store", response["Cache-Control"])

    @patch("anpr.live_views.get_live_transport")
    def test_active_gate_without_live_frame_returns_no_content(
        self,
        get_transport,
    ):
        transport = FakeFrameTransport(snapshot=None)
        get_transport.return_value = transport

        response = self.view(
            self.authenticated_request(),
            gate_id=self.gate.id,
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertEqual(transport.requested_gate_ids, [self.gate.id])
        self.assertIn("no-store", response["Cache-Control"])

    @patch("anpr.live_views.get_live_transport")
    def test_inactive_gate_returns_not_found_without_redis_read(
        self,
        get_transport,
    ):
        transport = Mock()
        get_transport.return_value = transport

        response = self.view(
            self.authenticated_request(),
            gate_id=self.inactive_gate.id,
        )

        self.assertEqual(response.status_code, 404)
        get_transport.assert_not_called()
        transport.get_latest_frame.assert_not_called()

    @patch("anpr.live_views.get_live_transport")
    def test_missing_gate_returns_not_found_without_redis_read(
        self,
        get_transport,
    ):
        response = self.view(
            self.authenticated_request(),
            gate_id=999999,
        )

        self.assertEqual(response.status_code, 404)
        get_transport.assert_not_called()

    @patch("anpr.live_views.get_live_transport")
    def test_non_numeric_metadata_is_not_copied_to_response_headers(
        self,
        get_transport,
    ):
        get_transport.return_value = FakeFrameTransport(
            self.snapshot(
                fps="unknown",
                vehicle_count=True,
                tracked_count=None,
            )
        )

        response = self.view(
            self.authenticated_request(),
            gate_id=self.gate.id,
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("X-ANPR-FPS", response)
        self.assertNotIn("X-ANPR-Vehicle-Count", response)
        self.assertNotIn("X-ANPR-Tracked-Count", response)