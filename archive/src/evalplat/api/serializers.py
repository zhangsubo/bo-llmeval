"""API response serializers that enforce the public redaction boundary."""

from evalplat.schemas.comparison import RunSummary
from evalplat.schemas.run import RunRead
from evalplat.services.security_service import redact_sensitive_text, redact_sensitive_value


def serialize_run(run) -> RunRead:
    """Serialize a Run without exposing host paths or credential evidence."""

    payload = RunRead.model_validate(run).model_dump()
    payload["run_snapshot"] = redact_sensitive_value(payload.get("run_snapshot"))
    payload["error_summary"] = redact_sensitive_text(payload.get("error_summary")) or None
    return RunRead(**payload)


def serialize_run_summary(run) -> RunSummary:
    """Serialize a comparison Run summary through the same redaction boundary."""

    payload = RunSummary.model_validate(run).model_dump()
    payload["snapshot"] = redact_sensitive_value(payload.get("snapshot"))
    return RunSummary(**payload)
