"""Local web application for Danish Immigration RAG."""

from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .answer_pipeline import (
    AnswerPipelineError,
    AnswerService,
    AnswerValidationError,
    LocalProviderAnswerGenerator,
)
from .conversation_store import ConversationStore
from .embedding_provider import EmbeddingProvider
from .github_release_client import ArtifactDownloadApproval, GitHubReleaseClient
from .knowledge_release import (
    APPLICATION_VERSION,
    DEFAULT_RELEASE_CATALOG_DIR,
    KnowledgeReleaseError,
    active_corpus_summary,
    default_data_dir,
    discover_knowledge_update,
    dismiss_available_github_knowledge_update,
    dismiss_pending_knowledge_update,
    ensure_minimal_knowledge_release,
    install_knowledge_release,
    load_available_github_knowledge_update,
    load_pending_knowledge_update,
    prepare_github_knowledge_update,
    prepared_github_knowledge_release_dir,
    save_available_github_knowledge_update,
    save_pending_knowledge_update,
    select_github_knowledge_update,
)
from .provider_setup import (
    CapabilityTestResult,
    ProviderCapabilityTester,
    ProviderConfiguration,
    default_config_path,
    load_provider_configuration,
    normalize_provider_form,
    provider_options,
    save_provider_configuration,
    validate_provider_configuration,
    validated_configuration,
)
from .retrieval import HybridRetriever, RetrievalError
from .runtime_policy import load_runtime_policy


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = Path(__file__).resolve().parent / "web"
TEMPLATES = Environment(
    loader=FileSystemLoader(WEB_ROOT / "templates"),
    autoescape=select_autoescape(["html", "xml"]),
)
AUTOMATIC_UPDATE_CHECK_INTERVAL_SECONDS = 6 * 60 * 60


