import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import auth
from message_envelope import (
    MessageEnvelope,
    set_current_envelope,
    get_current_envelope,
    require_current_envelope,
)

_running = False
_last_message = ""
_msg_lock = threading.Lock()
_state_lock = threading.Lock()

_bot_token = ""
_api_base = ""
_chat_id = ""
_allowed_chat_ids = set()
_reply_chat_id = ""  # legacy fallback, not used for ordinary replies
_active_chat_id = ""  # legacy fallback, not used for ordinary replies
_pending_messages = []  # now stores MessageEnvelope objects
_poll_timeout = 20
_offset = None
_connected = False

_authenticated_user_id = None
_allowed_user_ids = set()
_private_only = False
_sync_poll = False
_receive_transport = "bot_api"
_current_inbound_identity = None

_attachment_dir = "/home/openclaw/tmp/omegaclaw-telegram-attachments"
_download_attachments = True
_attachment_max_bytes = 5 * 1024 * 1024
_attachment_max_chars = 20000

_last_preack_key = ""
_last_preack_time = 0.0
_last_sent_key = ""
_last_sent_time = 0.0
_last_sent_lock = threading.Lock()

# --- Bot-to-bot loop-prevention safeguards (Bot API 10.0 requirement) ---
_self_bot_id = None  # populated at start_telegram() from getMe
_bot_sender_last_ts = {}  # user_id -> last reply timestamp
_bot_sender_lock = threading.Lock()
_bot_interaction_chain = []  # list of (timestamp, sender_id) for recent bot-bot replies
_bot_interaction_lock = threading.Lock()
_BOT_RATE_LIMIT_S = 1.0  # min seconds between replies to the same bot sender
_BOT_MAX_CHAIN_DEPTH = 8  # max consecutive bot-to-bot replies in window
_BOT_CHAIN_WINDOW_S = 60.0  # sliding window for chain counting


def _looks_like_long_request(msg):
    """Heuristic for requests that should get an immediate model-independent ack.

    This is intentionally conservative: it does not attempt to answer or schedule
    work, it only confirms receipt for messages that are likely to require a
    slow/deep response.  This prevents the conversational-pragmatics guarantee
    from depending on the LLM successfully returning a `(send ...)` action.
    """
    text = str(msg or "").strip()
    if not text:
        return False
    lowered = text.lower()

    if len(text) >= 450 and any(marker in lowered for marker in (
        "please", "can you", "could you", "would you", "formalize", "prove",
        "theorem", "mathematical", "rigorous", "research programme",
        "simulation", "omegasim", "hyperseed",
    )):
        return True

    strong_markers = (
        "rigorous mathematical formalization",
        "prove what you can",
        "draw useful conclusions",
        "theory-motivated suggestions",
        "consult gpt-5.5",
        "challenging thing",
    )
    if any(marker in lowered for marker in strong_markers):
        return True

    # Structured multi-step requests are often long-running even if not huge.
    if len(text) >= 250 and len(re.findall(r"\bS\d+\)", text, flags=re.IGNORECASE)) >= 2:
        return True

    return False


def _maybe_send_preack(display_name, msg, chat_id=""):
    global _last_preack_key, _last_preack_time

    if not _parse_bool_env("TG_PREACK_LONG_REQUESTS", False):
        return
    if not _looks_like_long_request(msg):
        return

    # Suppress exact-message duplicates/replays, but allow a later distinct long
    # request to receive its own acknowledgement.
    now = time.time()
    key = re.sub(r"\s+", " ", str(msg or "").strip())[:500]
    if key and key == _last_preack_key and (now - _last_preack_time) < 6 * 3600:
        return
    _last_preack_key = key
    _last_preack_time = now

    print("[TELEGRAM_OPERATOR] preack suppressed: acknowledgements are internal state, not speech")


def _set_reply_chat(chat_id):
    global _reply_chat_id
    chat_id = str(chat_id or "").strip()
    if not chat_id:
        return
    with _state_lock:
        _reply_chat_id = chat_id


def _set_active_chat(chat_id):
    """Bind ordinary `(send ...)` replies to the dequeued inbound message.

    The Telegram poller may accept updates from several configured chats before
    the OmegaClaw loop asks for the next message.  `_reply_chat_id` tracks the
    latest accepted chat for backward compatibility, but user-facing replies
    should target the chat of the message actually handed to the LLM until the
    next message is dequeued.
    """
    global _active_chat_id, _reply_chat_id
    chat_id = str(chat_id or "").strip()
    if not chat_id:
        return
    with _state_lock:
        _active_chat_id = chat_id
        _reply_chat_id = chat_id
_last_skip_response = False

def _set_last(msg, chat_id="", skip_response=False, *, envelope=None):
    """Enqueue a message for the OmegaClaw loop.

    If *envelope* is provided it is stored and later used as the immutable
    routing context for send_message.  The legacy (msg, chat_id, skip) tuple
    path is kept only for backward compatibility with code paths that have
    not been migrated yet.
    """
    global _last_message, _pending_messages, _last_skip_response
    with _msg_lock:
        if envelope is not None:
            _pending_messages.append(envelope)
        else:
            _pending_messages.append((msg, chat_id, skip_response))
        if _last_message == "":
            _last_message = msg
        else:
            _last_message = _last_message + " | " + msg


