import base64
import json
from email import message_from_bytes, policy

import httpx
import pytest
from google.auth.exceptions import RefreshError, TransportError

from raioxia.email.base import EmailError, OutgoingEmail
from raioxia.email.gmail import SCOPES, SEND_URL, GmailApiSender, HttpxAuthRequest
from raioxia.settings import Settings

EMAIL = OutgoingEmail(
    to="ana@exemplo.com.br", subject="Raio-X IA de exemplo.com.br: nota 71 de 100", html="<p>olá</p>", text="olá",
    headers={"List-Unsubscribe": "<https://raiox.primesolutions.net.br/descadastrar/1.0.abc>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"},
)


class FakeCredentials:
    def __init__(self, error=None):
        self.token = None
        self.refreshes = 0
        self.error = error

    @property
    def valid(self):
        return self.token is not None

    def refresh(self, request):
        assert isinstance(request, HttpxAuthRequest)
        self.refreshes += 1
        if self.error:
            raise self.error
        self.token = f"access-{self.refreshes}"


def sender(handler, credentials=None) -> GmailApiSender:
    return GmailApiSender(
        credentials or FakeCredentials(), from_name="Raio-X IA", from_address="raiox@primesolutions.net.br",
        transport=httpx.MockTransport(handler),
    )


def test_sends_raw_mime_from_the_alias():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"id": "18f0c0ffee", "threadId": "t"})

    gmail = sender(handler)
    assert gmail.send(EMAIL) == "18f0c0ffee"
    assert gmail.send(EMAIL) == "18f0c0ffee"
    assert gmail.credentials.refreshes == 1  # token reused while valid
    request = seen[0]
    assert str(request.url) == SEND_URL and request.headers["authorization"] == "Bearer access-1"
    mime = message_from_bytes(base64.urlsafe_b64decode(json.loads(request.content)["raw"]), policy=policy.default)
    assert mime["From"] == "Raio-X IA <raiox@primesolutions.net.br>" and mime["Reply-To"] == "raiox@primesolutions.net.br"
    assert mime["To"] == "ana@exemplo.com.br" and mime["Subject"] == EMAIL.subject
    assert mime["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert mime.get_body(("plain",)).get_content().strip() == "olá"


@pytest.mark.parametrize(
    ("status", "body", "transient"),
    [
        (429, {"error": {"status": "RESOURCE_EXHAUSTED"}}, True),
        (500, {}, True),
        (503, "oops", True),
        (403, {"error": {"errors": [{"reason": "dailyLimitExceeded"}]}}, True),
        (403, {"error": {"errors": [{"reason": "forbidden"}]}}, False),
        (400, {"error": {"errors": [{"reason": "invalidArgument"}], "status": "INVALID_ARGUMENT"}}, False),
    ],
)
def test_error_classification(status, body, transient):
    response = httpx.Response(status, json=body) if isinstance(body, dict) else httpx.Response(status, text=body)
    with pytest.raises(EmailError) as exc:
        sender(lambda r: response).send(EMAIL)
    assert exc.value.transient is transient and str(status) in str(exc.value)


def test_401_drops_the_token_for_the_next_attempt():
    gmail = sender(lambda r: httpx.Response(401, json={"error": {"status": "UNAUTHENTICATED"}}))
    with pytest.raises(EmailError) as exc:
        gmail.send(EMAIL)
    assert exc.value.transient and gmail.credentials.token is None


@pytest.mark.parametrize("error", [TransportError("dns"), RefreshError("unauthorized_client: delegation missing")])
def test_auth_failures_are_retried(error):
    with pytest.raises(EmailError) as exc:
        sender(lambda r: httpx.Response(200, json={"id": "x"}), FakeCredentials(error)).send(EMAIL)
    assert exc.value.transient


def test_network_error_is_transient():
    def down(request):
        raise httpx.ConnectError("unreachable")

    with pytest.raises(EmailError) as exc:
        sender(down).send(EMAIL)
    assert exc.value.transient


def test_real_service_account_token_exchange_uses_delegation(tmp_path):
    """google-auth signs a JWT for eric.lieb (sub) with only gmail.send, exchanged over our httpx adapter."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    key_file = tmp_path / "sa.json"
    key_file.write_text(json.dumps({
        "type": "service_account", "project_id": "raiox", "private_key_id": "k1", "private_key": pem,
        "client_email": "raiox-mailer@raiox.iam.gserviceaccount.com", "client_id": "1",
        "token_uri": "https://oauth2.googleapis.com/token",
    }))
    settings = Settings(_env_file=None, secret_key="k" * 40, email_sender="gmail", gmail_service_account_file=key_file)
    gmail = GmailApiSender.from_settings(settings)

    claims = {}

    def google(request):
        if request.url.host == "oauth2.googleapis.com":
            form = dict(httpx.QueryParams(request.content.decode()))
            payload = form["assertion"].split(".")[1]
            claims.update(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))))
            return httpx.Response(200, json={"access_token": "ya29.test", "expires_in": 3600, "token_type": "Bearer"})
        assert request.headers["authorization"] == "Bearer ya29.test"
        return httpx.Response(200, json={"id": "sent-1"})

    gmail.client = httpx.Client(transport=httpx.MockTransport(google))
    assert gmail.send(EMAIL) == "sent-1"
    assert claims["sub"] == "eric.lieb@primesolutions.net.br"
    assert claims["iss"] == "raiox-mailer@raiox.iam.gserviceaccount.com"
    assert claims["scope"] == " ".join(SCOPES) == "https://www.googleapis.com/auth/gmail.send"
