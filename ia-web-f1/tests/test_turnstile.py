import httpx

from raioxia.api.turnstile import SITEVERIFY_URL, TurnstileVerifier


def verifier(handler) -> TurnstileVerifier:
    return TurnstileVerifier("secret", transport=httpx.MockTransport(handler))


def test_success_sends_secret_token_and_ip():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["form"] = dict(httpx.QueryParams(request.content.decode()))
        return httpx.Response(200, json={"success": True})

    assert verifier(handler)("tok", "203.0.113.9")
    assert seen == {"url": SITEVERIFY_URL, "form": {"secret": "secret", "response": "tok", "remoteip": "203.0.113.9"}}


def test_rejection_empty_token_and_outage_fail_closed():
    assert not verifier(lambda r: httpx.Response(200, json={"success": False, "error-codes": ["invalid-input-response"]}))("t", None)
    assert not verifier(lambda r: httpx.Response(200, json={"success": True}))("", None)
    assert not verifier(lambda r: httpx.Response(503))("t", None)
    assert not verifier(lambda r: httpx.Response(200, text="not json"))("t", None)

    def down(request):
        raise httpx.ConnectError("unreachable")

    assert not verifier(down)("t", None)