def getLastMessage():
    global _last_message, _reply_chat_id, _last_skip_response, _current_inbound_identity
    if _receive_transport == "bot_api" and _sync_poll and _running:
        _poll_once()
    with _msg_lock:
        if _pending_messages:
            item = _pending_messages.pop(0)
            if hasattr(item, "source_chat_id") and hasattr(item, "response_policy"):
                _last_message = ""
                _last_skip_response = item.response_policy == "skip"
                set_current_envelope(item)
                _current_inbound_identity = item.inbound_identity
                # Keep legacy globals in sync for unmigrated code paths
                _set_active_chat(item.source_chat_id)
                if item.inbound_identity:
                    identity = json.dumps(item.inbound_identity, sort_keys=True, separators=(",", ":"))
                    return f"[TELEGRAM_IDENTITY_V2 {identity}]\n{item.text}"
                return item.text
            # Legacy tuple path
            msg, cid, skip = item
            _last_message = ""
            _last_skip_response = skip
            set_current_envelope(None)
            if cid:
                _set_active_chat(cid)
            return msg
        # Fallback for any remaining accumulated message
        tmp = _last_message
        _last_message = ""
        _last_skip_response = False
        set_current_envelope(None)
        return tmp


def current_message_correlation_id():
    env = get_current_envelope()
    if env is not None:
        return env.correlation_id
    return None


def should_skip_response():
    global _last_skip_response
    return _last_skip_response


def current_addressee_classification():
    identity = _current_inbound_identity or {}
    return str(identity.get("addressee_classification", "GROUP"))


def _bot_registry():
    """Load the non-secret shared bot registry from TG_BOT_REGISTRY_JSON."""
    raw = os.environ.get("TG_BOT_REGISTRY_JSON", "").strip()
    entries = []
    if raw:
        try:
            parsed = json.loads(raw)
            entries = parsed.get("entries", parsed) if isinstance(parsed, dict) else parsed
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid TG_BOT_REGISTRY_JSON: {exc}") from exc
    result = {}
    for entry in entries or []:
        if not isinstance(entry, dict) or not str(entry.get("telegram_user_id", "")).strip():
            raise ValueError("bot registry entries require telegram_user_id")
        result[str(entry["telegram_user_id"])] = dict(entry)
    return result


def _mention_ids(message, registry):
    ids = set()
    usernames = {
        str(entry.get("telegram_username", "")).lstrip("@").lower(): bot_id
        for bot_id, entry in registry.items()
        if str(entry.get("telegram_username", "")).strip()
    }
    for entities_key, text_key in (("entities", "text"), ("caption_entities", "caption")):
        source = str(message.get(text_key, "") or "")
        for entity in message.get(entities_key) or []:
            user = entity.get("user") or {}
            if entity.get("type") == "text_mention" and user.get("id") is not None:
                ids.add(str(user["id"]))
            elif entity.get("type") == "mention":
                token = source[entity.get("offset", 0):][:entity.get("length", 0)].lstrip("@").lower()
                if token in usernames:
                    ids.add(usernames[token])
                elif _self_bot_id and token in {
                    str(os.environ.get("TG_BOT_USERNAME", "")).lstrip("@").lower(),
                    "protomegabot",
                }:
                    ids.add(str(_self_bot_id))
    return sorted(ids)


def _build_inbound_identity(message, chat, user, msg):
    registry = _bot_registry()
    bot_id = str(_self_bot_id or "")
    mentions = _mention_ids(message, registry)
    reply = message.get("reply_to_message") or {}
    reply_from = reply.get("from") or {}
    reply_id = str(reply_from.get("id", ""))
    mentioned_me = bool(bot_id and bot_id in mentions)
    reply_to_me = bool(bot_id and reply_id == bot_id)
    if str(chat.get("type", "")) == "private":
        classification = "DIRECT"
    elif mentioned_me:
        classification = "DIRECT"
    elif reply_to_me:
        classification = "DIRECT"
    elif reply_id and reply_id in registry:
        classification = "SECONDARY"
    elif not mentions and not reply:
        classification = "GROUP"
    else:
        classification = "INCIDENTAL"
    self_entry = registry.get(bot_id, {})
    return {
        "inbound_envelope": {
            "version": 2,
            "message_id": str(message.get("message_id", "")),
            "chat_id": str(chat.get("id", "")),
            "thread_id": str(message.get("message_thread_id", "")) or None,
            "sender": {
                "telegram_user_id": str(user.get("id", "")),
                "display_name": _display_name(user, chat),
                "is_bot": str(user.get("id", "")) in registry,
            },
            "reply_to": ({
                "message_id": str(reply.get("message_id", "")),
                "sender_telegram_user_id": reply_id or None,
                "sender_is_bot": reply_id in registry,
            } if reply else None),
            "mentions": mentions,
            "text": msg,
            "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "self_context": {
            "version": 2,
            "agent_instance_id": self_entry.get("agent_instance_id", "protomegabot"),
            "telegram_bot_id": bot_id,
            "display_name": self_entry.get("display_name", "ProtoMegaBot"),
            "workspace": os.getcwd(),
            "session_key": f"omegaclaw:telegram:{chat.get('id', '')}",
            "social_role": self_entry.get("social_role", "always-attending"),
            "important_bots": [str(value) for value in self_entry.get("important_bots", [])],
        },
        "addressee_classification": classification,
        "reinforced": mentioned_me and reply_to_me,
    }


def _is_self_bot_message(user):
    """True when the message was sent by this bot itself."""
    if not _self_bot_id:
        return False
    return str(user.get("id", "")) == str(_self_bot_id)


