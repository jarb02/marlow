"""Tests for marlow.kernel.sensor_fusion — Sensor Fusion Framework."""

import pytest
from marlow.kernel.sensor_fusion import (
    BoundingBox,
    DetectedElement,
    FusedElement,
    SensorFusion,
    SensorTier,
)


# ------------------------------------------------------------------
# SensorTier
# ------------------------------------------------------------------

class TestSensorTier:
    def test_sensor_tier_ordering(self):
        assert SensorTier.UIA < SensorTier.CDP < SensorTier.OCR
        assert SensorTier.OCR < SensorTier.SCREENSHOT < SensorTier.VLM


# ------------------------------------------------------------------
# BoundingBox
# ------------------------------------------------------------------

class TestBoundingBox:
    def test_bounding_box_width_height(self):
        bb = BoundingBox(10, 20, 110, 70)
        assert bb.width == 100
        assert bb.height == 50

    def test_bounding_box_center(self):
        bb = BoundingBox(0, 0, 100, 100)
        assert bb.center == (50, 50)

    def test_bounding_box_area(self):
        bb = BoundingBox(0, 0, 10, 20)
        assert bb.area == 200

    def test_bounding_box_overlaps_true(self):
        a = BoundingBox(0, 0, 100, 100)
        b = BoundingBox(50, 50, 150, 150)
        assert a.overlaps(b) is True
        assert b.overlaps(a) is True

    def test_bounding_box_overlaps_false(self):
        a = BoundingBox(0, 0, 50, 50)
        b = BoundingBox(100, 100, 200, 200)
        assert a.overlaps(b) is False

    def test_bounding_box_overlap_ratio_full(self):
        a = BoundingBox(0, 0, 100, 100)
        assert a.overlap_ratio(a) == 1.0

    def test_bounding_box_overlap_ratio_none(self):
        a = BoundingBox(0, 0, 50, 50)
        b = BoundingBox(200, 200, 300, 300)
        assert a.overlap_ratio(b) == 0.0

    def test_bounding_box_overlap_ratio_partial(self):
        a = BoundingBox(0, 0, 100, 100)
        b = BoundingBox(50, 50, 150, 150)
        ratio = a.overlap_ratio(b)
        assert 0.0 < ratio < 1.0
        # Intersection = 50x50 = 2500, Union = 10000+10000-2500 = 17500
        assert abs(ratio - 2500 / 17500) < 0.01

    def test_bounding_box_frozen(self):
        bb = BoundingBox(0, 0, 10, 10)
        with pytest.raises(AttributeError):
            bb.left = 5


# ------------------------------------------------------------------
# DetectedElement
# ------------------------------------------------------------------

class TestDetectedElement:
    def test_detected_element_is_interactive_button(self):
        e = DetectedElement(name="OK", element_type="button")
        assert e.is_interactive is True

    def test_detected_element_is_interactive_text(self):
        e = DetectedElement(name="Hello", element_type="text")
        assert e.is_interactive is False

    def test_detected_element_is_clickable(self):
        bb = BoundingBox(0, 0, 50, 30)
        e = DetectedElement(name="OK", element_type="button", bbox=bb)
        assert e.is_clickable is True

    def test_detected_element_not_clickable_no_bbox(self):
        e = DetectedElement(name="OK", element_type="button")
        assert e.is_clickable is False


# ------------------------------------------------------------------
# FusedElement
# ------------------------------------------------------------------

class TestFusedElement:
    def test_fused_element_source_count(self):
        f = FusedElement(
            name="OK", element_type="button",
            sources=[SensorTier.UIA, SensorTier.OCR],
        )
        assert f.source_count == 2

    def test_fused_element_best_source(self):
        f = FusedElement(
            name="OK", element_type="button",
            sources=[SensorTier.OCR, SensorTier.UIA],
        )
        assert f.best_source == SensorTier.UIA

    def test_fused_element_best_source_empty(self):
        f = FusedElement(name="X", element_type="text")
        assert f.best_source is None


# ------------------------------------------------------------------
# SensorFusion — add + merge
# ------------------------------------------------------------------

