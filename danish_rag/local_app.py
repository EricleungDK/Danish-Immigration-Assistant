"""Local web application for Danish Immigration RAG."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
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
    LexicalAnswerVerifier,
    ModelAnswerVerifier,
    TransportAnswerGenerator,
    fixed_transport,
    provider_transport,
)
from .conversation_store import ConversationStore
from .embedding_provider import EmbeddingProvider, resolve_embedding_provider
from .retriever_cache import RetrieverCache, release_cache_key
from .evidence_integrity import system_utc_clock
from .github_release_client import GitHubReleaseClient
from .local_model_transport import LocalModelTransport
from .social_conversation import (
    SocialConversation,
    TransportSocialReplies,
    classify_conversation_turn,
)
from .knowledge_release import (
    APPLICATION_VERSION,
    BUNDLED_MINIMAL_RELEASE,
    DEFAULT_RELEASE_CATALOG_DIR,
    KnowledgeReleaseError,
    corpus_summary,
    default_data_dir,
    ensure_minimal_knowledge_release,
    load_available_github_knowledge_update,
    load_pending_knowledge_update,
    newest_bundled_release_dir,
)
from .knowledge_update_controller import (
    AUTOMATIC_UPDATE_CHECK_INTERVAL_SECONDS,
    KnowledgeUpdateConflict,
    KnowledgeUpdateController,
    KnowledgeUpdateUnavailable,
    active_release_id,
    knowledge_update_failure_message,
)
from .provider_setup import (
    CapabilityTestResult,
    ModelDiscoveryResult,
    ProviderCapabilityTester,
    ProviderConfiguration,
    ProviderModelDiscoverer,
    default_config_path,
    load_provider_configuration,
    normalize_provider_form,
    provider_options,
    save_provider_configuration,
    validate_provider_configuration,
    validated_configuration,
)
from .retrieval import (
    HybridRetriever,
    RetrievalError,
    embedding_model_profile,
    inspect_embedding_model,
)
from .runtime_policy import load_runtime_policy
from .snapshot_clock import (
    SnapshotState,
    current_snapshot_state,
    snapshot_aware_clock,
    snapshot_clock,
)


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = Path(__file__).resolve().parent / "web"
TEMPLATES = Environment(
    loader=FileSystemLoader(WEB_ROOT / "templates"),
    autoescape=select_autoescape(["html", "xml"]),
)


def create_app(
    *,
    config_path: str | Path | None = None,
    data_dir: str | Path | None = None,
    answer_generator: Any | None = None,
    transport: LocalModelTransport | None = None,
    capability_tester: Callable[[ProviderConfiguration], CapabilityTestResult | dict[str, Any]]
    | None = None,
    model_discoverer: Callable[
        [ProviderConfiguration],
        ModelDiscoveryResult | dict[str, Any],
    ]
    | None = None,
    release_catalog_dir: str | Path | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    trust_root_path: str | Path | None = None,
    github_release_client: GitHubReleaseClient | None = None,
    automatic_update_check_interval_seconds: float = (
        AUTOMATIC_UPDATE_CHECK_INTERVAL_SECONDS
    ),
    update_check_clock: Callable[[], float] = time.monotonic,
    initial_release_dir: str | Path | Callable[..., str | Path] = BUNDLED_MINIMAL_RELEASE,
    clock: Callable[[], datetime] = system_utc_clock,
) -> FastAPI:
    app = FastAPI(title="Danish Immigration RAG")
    # Freshness is judged at this time (a bundled release's snapshot time inside a request
    # that uses one); record and install timestamps stay on the real clock.
    request_clock = snapshot_aware_clock(clock)
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
    # A callable is resolved lazily, only when a fresh install needs it.
    app.state.initial_release_dir = (
        initial_release_dir if callable(initial_release_dir) else Path(initial_release_dir)
    )

    app.add_middleware(_SnapshotClockMiddleware)

    def ensure_release_unscoped() -> dict[str, Any]:
        """Verified active release with no snapshot state (background workers)."""

        return ensure_minimal_knowledge_release(
            resolved_data_dir,
            release_dir=app.state.initial_release_dir,
            embedding_provider=embedding_provider,
            trust_root_path=resolved_trust_root_path,
            clock=clock,
        )

    def ensure_release() -> dict[str, Any]:
        """Verified active release for a request; its manifest sets the snapshot state."""

        state = current_snapshot_state()
        try:
            release = ensure_release_unscoped()
        except Exception as exc:
            state.mark_unavailable(exc)
            raise
        # Snapshot state comes only from the verified release this request uses (also
        # right after a fresh install).
        state.adopt(release["manifest"])
        return release

    retriever_cache: RetrieverCache[HybridRetriever] = RetrieverCache()
    tester = capability_tester or ProviderCapabilityTester()
    discover_models = model_discoverer or ProviderModelDiscoverer()
    if answer_generator is not None and transport is not None:
        raise ValueError("Choose either an injected answer generator or a transport.")
    if answer_generator is not None:
        # Fixture path only (browser/monitor/test fixtures; deleted with the generator
        # protocol in #77): a controlled generator gets the deterministic lexical guard.
        # No production entry point passes answer_generator; live runs use a transport.
        generator, verifier = answer_generator, LexicalAnswerVerifier()
        social = SocialConversation(answer_generator)
    else:
        # Production: the configured provider's transport (or the injected one) drafts,
        # verifies with a separate request, and repairs once.
        transports = fixed_transport(transport) if transport is not None else provider_transport()
        generator = TransportAnswerGenerator(transports)
        verifier = ModelAnswerVerifier(transports)
        social = SocialConversation(TransportSocialReplies(transports))
    store = ConversationStore(resolved_data_dir / "conversations.sqlite3")
    update_controller = KnowledgeUpdateController(
        data_dir=resolved_data_dir,
        release_catalog_dir=resolved_release_catalog_dir,
        github_release_client=resolved_github_release_client,
        embedding_provider=embedding_provider,
        trust_root_path=resolved_trust_root_path,
        ensure_release=ensure_release_unscoped,
        automatic_check_interval_seconds=automatic_update_check_interval_seconds,
        clock=update_check_clock,
        evaluation_clock=clock,
    )
    app.state.update_controller = update_controller

    def render_home(
        *,
        status_code: int = 200,
        setup_form: ProviderConfiguration | None = None,
        setup_error: str = "",
        setup_error_title: str = "Connection test failed",
        setup_reason: str = "",
        discovered_models: list[str] | None = None,
        active_question: str = "",
        composer_error: str = "",
        active_conversation: dict[str, Any] | None = None,
        update_status: dict[str, str] | None = None,
        delete_all_error: str = "",
        delete_all_status: str = "",
    ) -> HTMLResponse:
        template = TEMPLATES.get_template("home.html")
        return HTMLResponse(
            template.render(
                _page_context(
                    setup_form=setup_form,
                    setup_error=setup_error,
                    setup_error_title=setup_error_title,
                    setup_reason=setup_reason,
                    discovered_models=discovered_models,
                    active_question=active_question,
                    composer_error=composer_error,
                    active_conversation=active_conversation,
                    update_status=update_status,
                    delete_all_error=delete_all_error,
                    delete_all_status=delete_all_status,
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
        history_oob: bool = False,
        reveal_latest_turn: bool = False,
    ) -> HTMLResponse:
        template = TEMPLATES.get_template("conversation_main.html")
        return HTMLResponse(
            template.render(
                _page_context(
                    active_question=active_question,
                    composer_error=composer_error,
                    active_conversation=active_conversation,
                    history_oob=history_oob,
                    reveal_latest_turn=reveal_latest_turn,
                    # htmx swaps only #conversation-main: refresh the banner out of band.
                    banner_oob=True,
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
        history_oob: bool = False,
        reveal_latest_turn: bool = False,
    ) -> HTMLResponse:
        if _is_htmx_request(request):
            return render_conversation_main(
                status_code=status_code,
                active_question=active_question,
                composer_error=composer_error,
                active_conversation=active_conversation,
                history_oob=history_oob,
                reveal_latest_turn=reveal_latest_turn,
            )
        return render_home(
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
        setup_error_title: str = "Connection test failed",
        setup_reason: str = "",
        discovered_models: list[str] | None = None,
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
                setup_error_title=setup_error_title,
                setup_reason=setup_reason,
                discovered_models=discovered_models,
            ),
            status_code=status_code,
        )

    def render_knowledge_updates(
        *,
        manual_check_message: str | None = None,
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
        if manual_check_message == "checked":
            manual_check_message = (
                "Knowledge update available. Review the release details below."
                if pending_update or available_github_update
                else "Check complete. No newer knowledge update is available."
            )
        template = TEMPLATES.get_template("knowledge_updates.html")
        return HTMLResponse(
            template.render(
                pending_update=pending_update,
                available_github_update=available_github_update,
                update_status=None,
                manual_check_message=manual_check_message,
                automatic_check_status=automatic_check_status,
                automatic_check_enabled=automatic_check_enabled,
                installation_status=update_controller.installation_status(),
            )
        )

    def render_installation_status() -> HTMLResponse:
        template = TEMPLATES.get_template("installation_status.html")
        status = update_controller.installation_status()
        banner: dict[str, Any] = {}
        if status is not None and status.get("state") != "running":
            # The active release may have changed: refresh the banner from what is
            # actually active now (verified), out of band.
            try:
                ensure_release()
            except Exception:
                pass  # the state is marked unavailable and logged
            banner = {**_snapshot_banner_context(), "banner_oob": True}
        return HTMLResponse(template.render(installation_status=status, **banner))

    def _page_context(
        *,
        setup_form: ProviderConfiguration | None = None,
        setup_error: str = "",
        setup_error_title: str = "Connection test failed",
        setup_reason: str = "",
        discovered_models: list[str] | None = None,
        active_question: str = "",
        composer_error: str = "",
        active_conversation: dict[str, Any] | None = None,
        update_status: dict[str, str] | None = None,
        delete_all_error: str = "",
        delete_all_status: str = "",
        history_oob: bool = False,
        reveal_latest_turn: bool = False,
        banner_oob: bool = False,
    ) -> dict[str, Any]:
        corpus_error = ""
        try:
            # The panel describes the same verified release the snapshot state came from.
            corpus = corpus_summary(ensure_release())
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
            "setup_error_title": setup_error_title,
            "setup_reason": setup_reason,
            "discovered_models": discovered_models,
            "active_question": active_question,
            "composer_error": composer_error,
            "active_conversation": active_conversation,
            "conversations": conversations,
            "storage_error": storage_error,
            "delete_all_error": delete_all_error,
            "delete_all_status": delete_all_status,
            "history_oob": history_oob,
            "reveal_latest_turn": reveal_latest_turn,
            "corpus": corpus,
            "corpus_error": corpus_error,
            **_snapshot_banner_context(),
            "banner_oob": banner_oob,
            "pending_update": pending_update,
            "available_github_update": available_github_update,
            "update_status": update_status,
            "automatic_check_enabled": update_controller.automatic_check_enabled,
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
                if update_controller.automatic_check_enabled
                else None
            ),
            "installation_status": update_controller.installation_status(),
        }

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        return render_home(
            update_status=_update_status_from_request(
                request,
                resolved_data_dir,
                trust_root_path=resolved_trust_root_path,
            ),
            delete_all_status=(
                "All local conversation records deleted."
                if request.query_params.get("records_deleted") == "all"
                else ""
            ),
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
    async def delete_all_conversations(request: Request) -> Response:
        _validate_state_changing_request(request)
        form_data = await _read_urlencoded_form(request)
        confirmation = form_data.get("confirmation", "").strip()
        if confirmation != "DELETE ALL LOCAL CONVERSATIONS":
            return render_home(
                status_code=422,
                delete_all_error=(
                    'Type "DELETE ALL LOCAL CONVERSATIONS" to delete all records.'
                ),
            )
        try:
            store.delete_all_conversations()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=_storage_failure_message("deleting conversation records", exc),
            ) from exc
        return RedirectResponse("/?records_deleted=all", status_code=303)

    @app.post("/knowledge-updates/check", response_model=None)
    async def check_knowledge_updates(request: Request) -> HTMLResponse | RedirectResponse:
        _validate_state_changing_request(request)
        try:
            update_controller.manual_check(ensure_release)
        except KnowledgeUpdateConflict as exc:
            if _is_htmx_request(request):
                return render_knowledge_updates(manual_check_message=str(exc))
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KnowledgeUpdateUnavailable as exc:
            if _is_htmx_request(request):
                return render_knowledge_updates(manual_check_message=str(exc))
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if _is_htmx_request(request):
            return render_knowledge_updates(manual_check_message="checked")
        return RedirectResponse("/?update_status=checked#knowledge-updates", status_code=303)

    @app.post("/knowledge-updates/automatic-check", response_class=HTMLResponse)
    async def automatic_knowledge_update_check(request: Request) -> HTMLResponse:
        _validate_state_changing_request(request)
        return render_knowledge_updates(
            automatic_check_status=update_controller.request_automatic_check()
        )

    @app.get("/knowledge-updates/automatic-check-status", response_class=HTMLResponse)
    async def automatic_knowledge_update_check_status() -> HTMLResponse:
        return render_knowledge_updates(
            automatic_check_status=update_controller.automatic_check_status()
        )

    @app.post("/knowledge-updates/dismiss")
    async def dismiss_knowledge_update(request: Request) -> RedirectResponse:
        _validate_state_changing_request(request)
        try:
            update_controller.dismiss()
        except KnowledgeUpdateConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse("/", status_code=303)

    @app.post("/knowledge-updates/download", response_model=None)
    async def download_knowledge_update(request: Request) -> HTMLResponse | RedirectResponse:
        _validate_state_changing_request(request)
        if not update_controller.github_downloads_enabled:
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
        try:
            update_controller.approve_download(
                release_id=requested_release_id,
                asset_id=requested_asset_id,
                asset_name=requested_asset_name,
            )
        except KnowledgeUpdateConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KnowledgeUpdateUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if _is_htmx_request(request):
            return render_knowledge_updates(
                manual_check_message="Download verified. Review the signed release details before installing."
            )
        return RedirectResponse("/", status_code=303)

    @app.post("/knowledge-updates/install")
    async def install_knowledge_update(request: Request) -> RedirectResponse:
        _validate_state_changing_request(request)
        form_data = await _read_urlencoded_form(request)
        requested_release_id = form_data.get("release_id", "").strip()
        try:
            update_controller.start_install(requested_release_id)
        except KnowledgeUpdateConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KnowledgeUpdateUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
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

    @app.post("/setup/models")
    async def setup_models(request: Request) -> HTMLResponse:
        _validate_state_changing_request(request)
        form_data = await _read_urlencoded_form(request)
        attempted = normalize_provider_form(form_data)
        result = ModelDiscoveryResult.from_value(discover_models(attempted))
        if not result.ok:
            return render_setup_panel(
                setup_form=attempted,
                setup_error_title="Unable to discover local models",
                setup_error=result.message,
                setup_reason=result.reason,
            )
        selected_model = (
            attempted.model
            if attempted.model in result.models
            else result.models[0]
        )
        return render_setup_panel(
            setup_form=ProviderConfiguration(
                provider_id=attempted.provider_id,
                endpoint=attempted.endpoint,
                model=selected_model,
            ),
            discovered_models=result.models,
        )

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
            verified_release = ensure_release()
            snapshot_state = current_snapshot_state()
            try:
                # Keyed by the release this request just verified plus the live embedding
                # model identity (checked every request, as a fresh build did); the clock
                # is read per retrieve().
                index_metadata = verified_release["index"]
                live_identity = inspect_embedding_model(
                    resolve_embedding_provider(embedding_provider),
                    str(embedding_model_profile(index_metadata["embedding_model"])["name"]),
                )
                built_here: list[HybridRetriever] = []

                def build_retriever() -> HybridRetriever:
                    built_here.append(
                        HybridRetriever.from_data_dir(
                            resolved_data_dir,
                            embedding_provider=embedding_provider,
                            trust_root_path=resolved_trust_root_path,
                            clock=request_clock,
                        )
                    )
                    return built_here[0]

                retriever = retriever_cache.get_or_build(
                    release_cache_key(
                        verified_release["active"], index_metadata, live_identity
                    ),
                    build_retriever,
                    lambda built: release_cache_key(
                        built.active_release,
                        built.dense_index["metadata"],
                        built.embedding_model_identity,
                    ),
                )
                if not built_here:
                    # Cache hit: re-run the build-time checks (eligibility and index
                    # coverage depend on the request's evaluation time) as a fresh
                    # build would.
                    retriever._validate_index()
            except Exception as exc:
                snapshot_state.mark_unavailable(exc)
                raise
            # The data judged is the data loaded: follow the retriever's verified release
            # if it differs from the one ensure_release() returned (a concurrent install).
            snapshot_state.adopt(retriever.manifest)
            # Settled: the page render below must not replace the basis this answer used.
            snapshot_state.freeze()
            turn_kind = classify_conversation_turn(
                question, conversation_turns=conversation_turns,
            )
            if turn_kind == "answer":
                result = AnswerService(
                    retriever=retriever,
                    generator=generator,
                    verifier=verifier,
                    clock=request_clock,
                ).answer(
                    question,
                    configuration,
                    conversation_turns=conversation_turns,
                )
            else:
                # Greeting or non-domain turn: no retrieval, evidence, or verification.
                result = social.reply(
                    question,
                    configuration,
                    kind=turn_kind,
                    corpus_identity=str(retriever.manifest["corpus_id"]),
                )
            try:
                record = store.save_answer(
                    question=result.question,
                    normalized_question=result.normalized_question,
                    answer=(
                        {**result.answer, "knowledge_snapshot": snapshot_state.metadata()}
                        if snapshot_state.snapshot
                        else result.answer
                    ),
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
            history_oob=_is_htmx_request(request),
            reveal_latest_turn=_is_htmx_request(request),
        )

    @app.get("/status")
    async def status() -> dict[str, Any]:
        configuration = _load_configuration_or_none(resolved_config_path)
        corpus_error = ""
        try:
            corpus = corpus_summary(ensure_release())
        except Exception as exc:
            corpus_error = _retrieval_failure_message(exc)
            corpus = _unavailable_corpus_summary()
        return {
            "configured": configuration is not None,
            "configuration": configuration.to_public_dict() if configuration else None,
            "corpus": corpus,
            "corpus_error": corpus_error,
            # Explicit qualifier: freshness is judged at this snapshot, not today.
            "snapshot_status": current_snapshot_state().status,
            "knowledge_snapshot": current_snapshot_state().metadata(),
        }

    return app


class _SnapshotClockMiddleware:
    """Give every HTTP request its own `SnapshotState` and clock scope (#67).

    The state starts empty (wall clock). It is filled only from the verified release the
    handler actually loads (`ensure_release()`, the retriever's manifest); this middleware
    does no release I/O.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        with snapshot_clock(SnapshotState()):
            await self.app(scope, receive, send)


app = create_app(initial_release_dir=newest_bundled_release_dir)


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


def _update_status_from_request(
    request: Request,
    data_dir: Path,
    *,
    trust_root_path: str | Path | None = None,
) -> dict[str, str] | None:
    if request.query_params.get("update_status") == "checked":
        available = load_pending_knowledge_update(data_dir) or load_available_github_knowledge_update(data_dir)
        return {
            "kind": "success", "role": "status", "title": "Knowledge update check complete",
            "message": "Knowledge update available. Review the release details below." if available else "No newer knowledge update is available.",
        }
    if request.query_params.get("update_status") != "installed":
        return None
    active_release = active_release_id(
        data_dir,
        trust_root_path=trust_root_path,
    )
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
    active_release = active_release_id(data_dir) or "Unavailable"
    return {
        "kind": "error",
        "role": "alert",
        "title": "Knowledge update rolled back",
        "message": (
            f"{knowledge_update_failure_message('installing', exc)} "
            f"Active corpus: {active_release}."
        ),
    }


def _snapshot_banner_context() -> dict[str, Any]:
    state = current_snapshot_state()
    return {
        "snapshot_status": state.status,
        "snapshot_date": state.snapshot.date if state.snapshot else "",
        "snapshot_changed": state.changed_after_freeze,
        "snapshot_reload_failed": state.reload_failed_after_freeze,
    }


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