def _bot_to_bot_loop_guard(user, chat_id):
    """Return True when this bot-to-bot reply should be suppressed.

    Telegram Bot API 10.0 bot-to-bot mode requires loop prevention.
    Enforces:
    1. Per-sender rate limit (_BOT_RATE_LIMIT_S seconds between replies).
    2. Global interaction-depth cap (_BOT_MAX_CHAIN_DEPTH in _BOT_CHAIN_WINDOW_S).
    Returns True (suppress) when either limit is exceeded.
    """
    sender_id = str(user.get("id", ""))
    if not sender_id or not user.get("is_bot"):
        return False  # only guard bot-originated traffic

    now = time.time()

    # 1. Per-sender rate limit
    with _bot_sender_lock:
        last = _bot_sender_last_ts.get(sender_id, 0.0)
        if (now - last) < _BOT_RATE_LIMIT_S:
            print(f"[TELEGRAM] Bot-to-bot rate limit for sender {sender_id}; suppressing.")
            return True
        _bot_sender_last_ts[sender_id] = now

    # 2. Global interaction-depth cap
    with _bot_interaction_lock:
        _bot_interaction_chain.append((now, sender_id))
        cutoff = now - _BOT_CHAIN_WINDOW_S
        while _bot_interaction_chain and _bot_interaction_chain[0][0] < cutoff:
            _bot_interaction_chain.pop(0)
        if len(_bot_interaction_chain) > _BOT_MAX_CHAIN_DEPTH:
            print(f"[TELEGRAM] Bot-to-bot chain depth {len(_bot_interaction_chain)} exceeds {_BOT_MAX_CHAIN_DEPTH}; suppressing.")
            return True

    return False


def _should_skip_group_response(message, msg):
    """Return whether a group message is addressed only to another bot.

    An explicit mention of this bot takes precedence over incidental mentions
    of collaborators (for example, "@Protomegabot ... from @Protocosmobot").

    Mentions can arrive in two independent entity lists whose offsets index
    into two different source strings:
      * ``entities``          -> offsets into ``message['text']``
      * ``caption_entities``  -> offsets into ``message['caption']``
    A document/photo/PDF sent with a caption like "@Protomegabot look" carries
    the mention only in ``caption_entities``.  Reading ``entities`` alone (and
    slicing the composite ``msg`` string with caption offsets) silently drops
    those mentions, so which bot responds ends up depending on incidental
    reply framing rather than on who was actually @-mentioned.  Read both
    lists, each against its own source string, and also honour ``text_mention``
    entities that name a user object without a literal ``@``.
    """
    mentions = []

    def _collect(entities, source):
        source = source or ""
        for ent in entities or []:
            etype = ent.get("type")
            if etype not in ("mention", "text_mention"):
                continue
            start = ent.get("offset", 0)
            length = ent.get("length", 0)
            token = source[start:start + length].lstrip("@").lower()
            if token:
                mentions.append(token)
            if etype == "text_mention":
                user = ent.get("user") or {}
                for key in ("username", "first_name", "last_name"):
                    val = str(user.get(key, "") or "").strip().lower()
                    if val:
                        mentions.append(val)

    _collect(message.get("entities"), message.get("text") or "")
    _collect(message.get("caption_entities"), message.get("caption") or "")

    self_mentioned = any(
        mentioned.startswith("protomega") or mentioned == "protom"
        for mentioned in mentions
    )
    if self_mentioned:
        return False

    if mentions:
        return True

    reply_to = message.get("reply_to_message") or {}
    reply_from = reply_to.get("from") or {}
    return bool(reply_from.get("is_bot") and str(reply_from.get("id", "")))


def _parse_auth_candidate(msg):
    text = msg.strip()
    lower = text.lower()
    if lower.startswith("auth "):
        return text[5:].strip()
    if lower.startswith("/auth "):
        return text[6:].strip()
    return text


def _display_name(user, chat):
    username = str(user.get("username", "")).strip()
    if username:
        return f"@{username}"

    first = str(user.get("first_name", "")).strip()
    last = str(user.get("last_name", "")).strip()
    full = f"{first} {last}".strip()
    if full:
        return full

    title = str(chat.get("title", "")).strip()
    if title:
        return title

    return "telegram_user"


def _safe_text(value):
    return str(value or "").replace("\r", "").strip()


def _origin_display_name(origin):
    """Return a compact human-readable Telegram forward origin description."""
    if not isinstance(origin, dict):
        return ""

    origin_type = _safe_text(origin.get("type"))
    if origin_type == "user":
        sender = origin.get("sender_user") or {}
        return _display_name(sender, {})
    if origin_type == "hidden_user":
        name = _safe_text(origin.get("sender_user_name"))
        return name or "hidden_user"
    if origin_type == "chat":
        chat = origin.get("sender_chat") or {}
        return _safe_text(chat.get("title")) or _safe_text(chat.get("username")) or "chat"
    if origin_type == "channel":
        chat = origin.get("chat") or {}
        title = _safe_text(chat.get("title")) or _safe_text(chat.get("username")) or "channel"
        message_id = _safe_text(origin.get("message_id"))
        return f"{title} message_id={message_id}" if message_id else title
    return origin_type