def create_app(
    *,
    config_path: str | Path | None = None,
    data_dir: str | Path | None = None,
    answer_generator: Any | None = None,
    capability_tester: Callable[[ProviderConfiguration], CapabilityTestResult | dict[str, Any]]
    | None = None,
    release_catalog_dir: str | Path | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    trust_root_path: str | Path | None = None,
    github_release_client: GitHubReleaseClient | None = None,
    automatic_update_check_interval_seconds: float = (
        AUTOMATIC_UPDATE_CHECK_INTERVAL_SECONDS
    ),
    update_check_clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    app = FastAPI(title="Danish Immigration RAG")
    app.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")

    resolved_config_path = Path(config_path) if config_path else default_config_path()
    resolved_data_dir = Path(data_dir) if data_dir else default_data_dir()
    use_local_release_catalog = release_catalog_dir is not None
    if use_local_release_catalog and github_release_client is not None:
        raise ValueError(
            "Choose either the injected local release catalogue or GitHub Releases."
        )
    resolved_release_catalog_dir = Path(
        release_catalog_dir if release_catalog_dir is not None else DEFAULT_RELEASE_CATALOG_DIR
    )
    resolved_github_release_client = (
        None
        if use_local_release_catalog
        else github_release_client
        or GitHubReleaseClient(application_version=APPLICATION_VERSION)
    )
    resolved_trust_root_path = Path(trust_root_path) if trust_root_path else None
    if (
        isinstance(automatic_update_check_interval_seconds, bool)
        or not isinstance(automatic_update_check_interval_seconds, int | float)
        or automatic_update_check_interval_seconds <= 0
    ):
        raise ValueError("Automatic update check interval must be greater than zero.")
    tester = capability_tester or ProviderCapabilityTester()
    generator = answer_generator or LocalProviderAnswerGenerator()
    store = ConversationStore(resolved_data_dir / "conversations.sqlite3")
    automatic_check_state: dict[str, Any] = {
        "last_attempt": None,
        "running": False,
        "status": None,
    }
    automatic_check_lock = threading.Lock()
    update_record_lock = threading.RLock()
    installation_state: dict[str, Any] | None = None
    installation_state_lock = threading.Lock()

    def installation_is_running() -> bool:
        with installation_state_lock:
            return (
                installation_state is not None
                and installation_state.get("state") == "running"
            )

    def acquire_update_record_lock_or_conflict() -> None:
        if not update_record_lock.acquire(blocking=False):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Another knowledge update action is in progress. Ordinary local "
                    "use remains available; retry this update action shortly."
                ),
            )

    def automatic_check_status_snapshot() -> dict[str, str] | None:
        with automatic_check_lock:
            status = automatic_check_state["status"]
            return dict(status) if isinstance(status, dict) else None

    def run_automatic_metadata_check() -> None:
        try:
            assert resolved_github_release_client is not None
            with update_record_lock:
                if installation_is_running():
                    deferred_for_installation = True
                else:
                    deferred_for_installation = False
                    _check_github_update_metadata(
                        data_dir=resolved_data_dir,
                        client=resolved_github_release_client,
                        embedding_provider=embedding_provider,
                        trust_root_path=resolved_trust_root_path,
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
        with automatic_check_lock:
            automatic_check_state["running"] = False
            automatic_check_state["status"] = status

    def installation_status_snapshot() -> dict[str, Any] | None:
        with installation_state_lock:
            if installation_state is None:
                return None
            return {
                **installation_state,
                "events": [dict(event) for event in installation_state["events"]],
            }

    def record_installation_progress(event: dict[str, Any]) -> None:
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
        progress_event = {
            "phase": phase,
            "message": message,
            "percent": percent,
        }
        with installation_state_lock:
            if installation_state is None or installation_state["state"] != "running":
                raise RuntimeError("No knowledge release installation is active.")
            installation_state["events"].append(progress_event)

    def perform_installation_work(
        *,
        release_id: str,
        release_dir: Path,
        previous_release_id: str,
    ) -> dict[str, Any]:
        try:
            install_knowledge_release(
                resolved_data_dir,
                release_dir=release_dir,
                embedding_provider=embedding_provider,
                trust_root_path=resolved_trust_root_path,
                expected_release_id=release_id,
                progress_callback=record_installation_progress,
            )
            active_release_id = _active_release_id(resolved_data_dir)
            with installation_state_lock:
                latest_phase = (
                    installation_state["events"][-1]["phase"]
                    if installation_state and installation_state["events"]
                    else None
                )
            if latest_phase not in {"complete", "already_active"}:
                raise RuntimeError(
                    "Knowledge release installer returned without a completion event."
                )
            if active_release_id != release_id:
                raise RuntimeError(
                    "Knowledge release installer completed without activating the approved release."
                )
            with update_record_lock:
                dismiss_pending_knowledge_update(resolved_data_dir)
        except Exception as exc:
            active_release_id = _active_release_id(resolved_data_dir) or "Unavailable"
            return {
                "state": "failed",
                "active_release_id": active_release_id,
                "rolled_back": active_release_id == previous_release_id,
                "error_type": type(exc).__name__,
            }
        return {
            "state": "completed",
            "active_release_id": release_id,
            "rolled_back": False,
            "error_type": None,
        }

    def run_installation_job(
        *,
        release_id: str,
        release_dir: Path,
        previous_release_id: str,
    ) -> None:
        outcome = perform_installation_work(
            release_id=release_id,
            release_dir=release_dir,
            previous_release_id=previous_release_id,
        )
        with installation_state_lock:
            if installation_state is not None:
                installation_state.update(outcome)

    def render_home(
        *,
        status_code: int = 200,
        setup_form: ProviderConfiguration | None = None,
        setup_error: str = "",
        setup_reason: str = "",
        active_question: str = "",
        composer_error: str = "",
        active_conversation: dict[str, Any] | None = None,
        update_status: dict[str, str] | None = None,
    ) -> HTMLResponse:
        template = TEMPLATES.get_template("home.html")
        return HTMLResponse(
            template.render(
                _page_context(
                    setup_form=setup_form,
                    setup_error=setup_error,
                    setup_reason=setup_reason,
                    active_question=active_question,
                    composer_error=composer_error,
                    active_conversation=active_conversation,
                    update_status=update_status,
                )
            ),
            status_code=status_code,
        )

    def render_conversation_main(
        *,
        status_code: int = 200,
        active_question: str = "",
        composer_error: str = "",
        active_conversation: dict[str, Any] | None = None,
    ) -> HTMLResponse:
        template = TEMPLATES.get_template("conversation_main.html")
        return HTMLResponse(
            template.render(
                _page_context(
                    active_question=active_question,
                    composer_error=composer_error,
                    active_conversation=active_conversation,
                )
            ),
            status_code=status_code,
        )

    def render_ask_response(
        request: Request,
        *,
        status_code: int = 200,
        active_question: str = "",
        composer_error: str = "",
        active_conversation: dict[str, Any] | None = None,
    ) -> HTMLResponse:
        renderer = render_conversation_main if _is_htmx_request(request) else render_home
        return renderer(
            status_code=status_code,
            active_question=active_question,
            composer_error=composer_error,
            active_conversation=active_conversation,
        )

    def render_setup_panel(
        *,
        status_code: int = 200,
        setup_form: ProviderConfiguration | None = None,
        setup_error: str = "",
        setup_reason: str = "",
    ) -> HTMLResponse:
        configuration = _load_configuration_or_none(resolved_config_path)
        if setup_form is None:
            setup_form = configuration or _default_provider_configuration()
        template = TEMPLATES.get_template("setup_panel.html")
        return HTMLResponse(
            template.render(
                configuration=configuration.to_public_dict() if configuration else None,
                providers=provider_options(),
                form=setup_form,
                setup_error=setup_error,
                setup_reason=setup_reason,
            ),
            status_code=status_code,
        )

    def render_knowledge_updates(
        *,
        automatic_check_status: dict[str, str] | None = None,
        automatic_check_enabled: bool = False,
    ) -> HTMLResponse:
        try:
            pending_update = load_pending_knowledge_update(resolved_data_dir)
        except Exception:
            pending_update = None
        try:
            available_github_update = load_available_github_knowledge_update(
                resolved_data_dir
            )
        except Exception:
            available_github_update = None
        template = TEMPLATES.get_template("knowledge_updates.html")
        return HTMLResponse(
            template.render(
                pending_update=pending_update,
                available_github_update=available_github_update,
                update_status=None,
                automatic_check_status=automatic_check_status,
                automatic_check_enabled=automatic_check_enabled,
                installation_status=installation_status_snapshot(),
            )
        )

    def render_installation_status() -> HTMLResponse:
        template = TEMPLATES.get_template("installation_status.html")
        return HTMLResponse(
            template.render(installation_status=installation_status_snapshot())
        )

    def _page_context(
        *,
        setup_form: ProviderConfiguration | None = None,
        setup_error: str = "",
        setup_reason: str = "",
        active_question: str = "",
        composer_error: str = "",
        active_conversation: dict[str, Any] | None = None,
        update_status: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        corpus_error = ""
        try:
            ensure_minimal_knowledge_release(
                resolved_data_dir,
                embedding_provider=embedding_provider,
                trust_root_path=resolved_trust_root_path,
            )
            corpus = active_corpus_summary(resolved_data_dir)
        except Exception as exc:
            corpus_error = _retrieval_failure_message(exc)
            corpus = _unavailable_corpus_summary()
        configuration = _load_configuration_or_none(resolved_config_path)
        if setup_form is None:
            setup_form = configuration or _default_provider_configuration()
        storage_error = ""
        try:
            conversations = store.list_conversations()
        except Exception as exc:
            storage_error = _storage_failure_message("opening conversation history", exc)
            conversations = []
        try:
            pending_update = load_pending_knowledge_update(resolved_data_dir)
        except Exception:
            pending_update = None
        try:
            available_github_update = load_available_github_knowledge_update(
                resolved_data_dir
            )
        except Exception:
            available_github_update = None
        return {
            "configuration": configuration.to_public_dict() if configuration else None,
            "providers": provider_options(),
            "form": setup_form,
            "setup_error": setup_error,
            "setup_reason": setup_reason,
            "active_question": active_question,
            "composer_error": composer_error,
            "active_conversation": active_conversation,
            "conversations": conversations,
            "storage_error": storage_error,
            "corpus": corpus,
            "corpus_error": corpus_error,
            "pending_update": pending_update,
            "available_github_update": available_github_update,
            "update_status": update_status,
            "automatic_check_enabled": not use_local_release_catalog,
            "automatic_check_status": (
                {
                    "state": "pending",
                    "kind": "pending",
                    "role": "status",
                    "title": "Checking release metadata automatically",
                    "message": (
                        "This check sends no questions, answers, evidence, or "
                        "conversation records. Downloads and installation still require "
                        "separate approval."
                    ),
                }
                if not use_local_release_catalog
                else None
            ),
            "installation_status": installation_status_snapshot(),
        }

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        return render_home(
            update_status=_update_status_from_request(request, resolved_data_dir)
        )

    @app.get("/conversations/export.json")
    async def export_conversations() -> Response:
        try:
            payload = store.export_conversations()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=_storage_failure_message("exporting conversation records", exc),
            ) from exc
        return _json_download_response(
            payload,
            filename="danish-rag-conversation-records.json",
        )

    @app.get("/conversations/{conversation_id}", response_class=HTMLResponse)
    async def conversation(conversation_id: str) -> HTMLResponse:
        try:
            record = store.get_conversation(conversation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Conversation not found.") from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=_storage_failure_message("opening a conversation record", exc),
            ) from exc
        return render_home(active_conversation=record)

    @app.get("/conversations/{conversation_id}/export.json")
    async def export_conversation(conversation_id: str) -> Response:
        try:
            payload = store.export_conversation(conversation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Conversation not found.") from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=_storage_failure_message("exporting conversation records", exc),
            ) from exc
        return _json_download_response(
            payload,
            filename=f"danish-rag-conversation-{conversation_id}.json",
        )

    @app.post("/conversations/{conversation_id}/delete")
    async def delete_conversation(request: Request, conversation_id: str) -> RedirectResponse:
        _validate_state_changing_request(request)
        try:
            store.delete_conversation(conversation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Conversation not found.") from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=_storage_failure_message("deleting conversation records", exc),
            ) from exc
        return RedirectResponse("/", status_code=303)

    @app.post("/conversations/delete-all")
    async def delete_all_conversations(request: Request) -> RedirectResponse:
        _validate_state_changing_request(request)
        form_data = await _read_urlencoded_form(request)
        confirmation = form_data.get("confirmation", "").strip()
        if confirmation != "DELETE ALL LOCAL CONVERSATIONS":
            raise HTTPException(
                status_code=422,
                detail='Type "DELETE ALL LOCAL CONVERSATIONS" to delete all records.',
            )
        try:
            store.delete_all_conversations()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=_storage_failure_message("deleting conversation records", exc),
            ) from exc
        return RedirectResponse("/", status_code=303)

    @app.post("/knowledge-updates/check")
    async def check_knowledge_updates(request: Request) -> RedirectResponse:
        _validate_state_changing_request(request)
        acquire_update_record_lock_or_conflict()
        try:
            if installation_is_running():
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Wait for the current knowledge release installation "
                        "before checking for another update."
                    ),
                )
            ensure_minimal_knowledge_release(
                resolved_data_dir,
                embedding_provider=embedding_provider,
                trust_root_path=resolved_trust_root_path,
            )
            if use_local_release_catalog:
                update = discover_knowledge_update(
                    resolved_data_dir,
                    resolved_release_catalog_dir,
                    trust_root_path=resolved_trust_root_path,
                )
                save_pending_knowledge_update(resolved_data_dir, update)
            else:
                assert resolved_github_release_client is not None
                prepared = load_pending_knowledge_update(resolved_data_dir)
                prepared_distribution = (
                    prepared.get("distribution")
                    if isinstance(prepared, dict)
                    else None
                )
                if (
                    isinstance(prepared_distribution, dict)
                    and prepared_distribution.get("channel") == "github-releases"
                ):
                    dismiss_available_github_knowledge_update(resolved_data_dir)
                else:
                    update = select_github_knowledge_update(
                        resolved_data_dir,
                        resolved_github_release_client.list_published_releases(),
                    )
                    save_available_github_knowledge_update(resolved_data_dir, update)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=_knowledge_update_failure_message("checking", exc),
            ) from exc
        finally:
            update_record_lock.release()
        return RedirectResponse("/", status_code=303)

    @app.post("/knowledge-updates/automatic-check", response_class=HTMLResponse)
    async def automatic_knowledge_update_check(request: Request) -> HTMLResponse:
        _validate_state_changing_request(request)
        if use_local_release_catalog or resolved_github_release_client is None:
            return render_knowledge_updates(
                automatic_check_status={
                    "state": "unavailable",
                    "kind": "neutral",
                    "role": "status",
                    "title": "Automatic release metadata check unavailable",
                    "message": "Use the manual check for this local release catalogue.",
                }
            )

        now = update_check_clock()
        with automatic_check_lock:
            last_attempt = automatic_check_state["last_attempt"]
            if automatic_check_state["running"]:
                check_disposition = "running"
            elif (
                isinstance(last_attempt, int | float)
                and now - last_attempt
                < float(automatic_update_check_interval_seconds)
            ):
                check_disposition = "throttled"
            else:
                automatic_check_state["last_attempt"] = now
                automatic_check_state["running"] = True
                automatic_check_state["status"] = {
                    "state": "running",
                    "kind": "neutral",
                    "role": "status",
                    "title": "Checking release metadata automatically",
                    "message": (
                        "Only bounded release metadata is being checked. Ordinary local "
                        "use remains available."
                    ),
                }
                check_disposition = "run"

        if check_disposition == "running":
            return render_knowledge_updates(
                automatic_check_status={
                    "state": "running",
                    "kind": "neutral",
                    "role": "status",
                    "title": "Automatic release metadata check already running",
                    "message": "Ordinary local use remains available while it finishes.",
                }
            )
        if check_disposition == "throttled":
            return render_knowledge_updates(
                automatic_check_status={
                    "state": "completed",
                    "kind": "neutral",
                    "role": "status",
                    "title": "Automatic release metadata check recently completed",
                    "message": "The next automatic check is deferred by the local throttle.",
                }
            )

        worker = threading.Thread(
            target=run_automatic_metadata_check,
            name="knowledge-release-automatic-metadata-check",
            daemon=True,
        )
        worker.start()
        return render_knowledge_updates(
            automatic_check_status=automatic_check_status_snapshot()
        )

    @app.get("/knowledge-updates/automatic-check-status", response_class=HTMLResponse)
    async def automatic_knowledge_update_check_status() -> HTMLResponse:
        return render_knowledge_updates(
            automatic_check_status=automatic_check_status_snapshot()
        )

    @app.post("/knowledge-updates/dismiss")
    async def dismiss_knowledge_update(request: Request) -> RedirectResponse:
        _validate_state_changing_request(request)
        acquire_update_record_lock_or_conflict()
        try:
            if installation_is_running():
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Wait for the current knowledge release installation before "
                        "dismissing update records."
                    ),
                )
            dismiss_pending_knowledge_update(resolved_data_dir)
            dismiss_available_github_knowledge_update(resolved_data_dir)
        finally:
            update_record_lock.release()
        return RedirectResponse("/", status_code=303)

    @app.post("/knowledge-updates/download")
    async def download_knowledge_update(request: Request) -> RedirectResponse:
        _validate_state_changing_request(request)
        if use_local_release_catalog or resolved_github_release_client is None:
            raise HTTPException(
                status_code=409,
                detail="GitHub knowledge release downloads are not enabled for this app.",
            )
        form_data = await _read_urlencoded_form(request)
        requested_release_id = form_data.get("release_id", "").strip()
        requested_asset_name = form_data.get("asset_name", "").strip()
        try:
            requested_asset_id = int(form_data.get("asset_id", "").strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="GitHub release asset ID is invalid.",
            ) from exc
        approval = ArtifactDownloadApproval(
            approved=True,
            requested_knowledge_release_id=requested_release_id,
            artifact_id=requested_asset_id,
            artifact_name=requested_asset_name,
        )
        acquire_update_record_lock_or_conflict()
        try:
            if installation_is_running():
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Wait for the current knowledge release installation before "
                        "downloading another update."
                    ),
                )
            prepare_github_knowledge_update(
                resolved_data_dir,
                resolved_github_release_client,
                approval=approval,
                trust_root_path=resolved_trust_root_path,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=_knowledge_update_failure_message("downloading and verifying", exc),
            ) from exc
        finally:
            update_record_lock.release()
        return RedirectResponse("/", status_code=303)

    @app.post("/knowledge-updates/install")
    async def install_knowledge_update(request: Request) -> RedirectResponse:
        nonlocal installation_state
        _validate_state_changing_request(request)
        form_data = await _read_urlencoded_form(request)
        requested_release_id = form_data.get("release_id", "").strip()
        acquire_update_record_lock_or_conflict()
        try:
            try:
                pending_update = load_pending_knowledge_update(resolved_data_dir)
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail=_knowledge_update_failure_message("loading pending update", exc),
                ) from exc
            if not pending_update:
                raise HTTPException(
                    status_code=409,
                    detail="No reviewed knowledge update is pending.",
                )
            pending_release_id = pending_update["release"]["knowledge_release_id"]
            if requested_release_id != pending_release_id:
                raise HTTPException(
                    status_code=409,
                    detail="Requested release is not pending review.",
                )
            distribution = pending_update.get("distribution")
            is_github_release = (
                isinstance(distribution, dict)
                and distribution.get("channel") == "github-releases"
            )
            release_dir = (
                prepared_github_knowledge_release_dir(
                    resolved_data_dir,
                    requested_release_id,
                )
                if is_github_release
                else resolved_release_catalog_dir / requested_release_id
            )
            previous_release_id = _active_release_id(resolved_data_dir) or "Unavailable"
            with installation_state_lock:
                if (
                    installation_state is not None
                    and installation_state["state"] == "running"
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="A knowledge release installation is already running.",
                    )
                installation_state = {
                    "release_id": requested_release_id,
                    "state": "running",
                    "events": [],
                    "active_release_id": previous_release_id,
                    "previous_release_id": previous_release_id,
                    "rolled_back": False,
                    "error_type": None,
                }
        finally:
            update_record_lock.release()
        worker = threading.Thread(
            target=run_installation_job,
            kwargs={
                "release_id": requested_release_id,
                "release_dir": release_dir,
                "previous_release_id": previous_release_id,
            },
            name=f"knowledge-release-install-{requested_release_id}",
            daemon=True,
        )
        worker.start()
        return RedirectResponse("/#knowledge-installation-status", status_code=303)

    @app.get("/knowledge-updates/install-status", response_class=HTMLResponse)
    async def knowledge_update_install_status() -> HTMLResponse:
        return render_installation_status()

    @app.get("/vendor/htmx.min.js", include_in_schema=False)
    async def htmx_asset() -> FileResponse:
        asset_path = ROOT / "node_modules" / "htmx.org" / "dist" / "htmx.min.js"
        if not asset_path.exists():
            raise HTTPException(status_code=404, detail="Run npm install to install HTMX.")
        return FileResponse(asset_path, media_type="application/javascript")

    @app.post("/setup")
    async def setup(request: Request) -> Response:
        _validate_state_changing_request(request)
        form_data = await _read_urlencoded_form(request)
        attempted = normalize_provider_form(form_data)
        failures = validate_provider_configuration(attempted)
        if failures:
            is_htmx = _is_htmx_request(request)
            renderer = render_setup_panel if is_htmx else render_home
            return renderer(
                status_code=200 if is_htmx else 422,
                setup_form=attempted,
                setup_error=" ".join(failures),
                setup_reason="invalid_configuration",
            )

        result = CapabilityTestResult.from_value(tester(attempted))
        if not result.ok:
            is_htmx = _is_htmx_request(request)
            renderer = render_setup_panel if is_htmx else render_home
            return renderer(
                status_code=200 if is_htmx else 422,
                setup_form=attempted,
                setup_error=result.message,
                setup_reason=result.reason,
            )

        save_provider_configuration(
            resolved_config_path,
            validated_configuration(attempted, result),
        )
        if _is_htmx_request(request):
            return HTMLResponse("", status_code=204, headers={"HX-Redirect": "/"})
        return RedirectResponse("/", status_code=303)

    @app.post("/ask")
    async def ask(request: Request) -> HTMLResponse:
        _validate_state_changing_request(request)
        form_data = await _read_urlencoded_form(request)
        question = form_data.get("question", "").strip()
        conversation_id = form_data.get("conversation_id", "").strip() or None
        if not question:
            return render_ask_response(
                request,
                status_code=422,
                active_question=question,
                composer_error="Enter a question before sending.",
            )
        configuration = _load_configuration_or_none(resolved_config_path)
        if not configuration:
            return render_ask_response(
                request,
                status_code=409,
                active_question=question,
                composer_error=(
                    "Connect and test a local generation-model provider before asking "
                    "the first question."
                ),
            )
        active_conversation_for_error = None
        conversation_turns = None
        if conversation_id:
            try:
                active_conversation_for_error = store.get_conversation(conversation_id)
                conversation_turns = active_conversation_for_error["turns"]
            except KeyError:
                return render_ask_response(
                    request,
                    status_code=404,
                    active_question=question,
                    composer_error="Conversation not found. Start a new conversation and retry.",
                )
            except Exception as exc:
                return render_ask_response(
                    request,
                    status_code=503,
                    active_question=question,
                    composer_error=_storage_failure_message(
                        "opening a conversation record",
                        exc,
                    ),
                )
        try:
            ensure_minimal_knowledge_release(
                resolved_data_dir,
                embedding_provider=embedding_provider,
                trust_root_path=resolved_trust_root_path,
            )
            retriever = HybridRetriever.from_data_dir(
                resolved_data_dir,
                embedding_provider=embedding_provider,
            )
            result = AnswerService(
                retriever=retriever,
                generator=generator,
            ).answer(
                question,
                configuration,
                conversation_turns=conversation_turns,
            )
            try:
                record = store.save_answer(
                    question=result.question,
                    normalized_question=result.normalized_question,
                    answer=result.answer,
                    model_identity=result.model_identity,
                    corpus_identity=result.corpus_identity,
                    conversation_id=conversation_id,
                )
            except Exception as exc:
                return render_ask_response(
                    request,
                    status_code=503,
                    active_question=question,
                    active_conversation=active_conversation_for_error,
                    composer_error=_storage_failure_message(
                        "saving conversation records",
                        exc,
                    ),
                )
        except AnswerValidationError as exc:
            return render_ask_response(
                request,
                status_code=422,
                active_question=question,
                composer_error=(
                    "Structured answer validation failed; no answer was saved. "
                    f"{exc}"
                ),
                active_conversation=active_conversation_for_error,
            )
        except AnswerPipelineError as exc:
            return render_ask_response(
                request,
                status_code=503,
                active_question=question,
                composer_error=_provider_failure_message(exc),
                active_conversation=active_conversation_for_error,
            )
        except (
            FileNotFoundError,
            json.JSONDecodeError,
            KeyError,
            KnowledgeReleaseError,
            RetrievalError,
        ) as exc:
            return render_ask_response(
                request,
                status_code=503,
                active_question=question,
                composer_error=_retrieval_failure_message(exc),
                active_conversation=active_conversation_for_error,
            )
        except Exception as exc:
            return render_ask_response(
                request,
                status_code=503,
                active_question=question,
                active_conversation=active_conversation_for_error,
                composer_error=(
                    "The answer path failed safely before saving a complete answer. "
                    "Check the active corpus, local index, and local conversation storage, then retry. "
                    f"Detail: {exc}"
                ),
            )
        return render_ask_response(
            request,
            status_code=200,
            active_conversation=record,
        )

    @app.get("/status")
    async def status() -> dict[str, Any]:
        configuration = _load_configuration_or_none(resolved_config_path)
        corpus_error = ""
        try:
            ensure_minimal_knowledge_release(
                resolved_data_dir,
                embedding_provider=embedding_provider,
                trust_root_path=resolved_trust_root_path,
            )
            corpus = active_corpus_summary(resolved_data_dir)
        except Exception as exc:
            corpus_error = _retrieval_failure_message(exc)
            corpus = _unavailable_corpus_summary()
        return {
            "configured": configuration is not None,
            "configuration": configuration.to_public_dict() if configuration else None,
            "corpus": corpus,
            "corpus_error": corpus_error,
        }

    return app


