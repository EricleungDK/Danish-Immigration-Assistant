"""Record the README demo from a pinned revision, end to end.

    .venv/bin/python -m recording.record_demo --revision <commit>

1. Checks out <commit> into a temporary git worktree (the app that is shown).
2. Starts it with recording/isolated_app.py: fresh XDG workspace, the signed
   knowledge release, the real local Ollama model; no personal data.
3. Captures the journey with recording/capture.mjs (Playwright, 2x).
4. Shortens the model wait with a visible label, applies the eased camera,
   and encodes docs/assets/demo.{gif,mp4,webm}, demo-poster.png and
   demo-recording.json (revision, release, model, waits, output hashes).

Needs ffmpeg, Node with the repository's Playwright, and recording/requirements.txt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from recording.compose import Cut, output_frames, recording_record, render_frames

REPO = Path(__file__).resolve().parents[1]
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SIZE = (1280, 800)
VIDEO_FPS = 25
GIF_FPS = 15
GIF_WIDTH = 960
GIF_COLORS = 96
GIF_LIMIT_BYTES = 5_000_000
WAIT_KEEP_SECONDS = 1.4
MIN_ZOOM_WIDTH = 720
MAX_ANSWER_ZOOM_WIDTH = 1120


def _run(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, **kwargs)


def _expand(box: list[float], margin: float = 32) -> tuple[float, float, float, float]:
    x, y, w, h = box
    x, y, w, h = x - margin, y - margin, w + 2 * margin, h + 2 * margin
    if w < MIN_ZOOM_WIDTH:
        x -= (MIN_ZOOM_WIDTH - w) / 2
        w = MIN_ZOOM_WIDTH
    return (x, y, w, h)


def camera_keys(events: dict[str, float], rects: dict) -> list[tuple[float, tuple]]:
    full = (0.0, 0.0, *map(float, rects["viewport"]))
    composer = _expand(rects["composer"])
    ax, ay, aw, _ = rects["answer"]  # the top of the conversation column: answer and first fact
    answer_width = min(aw + 48, MAX_ANSWER_ZOOM_WIDTH)
    answer = (ax - 24, ay - 24, answer_width, answer_width * full[3] / full[2])
    dx, _, dw, _ = rects["drawer"]
    drawer = (dx - 16, 40.0, max(dw + 32, MIN_ZOOM_WIDTH), 0.0)
    drawer = (drawer[0], drawer[1], drawer[2], drawer[2] * full[3] / full[2])
    return [
        (0.0, full),
        (events["composer_ready"], full),
        (events["type_start"] - 0.1, composer),
        (events["answer"], composer),
        (events["scroll"], answer),
        (events["evidence_click"], answer),
        (events["drawer"] + 0.8, drawer),
        (events["end"] - 1.3, drawer),
        (events["end"], full),
    ]


def _wait_for(url: str, seconds: float = 30) -> None:
    import time

    deadline = time.monotonic() + seconds
    while True:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.3)


def _sha(path: Path) -> dict:
    data = path.read_bytes()
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def capture(args: argparse.Namespace, work: Path) -> Path:
    """Run the pinned app in isolation and capture the journey; returns the capture dir."""

    revision = _run("git", "-C", str(REPO), "rev-parse", "--verify", f"{args.revision}^{{commit}}",
                    capture_output=True, text=True).stdout.strip()
    source, workspace, capture_dir = work / "source", work / "workspace", work / "capture"
    server = None
    try:
        _run("git", "-C", str(REPO), "worktree", "add", "--detach", str(source), revision, capture_output=True)
        env = {**os.environ, "PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1"}
        # cwd = the pinned worktree, so its danish_rag shadows the checkout's.
        server = subprocess.Popen(
            [sys.executable, "-m", "recording.isolated_app", "--workspace", str(workspace),
             "--port", str(args.port), "--model", args.model, "--release", args.release],
            cwd=source, env=env, stdout=subprocess.PIPE, text=True,
        )
        ready = server.stdout.readline()
        if not ready.startswith("READY"):
            raise RuntimeError(f"Isolated app did not start: {ready!r}")
        url, app = ready.split()[1], ready.split()[2].removeprefix("app=")
        if Path(app) != source / "danish_rag":
            raise RuntimeError(f"App code {app} is not the pinned worktree.")
        _wait_for(url)
        recorded_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=10) as response:
            ollama_version = json.load(response)["version"]
        _run("node", str(REPO / "recording" / "capture.mjs"), url, str(capture_dir), args.question, cwd=REPO)
    finally:
        if server:
            server.terminate()
            try:
                server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
        subprocess.run(["git", "-C", str(REPO), "worktree", "remove", "--force", str(source)], check=False)
        shutil.rmtree(workspace, ignore_errors=True)  # isolated conversation record
    (capture_dir / "source.json").write_text(json.dumps({
        "revision": revision, "recorded_at": recorded_at, "model": args.model, "ollama_version": ollama_version,
        "release": args.release, "question": args.question,
    }, indent=2) + "\n")
    return capture_dir


def compose(capture_dir: Path, out: Path, work: Path) -> dict:
    """Cut, frame and encode a capture into the README assets and their record."""

    source = json.loads((capture_dir / "source.json").read_text())
    frames = json.loads((capture_dir / "frames.json").read_text())
    events = json.loads((capture_dir / "events.json").read_text())
    rects = json.loads((capture_dir / "rects.json").read_text())
    wait = events["answer"] - events["send"]
    cut_start, cut_end = events["send"] + 1.2, events["answer"] - 0.3
    cuts = []
    if cut_end - cut_start > WAIT_KEEP_SECONDS:
        shown = wait - (cut_end - cut_start) + WAIT_KEEP_SECONDS
        label = f"Local {source['model']} wait shortened: {wait:.0f} s \u2192 {shown:.0f} s"
        cuts.append(Cut(cut_start, cut_end, WAIT_KEEP_SECONDS, label))
    keys = camera_keys(events, rects)
    viewport = tuple(rects["viewport"])
    common = dict(
        capture_dir=capture_dir, frame_times=[f["t"] for f in frames], frame_files=[f["file"] for f in frames],
        keys=keys, viewport=viewport, font_path=FONT,
    )
    video_plan = output_frames(duration=events["end"], fps=VIDEO_FPS, cuts=cuts)
    gif_plan = output_frames(duration=events["end"], fps=GIF_FPS, cuts=cuts)
    video_dir, gif_dir = work / "video", work / "gif"
    for directory in (video_dir, gif_dir):
        shutil.rmtree(directory, ignore_errors=True)
    render_frames(plan=video_plan, size=SIZE, out_dir=video_dir, **common)
    gif_size = (GIF_WIDTH, round(GIF_WIDTH * SIZE[1] / SIZE[0]))
    render_frames(plan=gif_plan, size=gif_size, out_dir=gif_dir, cut=True, **common)

    # Encode into the work dir; only a passing set replaces the committed assets.
    final_out, out = out, work / "encoded"
    out.mkdir(parents=True, exist_ok=True)
    video_in = ("ffmpeg", "-y", "-loglevel", "error", "-framerate", str(VIDEO_FPS), "-i", str(video_dir / "%05d.png"))
    _run(*video_in, "-c:v", "libx264", "-preset", "slow", "-crf", "26", "-tune", "stillimage",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out / "demo.mp4"))
    _run(*video_in, "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "46", "-row-mt", "1", "-pix_fmt", "yuv420p",
         str(out / "demo.webm"))
    gif_filter = (
        f"split[a][b];[a]palettegen=max_colors={GIF_COLORS}:stats_mode=diff[p];"
        "[b][p]paletteuse=dither=none:diff_mode=rectangle"
    )
    _run("ffmpeg", "-y", "-loglevel", "error", "-framerate", str(GIF_FPS), "-i", str(gif_dir / "%05d.png"),
         "-filter_complex", gif_filter, "-loop", "0", str(out / "demo.gif"))
    # Poster: the cited answer, mid-read.
    poster_time = (events["reading"] + events["evidence_click"]) / 2
    poster_index = min(range(len(video_plan)), key=lambda i: abs(video_plan[i].source_time - poster_time))
    shutil.copyfile(video_dir / f"{poster_index:05d}.png", out / "demo-poster.png")

    gif_bytes = (out / "demo.gif").stat().st_size
    if gif_bytes >= GIF_LIMIT_BYTES:
        raise RuntimeError(f"GIF is {gif_bytes} bytes; limit {GIF_LIMIT_BYTES}.")
    names = ["demo.gif", "demo.mp4", "demo.webm", "demo-poster.png"]
    final_out.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copyfile(out / name, final_out / name)
    out = final_out
    record = recording_record(
        revision=source["revision"],
        recorded_at=source["recorded_at"],
        knowledge_release=source["release"],
        model=source["model"],
        question=source["question"],
        cuts=cuts,
        outputs={name: _sha(out / name) for name in names},
        size=SIZE,
        fps=VIDEO_FPS,
        duration=len(video_plan) / VIDEO_FPS,
    )
    record["gif"] = {"fps": GIF_FPS, "width": gif_size[0], "height": gif_size[1], "camera": "cuts"}
    record["ollama_version"] = source["ollama_version"]
    record["actual_model_wait_seconds"] = round(wait, 1)
    record["answer_text"] = (capture_dir / "answer.txt").read_text()
    (out / "demo-recording.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--revision", help="Commit whose app is recorded (required unless --from-capture).")
    parser.add_argument("--question", default="What is PD3?")
    parser.add_argument("--model", default="gemma4:12b")
    parser.add_argument("--release", default="kr-2026-09-05.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--out", type=Path, default=REPO / "docs" / "assets")
    parser.add_argument("--from-capture", type=Path, help="Re-encode an existing capture directory.")
    parser.add_argument("--keep-work", action="store_true", help="Keep captured frames for inspection.")
    args = parser.parse_args(argv)
    if not args.revision and not args.from_capture:
        parser.error("--revision is required")

    work = Path(tempfile.mkdtemp(prefix="dia-demo-"))
    capture_dir = args.from_capture or capture(args, work)
    record = compose(capture_dir, args.out, work)
    print(json.dumps({k: record[k] for k in ("source_revision", "duration_seconds", "actual_model_wait_seconds")}))
    print({name: output["bytes"] for name, output in record["outputs"].items()})
    if args.keep_work:
        print(f"work kept at {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
