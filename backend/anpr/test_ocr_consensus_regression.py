from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
from django.test import SimpleTestCase

from anpr.detector import run_conflict_ocr
from anpr.track_buffer import VehicleFrameCandidate
from anpr.vehicle_processor import (
    PlateObservation,
    VehicleProcessor,
    VehicleProcessorConfig,
)


class ConflictOcrEnsembleRegressionTests(SimpleTestCase):
    @patch("anpr.detector.get_conflict_text_recognizer")
    def test_frame_vote_prefers_largest_valid_variant_group(
        self,
        get_recognizer,
    ):
        recognizer = Mock()
        recognizer.predict.return_value = [
            {"rec_text": "KA 02 MN 182G", "rec_score": 0.94},
            {"rec_text": "KA 02 MN 1826", "rec_score": 0.93},
            {"rec_text": "KA 02 MN 182G", "rec_score": 0.92},
            {"rec_text": "KA 02 HN 182G", "rec_score": 0.96},
            {"rec_text": "KA 02 HN 182G", "rec_score": 0.95},
        ]
        get_recognizer.return_value = recognizer

        raw_text, confidence = run_conflict_ocr(
            np.full((30, 110, 3), 255, dtype=np.uint8)
        )

        self.assertIn("MN", raw_text.replace(" ", ""))
        self.assertAlmostEqual(confidence, 0.93, places=2)
        recognizer.predict.assert_called_once()


class CrossFrameConflictRegressionTests(SimpleTestCase):
    def setUp(self):
        self.config = VehicleProcessorConfig(
            gate_id=1,
            direction="ENTRY",
            recorded_by_id=1,
            maximum_candidates=2,
            required_unknown_votes=2,
            evaluate_all_unknown_candidates=True,
            conflict_track_votes=2,
        )
        success, encoded = cv2.imencode(
            ".jpg",
            np.full((30, 110, 3), 255, dtype=np.uint8),
        )
        self.assertTrue(success)
        self.plate_bytes = encoded.tobytes()

    def _observation(self, frame_index):
        candidate = VehicleFrameCandidate(
            track_id=6,
            frame_index=frame_index,
            captured_at=float(frame_index),
            vehicle_type="car",
            vehicle_confidence=0.90,
            source_bbox=(0, 0, 300, 200),
            sharpness=100.0,
            quality_score=10.0,
            crop=np.full((200, 300, 3), 255, dtype=np.uint8),
        )
        return PlateObservation(
            plate_text="KA02HN1826",
            raw_text="KA 02 HN 1826",
            confidence=0.92,
            plate_yolo_confidence=0.90,
            ocr_confidence=0.94,
            corrections=0,
            bounding_box=(0, 0, 110, 30),
            plate_image_bytes=self.plate_bytes,
            candidate=candidate,
        )

    def _processor(self, readings):
        iterator = iter(readings)
        return VehicleProcessor(
            config=self.config,
            cache=SimpleNamespace(),
            conflict_recognizer=lambda image: next(iterator),
        )

    def test_two_v6_frames_can_override_one_mobile_character(self):
        observations = [self._observation(10), self._observation(11)]
        processor = self._processor(
            [
                ("KA 02 MN 182G", 0.94),
                ("KA 02 MN 1826", 0.93),
            ]
        )

        result = processor._resolve_unknown_identity_conflict(
            {"KA02HN1826": observations}
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.plate_text, "KA02MN1826")

    def test_one_v6_frame_is_not_enough_to_override_mobile(self):
        observations = [self._observation(10), self._observation(11)]
        processor = self._processor(
            [
                ("KA 02 MN 182G", 0.94),
                ("", 0.0),
            ]
        )

        result = processor._resolve_unknown_identity_conflict(
            {"KA02HN1826": observations}
        )

        self.assertIsNone(result)

    def test_full_evaluation_fails_closed_after_v6_rejection(self):
        observations = [self._observation(10), self._observation(11)]
        processor = self._processor(
            [
                ("", 0.0),
                ("", 0.0),
            ]
        )

        result, votes = processor._choose_unknown_consensus(observations)

        self.assertIsNone(result)
        self.assertEqual(votes, 2)