def _forward_metadata(message):
    """Render Telegram forwarding metadata so forwarded content is explicit.

    Telegram includes the forwarded message body in the normal message fields
    when the bot actually receives the update.  This helper preserves the
    forward provenance, which is otherwise easy for the LLM to miss.
    """
    if not isinstance(message, dict):
        return ""

    origin = message.get("forward_origin")
    if isinstance(origin, dict):
        parts = ["forwarded=true"]
        origin_type = _safe_text(origin.get("type"))
        if origin_type:
            parts.append(f"origin_type={origin_type}")
        name = _origin_display_name(origin)
        if name:
            parts.append(f"origin={name}")
        date = _safe_text(origin.get("date") or message.get("forward_date"))
        if date:
            parts.append(f"date={date}")
        return "[Telegram forward metadata: " + ", ".join(parts) + "]"

    # Backward-compatible Bot API fields used by older Telegram deployments.
    if message.get("forward_from") or message.get("forward_sender_name") or message.get("forward_from_chat"):
        parts = ["forwarded=true"]
        if message.get("forward_from"):
            parts.append(f"origin={_display_name(message.get('forward_from') or {}, {})}")
        if message.get("forward_sender_name"):
            parts.append(f"origin={_safe_text(message.get('forward_sender_name'))}")
        if message.get("forward_from_chat"):
            chat = message.get("forward_from_chat") or {}
            parts.append(f"origin_chat={_safe_text(chat.get('title')) or _safe_text(chat.get('username')) or _safe_text(chat.get('id'))}")
        if message.get("forward_date"):
            parts.append(f"date={_safe_text(message.get('forward_date'))}")
        return "[Telegram forward metadata: " + ", ".join(part for part in parts if part) + "]"

    return ""


def _api_call(method, params=None, timeout=30, use_post=False):
    if not _api_base:
        raise RuntimeError("Telegram adapter not initialized")

    params = params or {}
    encoded = urllib.parse.urlencode(params).encode("utf-8")
    url = f"{_api_base}/{method}"

    if use_post:
        req = urllib.request.Request(url, data=encoded)
    else:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url)

    with urllib.request.urlopen(req, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", errors="ignore"))

    if not payload.get("ok"):
        raise RuntimeError(payload.get("description", f"{method} failed"))

    return payload.get("result")


def _download_file(file_id, label, file_size=0):
    if not _download_attachments or not file_id:
        return "", ""
    try:
        size = int(file_size or 0)
        if _attachment_max_bytes and size and size > _attachment_max_bytes:
            return "", f"file too large ({size} bytes > {_attachment_max_bytes} byte limit)"

        info = _api_call("getFile", {"file_id": file_id}, timeout=15) or {}
        file_path = str(info.get("file_path", "")).strip()
        if not file_path:
            return "", "Telegram getFile returned no file_path"

        os.makedirs(_attachment_dir, exist_ok=True)
        base = os.path.basename(file_path) or label or file_id
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "attachment"
        dest = os.path.join(_attachment_dir, f"{int(time.time())}-{safe}")
        if _bot_token == "proxy":
            url = f"{_api_base}/file/{file_path}"
        else:
            url = f"https://api.telegram.org/file/bot{_bot_token}/{file_path}"
        with urllib.request.urlopen(url, timeout=30) as response, open(dest, "wb") as out:
            data = response.read(_attachment_max_bytes + 1 if _attachment_max_bytes else -1)
            if _attachment_max_bytes and len(data) > _attachment_max_bytes:
                return "", f"download exceeded {_attachment_max_bytes} byte limit"
            out.write(data)
        return dest, ""
    except Exception as exc:
        print(f"[TELEGRAM] Attachment download failed: {exc}")
        return "", str(exc)


def _read_text_attachment(path):
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            with open(path, "r", encoding=encoding) as f:
                return f.read(), ""
        except UnicodeError:
            continue
        except Exception as exc:
            return "", str(exc)
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", errors="replace"), ""
    except Exception as exc:
        return "", str(exc)


def _read_pdf_attachment(path):
    out_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="omegaclaw-tg-pdf-", suffix=".txt", delete=False) as tmp:
            out_path = tmp.name
        subprocess.run(
            ["pdftotext", "-layout", path, out_path],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=20,
        )
        with open(out_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), ""
    except FileNotFoundError:
        return "", "pdftotext is not installed"
    except Exception as exc:
        return "", f"PDF text extraction failed: {exc}"
    finally:
        if out_path:
            try:
                os.unlink(out_path)
            except Exception:
                pass


def _clip_attachment_text(text):
    text = str(text).replace("\r", "")
    if len(text) <= _attachment_max_chars:
        return text
    return text[:_attachment_max_chars] + f"\n[... attachment text truncated at {_attachment_max_chars} characters ...]"


def _write_attachment_chunks(path, text):
    """Persist full extracted text plus bounded chunks for later read-file calls."""
    text = str(text).replace("\r", "")
    extracted_path = f"{path}.extracted.txt"
    chunk_size = max(1000, _attachment_max_chars)
    chunk_paths = []

    try:
        with open(extracted_path, "w", encoding="utf-8") as f:
            f.write(text)
        for idx in range(0, len(text), chunk_size):
            chunk_no = len(chunk_paths) + 1
            chunk_path = f"{path}.chunk{chunk_no:03d}.txt"
            with open(chunk_path, "w", encoding="utf-8") as f:
                f.write(text[idx:idx + chunk_size])
            chunk_paths.append(chunk_path)
    except Exception as exc:
        print(f"[TELEGRAM] Attachment chunk write failed: {exc}")
        return "", []

    return extracted_path, chunk_paths


def _render_attachment_text(path, text, marker):
    text = str(text).replace("\r", "")
    if len(text) <= _attachment_max_chars:
        return f"\n<<<{marker}>>>\n{text}\n<<<END_{marker}>>>"

    extracted_path, chunk_paths = _write_attachment_chunks(path, text)
    first_chunk = text[:_attachment_max_chars]
    chunk_list = "\n".join(f"- {chunk_path}" for chunk_path in chunk_paths)
    return (
        f"\n<<<{marker}>>>\n"
        f"{first_chunk}\n"
        f"[... attachment preview truncated at {_attachment_max_chars} characters ...]\n"
        f"[Full extracted attachment text saved to: {extracted_path or '<write_failed>'}]\n"
        f"[Attachment split into {len(chunk_paths)} chunks of up to {_attachment_max_chars} characters; use read-file on chunk paths to inspect the rest.]\n"
        f"{chunk_list}\n"
        f"<<<END_{marker}>>>"
    )


