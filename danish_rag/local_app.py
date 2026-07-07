"""Local web application for Danish Immigration RAG."""

from __future__ import annotations

import argparse
import json
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
from .knowledge_release import (
    DEFAULT_RELEASE_CATALOG_DIR,
    active_corpus_summary,
    default_data_dir,
    discover_knowledge_update,
    dismiss_pending_knowledge_update,
    ensure_minimal_knowledge_release,
    install_knowledge_release,
    load_pending_knowledge_update,
    save_pending_knowledge_update,
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
from .retrieval import HybridRetriever
from .runtime_policy import load_runtime_policy


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
    capability_tester: Callable[[ProviderConfiguration], CapabilityTestResult | dict[str, Any]]
    | None = None,
    release_catalog_dir: str | Path | None = None,
) -> FastAPI:
    app = FastAPI(title="Danish Immigration RAG")
    app.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")

    resolved_config_path = Path(config_path) if config_path else default_config_path()
    resolved_data_dir = Path(data_dir) if data_dir else default_data_dir()
    resolved_release_catalog_dir = (
        Path(release_catalog_dir) if release_catalog_dir else DEFAULT_RELEASE_CATALOG_DIR
    )
    tester = capability_tester or ProviderCapabilityTester()
    generator = answer_generator or LocalProviderAnswerGenerator()
    store = ConversationStore(resolved_data_dir / "conversations.sqlite3")

    def render_home(
        *,
        status_code: int = 200,
        setup_form: ProviderConfiguration | None = None,
        setup_error: str = "",
        setup_reason: str = "",
        active_question: str = "",
        composer_error: str = "",
        active_conversation: dict[str, Any] | None = None,
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

    def _page_context(
        *,
        setup_form: ProviderConfiguration | None = None,
        setup_error: str = "",
        setup_reason: str = "",
        active_question: str = "",
        composer_error: str = "",
        active_conversation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ensure_minimal_knowledge_release(resolved_data_dir)
        configuration = _load_configuration_or_none(resolved_config_path)
        if setup_form is None:
            setup_form = configuration or _default_provider_configuration()
        return {
            "configuration": configuration.to_public_dict() if configuration else None,
            "providers": provider_options(),
            "form": setup_form,
            "setup_error": setup_error,
            "setup_reason": setup_reason,
            "active_question": active_question,
            "composer_error": composer_error,
            "active_conversation": active_conversation,
            "conversations": store.list_conversations(),
            "corpus": active_corpus_summary(resolved_data_dir),
            "pending_update": load_pending_knowledge_update(resolved_data_dir),
        }

    @app.get("/", response_class=HTMLResponse)
    async def home() -> HTMLResponse:
        return render_home()

    @app.get("/conversations/export.json")
    async def export_conversations() -> Response:
        return _json_download_response(
            store.export_conversations(),
            filename="danish-rag-conversation-records.json",
        )

    @app.get("/conversations/{conversation_id}", response_class=HTMLResponse)
    async def conversation(conversation_id: str) -> HTMLResponse:
        try:
            record = store.get_conversation(conversation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Conversation not found.") from exc
        return render_home(active_conversation=record)

    @app.get("/conversations/{conversation_id}/export.json")
    async def export_conversation(conversation_id: str) -> Response:
        try:
            payload = store.export_conversation(conversation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Conversation not found.") from exc
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
        store.delete_all_conversations()
        return RedirectResponse("/", status_code=303)

    @app.post("/knowledge-updates/check")
    async def check_knowledge_updates(request: Request) -> RedirectResponse:
        _validate_state_changing_request(request)
        ensure_minimal_knowledge_release(resolved_data_dir)
        update = discover_knowledge_update(
            resolved_data_dir,
            resolved_release_catalog_dir,
        )
        save_pending_knowledge_update(resolved_data_dir, update)
        return RedirectResponse("/", status_code=303)

    @app.post("/knowledge-updates/dismiss")
    async def dismiss_knowledge_update(request: Request) -> RedirectResponse:
        _validate_state_changing_request(request)
        dismiss_pending_knowledge_update(resolved_data_dir)
        return RedirectResponse("/", status_code=303)

    @app.post("/knowledge-updates/install")
    async def install_knowledge_update(request: Request) -> RedirectResponse:
        _validate_state_changing_request(request)
        form_data = await _read_urlencoded_form(request)
        requested_release_id = form_data.get("release_id", "").strip()
        pending_update = load_pending_knowledge_update(resolved_data_dir)
        if not pending_update:
            raise HTTPException(status_code=409, detail="No reviewed knowledge update is pending.")
        pending_release_id = pending_update["release"]["knowledge_release_id"]
        if requested_release_id != pending_release_id:
            raise HTTPException(status_code=409, detail="Requested release is not pending review.")
        release_dir = resolved_release_catalog_dir / requested_release_id
        install_knowledge_release(resolved_data_dir, release_dir=release_dir)
        dismiss_pending_knowledge_update(resolved_data_dir)
        return RedirectResponse("/", status_code=303)

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
        try:
            ensure_minimal_knowledge_release(resolved_data_dir)
            retriever = HybridRetriever.from_data_dir(resolved_data_dir)
            conversation_turns = None
            if conversation_id:
                active_conversation_for_error = store.get_conversation(conversation_id)
                conversation_turns = active_conversation_for_error["turns"]
            result = AnswerService(
                retriever=retriever,
                generator=generator,
            ).answer(
                question,
                configuration,
                conversation_turns=conversation_turns,
            )
            record = store.save_answer(
                question=result.question,
                normalized_question=result.normalized_question,
                answer=result.answer,
                model_identity=result.model_identity,
                corpus_identity=result.corpus_identity,
                conversation_id=conversation_id,
            )
        except KeyError:
            return render_ask_response(
                request,
                status_code=404,
                active_question=question,
                composer_error="Conversation not found. Start a new conversation and retry.",
            )
        except AnswerValidationError as exc:
            return render_ask_response(
                request,
                status_code=422,
                active_question=question,
                composer_error=str(exc),
                active_conversation=active_conversation_for_error,
            )
        except AnswerPipelineError as exc:
            return render_ask_response(
                request,
                status_code=503,
                active_question=question,
                composer_error=str(exc),
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
                    "Check the active corpus, local index, and local storage, then retry. "
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
        ensure_minimal_knowledge_release(resolved_data_dir)
        return {
            "configured": configuration is not None,
            "configuration": configuration.to_public_dict() if configuration else None,
            "corpus": active_corpus_summary(resolved_data_dir),
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


def _json_download_response(payload: dict[str, Any], *, filename: str) -> Response:
    return Response(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


if __name__ == "__main__":
    raise SystemExit(main())
