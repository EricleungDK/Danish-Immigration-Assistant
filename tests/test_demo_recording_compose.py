"""Pure editing rules for the README demo recording (recording/compose.py)."""

from __future__ import annotations

import math
import unittest

from recording.compose import (
    Cut,
    camera_box,
    output_frames,
    pick_frame,
    recording_record,
)


class OutputFramesTest(unittest.TestCase):
    def test_uncut_recording_maps_output_time_to_source_time(self) -> None:
        frames = output_frames(duration=1.0, fps=4, cuts=[])
        self.assertEqual([f.source_time for f in frames], [0.0, 0.25, 0.5, 0.75])
        self.assertTrue(all(f.label is None for f in frames))

    def test_model_wait_is_shortened_and_labelled(self) -> None:
        cut = Cut(start=1.0, end=11.0, keep=1.0, label="Model wait shortened")
        frames = output_frames(duration=12.0, fps=2, cuts=[cut])
        # 12s source minus 10s wait plus 1s kept = 3s output.
        self.assertEqual(len(frames), 6)
        self.assertEqual(
            [f.source_time for f in frames], [0.0, 0.5, 1.0, 6.0, 11.0, 11.5]
        )
        self.assertEqual(
            [f.label for f in frames],
            [None, None, "Model wait shortened", "Model wait shortened", None, None],
        )

    def test_segment_boundaries_off_the_frame_grid_never_repeat_or_reverse(self) -> None:
        cut = Cut(start=5.01, end=20.0, keep=1.4, label="wait")
        frames = output_frames(duration=25.0, fps=25, cuts=[cut])
        times = [f.source_time for f in frames]
        self.assertEqual(times, sorted(set(times)))
        self.assertTrue(all(f.source_time >= 5.01 for f in frames if f.label))
        self.assertEqual(len(frames), math.ceil((25.0 - 14.99 + 1.4) * 25))

    def test_cut_must_shorten_and_stay_inside_recording(self) -> None:
        with self.assertRaises(ValueError):
            output_frames(duration=5.0, fps=2, cuts=[Cut(1.0, 2.0, 3.0, "x")])
        with self.assertRaises(ValueError):
            output_frames(duration=5.0, fps=2, cuts=[Cut(4.0, 6.0, 1.0, "x")])

    def test_overlapping_cuts_are_rejected(self) -> None:
        cuts = [Cut(1.0, 3.0, 0.5, "a"), Cut(2.0, 4.0, 0.5, "b")]
        with self.assertRaises(ValueError):
            output_frames(duration=6.0, fps=2, cuts=cuts)


class PickFrameTest(unittest.TestCase):
    def test_latest_captured_frame_at_or_before_time_is_shown(self) -> None:
        times = [0.0, 0.4, 1.0]
        self.assertEqual(pick_frame(times, 0.0), 0)
        self.assertEqual(pick_frame(times, 0.39), 0)
        self.assertEqual(pick_frame(times, 0.4), 1)
        self.assertEqual(pick_frame(times, 5.0), 2)

    def test_time_before_first_frame_uses_first_frame(self) -> None:
        self.assertEqual(pick_frame([0.2, 0.5], 0.0), 0)


class CameraBoxTest(unittest.TestCase):
    viewport = (1280, 800)

    def test_holds_keyframe_box_and_keeps_viewport_aspect(self) -> None:
        keys = [(0.0, (0, 0, 1280, 800)), (2.0, (400, 400, 640, 400))]
        self.assertEqual(camera_box(keys, 0.0, self.viewport), (0, 0, 1280, 800))
        self.assertEqual(camera_box(keys, 9.0, self.viewport), (400, 400, 640, 400))

    def test_eases_between_keyframes_without_overshoot(self) -> None:
        keys = [(0.0, (0, 0, 1280, 800)), (1.0, (640, 400, 640, 400))]
        quarter = camera_box(keys, 0.25, self.viewport)
        half = camera_box(keys, 0.5, self.viewport)
        self.assertLess(quarter[0], 640 * 0.25)  # eased: slow start
        self.assertAlmostEqual(half[0], 320)
        self.assertAlmostEqual(half[2] / half[3], 1280 / 800)

    def test_cut_mode_switches_boxes_at_the_midpoint_without_easing(self) -> None:
        keys = [(0.0, (0, 0, 1280, 800)), (1.0, (640, 400, 640, 400))]
        self.assertEqual(camera_box(keys, 0.49, self.viewport, cut=True), (0, 0, 1280, 800))
        self.assertEqual(camera_box(keys, 0.5, self.viewport, cut=True), (640, 400, 640, 400))

    def test_box_is_clamped_inside_viewport(self) -> None:
        keys = [(0.0, (1000, 700, 640, 400))]
        x, y, w, h = camera_box(keys, 0.0, self.viewport)
        self.assertEqual((x + w, y + h), (1280, 800))

    def test_box_aspect_is_corrected_to_viewport(self) -> None:
        keys = [(0.0, (100, 100, 640, 200))]
        _, _, w, h = camera_box(keys, 0.0, self.viewport)
        self.assertAlmostEqual(w / h, 1280 / 800)
        self.assertGreaterEqual(h, 200)


class RecordingRecordTest(unittest.TestCase):
    def test_record_states_revision_release_model_and_wait(self) -> None:
        record = recording_record(
            revision="5d6e02b95839ddc05f540305d712c03a3a0ea5e9",
            recorded_at="2026-09-30T12:00:00Z",
            knowledge_release="kr-2026-09-05.1",
            model="gemma4:12b",
            question="What is PD3?",
            cuts=[Cut(3.0, 41.0, 1.0, "wait")],
            outputs={"gif": {"bytes": 10}},
            size=(1280, 800),
            fps=15,
            duration=24.0,
        )
        self.assertEqual(record["source_revision"], "5d6e02b95839ddc05f540305d712c03a3a0ea5e9")
        self.assertEqual(record["knowledge_release"], "kr-2026-09-05.1")
        self.assertEqual(record["model"], "gemma4:12b")
        self.assertEqual(record["shortened_waits"], [{"source_seconds": 38.0, "shown_seconds": 1.0, "label": "wait"}])
        self.assertEqual(record["width"], 1280)
        self.assertEqual(record["height"], 800)

    def test_record_requires_full_revision(self) -> None:
        with self.assertRaises(ValueError):
            recording_record(
                revision="5d6e02b",
                recorded_at="2026-09-30T12:00:00Z",
                knowledge_release="kr-2026-09-05.1",
                model="gemma4:12b",
                question="q",
                cuts=[],
                outputs={},
                size=(1280, 800),
                fps=15,
                duration=20.0,
            )


if __name__ == "__main__":
    unittest.main()
