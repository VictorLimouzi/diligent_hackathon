"""The one place that talks to the Anthropic API.

A request is answered in this order:

1. from the on-disk cache, keyed by a hash of the whole request (model included),
   so a demo replays offline;
2. by the live API, when a key is configured;
3. by neither: `Unavailable` is raised and the calling feature answers with its
   deterministic mock, marked `stub: true`.

Environment:
  ANTHROPIC_API_KEY        the key (ANTHROPIC_AUTH_TOKEN also counts)
  CASEBOOK_LLM_MODEL       interactive model, default claude-sonnet-5-5
  CASEBOOK_LLM_BATCH_MODEL batch model, default claude-haiku-5-5
  CASEBOOK_LLM_CACHE       cache folder, default <repo>/out/llm/cache (or $CASEBOOK_OUT/llm/cache)
  CASEBOOK_LLM_MOCK=1      never call the API, even with a key
  CASEBOOK_LLM_REFRESH=1   ignore cached responses (still writes new ones)
  CASEBOOK_LLM_FALLBACK=0  do not ask for server-side refusal fallback
  CASEBOOK_LLM_TIMEOUT     seconds per request, default 120
  CASEBOOK_LLM_MAX_RETRIES SDK retries on 429/5xx/connection errors, default 3
"""
import hashlib
import json
import os
import re
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_INTERACTIVE = "claude-sonnet-5-5"
DEFAULT_BATCH = "claude-haiku-5-5"

# Effort per feature. Sonnet 5.5 guidance: medium for multistep tool use, low for
# short writing and extraction. Haiku 5.5 defaults to medium, which suits applying
# a written policy to one case.
EFFORT = {"investigate": "medium", "chat": "medium", "note": "low", "queue": "low",
          "second_opinion": "medium", "extract": "low"}
MAX_TOKENS = 16000   # thinking counts toward it, so it is not sized for the short replies alone

# Server-side refusal fallback: on a policy decline the API re-runs the request on
# the model's default fallback inside the same call. Not offered on Haiku.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
_FALLBACK_MODELS = ("claude-sonnet-5-5", "claude-opus-5", "claude-fable-5", "claude-mythos-5")

# Models that accept {"role": "system"} entries inside `messages` (no beta header).
_SYSTEM_TURN_MODELS = ("claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5", "claude-opus-4-8",
                       "claude-fable-5", "claude-mythos-5")

_CACHEABLE_STOPS = ("end_turn", "tool_use", "stop_sequence")

_lock = threading.Lock()
_sdk_client = None
_fallback_rejected = False


class LLMError(Exception):
    """A model call could not be completed. Features catch this and fall back."""


class Unavailable(LLMError):
    """No cached response and no live model."""


