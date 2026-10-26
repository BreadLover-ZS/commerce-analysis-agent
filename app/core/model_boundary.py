"""Program-owned send identity and durable, fail-closed proof of non-submission."""

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass
class SendAttempt:
    identity: dict
    admissions: int = 0
    denial: dict | None = None
    provider_usage_valid: bool | None = None
    marker: object = field(default_factory=object)


CURRENT_ATTEMPT = ContextVar("commerce_send_attempt", default=None)


@contextmanager
def send_attempt(identity):
    attempt = SendAttempt(dict(identity))
    token = CURRENT_ATTEMPT.set(attempt)
    try:
        yield attempt
    finally:
        CURRENT_ATTEMPT.reset(token)


class BeforeProviderSendDenied(RuntimeError):
    def __init__(self, proof=None, marker=None):
        super().__init__("provider_request_denied_before_send")
        self.proof, self.marker = proof, marker


def verify_proof(proof, identity):
    """Verify persisted proof again on settlement/recovery. Absence is never zero."""
    try:
        event = proof["event"]
        if (
            event["protocol"] != 2
            or event["event"] != "denied_before_send"
            or event["attempt_identity"] != identity
            or event["attempt_admissions"] != 0
            or event["reserved_tokens"] != 0
            or not event["run_id"]
            or event["reason"]
            not in {"token_budget_capacity", "paid_request_limit_or_unknown_usage"}
            or fingerprint(event) != proof["event_sha256"]
        ):
            return False
        data = Path(proof["journal"]).read_bytes()
        # Reject duplicated/conflicting identities, even if one entry matches.
        entries = [json.loads(line) for line in data.splitlines()]
        bound = [e for e in entries if e.get("attempt_identity") == identity]
        return len(bound) == 1 and bound[0] == event
    except (KeyError, TypeError, ValueError, OSError):
        return False


def trusted_denial(error, attempt):
    seen = set()
    while error is not None and id(error) not in seen and len(seen) < 8:
        seen.add(id(error))
        if isinstance(error, BeforeProviderSendDenied):
            if (
                attempt is not None
                and attempt.admissions == 0
                and error.marker is attempt.marker
                and error.proof == attempt.denial
                and verify_proof(error.proof, attempt.identity)
            ):
                return error.proof
            return None
        error = error.__cause__
    return None
