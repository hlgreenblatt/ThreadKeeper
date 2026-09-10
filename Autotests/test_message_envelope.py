"""Step 8 concurrency tests: immutable per-message routing envelopes.

Verify that interleaving two chats and delayed/retried responses never
produces cross-chat delivery when using MessageEnvelope-based routing.
"""
import importlib
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "channels"))
sys.path.insert(0, str(ROOT))


def _envelope_module():
    return importlib.import_module("message_envelope")


def _telegram():
    return importlib.import_module("channels.telegram")


def test_envelope_is_frozen():
    me = _envelope_module()
    env = me.MessageEnvelope(
        run_id="r", generation_id="g", update_id=1,
        source_chat_id="100", source_message_id=42,
        sender_id="7", sender_display="ben",
        correlation_id="abc123", response_policy="ordinary",
        receive_timestamp=time.time(),
    )
    with pytest.raises(AttributeError):
        env.source_chat_id = "200"


def test_envelope_from_ingress_populates_ids():
    me = _envelope_module()
    os.environ["OMEGACLAW_RUN_ID"] = "run-99"
    os.environ["OMEGACLAW_GENERATION_ID"] = "gen-5"
    env = me.MessageEnvelope.from_ingress(
        update_id=77,
        source_chat_id="-100123",
        source_message_id=456,
        sender_id="42",
        sender_display="ben",
        text="hello",
    )
    assert env.run_id == "run-99"
    assert env.generation_id == "gen-5"
    assert env.update_id == 77
    assert env.source_chat_id == "-100123"
    assert env.source_message_id == 456
    assert env.correlation_id  # auto-generated
    assert env.text == "hello"


def test_current_message_correlation_id_tracks_dequeued_envelope(monkeypatch):
    telegram = _telegram()
    me = _envelope_module()

    monkeypatch.setattr(telegram, "_receive_transport", "mtproto")
    monkeypatch.setattr(telegram, "_sync_poll", False)
    monkeypatch.setattr(telegram, "_running", True)
    monkeypatch.setattr(telegram, "_pending_messages", [])
    monkeypatch.setattr(telegram, "_last_message", "")

    env = me.MessageEnvelope.from_ingress(
        update_id=2, source_chat_id="111", source_message_id=10,
        sender_id="7", sender_display="ben", text="same text",
    )
    telegram._set_last("same text", "111", envelope=env)
    telegram.getLastMessage()

    assert telegram.current_message_correlation_id() == env.correlation_id


def test_envelope_debug_dict_has_no_message_body():
    me = _envelope_module()
    env = me.MessageEnvelope.from_ingress(
        update_id=1, source_chat_id="42", source_message_id=1,
        sender_id="7", sender_display="ben", text="secret content here",
    )
    d = env.as_debug_dict()
    assert "text" not in d
    assert d["source_chat_id"] == "42"
    assert d["update_id"] == 1


def test_interleaved_messages_route_correctly(monkeypatch):
    """Two messages from different chats, dequeued in order, each reply
    must go to the correct source_chat_id."""
    telegram = _telegram()
    me = _envelope_module()

    sent_targets = []

    def fake_send_to(text, chat_id):
        sent_targets.append(chat_id)
        return True

    monkeypatch.setattr(telegram, "_send_message_to", fake_send_to)
    monkeypatch.setattr(telegram, "_receive_transport", "mtproto")
    monkeypatch.setattr(telegram, "_sync_poll", False)
    monkeypatch.setattr(telegram, "_running", True)
    monkeypatch.setattr(telegram, "_pending_messages", [])
    monkeypatch.setattr(telegram, "_last_message", "")

    # Enqueue two messages from different chats
    env_a = me.MessageEnvelope.from_ingress(
        update_id=1, source_chat_id="111", source_message_id=10,
        sender_id="7", sender_display="ben", text="msg A",
    )
    env_b = me.MessageEnvelope.from_ingress(
        update_id=2, source_chat_id="222", source_message_id=20,
        sender_id="8", sender_display="zari", text="msg B",
    )
    telegram._set_last("msg A", "111", envelope=env_a)
    telegram._set_last("msg B", "222", envelope=env_b)

    # Dequeue A, send reply → must go to chat 111
    msg_a = telegram.getLastMessage()
    assert "msg A" in msg_a
    telegram.send_message("reply A")
    assert sent_targets[-1] == "111"

    # Dequeue B, send reply → must go to chat 222 (not 111)
    msg_b = telegram.getLastMessage()
    assert "msg B" in msg_b
    telegram.send_message("reply B")
    assert sent_targets[-1] == "222"

    # No cross-chat delivery
    assert sent_targets == ["111", "222"]


def test_delayed_reply_uses_correct_envelope(monkeypatch):
    """A reply sent after a newer message has been enqueued must still
    target the original envelope's chat."""
    telegram = _telegram()
    me = _envelope_module()

    sent_targets = []
    monkeypatch.setattr(telegram, "_send_message_to", lambda t, c: sent_targets.append(c) or True)
    monkeypatch.setattr(telegram, "_receive_transport", "mtproto")
    monkeypatch.setattr(telegram, "_sync_poll", False)
    monkeypatch.setattr(telegram, "_running", True)
    monkeypatch.setattr(telegram, "_pending_messages", [])
    monkeypatch.setattr(telegram, "_last_message", "")

    env_a = me.MessageEnvelope.from_ingress(
        update_id=1, source_chat_id="999", source_message_id=1,
        sender_id="7", sender_display="ben", text="important",
    )
    telegram._set_last("important", "999", envelope=env_a)

    # Dequeue A but don't reply yet
    telegram.getLastMessage()

    # Enqueue B (simulates a second message arriving while LLM thinks)
    env_b = me.MessageEnvelope.from_ingress(
        update_id=2, source_chat_id="888", source_message_id=2,
        sender_id="8", sender_display="zari", text="interrupt",
    )
    telegram._set_last("interrupt", "888", envelope=env_b)

    # Now send reply to A — the envelope for A is still active
    telegram.send_message("reply to important")
    assert sent_targets[-1] == "999", "reply should go to chat 999, not 888"


def test_send_without_envelope_fails_closed(monkeypatch):
    """Ordinary replies cannot silently fall back to stale mutable routing."""
    telegram = _telegram()
    me = _envelope_module()

    sent_targets = []
    monkeypatch.setattr(telegram, "_send_message_to", lambda t, c: sent_targets.append(c) or True)
    monkeypatch.setattr(telegram, "_active_chat_id", "")
    monkeypatch.setattr(telegram, "_reply_chat_id", "")
    monkeypatch.setattr(telegram, "_chat_id", "fallback-chat")

    me.set_current_envelope(None)
    with pytest.raises(RuntimeError, match="without an active message envelope"):
        telegram.send_message("admin broadcast")
    assert sent_targets == []


def test_explicit_admin_send_uses_configured_target(monkeypatch):
    telegram = _telegram()
    me = _envelope_module()

    sent_targets = []
    monkeypatch.setattr(telegram, "_send_message_to", lambda t, c: sent_targets.append(c) or True)
    monkeypatch.setattr(telegram, "_chat_id", "fallback-chat")

    me.set_current_envelope(None)
    telegram.send_admin_message("admin broadcast")
    assert sent_targets == ["fallback-chat"]