def _attachment_content(path, name, mime):
    if not path:
        return ""
    lower_name = name.lower()
    if mime.startswith("text/") or lower_name.endswith((".txt", ".md", ".tex", ".csv", ".json", ".yaml", ".yml", ".log")):
        text, error = _read_text_attachment(path)
        if error:
            return f"\n[Attachment saved but text read failed: {error}]"
        return _render_attachment_text(path, text, "ATTACHMENT_CONTENT_UNTRUSTED")
    if mime == "application/pdf" or lower_name.endswith(".pdf"):
        text, error = _read_pdf_attachment(path)
        if error:
            return f"\n[Attachment saved but PDF text extraction failed: {error}]"
        return _render_attachment_text(path, text, "ATTACHMENT_TEXT_UNTRUSTED extracted_by=pdftotext")
    return ""


def _attachment_summary(kind, item, *, include_content=True):
    if not isinstance(item, dict):
        return ""
    file_id = str(item.get("file_id", "")).strip()
    name = str(item.get("file_name", "") or item.get("title", "") or "").strip()
    mime = str(item.get("mime_type", "")).strip()
    size = item.get("file_size")
    parts = [f"type={kind}"]
    if name:
        parts.append(f"name={name}")
    if mime:
        parts.append(f"mime={mime}")
    if size:
        parts.append(f"size={size}")
    saved, error = _download_file(file_id, name or kind, size or 0)
    if saved:
        parts.append(f"saved_path={saved}")
        if include_content:
            content = _attachment_content(saved, name, mime)
        else:
            content = "\n[Attachment content omitted from reply-context; read saved_path explicitly if needed.]"
    elif file_id:
        parts.append("saved_path=<not_downloaded>")
        content = f"\n[Attachment not read: {error}]" if error else ""
    else:
        content = ""
    return "[Telegram attachment: " + ", ".join(parts) + "]" + content


def _message_text_and_attachments(message, *, _depth=0, include_reply_content=True):
    text = str(message.get("text") or message.get("caption") or "").strip()
    attachments = []

    forward = _forward_metadata(message)
    if forward:
        attachments.append(forward)

    reply = message.get("reply_to_message")
    if isinstance(reply, dict) and _depth < 1:
        # Include replied-to text and attachment metadata, but not extracted
        # document/PDF bodies. Large quoted docs should be explicit context,
        # not silently injected into router/main prompts.
        #
        # Exception: when this bot is explicitly @-mentioned in the reply
        # text/caption, extract full attachment content from the replied-to
        # message so the bot can actually read the document it's being asked
        # about. This closes the framing asymmetry where a direct post with
        # attachment works but a reply-with-mention only shows metadata.
        reply_mentioned = False
        if _self_bot_id:
            # Text-based fallback: also check raw text for @Protomegabot mentions
            # in case Telegram doesn't include mention entities (can happen in replies).
            for src_key in ("text", "caption"):
                raw = message.get(src_key, "") or ""
                if re.search(r"@protomega\w*", raw, re.IGNORECASE):
                    reply_mentioned = True
                    break
        if _self_bot_id:
            for ent_list_key, src_key in (("entities", "text"), ("caption_entities", "caption")):
                for ent in message.get(ent_list_key, []) or []:
                    if ent.get("type") in ("mention", "text_mention"):
                        src = message.get(src_key, "") or ""
                        token = src[ent.get("offset", 0):ent.get("offset", 0) + ent.get("length", 0)].lstrip("@").lower()
                        if any(n in token for n in ("protomega", "protom")):
                            reply_mentioned = True
                        if ent.get("type") == "text_mention":
                            u = ent.get("user") or {}
                            for k in ("username", "first_name", "last_name"):
                                v = str(u.get(k, "") or "").strip().lower()
                                if v and any(n in v for n in ("protomega", "protom")):
                                    reply_mentioned = True
        reply_text, _ = _message_text_and_attachments(
            reply, _depth=_depth + 1, include_reply_content=reply_mentioned
        )
        if reply_text:
            attachments.append(
                "[Telegram replied-to message summary follows]\n"
                f"{reply_text}\n"
                "[End Telegram replied-to message summary]"
            )

    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        # Telegram sends multiple sizes for the same image; use the largest.
        best = max((p for p in photos if isinstance(p, dict)),
                   key=lambda p: int(p.get("file_size") or p.get("width") or 0),
                   default=None)
        if best:
            attachments.append(_attachment_summary("photo", best, include_content=include_reply_content))

    for kind in ("document", "animation", "audio", "voice", "video", "video_note", "sticker"):
        summary = _attachment_summary(kind, message.get(kind), include_content=include_reply_content)
        if summary:
            attachments.append(summary)

    location = message.get("location")
    if isinstance(location, dict):
        lat = location.get("latitude")
        lon = location.get("longitude")
        attachments.append(f"[Telegram attachment: type=location, latitude={lat}, longitude={lon}]")

    contact = message.get("contact")
    if isinstance(contact, dict):
        name = " ".join(str(contact.get(k, "")).strip() for k in ("first_name", "last_name")).strip()
        attachments.append(f"[Telegram attachment: type=contact, name={name or '<unknown>'}]")

    combined = "\n".join(part for part in [text] + attachments if part)
    return combined, text