class TestSensorFusionAdd:
    def setup_method(self):
        self.fusion = SensorFusion()

    def test_add_single_detection(self):
        bb = BoundingBox(10, 10, 60, 40)
        self.fusion.add_detections([
            DetectedElement("OK", "button", bbox=bb, source=SensorTier.UIA),
        ])
        elems = self.fusion.get_elements()
        assert len(elems) == 1
        assert elems[0].name == "OK"
        assert SensorTier.UIA in elems[0].sources

    def test_add_merge_overlapping(self):
        """Two detections with high IoU merge into one."""
        bb1 = BoundingBox(10, 10, 60, 40)
        bb2 = BoundingBox(12, 12, 62, 42)  # almost same position
        self.fusion.add_detections([
            DetectedElement("OK", "button", bbox=bb1, confidence=0.9, source=SensorTier.UIA),
        ])
        self.fusion.add_detections([
            DetectedElement("OK", "button", bbox=bb2, confidence=0.8, source=SensorTier.OCR),
        ])
        elems = self.fusion.get_elements()
        assert len(elems) == 1
        assert SensorTier.UIA in elems[0].sources
        assert SensorTier.OCR in elems[0].sources

    def test_no_merge_distant(self):
        """Two detections far apart stay separate."""
        bb1 = BoundingBox(0, 0, 50, 30)
        bb2 = BoundingBox(500, 500, 550, 530)
        self.fusion.add_detections([
            DetectedElement("OK", "button", bbox=bb1, source=SensorTier.UIA),
        ])
        self.fusion.add_detections([
            DetectedElement("Cancel", "button", bbox=bb2, source=SensorTier.UIA),
        ])
        assert len(self.fusion.get_elements()) == 2


# ------------------------------------------------------------------
# SensorFusion — find + interactive
# ------------------------------------------------------------------

class TestSensorFusionFind:
    def setup_method(self):
        self.fusion = SensorFusion()
        self.fusion.add_detections([
            DetectedElement("OK", "button", BoundingBox(10, 10, 60, 40), source=SensorTier.UIA),
            DetectedElement("Cancel", "button", BoundingBox(70, 10, 140, 40), source=SensorTier.UIA),
            DetectedElement("Status: Ready", "text", BoundingBox(10, 50, 200, 70), source=SensorTier.OCR),
        ])

    def test_find_by_name(self):
        found = self.fusion.find("OK")
        assert len(found) == 1
        assert found[0].name == "OK"

    def test_find_interactive(self):
        interactive = self.fusion.find_interactive()
        assert len(interactive) == 2
        names = {e.name for e in interactive}
        assert names == {"OK", "Cancel"}


# ------------------------------------------------------------------
# SensorFusion — confidence + clear + escalation + stats
# ------------------------------------------------------------------

class TestSensorFusionAdvanced:
    def test_confidence_boost_multi_source(self):
        fusion = SensorFusion()
        bb = BoundingBox(10, 10, 60, 40)
        fusion.add_detections([
            DetectedElement("OK", "button", bbox=bb, confidence=0.7, source=SensorTier.UIA),
        ])
        fusion.add_detections([
            DetectedElement("OK", "button", bbox=bb, confidence=0.7, source=SensorTier.OCR),
        ])
        elem = fusion.get_elements()[0]
        assert elem.confidence > 0.7  # boosted by multi-source

    def test_clear(self):
        fusion = SensorFusion()
        fusion.add_detections([
            DetectedElement("OK", "button", source=SensorTier.UIA),
        ])
        assert len(fusion.get_elements()) == 1
        fusion.clear()
        assert len(fusion.get_elements()) == 0

    def test_needs_escalation_not_found(self):
        fusion = SensorFusion()
        # No elements at all -> suggest UIA (tier 0)
        tier = fusion.needs_escalation("Submit")
        assert tier == SensorTier.UIA

    def test_needs_escalation_found(self):
        fusion = SensorFusion()
        fusion.add_detections([
            DetectedElement("Submit", "button", BoundingBox(0, 0, 50, 30),
                            confidence=0.95, source=SensorTier.UIA),
        ])
        tier = fusion.needs_escalation("Submit")
        assert tier is None  # found with high confidence

    def test_needs_escalation_all_used(self):
        fusion = SensorFusion()
        # Add elements from every tier so all are "used"
        for t in SensorTier:
            fusion.add_detections([
                DetectedElement(f"elem_{t.name}", "text", confidence=0.3, source=t),
            ])
        tier = fusion.needs_escalation("NotFound")
        assert tier is None  # all sensors exhausted

    def test_stats(self):
        fusion = SensorFusion()
        bb = BoundingBox(10, 10, 60, 40)
        fusion.add_detections([
            DetectedElement("OK", "button", bbox=bb, source=SensorTier.UIA),
            DetectedElement("Cancel", "button", source=SensorTier.UIA),
        ])
        fusion.add_detections([
            DetectedElement("OK", "button", bbox=bb, confidence=0.8, source=SensorTier.OCR),
        ])
        stats = fusion.stats
        assert stats["total_elements"] == 2  # OK merged, Cancel separate
        assert stats["sensor_calls"]["UIA"] == 2
        assert stats["sensor_calls"]["OCR"] == 1
        assert stats["multi_source"] == 1  # OK has UIA+OCR