def _flag(name, default=False):
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def load_env():
    """Read <repo>/.env, as the server does, so the batch commands find the key too. Never overrides the environment."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(os.path.join(ROOT, ".env"))


def model_interactive():
    return os.environ.get("CASEBOOK_LLM_MODEL") or DEFAULT_INTERACTIVE


def model_batch():
    return os.environ.get("CASEBOOK_LLM_BATCH_MODEL") or DEFAULT_BATCH


def takes_system_turns(model):
    return str(model).startswith(_SYSTEM_TURN_MODELS)


def cache_dir():
    explicit = os.environ.get("CASEBOOK_LLM_CACHE")
    if explicit:
        return explicit
    out = os.environ.get("CASEBOOK_OUT") or os.path.join(ROOT, "out")
    return os.path.join(out, "llm", "cache")


def is_live():
    """True when a request that misses the cache would go to the API."""
    if _flag("CASEBOOK_LLM_MOCK"):
        return False
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return False
    try:
        import anthropic  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def llm_status():
    status = {"live": is_live(), "model_interactive": model_interactive(), "model_batch": model_batch()}
    if not status["live"]:
        status["stub"] = True
    return status


# ---------------------------------------------------------------- requests

def build_request(*, model, system, messages, feature, tools=None, schema=None,
                  show_thinking=False, cache_tail=False, tool_choice=None):
    """The keyword arguments of one Messages API call, as plain JSON-ready data.

    `system` is one string or a list of strings; each becomes a text block with a
    cache breakpoint, so a stable prefix (tools render before system) is reused
    across requests. `cache_tail` adds automatic caching of the growing
    conversation, for tool loops. No sampling parameters and no prefill: the
    current models reject both.
    """
    blocks = [system] if isinstance(system, str) else list(system)
    output_config = {"effort": EFFORT[feature]}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    request = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": t, "cache_control": {"type": "ephemeral"}} for t in blocks],
        "messages": messages,
        "output_config": output_config,
    }
    if tools:
        request["tools"] = tools
    if tool_choice:
        request["tool_choice"] = tool_choice
    if show_thinking:
        # Adaptive thinking is the default; this only asks for readable summaries,
        # which the investigator shows as "thought" events.
        request["thinking"] = {"type": "adaptive", "display": "summarized"}
    if cache_tail:
        request["cache_control"] = {"type": "ephemeral"}
    return request


def text_of(message):
    """The visible text of a response. Blocks are read by type: a response can start with thinking."""
    return "".join(b.get("text") or "" for b in message.get("content") or [] if b.get("type") == "text")


# ------------------------------------------------------------------- cache

def cache_key(request):
    blob = json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def cache_get(key):
    try:
        with open(os.path.join(cache_dir(), key + ".json"), encoding="utf-8") as f:
            entry = json.load(f)
    except (OSError, ValueError):
        return None
    message = entry.get("response") if isinstance(entry, dict) else None
    return message if isinstance(message, dict) and isinstance(message.get("content"), list) else None


def cache_put(key, request, message, feature=""):
    folder = cache_dir()
    try:
        os.makedirs(folder, exist_ok=True)
        tmp = os.path.join(folder, f".{key}.{os.getpid()}.{threading.get_ident()}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"key": key, "feature": feature, "model": request.get("model"),
                       "created": time.time(), "request": request, "response": message},
                      f, ensure_ascii=False)
        os.replace(tmp, os.path.join(folder, key + ".json"))
    except OSError:
        pass   # a read-only disk must not break a request


def forget(request):
    """Drop a cached response that turned out to be unusable, so a later run asks again."""
    try:
        os.remove(os.path.join(cache_dir(), cache_key(request) + ".json"))
    except OSError:
        pass


# -------------------------------------------------------------------- calls

def stream(request, feature=""):
    """Run one request. Yields ("text", delta) as text arrives, then ("message", dict, cached).

    Raises Unavailable when there is no cached response and no live model, and
    LLMError when the API call fails.
    """
    key = cache_key(request)
    if not _flag("CASEBOOK_LLM_REFRESH"):
        message = cache_get(key)
        if message is not None:
            for piece in re.findall(r"\S+\s*|\s+", text_of(message)):
                yield ("text", piece)
            yield ("message", message, True)
            return
    if not is_live():
        raise Unavailable("no cached response and no API key")
    message = yield from _live(request)
    if message.get("stop_reason") in _CACHEABLE_STOPS:
        cache_put(key, request, message, feature)
    yield ("message", message, False)


def complete(request, feature=""):
    """Run one request to the end. Returns (message dict, cached)."""
    for item in stream(request, feature):
        if item[0] == "message":
            return item[1], item[2]
    raise LLMError("the stream ended without a message")


def _sdk():
    global _sdk_client
    with _lock:
        if _sdk_client is None:
            import anthropic
            _sdk_client = anthropic.Anthropic(
                max_retries=int(os.environ.get("CASEBOOK_LLM_MAX_RETRIES") or 3),
                timeout=float(os.environ.get("CASEBOOK_LLM_TIMEOUT") or 120),
            )
        return _sdk_client


def _wants_fallback(model):
    return (not _fallback_rejected and _flag("CASEBOOK_LLM_FALLBACK", True)
            and str(model).startswith(_FALLBACK_MODELS))


def _live(request):
    """Call the API with streaming. Yields ("text", delta); returns the final message as a dict."""
    import anthropic

    global _fallback_rejected
    sdk = _sdk()
    emitted = False
    json_retries = 0
    plain_retry = False
    while True:
        use_fallback = _wants_fallback(request["model"]) and not plain_retry
        try:
            if use_fallback:
                manager = sdk.beta.messages.stream(**request, betas=[FALLBACK_BETA], fallbacks="default")
            else:
                manager = sdk.messages.stream(**request)
            with manager as events:
                for event in events:
                    if event.type == "text":
                        emitted = True
                        yield ("text", event.text)
                message = events.get_final_message()
            if plain_retry:
                # The request only went through without the fallback parameter:
                # this account or platform does not take it, so stop sending it.
                _fallback_rejected = True
            return message.to_dict(mode="json")
        except anthropic.BadRequestError as exc:
            if use_fallback and not emitted:
                plain_retry = True
                continue
            raise LLMError(f"the API rejected the request: {exc.message}") from exc
        except anthropic.AuthenticationError as exc:
            raise LLMError("authentication failed: check ANTHROPIC_API_KEY") from exc
        except anthropic.PermissionDeniedError as exc:
            raise LLMError(f"the key may not use {request['model']}: {exc.message}") from exc
        except anthropic.NotFoundError as exc:
            raise LLMError(f"model {request['model']} was not found") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("rate limited by the API") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"API error {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("could not reach the API") from exc
        except ValueError as exc:
            # The SDK raises ValueError from the stream when a tool input is not
            # parseable JSON. Nothing was run; ask again, a bounded number of times.
            json_retries += 1
            if emitted or json_retries > 2:
                raise LLMError(f"unreadable tool input from the model: {exc}") from exc
