#!/usr/bin/env python3
"""
Telethon MTProto bridge subprocess for OmegaClaw.
Uses os.write(1,...) for stdout IPC.
"""
import json
import os
import sys
import asyncio
import ctypes
import fcntl
import signal

FIFO_PATH = os.environ.get("TG_MTPROTO_FIFO", "/tmp/protomega_mtproto_fifo")
_fifo_fd = None
_lock_fd = None


def _arm_parent_death_signal():
    """Exit if the SWI/Janus parent dies, including by fatal signal."""
    expected = int(os.environ.get("TG_MTPROTO_PARENT_PID", "0") or 0)
    if expected <= 1:
        raise RuntimeError("TG_MTPROTO_PARENT_PID is required")
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))
    # Parent may have died between Popen and prctl.
    if os.getppid() != expected:
        raise RuntimeError(
            f"MTProto parent changed before ownership was armed: expected {expected}, got {os.getppid()}"
        )


def _acquire_instance_lock(session_path):
    """Fence duplicate bridges before opening the Telethon session."""
    global _lock_fd
    lock_path = os.environ.get("TG_MTPROTO_LOCK", f"{session_path}.lock")
    _lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.fchmod(_lock_fd, 0o600)
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError(f"another MTProto bridge owns {lock_path}") from exc

def _emit(obj):
    global _fifo_fd
    data = (json.dumps(obj, default=str) + "\n").encode("utf-8")
    try:
        os.write(1, data)
    except Exception:
        pass
    try:
        if _fifo_fd is None:
            _fifo_fd = os.open(FIFO_PATH, os.O_WRONLY | os.O_NONBLOCK)
        os.write(_fifo_fd, data)
    except Exception:
        pass

def _is_self_message(event, self_id):
    """Fail closed against re-ingesting this bot's own outbound messages."""
    message = getattr(event, "message", None)
    if bool(getattr(event, "out", False)) or bool(getattr(message, "out", False)):
        return True
    sender_id = getattr(event, "sender_id", None)
    if sender_id is None and message is not None:
        sender_id = getattr(message, "sender_id", None)
    return sender_id == self_id


def _bot_api_chat_id(peer_id):
    """Convert an MTProto Peer* into Telegram Bot API's marked chat ID.

    Bot API IDs use positive user IDs, negative basic-group IDs, and
    ``-100<channel_id>`` for supergroups/channels.  Passing raw MTProto IDs
    caused every group event to miss the configured allowlist.
    """
    peer_type = type(peer_id).__name__
    if peer_type == "PeerUser" and hasattr(peer_id, "user_id"):
        return peer_id.user_id
    if peer_type == "PeerChat" and hasattr(peer_id, "chat_id"):
        return -peer_id.chat_id
    if peer_type == "PeerChannel" and hasattr(peer_id, "channel_id"):
        return int(f"-100{peer_id.channel_id}")
    raise ValueError(f"unsupported Telegram peer type: {type(peer_id).__name__}")


