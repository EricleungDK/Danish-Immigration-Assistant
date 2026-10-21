"""Knowledge update controller (#78).

Owns every piece of mutable knowledge-update state: the automatic check throttle and
status, the update-record lock, the installation state and its progress events, and the
background worker threads. The application factory only translates HTTP into controller
calls and controller errors into HTTP responses.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from .embedding_provider import EmbeddingProvider
from .github_release_client import ArtifactDownloadApproval, GitHubReleaseClient
from .knowledge_release import (
    active_corpus_summary,
    discover_knowledge_update,
    dismiss_available_github_knowledge_update,
    dismiss_pending_knowledge_update,
    install_knowledge_release,
    load_pending_knowledge_update,
    prepare_github_knowledge_update,
    prepared_github_knowledge_release_dir,
    save_available_github_knowledge_update,
    save_pending_knowledge_update,
    select_github_knowledge_update,
)

AUTOMATIC_UPDATE_CHECK_INTERVAL_SECONDS = 6 * 60 * 60


class KnowledgeUpdateConflict(Exception):
    """The requested update action cannot run now (HTTP 409)."""


class KnowledgeUpdateUnavailable(Exception):
    """The update action failed; the prior release stays active (HTTP 503)."""


def knowledge_update_failure_message(action: str, exc: BaseException) -> str:
    return (
        f"Knowledge update failed while {action}; the previously active corpus/index "
        f"pair remains active. Detail: {exc}"
    )


def active_release_id(
    data_dir: Path,
    *,
    trust_root_path: str | Path | None = None,
) -> str:
    try:
        return active_corpus_summary(
            data_dir,
            trust_root_path=trust_root_path,
        )["knowledge_release_id"]
    except Exception:
        return ""


def _check_github_update_metadata(
    *,
    ensure_release: Callable[[], Any],
    data_dir: Path,
    client: GitHubReleaseClient,
    trust_root_path: Path | None,
) -> None:
    """Check content-free GitHub metadata without downloading or installing a release."""

    ensure_release()
    prepared = load_pending_knowledge_update(data_dir)
    prepared_distribution = (
        prepared.get("distribution") if isinstance(prepared, dict) else None
    )
    if (
        isinstance(prepared_distribution, dict)
        and prepared_distribution.get("channel") == "github-releases"
    ):
        dismiss_available_github_knowledge_update(data_dir)
        return
    update = select_github_knowledge_update(
        data_dir,
        client.list_published_releases(),
        trust_root_path=trust_root_path,
    )
    save_available_github_knowledge_update(data_dir, update)


class KnowledgeUpdateController:
    """Release check (automatic and manual), download approval, install, status."""

    def __init__(
        self,
        *,
        data_dir: Path,
        release_catalog_dir: Path,
        github_release_client: GitHubReleaseClient | None,
        embedding_provider: EmbeddingProvider | None,
        trust_root_path: Path | None,
        ensure_release: Callable[[], Any],
        automatic_check_interval_seconds: float = AUTOMATIC_UPDATE_CHECK_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        install_fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        """`github_release_client=None` selects the injected local release catalogue."""

        if (
            isinstance(automatic_check_interval_seconds, bool)
            or not isinstance(automatic_check_interval_seconds, int | float)
            or automatic_check_interval_seconds <= 0
        ):
            raise ValueError("Automatic update check interval must be greater than zero.")
        self._data_dir = data_dir
        self._catalog_dir = release_catalog_dir
        self._client = github_release_client
        self._embedding_provider = embedding_provider
        self._trust_root_path = trust_root_path
        self._ensure_release = ensure_release
        self._interval = float(automatic_check_interval_seconds)
        self._clock = clock
        self._fault_injector = install_fault_injector
        self._automatic_check_state: dict[str, Any] = {
            "last_attempt": None,
            "running": False,
            "status": None,
        }
        self._automatic_check_lock = threading.Lock()
        self._record_lock = threading.RLock()
        self._installation_state: dict[str, Any] | None = None
        self._installation_lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._threads_lock = threading.Lock()

    # -- capabilities ------------------------------------------------------------

    @property
    def automatic_check_enabled(self) -> bool:
        return self._client is not None

    @property
    def github_downloads_enabled(self) -> bool:
        return self._client is not None

    # -- status ------------------------------------------------------------------

    @property
    def automatic_check_running(self) -> bool:
        with self._automatic_check_lock:
            return bool(self._automatic_check_state["running"])

    def automatic_check_status(self) -> dict[str, str] | None:
        with self._automatic_check_lock:
            status = self._automatic_check_state["status"]
            return dict(status) if isinstance(status, dict) else None

    def reset_automatic_check(self) -> None:
        """Forget the throttle and status (isolated test servers)."""

        with self._automatic_check_lock:
            self._automatic_check_state.update(
                {"last_attempt": None, "running": False, "status": None}
            )

    def installation_is_running(self) -> bool:
        with self._installation_lock:
            return (
                self._installation_state is not None
                and self._installation_state.get("state") == "running"
            )

    def installation_status(self) -> dict[str, Any] | None:
        with self._installation_lock:
            if self._installation_state is None:
                return None
            return {
                **self._installation_state,
                "events": [dict(event) for event in self._installation_state["events"]],
            }

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Join background workers; True when none is still running."""

        deadline = None if timeout is None else time.monotonic() + timeout
        with self._threads_lock:
            threads = list(self._threads)
        for thread in threads:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            thread.join(remaining)
        with self._threads_lock:
            self._threads = [t for t in self._threads if t.is_alive()]
            return not self._threads

    # -- actions -----------------------------------------------------------------

    def request_automatic_check(self) -> dict[str, str]:
        """Start a throttled background metadata check; return the status to show."""

        if self._client is None:
            return {
                "state": "unavailable",
                "kind": "neutral",
                "role": "status",
                "title": "Automatic release metadata check unavailable",
                "message": "Use the manual check for this local release catalogue.",
            }
        now = self._clock()
        with self._automatic_check_lock:
            last_attempt = self._automatic_check_state["last_attempt"]
            if self._automatic_check_state["running"]:
                disposition = "running"
            elif (
                isinstance(last_attempt, int | float)
                and now - last_attempt < self._interval
            ):
                disposition = "throttled"
            else:
                self._automatic_check_state["last_attempt"] = now
                self._automatic_check_state["running"] = True
                self._automatic_check_state["status"] = {
                    "state": "running",
                    "kind": "neutral",
                    "role": "status",
                    "title": "Checking release metadata automatically",
                    "message": (
                        "Only bounded release metadata is being checked. Ordinary local "
                        "use remains available."
                    ),
                }
                disposition = "run"
        if disposition == "running":
            return {
                "state": "running",
                "kind": "neutral",
                "role": "status",
                "title": "Automatic release metadata check already running",
                "message": "Ordinary local use remains available while it finishes.",
            }
        if disposition == "throttled":
            return {
                "state": "completed",
                "kind": "neutral",
                "role": "status",
                "title": "Automatic release metadata check recently completed",
                "message": "The next automatic check is deferred by the local throttle.",
            }
        self._spawn(
            self._run_automatic_metadata_check,
            name="knowledge-release-automatic-metadata-check",
        )
        return self.automatic_check_status() or {
            "state": "running",
            "kind": "neutral",
            "role": "status",
            "title": "Checking release metadata automatically",
            "message": (
                "Only bounded release metadata is being checked. Ordinary local "
                "use remains available."
            ),
        }

    def manual_check(self, ensure_release: Callable[[], Any] | None = None) -> None:
        """Check for a newer release now. `ensure_release` overrides the verified-release
        loader (the app passes its request-scoped one so snapshot state is adopted)."""

        self._acquire_record_lock_or_conflict()
        try:
            if self.installation_is_running():
                raise KnowledgeUpdateConflict(
                    "Wait for the current knowledge release installation "
                    "before checking for another update."
                )
            loader = ensure_release or self._ensure_release
            if self._client is None:
                loader()
                update = discover_knowledge_update(
                    self._data_dir,
                    self._catalog_dir,
                    trust_root_path=self._trust_root_path,
                )
                save_pending_knowledge_update(self._data_dir, update)
            else:
                _check_github_update_metadata(
                    ensure_release=loader,
                    data_dir=self._data_dir,
                    client=self._client,
                    trust_root_path=self._trust_root_path,
                )
        except KnowledgeUpdateConflict:
            raise
        except Exception as exc:
            raise KnowledgeUpdateUnavailable(
                knowledge_update_failure_message("checking", exc)
            ) from exc
        finally:
            self._record_lock.release()

    def dismiss(self) -> None:
        self._acquire_record_lock_or_conflict()
        try:
            if self.installation_is_running():
                raise KnowledgeUpdateConflict(
                    "Wait for the current knowledge release installation before "
                    "dismissing update records."
                )
            dismiss_pending_knowledge_update(self._data_dir)
            dismiss_available_github_knowledge_update(self._data_dir)
        finally:
            self._record_lock.release()

    def approve_download(self, *, release_id: str, asset_id: int, asset_name: str) -> None:
        """Download and verify the exactly approved release asset (no installation)."""

        if self._client is None:
            raise KnowledgeUpdateConflict(
                "GitHub knowledge release downloads are not enabled for this app."
            )
        approval = ArtifactDownloadApproval(
            approved=True,
            requested_knowledge_release_id=release_id,
            artifact_id=asset_id,
            artifact_name=asset_name,
        )
        self._acquire_record_lock_or_conflict()
        try:
            if self.installation_is_running():
                raise KnowledgeUpdateConflict(
                    "Wait for the current knowledge release installation before "
                    "downloading another update."
                )
            prepare_github_knowledge_update(
                self._data_dir,
                self._client,
                approval=approval,
                trust_root_path=self._trust_root_path,
            )
        except KnowledgeUpdateConflict:
            raise
        except Exception as exc:
            raise KnowledgeUpdateUnavailable(
                knowledge_update_failure_message("downloading and verifying", exc)
            ) from exc
        finally:
            self._record_lock.release()

    def start_install(self, release_id: str) -> None:
        """Validate the reviewed pending release and install it in the background."""

        self._acquire_record_lock_or_conflict()
        try:
            try:
                pending_update = load_pending_knowledge_update(self._data_dir)
            except Exception as exc:
                raise KnowledgeUpdateUnavailable(
                    knowledge_update_failure_message("loading pending update", exc)
                ) from exc
            if not pending_update:
                raise KnowledgeUpdateConflict("No reviewed knowledge update is pending.")
            if release_id != pending_update["release"]["knowledge_release_id"]:
                raise KnowledgeUpdateConflict("Requested release is not pending review.")
            distribution = pending_update.get("distribution")
            is_github_release = (
                isinstance(distribution, dict)
                and distribution.get("channel") == "github-releases"
            )
            release_dir = (
                prepared_github_knowledge_release_dir(self._data_dir, release_id)
                if is_github_release
                else self._catalog_dir / release_id
            )
            previous_release_id = (
                active_release_id(self._data_dir, trust_root_path=self._trust_root_path)
                or "Unavailable"
            )
            with self._installation_lock:
                if (
                    self._installation_state is not None
                    and self._installation_state["state"] == "running"
                ):
                    raise KnowledgeUpdateConflict(
                        "A knowledge release installation is already running."
                    )
                self._installation_state = {
                    "release_id": release_id,
                    "state": "running",
                    "events": [],
                    "active_release_id": previous_release_id,
                    "previous_release_id": previous_release_id,
                    "rolled_back": False,
                    "error_type": None,
                }
        finally:
            self._record_lock.release()
        self._spawn(
            self._run_installation_job,
            name=f"knowledge-release-install-{release_id}",
            kwargs={
                "release_id": release_id,
                "release_dir": release_dir,
                "previous_release_id": previous_release_id,
            },
        )

    # -- internals ---------------------------------------------------------------

    def _spawn(
        self,
        target: Callable[..., None],
        *,
        name: str,
        kwargs: dict[str, Any] | None = None,
    ) -> None:
        worker = threading.Thread(target=target, kwargs=kwargs or {}, name=name, daemon=True)
        with self._threads_lock:
            # Start under the lock so no reader sees an unstarted thread.
            worker.start()
            self._threads = [t for t in self._threads if t.is_alive()]
            self._threads.append(worker)

    def _acquire_record_lock_or_conflict(self) -> None:
        if not self._record_lock.acquire(blocking=False):
            raise KnowledgeUpdateConflict(
                "Another knowledge update action is in progress. Ordinary local "
                "use remains available; retry this update action shortly."
            )

    def _run_automatic_metadata_check(self) -> None:
        deferred_for_installation = False
        try:
            assert self._client is not None
            with self._record_lock:
                if self.installation_is_running():
                    deferred_for_installation = True
                else:
                    _check_github_update_metadata(
                        ensure_release=self._ensure_release,
                        data_dir=self._data_dir,
                        client=self._client,
                        trust_root_path=self._trust_root_path,
                    )
        except Exception:
            status = {
                "state": "failed",
                "kind": "error",
                "role": "status",
                "title": "Automatic release metadata check unavailable",
                "message": (
                    "The metadata check failed safely. Ordinary local use is still "
                    "available, and no release was downloaded or installed."
                ),
            }
        else:
            if deferred_for_installation:
                status = {
                    "state": "deferred",
                    "kind": "neutral",
                    "role": "status",
                    "title": "Automatic release metadata check deferred",
                    "message": (
                        "A knowledge release installation is running. The metadata "
                        "check made no changes and can be retried afterward."
                    ),
                }
            else:
                status = {
                    "state": "completed",
                    "kind": "success",
                    "role": "status",
                    "title": "Automatic release metadata check complete",
                    "message": (
                        "Only bounded release metadata was checked. Download and installation "
                        "still require separate approval."
                    ),
                }
        with self._automatic_check_lock:
            self._automatic_check_state["running"] = False
            self._automatic_check_state["status"] = status

    def _record_installation_progress(self, event: dict[str, Any]) -> None:
        phase = event.get("phase")
        message = event.get("message")
        percent = event.get("percent")
        if (
            not isinstance(phase, str)
            or not phase.strip()
            or not isinstance(message, str)
            or not message.strip()
            or isinstance(percent, bool)
            or not isinstance(percent, int)
            or not 0 <= percent <= 100
        ):
            raise ValueError("Knowledge release installer emitted invalid progress.")
        progress_event = {"phase": phase, "message": message, "percent": percent}
        with self._installation_lock:
            state = self._installation_state
            if state is None or state["state"] != "running":
                raise RuntimeError("No knowledge release installation is active.")
            state["events"].append(progress_event)

    def _perform_installation_work(
        self,
        *,
        release_id: str,
        release_dir: Path,
        previous_release_id: str,
    ) -> dict[str, Any]:
        try:
            install_knowledge_release(
                self._data_dir,
                release_dir=release_dir,
                embedding_provider=self._embedding_provider,
                trust_root_path=self._trust_root_path,
                expected_release_id=release_id,
                progress_callback=self._record_installation_progress,
                fault_injector=self._fault_injector,
            )
            active = active_release_id(self._data_dir, trust_root_path=self._trust_root_path)
            with self._installation_lock:
                state = self._installation_state
                latest_phase = (
                    state["events"][-1]["phase"] if state and state["events"] else None
                )
            if latest_phase not in {"complete", "already_active"}:
                raise RuntimeError(
                    "Knowledge release installer returned without a completion event."
                )
            if active != release_id:
                raise RuntimeError(
                    "Knowledge release installer completed without activating the approved release."
                )
            with self._record_lock:
                dismiss_pending_knowledge_update(self._data_dir)
        except Exception as exc:
            active = (
                active_release_id(self._data_dir, trust_root_path=self._trust_root_path)
                or "Unavailable"
            )
            return {
                "state": "failed",
                "active_release_id": active,
                "rolled_back": active == previous_release_id,
                "error_type": type(exc).__name__,
            }
        return {
            "state": "completed",
            "active_release_id": release_id,
            "rolled_back": False,
            "error_type": None,
        }

    def _run_installation_job(
        self,
        *,
        release_id: str,
        release_dir: Path,
        previous_release_id: str,
    ) -> None:
        outcome = self._perform_installation_work(
            release_id=release_id,
            release_dir=release_dir,
            previous_release_id=previous_release_id,
        )
        with self._installation_lock:
            if self._installation_state is not None:
                self._installation_state.update(outcome)