app = create_app()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Danish Immigration RAG local app.")
    parser.add_argument("--host", default=None, help="Application bind host.")
    parser.add_argument("--port", default=None, type=int, help="Application bind port.")
    parser.add_argument("--policy", default="config/runtime-policy.json")
    args = parser.parse_args(argv)

    policy = load_runtime_policy(args.policy)
    host = args.host or policy["application"]["default_bind_host"]
    port = args.port or int(policy["application"]["default_bind_port"])

    import uvicorn

    uvicorn.run("danish_rag.local_app:app", host=host, port=port)
    return 0


async def _read_urlencoded_form(request: Request) -> dict[str, str]:
    form = await request.form()
    return {key: str(value) for key, value in form.items()}


def _load_configuration_or_none(path: Path) -> ProviderConfiguration | None:
    try:
        return load_provider_configuration(path)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None
    except Exception:
        return None


def _default_provider_configuration() -> ProviderConfiguration:
    provider = provider_options()[0]
    return ProviderConfiguration(
        provider_id=provider.id,
        endpoint=provider.default_endpoint,
        model=provider.default_model,
    )


def _is_htmx_request(request: Request) -> bool:
    return request.headers.get("hx-request", "").casefold() == "true"


def _validate_state_changing_request(request: Request) -> None:
    host_header = request.headers.get("host", "")
    host = _host_without_port(host_header)
    if host not in {"testserver", "127.0.0.1", "localhost", "::1"}:
        raise HTTPException(status_code=403, detail="State-changing requests require a loopback Host.")

    origin = request.headers.get("origin")
    if not origin:
        raise HTTPException(status_code=403, detail="State-changing requests require an Origin header.")

    parsed_origin = urlparse(origin)
    origin_host = parsed_origin.hostname or ""
    if parsed_origin.scheme not in {"http", "https"}:
        raise HTTPException(status_code=403, detail="State-changing requests require an HTTP Origin.")
    if origin_host not in {"testserver", "127.0.0.1", "localhost", "::1"}:
        raise HTTPException(status_code=403, detail="State-changing requests require a loopback Origin.")
    if _normalize_netloc(parsed_origin.netloc) != _normalize_netloc(host_header):
        raise HTTPException(status_code=403, detail="Origin must match Host.")


