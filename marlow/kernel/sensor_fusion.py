"""SensorFusion — unified element detection from multiple sensors.

Merges detections from UIA, CDP, OCR, Screenshot, and VLM into a single
representation with bounding boxes, confidence scores, and sensor-specific
metadata. Cascades from cheapest to most expensive sensor.

Tier 0 (UIA): Always run first. Free, fast, gives structure.
Tier 1 (CDP): For Electron apps. Free, gives full DOM.
Tier 2 (OCR): When UIA misses text. Free but slower.
Tier 3 (Screenshot): When need visual context. Free but slow.
Tier 4 (VLM): Last resort. Expensive but understands anything.

Based on: "UIA gives structure, OCR gives text, CV gives context."

/ Fusion de sensores: representacion unificada de elementos UI.
"""

import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

logger = logging.getLogger("marlow.kernel.sensor_fusion")


class SensorTier(IntEnum):
    """Sensor cost tiers. Lower = cheaper/faster."""

    UIA = 0        # UI Automation tree — free, ~10-50ms
    CDP = 1        # Chrome DevTools Protocol — free, ~5-20ms (Electron only)
    OCR = 2        # Windows OCR — free, ~50-500ms
    SCREENSHOT = 3 # Screenshot + analysis — free, ~100-500ms
    VLM = 4        # Vision Language Model — ~1500 tokens, ~1-3s


