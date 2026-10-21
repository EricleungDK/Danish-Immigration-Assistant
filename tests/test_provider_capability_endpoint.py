import json
import unittest

from danish_rag.local_model_transport import (
    FakeTransport,
    ModelMissingError,
    ModelProtocolError,
    ModelTimeoutError,
    ModelUnreachableError,
)
from danish_rag.provider_setup import ProviderCapabilityTester, ProviderConfiguration

OPENAI = ProviderConfiguration(
    provider_id="openai_compatible", endpoint="http://127.0.0.1:1234", model="chat-a"
)


def run_openai(transport):
    built = []

    def factory(provider_id, endpoint, timeout_seconds):
        built.append((provider_id, endpoint, timeout_seconds))
        return transport

    tester = ProviderCapabilityTester(transport_factory=factory, timeout_seconds=7.0)
    return tester(OPENAI), built


class ProviderCapabilityEndpointTests(unittest.TestCase):
    def test_scheme_odd_loopback_endpoint_returns_a_failure_result(self):
        result = ProviderCapabilityTester()._test_ollama(
            ProviderConfiguration(
                provider_id="ollama",
                endpoint="ftp://127.0.0.1:11434",
                model="gemma4:12b",
            )
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "service_unreachable")

    def test_openai_compatible_pass_goes_through_the_transport(self):
        transport = FakeTransport()
        transport.queue_models([{"name": "chat-a"}])
        transport.queue_chat(json.dumps({"status": "ok"}))

        result, built = run_openai(transport)

        self.assertTrue(result.ok, result)
        self.assertEqual(result.capabilities, ["generation"])
        self.assertEqual(result.model_identity, {"id": "chat-a"})
        self.assertEqual(built, [("openai_compatible", "http://127.0.0.1:1234", 7.0)])
        chat = transport.requests[1]
        self.assertEqual(chat.operation, "chat")
        self.assertEqual(chat.params["model"], "chat-a")
        self.assertEqual(chat.params["schema"]["required"], ["status"])

    def test_openai_compatible_unreachable_keeps_message_template(self):
        for error in (ModelUnreachableError("down"), ModelTimeoutError("slow"), ModelProtocolError("junk")):
            with self.subTest(error=type(error).__name__):
                transport = FakeTransport()
                transport.queue_error(error)

                result, _built = run_openai(transport)

                self.assertEqual(result.reason, "service_unreachable")
                self.assertEqual(
                    result.message,
                    "Provider service is unreachable. Start the local server, confirm "
                    f"http://127.0.0.1:1234/v1/models responds, and retry. Detail: {error}",
                )

    def test_openai_compatible_unlisted_model_is_unavailable(self):
        transport = FakeTransport()
        transport.queue_models([{"name": "other"}])

        result, _built = run_openai(transport)

        self.assertEqual(result.reason, "model_unavailable")
        self.assertEqual(
            result.message,
            "chat-a was not listed by the local provider. "
            "Load the model in the provider and retry setup.",
        )

    def test_openai_compatible_bad_structured_output_fails(self):
        cases = [
            ("wrong json", lambda t: t.queue_chat(json.dumps({"status": "nope"}))),
            ("not json", lambda t: t.queue_chat("hello")),
            ("transport error", lambda t: t.queue_error(ModelMissingError("gone"))),
        ]
        for name, script in cases:
            with self.subTest(name):
                transport = FakeTransport()
                transport.queue_models([{"name": "chat-a"}])
                script(transport)

                result, _built = run_openai(transport)

                self.assertEqual(result.reason, "structured_output_failed")
                self.assertTrue(
                    result.message.startswith(
                        "The selected model did not return the required structured JSON. "
                        "Choose a chat-capable local model and retry. Detail: "
                    )
                )

    def test_ollama_capability_test_runs_the_probe_over_the_injected_transport(self):
        transport = FakeTransport()
        transport.queue_version("0.31.0")
        transport.queue_error(ModelMissingError("none"), operation="show_model")

        tester = ProviderCapabilityTester(
            transport_factory=lambda provider_id, endpoint, timeout: transport
        )
        result = tester(
            ProviderConfiguration(
                provider_id="ollama",
                endpoint="http://127.0.0.1:11434",
                model="gemma4:12b",
            )
        )

        self.assertEqual(result.reason, "model_unavailable")
        self.assertEqual(
            [r.operation for r in transport.requests], ["version", "show_model"]
        )


if __name__ == "__main__":
    unittest.main()