def _host_without_port(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("[") and "]" in host:
        return host[1 : host.index("]")]
    return host.split(":", 1)[0]


def _normalize_netloc(value: str) -> str:
    return value.strip().lower()


def _provider_failure_message(exc: BaseException) -> str:
    return (
        "Local generation provider failed while preparing the answer; no answer was "
        "saved. Check that the configured local provider is running and that the "
        f"selected model can return structured JSON, then retry. Detail: {exc}"
    )


def _retrieval_failure_message(exc: BaseException) -> str:
    return (
        "Local retrieval index is unavailable for the active corpus; no answer was "
        "generated or saved. Reinstall or rebuild the active knowledge release, then "
        f"retry. Detail: {exc}"
    )


def _storage_failure_message(action: str, exc: BaseException) -> str:
    return (
        f"Local conversation storage failed while {action}; no persistence, deletion, "
        "or export success was reported. Check local disk access and retry. "
        f"Detail: {exc}"
    )


def _knowledge_update_failure_message(action: str, exc: BaseException) -> str:
    return (
        f"Knowledge update failed while {action}; the previously active corpus/index "
        f"pair remains active. Detail: {exc}"
    )


def _check_github_update_metadata(
    *,
    data_dir: Path,
    client: GitHubReleaseClient,
    embedding_provider: EmbeddingProvider | None,
    trust_root_path: Path | None,
) -> None:
    """Check content-free GitHub metadata without downloading or installing a release."""

    ensure_minimal_knowledge_release(
        data_dir,
        embedding_provider=embedding_provider,
        trust_root_path=trust_root_path,
    )
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
    )
    save_available_github_knowledge_update(data_dir, update)


