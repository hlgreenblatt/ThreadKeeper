"""Immutable per-message routing envelope for Telegram transport.

An envelope is created at ingress time and carried through dequeue, model
invocation, retries, and send.  Ordinary replies MUST target the
source_chat_id from the active envelope.  If no envelope is active,
send_message fails closed rather than falling back to stale global state.
"""
import os
import time
import uuid
import threading
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class MessageEnvelope:
    run_id: str
    generation_id: str
    update_id: int
    source_chat_id: str
    source_message_id: int
    sender_id: str
    sender_display: str
    correlation_id: str
    response_policy: str  # "ordinary" | "skip" | "auth_broadcast"
    receive_timestamp: float
    text: str = ""
    inbound_identity: Optional[dict[str, Any]] = None

    @classmethod
    def from_ingress(
        cls,
        update_id: int,
        source_chat_id: str,
        source_message_id: int,
        sender_id: str,
        sender_display: str,
        text: str,
        response_policy: str = "ordinary",
        inbound_identity: Optional[dict[str, Any]] = None,
    ) -> "MessageEnvelope":
        run_id = os.environ.get("OMEGACLAW_RUN_ID", "")
        generation_id = os.environ.get("OMEGACLAW_GENERATION_ID", "")
        return cls(
            run_id=run_id,
            generation_id=generation_id,
            update_id=update_id,
            source_chat_id=str(source_chat_id),
            source_message_id=int(source_message_id),
            sender_id=str(sender_id),
            sender_display=sender_display,
            correlation_id=uuid.uuid4().hex[:12],
            response_policy=response_policy,
            receive_timestamp=time.time(),
            text=text,
            inbound_identity=inbound_identity,
        )

    def as_debug_dict(self) -> dict:
        """Privacy-safe metadata for event journaling (no message body)."""
        return {
            "run_id": self.run_id,
            "generation_id": self.generation_id,
            "update_id": self.update_id,
            "source_chat_id": self.source_chat_id,
            "source_message_id": self.source_message_id,
            "sender_id": self.sender_id,
            "correlation_id": self.correlation_id,
            "response_policy": self.response_policy,
            "receive_timestamp": self.receive_timestamp,
        }


# Thread-local current envelope so that send_message picks up the right
# chat without a mutable global that can be overwritten by a concurrent message.
_current_envelope: Optional[MessageEnvelope] = None
_envelope_lock = threading.Lock()


def set_current_envelope(env: Optional[MessageEnvelope]) -> None:
    global _current_envelope
    with _envelope_lock:
        _current_envelope = env


def get_current_envelope() -> Optional[MessageEnvelope]:
    with _envelope_lock:
        return _current_envelope


def require_current_envelope() -> MessageEnvelope:
    """Fail closed if no envelope is active."""
    with _envelope_lock:
        if _current_envelope is None:
            raise RuntimeError(
                "send_message called without an active message envelope; "
                "refusing to fall back to stale global chat state"
            )
        return _current_envelope