def _convert_message(event):
    from telethon.tl.types import (
        PeerUser, PeerChat, PeerChannel,
        MessageMediaDocument, MessageMediaPhoto,
    )
    message = event.message
    chat = message.chat or message.peer_id
    sender = message.sender

    chat_dict = {"id": _bot_api_chat_id(message.peer_id)}

    if isinstance(message.peer_id, PeerUser):
        chat_dict["type"] = "private"
    elif isinstance(message.peer_id, PeerChat):
        chat_dict["type"] = "group"
    elif isinstance(message.peer_id, PeerChannel):
        chat_dict["type"] = "supergroup" if getattr(chat, "megagroup", False) else "channel"
    else:
        chat_dict["type"] = "group"

    user_dict = {}
    if sender:
        user_dict["id"] = sender.id
        if hasattr(sender, "first_name"): user_dict["first_name"] = sender.first_name or ""
        if hasattr(sender, "last_name"): user_dict["last_name"] = sender.last_name or ""
        if hasattr(sender, "username"): user_dict["username"] = sender.username or ""
        if hasattr(sender, "bot"): user_dict["is_bot"] = sender.bot
    elif message.sender_chat:
        user_dict["id"] = message.sender_chat.id
        user_dict["type"] = "chat"
        if hasattr(message.sender_chat, "title"): user_dict["title"] = message.sender_chat.title or ""

    text = message.message or ""
    msg_dict = {
        "message_id": message.id,
        "from": user_dict if user_dict else None,
        "chat": chat_dict,
        "text": text,
        "date": message.date.isoformat() if message.date else "",
    }
    if message.reply_to_msg_id:
        msg_dict["reply_to_message"] = {"message": message.reply_to_msg_id}

    if message.media:
        try:
            if isinstance(message.media, MessageMediaDocument):
                doc = message.media.document
                if doc:
                    attrs = {type(a).__name__: a for a in (doc.attributes or [])}
                    fn = None
                    if "DocumentAttributeFilename" in attrs: fn = attrs["DocumentAttributeFilename"].file_name
                    msg_dict["document"] = {"file_id": str(doc.id), "file_name": fn or "document", "mime_type": doc.mime_type or "application/octet-stream", "file_size": doc.size or 0}
            elif isinstance(message.media, MessageMediaPhoto):
                msg_dict["photo"] = [{"file_id": str(message.media.photo.id) if message.media.photo else ""}]
        except Exception as exc:
            _emit({"type": "error", "message": f"Media error: {exc}"})

    entities = []
    if hasattr(message, "entities") and message.entities:
        from telethon.tl.types import MessageEntityMention
        for ent in message.entities:
            if isinstance(ent, MessageEntityMention):
                entities.append({"type": "mention", "offset": ent.offset, "length": ent.length})
    if entities:
        msg_dict["entities"] = entities

    return {"update_id": message.id, "message": msg_dict}


async def main():
    from telethon import TelegramClient, events

    os.umask(0o077)
    _arm_parent_death_signal()

    api_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    bot_token = (os.environ.get("TG_BOT_TOKEN") or os.environ.get("OMEGACLAW_TG_BOT_TOKEN") or "").strip()

    if not api_id or not api_hash:
        sys.stderr.write("Missing TELEGRAM_API_ID or TELEGRAM_API_HASH\n")
        sys.stderr.flush()
        sys.exit(1)
    if not bot_token:
        sys.stderr.write("Missing bot token\n")
        sys.stderr.flush()
        sys.exit(1)

    session_path = os.environ.get("TG_MTPROTO_SESSION", "/home/openclaw/.openclaw/protomegabot_mtproto")
    _acquire_instance_lock(session_path)
    _emit({"type": "status", "message": "Starting owned Telethon bridge"})

    client = TelegramClient(session_path, int(api_id), api_hash)
    await client.start(bot_token=bot_token)
    for suffix in (".session", ".session-wal", ".session-shm", ""):
        path = session_path + suffix
        if os.path.isfile(path):
            os.chmod(path, 0o600)

    me = await client.get_me()
    _emit({"type": "status", "message": f"Connected as @{getattr(me, 'username', None) or '(unknown)'} (id={me.id})"})
    _emit({"type": "status", "message": "MTProto receive mode active; delivery capabilities require canary validation"})

    @client.on(events.NewMessage(incoming=True))
    async def _on_new_message(event):
        try:
            if _is_self_message(event, me.id):
                return
            update_dict = _convert_message(event)
            _emit({"type": "message", "update": update_dict})
        except Exception as exc:
            _emit({"type": "error", "message": f"Message handling error: {exc}"})

    _emit({"type": "status", "message": "Event handler registered; waiting for messages..."})
    await client.run_until_disconnected()
    _emit({"type": "status", "message": "Client disconnected"})


if __name__ == "__main__":
    asyncio.run(main())
