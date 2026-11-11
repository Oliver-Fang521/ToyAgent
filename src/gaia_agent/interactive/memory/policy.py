"""Conservative server gates for model-classified, current-text self reports."""

import re
import unicodedata
from dataclasses import dataclass

from .models import MemoryFailure, MemoryWrite

FACETS = {
    "profile": frozenset(
        {
            "preferred_language",
            "response_style",
            "education_background",
            "professional_background",
            "learning_subjects",
            "skill_experience",
        }
    ),
    "project": frozenset({"goal", "stack", "constraint", "status"}),
    "decision": frozenset({"choice"}),
}
# Defense in depth, not a general-purpose classifier. Uncertain inputs are skipped.
SENSITIVE = re.compile(
    r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}\b"
    r"|我住在|(?:home|street|mailing) address|\bI live at\b|"
    r"sk-[\w-]{12,}|(?:api[ _-]?key|password|passwd|secret|token|bearer|private[ _-]?key)\b"
    r"|密码|口令|密钥|令牌|验证码|私钥|身份证|护照|银行卡|信用卡|银行账户|社保号|社会安全号|住址|诊断|病史|宗教|性取向|政治立场"
    r"|\bSSN\b|\b\d{3}-\d{2}-\d{4}\b|(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"
    r"|\b(?:diagnosis|medical history|religion|sexual orientation|political affiliation|"
    r"bank account|social security|passport|home address)\b",
    re.IGNORECASE,
)
INDIRECT = re.compile(
    r"[\"“”「」『』`]|假设|假如|如果|扮演|角色|例如|举个例|他说|她说|朋友说|网页|助手|工具结果"
    r"|\b(?:pretend|role.?play|hypothetical|if I were|example|my friend|assistant says|website says)\b",
    re.IGNORECASE,
)
SELF = re.compile(r"我|\b(?:I|my|me)\b", re.IGNORECASE)
RELEARN = re.compile(r"(?:请)?重新记住|\b(?:remember (?:this )?again|relearn)\b", re.IGNORECASE)


def prepare_event_source(text):
    """Redact unsafe clauses before encryption; offsets refer to this result only.

    This is a safety filter, never an intent classifier or first-person prefilter.
    """
    if not isinstance(text, str) or not text.strip():
        raise MemoryFailure("memory_invalid", 422)
    parts = re.split(r"([；;，。\n])", text)
    result = "".join("[REDACTED]" if SENSITIVE.search(part) else part for part in parts)
    if len(result.encode("utf-8")) > 8000:
        raise MemoryFailure("memory_limit", 422)
    return result


@dataclass(frozen=True, repr=False)
class ValidatedMemoryOperation:
    operation: object
    normalized_key: str
    source_fragment: str


class MemoryPolicy:
    def validate_operation(self, event, operation, eligible_ids):
        if operation.target_id is not None and operation.target_id not in eligible_ids:
            return None
        if operation.barrier_id is not None and operation.barrier_id not in eligible_ids:
            return None
        source = event.source
        if operation.source_kind == "verified_task":
            observations = {
                o.id: o
                for o in event.observations
                if o.verified and o.source_run_id == event.source_run_id
            }
            observation = observations.get(operation.observation_id)
            if observation is None or operation.category == "profile":
                return None
            if operation.action not in {"add", "append", "noop"} or operation.intent != "automatic":
                return None
            source = observation.content
        start, end = operation.source_start, operation.source_end
        if not 0 <= start < end <= len(source):
            return None
        fragment = source[start:end]
        if "[REDACTED]" in fragment or SENSITIVE.search(fragment):
            return None
        if SENSITIVE.search(
            operation.topic
            + " "
            + operation.entity
            + " "
            + (operation.content or "")
            + " "
            + (operation.subject or "")
        ):
            return None
        if (
            operation.subject is not None
            and operation.action in {"add", "append", "replace"}
            and (
                SELF.search(operation.subject)
                or INDIRECT.search(operation.subject)
                or re.search(r"[，。；;!?！？:\n]", operation.subject)
                or len(operation.subject.split()) > 4
                or operation.subject.casefold() not in (operation.content or "").casefold()
            )
        ):
            return None
        if operation.intent == "clarify" and operation.action != "clarify":
            return None
        if operation.action in {"delete", "forget", "restore"} and operation.intent != "explicit":
            return None
        if operation.action == "append" and operation.cardinality != "set":
            return None
        if (
            operation.action in {"append", "replace", "delete", "forget"}
            and operation.target_id is None
        ):
            return None
        if operation.action == "restore" and operation.barrier_id is None:
            return None
        if operation.action in {"add", "append", "replace"}:
            if operation.content != fragment or INDIRECT.search(fragment):
                return None
            # Include the current clause prefix so a substring cannot discard
            # quotation/role-play attribution to masquerade as a self-report.
            prefix = re.split(r"[。；;\n]", source[:start])[-1]
            if INDIRECT.search(prefix):
                return None
        topic = " ".join(unicodedata.normalize("NFKC", operation.topic).casefold().split())
        entity = " ".join(unicodedata.normalize("NFKC", operation.entity).casefold().split())
        if not topic or not entity:
            return None
        return ValidatedMemoryOperation(
            operation, f"{operation.category}:{topic}:{entity}", fragment
        )

    def normalize(self, candidate):
        entity = " ".join(unicodedata.normalize("NFKC", candidate.entity).casefold().split())
        # Profile facets describe the account itself, not model-invented aliases.
        if candidate.category == "profile":
            entity = "self"
        return f"{candidate.category}:{candidate.facet}:{entity}"

    def prepare_text(self, text):
        if not isinstance(text, str) or not text.strip() or SENSITIVE.search(text):
            return None
        if INDIRECT.search(text) or not SELF.search(text):
            return None
        return text

    def validate_manual(self, candidate):
        if (
            not candidate.facet.strip()
            or SENSITIVE.search(candidate.content + " " + candidate.entity + " " + candidate.facet)
            or re.search(r"government[_ ]?id", candidate.facet, re.IGNORECASE)
            or not candidate.content.strip()
        ):
            raise MemoryFailure("memory_invalid", 422)
        return MemoryWrite(candidate, self.normalize(candidate), None)

    def validate_current(self, candidate, current_text):
        if self.prepare_text(current_text) is None:
            return None
        start, end = candidate.source_start, candidate.source_end
        if not 0 <= start < end <= len(current_text):
            return None
        fragment = current_text[start:end]
        # Extractive bodies avoid an unverifiable model paraphrase becoming a fact.
        if (
            candidate.content != fragment
            or not SELF.search(fragment)
            or INDIRECT.search(fragment)
            or candidate.explicit_relearn
            and not RELEARN.search(fragment)
        ):
            return None
        try:
            write = self.validate_manual(candidate)
        except MemoryFailure:
            return None
        return MemoryWrite(write.candidate, write.normalized_key, fragment)

    def decide(self, write, existing, tombstone, current_text):
        if tombstone:
            return (
                "restore"
                if (
                    write.candidate.explicit_relearn
                    and self.validate_current(write.candidate, current_text) is not None
                )
                else "skip"
            )
        if existing is None:
            return "add"
        if existing.edited_by_user or existing.content == write.candidate.content:
            return "skip"
        return "replace"