def _initialize_offset():
    global _offset
    try:
        updates = _api_call("getUpdates", {"timeout": 0}, timeout=10) or []
    except Exception as exc:
        print(f"[TELEGRAM] Could not read initial offset: {exc}")
        return

    max_update = -1
    for update in updates:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            max_update = max(max_update, update_id)

    if max_update >= 0:
        with _state_lock:
            _offset = max_update + 1


def _is_auth_command(msg):
    lower = msg.strip().lower()
    return lower.startswith("auth ") or lower.startswith("/auth ")


def _parse_bool_env(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _send_dedupe_window_s():
    try:
        value = float(os.environ.get("TG_SEND_DEDUPE_WINDOW_S", "10"))
    except Exception:
        return 10.0
    if value < 0:
        return 0.0
    return value


def _is_duplicate_send(text, target_chat):
    """Suppress immediate duplicate sends caused by MeTTa nondeterminism.

    Some skill evaluations can yield repeated identical `(send ...)` alternatives
    from one LLM response. The channel layer is the final side-effect boundary,
    so make it idempotent for the same target/text over a short window while
    still allowing an intentional repeated message later.
    """
    global _last_sent_key, _last_sent_time

    window_s = _send_dedupe_window_s()
    if window_s <= 0:
        return False

    normalized_text = re.sub(r"\s+", " ", str(text or "").strip())
    key = f"{target_chat}\0{normalized_text}"
    now = time.time()
    with _last_sent_lock:
        if key and key == _last_sent_key and (now - _last_sent_time) < window_s:
            print(f"[TELEGRAM] Suppressed duplicate send within {window_s:g}s window chat_id={target_chat}")
            return True
        _last_sent_key = key
        _last_sent_time = now
    return False


def _parse_csv_values(raw):
    values = []
    seen = set()
    for part in str(raw or "").split(","):
        value = part.strip()
        if value and value not in seen:
            values.append(value)
            seen.add(value)
    return values


def _parse_csv_env(name):
    return set(_parse_csv_values(os.environ.get(name, "")))


def _configure_chat_targets(chat_id):
    """Configure Telegram chat targets while preserving single-chat behavior.

    `chat_id` and legacy `TG_CHAT_ID` can still name one target.  New
    comma-separated `TG_CHAT_IDS` / `TG_ALLOWED_CHAT_ID(S)` values let the same
    bot observe more than one chat.  Outbound replies go to the active dequeued
    inbound chat, falling back to the first configured chat.
    """
    global _chat_id, _allowed_chat_ids, _reply_chat_id, _active_chat_id

    ordered = []
    for raw in (
        chat_id,
        os.environ.get("TG_CHAT_ID", ""),
        os.environ.get("TG_CHAT_IDS", ""),
        os.environ.get("TG_ALLOWED_CHAT_ID", ""),
        os.environ.get("TG_ALLOWED_CHAT_IDS", ""),
    ):
        for value in _parse_csv_values(raw):
            if value not in ordered:
                ordered.append(value)

    _allowed_chat_ids = set(ordered)
    _chat_id = ordered[0] if ordered else ""
    _reply_chat_id = _chat_id
    _active_chat_id = ""


def _skip_initial_offset():
    return _parse_bool_env("TG_SKIP_INITIAL_OFFSET", False)


def _parse_int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except Exception:
        return default


def _configure_access_controls():
    global _allowed_user_ids, _private_only, _download_attachments, _attachment_dir, _attachment_max_bytes, _attachment_max_chars
    _allowed_user_ids = _parse_csv_env("TG_ALLOWED_USER_ID") | _parse_csv_env("TG_ALLOWED_USER_IDS")
    _private_only = _parse_bool_env("TG_PRIVATE_ONLY", False)
    _download_attachments = _parse_bool_env("TG_DOWNLOAD_ATTACHMENTS", True)
    _attachment_dir = os.environ.get("TG_ATTACHMENT_DIR", _attachment_dir).strip() or _attachment_dir
    _attachment_max_bytes = max(0, _parse_int_env("TG_ATTACHMENT_MAX_BYTES", _attachment_max_bytes))
    _attachment_max_chars = max(1000, _parse_int_env("TG_ATTACHMENT_MAX_CHARS", _attachment_max_chars))


def _is_allowed_message(chat_id, user_id, msg, chat_type=""):
    global _chat_id, _authenticated_user_id

    with _state_lock:
        if _private_only and chat_type != "private":
            return "ignore"
        if _allowed_user_ids and user_id and user_id not in _allowed_user_ids:
            return "ignore"
        if _allowed_chat_ids and chat_id not in _allowed_chat_ids:
            return "ignore"
        if not auth.is_auth_enabled():
            if not _chat_id and not _allowed_chat_ids:
                _chat_id = chat_id
            return "allow"
        if _authenticated_user_id is not None:
            if _allowed_chat_ids and chat_id not in _allowed_chat_ids:
                return "ignore"
            if not _allowed_chat_ids and chat_id != _chat_id:
                return "ignore"
            return "allow" if user_id == _authenticated_user_id else "ignore"
        if not _is_auth_command(msg):
            return "ignore"
        candidate = _parse_auth_candidate(msg)
        if auth.verify_token(candidate):
            _authenticated_user_id = user_id
            if not _chat_id and not _allowed_chat_ids:
                _chat_id = chat_id
            return "auth_bound"
        return "ignore"


def _handle_updates(updates):
    global _offset
    for update in updates:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            with _state_lock:
                if _offset is None or (update_id + 1) > _offset:
                    _offset = update_id + 1

        message = (
            update.get("message")
            or update.get("edited_message")
            or update.get("channel_post")
            or update.get("edited_channel_post")
        )
        if not isinstance(message, dict):
            continue

        msg, auth_text = _message_text_and_attachments(message)
        if not msg:
            continue

        chat = message.get("chat") or {}
        user = message.get("from") or message.get("sender_chat") or {}
        chat_id = str(chat.get("id", "")).strip()
        chat_type = str(chat.get("type", "")).strip()
        user_id = str(user.get("id", "")).strip()
        if not chat_id:
            continue

        # --- Bot-to-bot safeguards (Bot API 10.0 requirement) ---
        # 1. Never process our own outgoing messages (self-echo prevention).
        if _is_self_bot_message(user):
            continue
        # 2. Rate-limit and depth-cap bot-originated traffic to prevent loops.
        if user.get("is_bot") and _bot_to_bot_loop_guard(user, chat_id):
            continue

        identity = _build_inbound_identity(message, chat, user, msg)
        _skip_response = identity["addressee_classification"] == "INCIDENTAL"

        state = _is_allowed_message(chat_id, user_id, auth_text, chat_type)
        display_name = _display_name(user, chat)
        source_message_id = int(message.get("message_id", 0) or 0)
        social_role = identity["self_context"]["social_role"]
        policy = "skip" if (
            _skip_response and chat_type != "private" and social_role != "always-attending"
        ) else "ordinary"
        if state == "allow":
            _set_reply_chat(chat_id)
            env = MessageEnvelope.from_ingress(
                update_id=int(update_id or 0),
                source_chat_id=chat_id,
                source_message_id=source_message_id,
                sender_id=user_id,
                sender_display=display_name,
                text=f"{display_name}: {msg}",
                response_policy=policy,
                inbound_identity=identity,
            )
            _set_last(f"{display_name}: {msg}", chat_id, skip_response=policy == "skip", envelope=env)
            if policy != "skip":
                _maybe_send_preack(display_name, auth_text or msg, chat_id=chat_id)
        elif state == "auth_bound":
            _set_reply_chat(chat_id)
            env = MessageEnvelope.from_ingress(
                update_id=int(update_id or 0),
                source_chat_id=chat_id,
                source_message_id=source_message_id,
                sender_id=user_id,
                sender_display=display_name,
                text=f"Authentication successful for {display_name}.",
                response_policy="auth_broadcast",
            )
            set_current_envelope(env)
            send_message(f"Authentication successful for {display_name}.")


def _poll_once(timeout=0):
    global _connected
    if _receive_transport != "bot_api":
        raise RuntimeError(
            f"Bot API getUpdates is disabled for receive transport {_receive_transport!r}"
        )
    try:
        params = {"timeout": int(timeout)}
        with _state_lock:
            if _offset is not None:
                params["offset"] = _offset
        updates = _api_call("getUpdates", params=params, timeout=int(timeout) + 10) or []
        _connected = True
        _handle_updates(updates)
    except Exception as exc:
        _connected = False
        print(f"[TELEGRAM] Poll error: {exc}")


def _poll_loop():
    global _connected
    print("[TELEGRAM] Polling started")

    while _running:
        _poll_once(_poll_timeout)
        if not _connected:
            time.sleep(2)

    _connected = False
    print("[TELEGRAM] Polling stopped")


def _configured_receive_transport():
    """Return the one authoritative inbound Telegram transport.

    TG_RECEIVE_TRANSPORT supersedes the legacy TG_USE_MTPROTO switch.  The
    latter remains accepted so existing launchers fail safely while migrating.
    """
    configured = os.environ.get("TG_RECEIVE_TRANSPORT", "").strip().lower()
    aliases = {
        "botapi": "bot_api",
        "bot-api": "bot_api",
        "poll": "bot_api",
        "polling": "bot_api",
        "telethon": "mtproto",
    }
    configured = aliases.get(configured, configured)
    if configured:
        if configured not in {"bot_api", "mtproto"}:
            raise ValueError(
                "TG_RECEIVE_TRANSPORT must be 'bot_api' or 'mtproto'"
            )
        legacy_mtproto = _parse_bool_env("TG_USE_MTPROTO", False)
        if "TG_USE_MTPROTO" in os.environ and legacy_mtproto != (configured == "mtproto"):
            raise ValueError(
                "TG_RECEIVE_TRANSPORT conflicts with legacy TG_USE_MTPROTO"
            )
        return configured
    return "mtproto" if _parse_bool_env("TG_USE_MTPROTO", False) else "bot_api"


def start_telegram(chat_id="", poll_timeout=20):
    global _running, _bot_token, _api_base, _poll_timeout, _offset, _connected, _sync_poll, _receive_transport, _self_bot_id

    proxy = auth.get_proxy_url()
    if proxy:
        _bot_token = "proxy"
        _api_base = f"{proxy}/telegram"
    else:
        _bot_token = (os.environ.get("TG_BOT_TOKEN") or os.environ.get("OMEGACLAW_TG_BOT_TOKEN") or "").strip()
        if not _bot_token:
            raise ValueError("TG_BOT_TOKEN is required")
        _api_base = f"https://api.telegram.org/bot{_bot_token}"

    _configure_chat_targets(chat_id)
    _configure_access_controls()

    try:
        _poll_timeout = max(1, int(poll_timeout))
    except Exception:
        _poll_timeout = 20

    _offset = None
    _running = True
    _connected = False
    _receive_transport = _configured_receive_transport()
    _sync_poll = _parse_bool_env("TG_SYNC_POLL", False) if _receive_transport == "bot_api" else False
    allowed = ",".join(sorted(_allowed_user_ids)) or "any"
    private_desc = "private-only" if _private_only else "all-chat-types"
    chat_desc = ",".join(sorted(_allowed_chat_ids)) if _allowed_chat_ids else (_chat_id or "auto-bind")

    if _receive_transport == "mtproto":
        print(f"[TELEGRAM] Starting adapter with chat target(s): {chat_desc}, allowed users: {allowed}, mode: {private_desc}, receive: MTProto (Telethon)")
        # Bot API getUpdates must remain unreachable in MTProto mode.  Bot API
        # sendMessage/getFile operations are still permitted.
        _connected = False
        try:
            import telegram_mtproto
            bridge = telegram_mtproto.start_mtproto()
            if bridge is None:
                raise RuntimeError("MTProto bridge did not start")
            print("[TELEGRAM] MTProto receive mode started; Bot API polling disabled")
            return bridge
        except Exception as exc:
            _running = False
            raise RuntimeError(f"MTProto receive startup failed: {exc}") from exc

    # Resolve our own bot ID for self-message filtering (bot-to-bot mode safety)
    try:
        me = _api_call("getMe", {}, timeout=10) or {}
        _self_bot_id = str(me.get("id", ""))
        if _self_bot_id:
            print(f"[TELEGRAM] Bot identity: @{me.get('username', '?')} (id={_self_bot_id})")
    except Exception as exc:
        print(f"[TELEGRAM] Warning: could not resolve bot identity: {exc}")

    poll_desc = "sync" if _sync_poll else "threaded"
    print(f"[TELEGRAM] Starting adapter with chat target(s): {chat_desc}, allowed users: {allowed}, mode: {private_desc}, polling: {poll_desc}")
    if _skip_initial_offset():
        print("[TELEGRAM] Preserving existing pending updates on startup")
    else:
        _initialize_offset()

    if _sync_poll:
        _connected = True
        print("[TELEGRAM] Synchronous polling enabled")
        return True

    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()
    return t


def stop_telegram():
    global _running
    _running = False
    # Stop MTProto client if running
    try:
        import telegram_mtproto
        telegram_mtproto.stop_mtproto()
    except Exception:
        pass


def _send_message_to(text, target_chat):
    text = str(text).replace("\\n", "\n").replace("\r", "")
    import re as _re
    def _decode_u_esc(m):
        return chr(int(m.group(1), 16))
    text = _re.sub(r'\\u([0-9a-fA-F]{4})', _decode_u_esc, text)
    text = _re.sub(r'(?<![A-Za-z0-9_])u([0-9a-fA-F]{4})(?![A-Za-z0-9_])', _decode_u_esc, text)
    publish, reason = _telegram_publish_gate(text)
    if not publish:
        print(f"[TELEGRAM_OPERATOR] outbound suppressed reason={reason}")
        return
    target_chat = str(target_chat or "").strip()
    if not text:
        return
    if not target_chat:
        raise RuntimeError("Telegram send unavailable: no target chat_id")

    if _is_duplicate_send(text, target_chat):
        return "TELEGRAM_SEND_DEDUPLICATED"

    max_len = 3900
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len) if text[i:i + max_len]]
    total = len(text)
    _norm_text = re.sub(r"\s+", " ", text.strip())
    sent_key = target_chat + chr(0) + _norm_text

    for idx, chunk in enumerate(chunks):
        if sent_key in _partial_send_chunks and idx < _partial_send_chunks[sent_key]:
            continue
        try:
            _api_call(
                "sendMessage",
                {"chat_id": target_chat, "text": chunk},
                timeout=15,
                use_post=True,
            )
            _partial_send_chunks[sent_key] = idx + 1
            print(f"[TELEGRAM] Sent message chunk chars={len(chunk)} chat_id={target_chat}")
        except Exception as exc:
            print(f"[TELEGRAM] Send failed: {exc}")
            raise RuntimeError(f"Telegram send failed: {exc}")

    _partial_send_chunks.pop(sent_key, None)
    return f"TELEGRAM_SEND_OK chunks={len(chunks)} chars={total}"