def _update_status_from_request(request: Request, data_dir: Path) -> dict[str, str] | None:
    if request.query_params.get("update_status") != "installed":
        return None
    active_release = _active_release_id(data_dir)
    if not active_release:
        return None
    requested_release_id = request.query_params.get("release_id", "").strip()
    if requested_release_id and requested_release_id != active_release:
        return {
            "kind": "error",
            "role": "alert",
            "title": "Knowledge update status expired",
            "message": (
                "The requested install status no longer matches the local active "
                f"corpus. Active corpus: {active_release}."
            ),
        }
    return {
        "kind": "success",
        "role": "status",
        "title": "Knowledge update installed",
        "message": (
            f"Active corpus: {active_release}. Future answers use this reviewed knowledge "
            "release; historical conversation records keep their original corpus identity."
        ),
    }


def _rollback_update_status(data_dir: Path, exc: BaseException) -> dict[str, str]:
    active_release = _active_release_id(data_dir) or "Unavailable"
    return {
        "kind": "error",
        "role": "alert",
        "title": "Knowledge update rolled back",
        "message": (
            f"{_knowledge_update_failure_message('installing', exc)} "
            f"Active corpus: {active_release}."
        ),
    }


def _active_release_id(data_dir: Path) -> str:
    try:
        return active_corpus_summary(data_dir)["knowledge_release_id"]
    except Exception:
        return ""


def _unavailable_corpus_summary() -> dict[str, str]:
    return {
        "knowledge_release_id": "Unavailable",
        "corpus_id": "Unavailable",
        "source_registry_version": "Unavailable",
        "created_at_utc": "Unavailable",
        "embedding_model": "Unavailable",
        "embedding_vector_dimensions": "Unavailable",
        "index_schema_version": "Unavailable",
    }


def _json_download_response(payload: dict[str, Any], *, filename: str) -> Response:
    return Response(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


if __name__ == "__main__":
    raise SystemExit(main())