@dataclass(frozen=True)
class BoundingBox:
    """Screen coordinates of a detected element."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def center(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)

    @property
    def area(self) -> int:
        return max(0, self.width * self.height)

    def overlaps(self, other: "BoundingBox") -> bool:
        """Check if two bounding boxes overlap."""
        return not (
            self.right <= other.left
            or self.left >= other.right
            or self.bottom <= other.top
            or self.top >= other.bottom
        )

    def overlap_ratio(self, other: "BoundingBox") -> float:
        """Calculate IoU (Intersection over Union) with another bbox."""
        if not self.overlaps(other):
            return 0.0
        inter_left = max(self.left, other.left)
        inter_top = max(self.top, other.top)
        inter_right = min(self.right, other.right)
        inter_bottom = min(self.bottom, other.bottom)
        inter_area = (
            max(0, inter_right - inter_left) * max(0, inter_bottom - inter_top)
        )
        union_area = self.area + other.area - inter_area
        if union_area <= 0:
            return 0.0
        return inter_area / union_area


@dataclass(frozen=True)
class DetectedElement:
    """A UI element detected by one or more sensors."""

    name: str                     # element name/text
    element_type: str             # button, edit, text, checkbox, etc.
    bbox: Optional[BoundingBox] = None
    confidence: float = 1.0       # 0-1, how confident in detection
    source: SensorTier = SensorTier.UIA
    properties: dict = field(default_factory=dict)

    @property
    def is_interactive(self) -> bool:
        return self.element_type in (
            "button", "edit", "checkbox", "combobox", "link",
            "menuitem", "tab", "slider", "radio", "toggle",
        )

    @property
    def is_clickable(self) -> bool:
        return self.bbox is not None and self.is_interactive


@dataclass
class FusedElement:
    """Element merged from multiple sensor sources.

    Contains the best data from each sensor that detected it.
    """

    name: str
    element_type: str
    bbox: Optional[BoundingBox] = None
    sources: list[SensorTier] = field(default_factory=list)
    confidence: float = 0.0
    uia_data: dict = field(default_factory=dict)   # from UIA tree
    ocr_text: str = ""                              # from OCR
    cdp_selector: str = ""                          # from CDP DOM
    properties: dict = field(default_factory=dict)

    @property
    def source_count(self) -> int:
        return len(self.sources)

    @property
    def best_source(self) -> Optional[SensorTier]:
        return min(self.sources) if self.sources else None


class SensorFusion:
    """Merges element detections from multiple sensors into unified view.

    Strategy: cascade from cheapest to most expensive sensor.
    Stop when confidence is high enough.
    """

    # IoU threshold for considering two detections the same element
    MERGE_IOU_THRESHOLD = 0.5

    # Confidence threshold to stop escalating
    CONFIDENCE_SUFFICIENT = 0.8

    def __init__(self):
        self._elements: list[FusedElement] = []
        self._sensor_stats: dict[str, int] = {}

    def add_detections(self, elements: list[DetectedElement]):
        """Add detected elements from a sensor.

        Merges with existing elements based on bounding box overlap.
        """
        for elem in elements:
            merged = False
            if elem.bbox:
                for fused in self._elements:
                    if (
                        fused.bbox
                        and elem.bbox.overlap_ratio(fused.bbox)
                        >= self.MERGE_IOU_THRESHOLD
                    ):
                        self._merge_into(fused, elem)
                        merged = True
                        break

            if not merged:
                fused = FusedElement(
                    name=elem.name,
                    element_type=elem.element_type,
                    bbox=elem.bbox,
                    sources=[elem.source],
                    confidence=elem.confidence,
                    properties=dict(elem.properties),
                )
                if elem.source == SensorTier.UIA:
                    fused.uia_data = dict(elem.properties)
                elif elem.source == SensorTier.OCR:
                    fused.ocr_text = elem.name
                elif elem.source == SensorTier.CDP:
                    fused.cdp_selector = elem.properties.get("selector", "")
                self._elements.append(fused)

            tier_name = elem.source.name
            self._sensor_stats[tier_name] = self._sensor_stats.get(tier_name, 0) + 1

    def _merge_into(self, fused: FusedElement, new: DetectedElement):
        """Merge a new detection into an existing fused element."""
        if new.source not in fused.sources:
            fused.sources.append(new.source)

        # Multi-source = higher confidence
        fused.confidence = min(1.0, fused.confidence + new.confidence * 0.3)

        # Update bbox if new one is better
        if new.bbox and (
            fused.bbox is None or new.confidence > fused.confidence - 0.3
        ):
            fused.bbox = new.bbox

        if new.source == SensorTier.UIA:
            fused.uia_data.update(new.properties)
        elif new.source == SensorTier.OCR:
            fused.ocr_text = new.name
        elif new.source == SensorTier.CDP:
            fused.cdp_selector = new.properties.get("selector", "")

        fused.properties.update(new.properties)

    def find(self, name: str, element_type: str = "") -> list[FusedElement]:
        """Find elements by name (fuzzy) and optionally type."""
        name_lower = name.lower()
        results = []
        for elem in self._elements:
            if name_lower in elem.name.lower() or name_lower in elem.ocr_text.lower():
                if not element_type or elem.element_type == element_type:
                    results.append(elem)
        return sorted(results, key=lambda e: e.confidence, reverse=True)

    def find_interactive(self) -> list[FusedElement]:
        """Get all interactive elements (buttons, edits, etc.)."""
        return [
            e for e in self._elements
            if e.element_type in (
                "button", "edit", "checkbox", "combobox", "link",
                "menuitem", "tab", "slider", "radio", "toggle",
            )
        ]

    def get_elements(self) -> list[FusedElement]:
        """Get all fused elements."""
        return list(self._elements)

    def clear(self):
        """Clear all elements (start fresh scan)."""
        self._elements.clear()

    def needs_escalation(self, target_name: str) -> Optional[SensorTier]:
        """Determine if we need a more expensive sensor to find a target.

        Returns the next sensor tier to try, or None if found with
        sufficient confidence.
        """
        found = self.find(target_name)
        if found and found[0].confidence >= self.CONFIDENCE_SUFFICIENT:
            return None

        used_tiers: set[SensorTier] = set()
        for elem in self._elements:
            for source in elem.sources:
                used_tiers.add(source)

        for tier in SensorTier:
            if tier not in used_tiers:
                return tier

        return None  # all sensors exhausted

    @property
    def stats(self) -> dict:
        return {
            "total_elements": len(self._elements),
            "sensor_calls": dict(self._sensor_stats),
            "multi_source": sum(1 for e in self._elements if e.source_count > 1),
        }
