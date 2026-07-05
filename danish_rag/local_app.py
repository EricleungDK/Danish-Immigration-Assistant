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
    capability_tester: Callable[[ProviderConfiguration], CapabilityTestResult | dict[str, Any]]
    | None = None,
) -> FastAPI:
    app = FastAPI(title="Danish Immigration RAG")
    app.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")

    resolved_config_path = Path(config_path) if config_path else default_config_path()
    tester = capability_tester or ProviderCapabilityTester()

    def render_home(
        *,
        status_code: int = 200,
        setup_form: ProviderConfiguration | None = None,
        setup_error: str = "",
        setup_reason: str = "",
        active_question: str = "",
        composer_error: str = "",
    ) -> HTMLResponse:
        configuration = _load_configuration_or_none(resolved_config_path)
        if setup_form is None:
            setup_form = configuration or _default_provider_configuration()
        template = TEMPLATES.get_template("home.html")
        return HTMLResponse(
            template.render(
                configuration=configuration.to_public_dict() if configuration else None,
                providers=provider_options(),
                form=setup_form,
                setup_error=setup_error,
                setup_reason=setup_reason,
                active_question=active_question,
                composer_error=composer_error,
            ),
            status_code=status_code,
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

    @app.get("/", response_class=HTMLResponse)
    async def home() -> HTMLResponse:
        return render_home()

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
        configuration = _load_configuration_or_none(resolved_config_path)
        if not configuration:
            return render_home(
                status_code=409,
                active_question=question,
                composer_error=(
                    "Connect and test a local generation-model provider before asking "
                    "the first question."
                ),
            )
        return render_home(status_code=202, active_question=question)

    @app.get("/status")
    async def status() -> dict[str, Any]:
        configuration = _load_configuration_or_none(resolved_config_path)
        return {
            "configured": configuration is not None,
            "configuration": configuration.to_public_dict() if configuration else None,
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


if __name__ == "__main__":
    raise SystemExit(main())
