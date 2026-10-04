"""Error classification."""

import pytest

from coding_agent.llm.errors import ErrorKind, classify


class HTTPError(Exception):
    def __init__(self, status_code: int | None, body: str = "") -> None:
        super().__init__(body or f"status {status_code}")
        self.status_code = status_code
        self.body = body


class ConnectTimeout(Exception):
    pass


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (HTTPError(429, "slow down"), ErrorKind.RATE_LIMIT),
        (HTTPError(402, "payment required"), ErrorKind.ACCOUNT_QUOTA),
        (HTTPError(429, "insufficient_quota"), ErrorKind.ACCOUNT_QUOTA),
        (HTTPError(401, "invalid api key"), ErrorKind.AUTH),
        (HTTPError(403, "forbidden"), ErrorKind.AUTH),
        (HTTPError(403, "quota exceeded"), ErrorKind.RATE_LIMIT),
        (HTTPError(403, "insufficient_quota"), ErrorKind.ACCOUNT_QUOTA),
        (HTTPError(404, "model not found"), ErrorKind.MODEL_NOT_FOUND),
        (HTTPError(500, "bad gateway"), ErrorKind.TRANSIENT),
        (HTTPError(400, "rate limit reached"), ErrorKind.RATE_LIMIT),
        (HTTPError(None, "You hit your TPM limit"), ErrorKind.RATE_LIMIT),
        (HTTPError(None, "insufficient quota for this account"), ErrorKind.ACCOUNT_QUOTA),
        (ConnectTimeout("deadline exceeded"), ErrorKind.TRANSIENT),
        (HTTPError(None, "connection reset by peer"), ErrorKind.TRANSIENT),
        (HTTPError(400, "malformed request"), ErrorKind.OTHER),
    ],
)
def test_classify(exc: Exception, kind: ErrorKind) -> None:
    assert classify(exc).kind is kind