def _telegram_publish_gate(text):
    """Phase-2 transport barrier: silence and acknowledgements never become speech."""
    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    upper = normalized.upper()
    if not normalized:
        return False, "empty"
    if upper in {"SUPPRESS", "NO_REPLY", '{"ACTION":"SUPPRESS"}'}:
        return False, "control_result"
    if re.fullmatch(r"(?:NOTED|ACKNOWLEDGED|GOT IT|OKAY|OK)[.!]?", upper):
        return False, "acknowledgement_only"
    if re.fullmatch(
        r"(?:I(?:'M| AM)?\s+)?(?:STAYING|REMAINING|KEEPING)\s+QUIET(?:\s+BECAUSE\s+.+)?[.!]?",
        upper,
    ):
        return False, "silence_explanation"
    return True, "reply"


def send_message(text):
    """Send a reply using the active envelope's source_chat_id.

    Fails closed (RuntimeError) when no envelope is active, preventing
    silent cross-chat delivery via stale mutable routing state.
    Use send_admin_message for administrative broadcasts.
    """
    env = get_current_envelope()
    if env is not None:
        return _send_message_to(text, env.source_chat_id)
    raise RuntimeError(
        "send_message called without an active message envelope; "
        "use send_admin_message for administrative broadcasts"
    )

def send_admin_message(text):
    """Send an administrative broadcast to the configured default chat.

    Unlike ``send_message``, this bypasses the per-message envelope routing
    because it is not a reply to any inbound user message.
    """
    target = _chat_id or os.environ.get("TG_CHAT_ID", "")
    if not target:
        raise RuntimeError("Telegram admin send unavailable: no configured chat target")
    return _send_message_to(text, target)

