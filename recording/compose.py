"""Editing rules for the README demo recording.

Pure functions (timeline, cuts, frame choice, camera, metadata) plus the frame
renderer. Times are seconds on the capture clock; boxes are CSS pixels
(x, y, width, height) of the captured viewport.
"""

from __future__ import annotations

import bisect
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class Cut:
    """A source interval [start, end) shown in `keep` seconds with a visible label."""

    start: float
    end: float
    keep: float
    label: str


@dataclass(frozen=True)
class OutputFrame:
    source_time: float
    label: str | None


def _segments(duration: float, cuts: Sequence[Cut]) -> list[tuple[float, float, float, str | None]]:
    ordered = sorted(cuts, key=lambda cut: cut.start)
    segments: list[tuple[float, float, float, str | None]] = []
    cursor = 0.0
    for cut in ordered:
        if not 0.0 <= cut.start < cut.end <= duration:
            raise ValueError(f"Cut {cut} lies outside the {duration}s recording.")
        if cut.start < cursor:
            raise ValueError("Cuts must not overlap.")
        if not 0.0 < cut.keep < cut.end - cut.start:
            raise ValueError("A cut must shorten its interval.")
        if cut.start > cursor:
            segments.append((cursor, cut.start, cut.start - cursor, None))
        segments.append((cut.start, cut.end, cut.keep, cut.label))
        cursor = cut.end
    if duration > cursor:
        segments.append((cursor, duration, duration - cursor, None))
    return segments


def output_frames(*, duration: float, fps: int, cuts: Sequence[Cut]) -> list[OutputFrame]:
    """Map each output frame to its source time; cut intervals play faster, labelled."""

    frames: list[OutputFrame] = []
    out_start = 0.0
    for src_start, src_end, out_length, label in _segments(duration, cuts):
        out_end = out_start + out_length
        index = math.ceil(out_start * fps - 1e-9)  # first grid frame inside this segment
        while index / fps < out_end - 1e-9:
            t_out = index / fps
            rate = (src_end - src_start) / out_length
            source = round(src_start + (t_out - out_start) * rate, 6)
            frames.append(OutputFrame(source_time=source, label=label))
            index += 1
        out_start = out_end
    return frames


def pick_frame(times: Sequence[float], t: float) -> int:
    """Index of the latest captured frame at or before `t` (first frame if earlier)."""

    return max(bisect.bisect_right(times, t + 1e-9) - 1, 0)


def _ease(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return u * u * (3.0 - 2.0 * u)


def _fit(box: Box, viewport: tuple[int, int]) -> Box:
    vw, vh = viewport
    x, y, w, h = box
    aspect = vw / vh
    cx, cy = x + w / 2, y + h / 2
    if w / h > aspect:
        h = w / aspect
    else:
        w = h * aspect
    w, h = min(w, vw), min(h, vh)
    x = min(max(cx - w / 2, 0.0), vw - w)
    y = min(max(cy - h / 2, 0.0), vh - h)
    return (x, y, w, h)


def camera_box(
    keys: Sequence[tuple[float, Box]], t: float, viewport: tuple[int, int], *, cut: bool = False
) -> Box:
    """Eased camera between keyframes (time, box); holds outside them.

    With `cut`, each move is a hard cut at its midpoint (a GIF pays for every
    changed pixel, so eased pans are kept for the video only).
    """

    fitted = [(time, _fit(box, viewport)) for time, box in sorted(keys, key=lambda k: k[0])]
    if t <= fitted[0][0]:
        return fitted[0][1]
    for (t0, a), (t1, b) in zip(fitted, fitted[1:]):
        if t < t1:
            u = _ease((t - t0) / (t1 - t0))
            if cut:
                return b if u >= 0.5 else a
            return tuple(av + (bv - av) * u for av, bv in zip(a, b))  # type: ignore[return-value]
    return fitted[-1][1]


def recording_record(
    *,
    revision: str,
    recorded_at: str,
    knowledge_release: str,
    model: str,
    question: str,
    cuts: Sequence[Cut],
    outputs: dict[str, Any],
    size: tuple[int, int],
    fps: int,
    duration: float,
) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Record the full 40-character source revision.")
    return {
        "source_revision": revision,
        "recorded_at_utc": recorded_at,
        "knowledge_release": knowledge_release,
        "model": model,
        "question": question,
        "shortened_waits": [
            {"source_seconds": round(c.end - c.start, 1), "shown_seconds": c.keep, "label": c.label}
            for c in cuts
        ],
        "width": size[0],
        "height": size[1],
        "fps": fps,
        "duration_seconds": round(duration, 2),
        "outputs": outputs,
    }


def render_frames(
    *,
    capture_dir: Path,
    frame_times: Sequence[float],
    frame_files: Sequence[str],
    plan: Sequence[OutputFrame],
    keys: Sequence[tuple[float, Box]],
    viewport: tuple[int, int],
    size: tuple[int, int],
    out_dir: Path,
    font_path: str,
    cut: bool = False,
) -> None:
    """Render planned frames: subpixel camera crop, downscale, wait label."""

    from PIL import Image, ImageDraw, ImageFont

    out_dir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(font_path, max(14, size[1] // 34))
    cache: dict[int, Image.Image] = {}
    for n, frame in enumerate(plan):
        index = pick_frame(frame_times, frame.source_time)
        if index not in cache:
            cache.clear()
            cache[index] = Image.open(capture_dir / frame_files[index]).convert("RGB")
        source = cache[index]
        scale = source.width / viewport[0]
        x, y, w, h = (v * scale for v in camera_box(keys, frame.source_time, viewport, cut=cut))
        image = source.transform(
            size,
            Image.Transform.EXTENT,
            (x, y, x + w, y + h),
            resample=Image.Resampling.BICUBIC,
        )
        if frame.label:
            draw = ImageDraw.Draw(image, "RGBA")
            pad = size[1] // 60
            left, top, right, bottom = draw.textbbox((0, 0), frame.label, font=font)
            bw, bh = right - left + 2 * pad, bottom - top + 2 * pad
            bx, by = (size[0] - bw) // 2, 3 * pad  # top: the composer and Send sit low
            draw.rounded_rectangle((bx, by, bx + bw, by + bh), radius=pad, fill=(20, 28, 26, 225))
            draw.text((bx + pad - left, by + pad - top), frame.label, font=font, fill=(255, 255, 255, 255))
        image.save(out_dir / f"{n:05d}.png", compress_level=1)
