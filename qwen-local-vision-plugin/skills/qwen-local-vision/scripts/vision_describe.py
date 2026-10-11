#!/usr/bin/env python3
"""Understand local images with either a local Ollama VLM or a cloud vision API.

Dependency-free (stdlib only). Two providers:
  ollama  - local vision model at 127.0.0.1:11434 (default, images never leave the machine)
  openai  - any OpenAI-compatible /chat/completions endpoint (Qwen-VL, GLM-4V, Doubao, GPT-4o, Gemini-compat, ...)

Loopback traffic deliberately bypasses the system HTTP proxy.

Windows note: Windows PowerShell 5.1 mangles non-ASCII command-line arguments
(console codepage 936). Pass Chinese prompts/questions with --prompt-file or
--prompt - (stdin) instead of --prompt "<text>".
"""

from __future__ import annotations

import argparse
import base64
import json
import locale
import mimetypes
import os
import sys
import time
from pathlib import Path
from urllib import error, request
from urllib.parse import urlparse

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = os.environ.get("QWEN_VISION_MODEL", "qwen3-vl:8b")
DEFAULT_OPENAI_MODEL = os.environ.get("VISION_MODEL", "qwen-vl-max")
DEFAULT_CONFIG_PATH = Path.home() / ".codex" / "qwen-local-vision.json"
DEFAULT_RETRIES = 3
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}

VISION_PREFERENCES = (
    "qwen3-vl", "qwen2.5-vl", "qwen2-vl", "internvl", "minicpm-v",
    "llama3.2-vision", "gemma3", "llava", "bakllava", "moondream",
)

PROMPTS = {
    "describe": (
        "Describe this image in detail. Cover: what is happening, the subject(s), "
        "setting/background, composition, lighting, art style, colors, and any visible "
        "text. Be concrete and factual; do not invent details you cannot see. "
        "Answer in the language the user used, or in Chinese if unspecified."
    ),
    "ocr": (
        "Extract ALL text visible in this image, verbatim, preserving the original "
        "language and line structure. If there is no text, say so explicitly. "
        "Do not translate and do not summarize."
    ),
}


def force_utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def read_stdin_text() -> str:
    """Read piped stdin as text, tolerating either UTF-8 or the ANSI codepage."""
    raw = sys.stdin.buffer.read()
    for encoding in ("utf-8-sig", "utf-8", locale.getpreferredencoding(False)):
        try:
            return raw.decode(encoding).strip()
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace").strip()


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr, flush=True)


class SmartOpener:
    """Honour the system proxy for remote hosts, but never for loopback.

    A misconfigured proxy must not be able to break 127.0.0.1:11434, while cloud
    vision endpoints may genuinely need the proxy that Clash/V2Ray installed.
    """

    LOOPBACK = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}

    def __init__(self):
        self.direct = request.build_opener(request.ProxyHandler({}))
        self.proxied = request.build_opener()

    def open(self, req, timeout=None):
        host = (urlparse(req.full_url).hostname or "").lower()
        opener = self.direct if host in self.LOOPBACK else self.proxied
        return opener.open(req, timeout=timeout)


def make_opener():
    return SmartOpener()


def request_json(opener, url, *, method="GET", payload=None, timeout=120.0, headers=None,
                 retries=DEFAULT_RETRIES):
    """Perform a JSON request, retrying transport failures.

    Some relay endpoints drop connections at the TLS layer intermittently
    (SSL: UNEXPECTED_EOF_WHILE_READING). Those are transport errors, not HTTP
    responses, so they are safe to retry. A real HTTP response (401/404/...) is
    returned to the caller immediately and never retried.
    """
    data = None
    head = {"Accept": "application/json"}
    if headers:
        head.update(headers)
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8", "replace")
        head["Content-Type"] = "application/json"

    last_error = None
    for attempt in range(max(0, retries) + 1):
        req = request.Request(url, data=data, headers=head, method=method)
        try:
            with opener.open(req, timeout=timeout) as response:
                raw = response.read()
            break
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} from {url}: {body[:4000]}") from exc
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                delay = 0.6 * (attempt + 1)
                eprint(f"[warn] {type(exc).__name__} on {url}; retry {attempt + 1}/{retries} in {delay:.1f}s")
                time.sleep(delay)
    if last_error is not None and attempt >= retries:
        raise RuntimeError(f"Request failed for {url}: {last_error}") from last_error
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from {url}: {raw[:1000]!r}") from exc


def list_ollama_models(opener, api_url):
    data = request_json(opener, f"{api_url}/api/tags", timeout=10.0) or {}
    names = []
    for item in data.get("models", []):
        name = item.get("name") or item.get("model") or ""
        if name:
            names.append(name)
    return names


def pick_model(installed):
    lowered = [(name, name.lower()) for name in installed]
    for wanted in VISION_PREFERENCES:
        for name, low in lowered:
            if wanted in low:
                return name
    return None


def user_env_var(name: str):
    """Read a *user* environment variable straight from the registry on Windows.

    `setx`/System Properties only reach processes started afterwards, so a key
    saved while the Codex app is already running would otherwise be invisible
    until the app restarts. On non-Windows platforms this returns None.
    """
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
            value, _ = winreg.QueryValueEx(handle, name)
    except OSError:
        return None
    value = (value or "").strip()
    return value or None


def read_text_source(path: str) -> str:
    if path == "-":
        return read_stdin_text()
    return Path(path).read_text(encoding="utf-8-sig").strip()


def resolve_images(raw_paths):
    resolved, missing = [], []
    for raw in raw_paths:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.is_file():
            missing.append(str(path))
            continue
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            eprint(f"[warn] unusual image extension, sending anyway: {path.name}")
        resolved.append(path)
    if missing:
        raise RuntimeError("image file(s) not found: " + "; ".join(missing))
    return resolved


def encode_images(images):
    return [base64.b64encode(p.read_bytes()).decode("ascii") for p in images]


def data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def normalize_chat_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def load_config(path_str):
    if not path_str:
        return {}
    path = Path(path_str).expanduser()
    if not path.is_file():
        raise RuntimeError(f"config file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"config file is not valid JSON: {path}: {exc}") from exc


def build_parser():
    parser = argparse.ArgumentParser(description="Local or API-based image understanding.")
    parser.add_argument("images", nargs="*", help="absolute or relative image path(s)")
    parser.add_argument("--images-from-file",
                        help="UTF-8 text file with one image path per line (use when paths are non-ASCII)")
    parser.add_argument("--mode", choices=("describe", "ocr", "ask"), default="describe",
                        help="describe = full description, ocr = verbatim text, ask = use the question")
    parser.add_argument("--prompt", help="custom question/instruction; use '-' to read it from stdin")
    parser.add_argument("--prompt-file", help="read the custom question from this UTF-8 file")

    parser.add_argument("--provider", choices=("auto", "ollama", "openai"), default="auto",
                        help="auto = openai if a base_url/api key is configured, else ollama")
    parser.add_argument("--model", default=None, help="model name; defaults per provider")
    parser.add_argument("--config", nargs="?", const=str(DEFAULT_CONFIG_PATH),
                        help=f"JSON config file (default path when flag given without value: {DEFAULT_CONFIG_PATH})")
    parser.add_argument("--api-url", help=f"Ollama base URL (default: {DEFAULT_OLLAMA_URL})")
    parser.add_argument("--base-url", help="OpenAI-compatible base URL, e.g. https://api.siliconflow.cn/v1")
    parser.add_argument("--api-key", help="API key; prefer the env var named by --api-key-env")
    parser.add_argument("--api-key-env", default=None,
                        help="env var holding the API key (default: VISION_API_KEY, or api_key_env from --config)")
    parser.add_argument("--image-detail", choices=("auto", "low", "high"), default="auto",
                        help="OpenAI-compatible image detail hint (default: auto)")

    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES,
                        help="retry transport failures (TLS resets, timeouts) this many times")
    parser.add_argument("--keep-alive", default=None, help="Ollama: e.g. 10m to keep the model warm")
    parser.add_argument("--max-image-mb", type=float, default=20.0)
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--check", action="store_true", help="report provider config and models, then exit")
    parser.add_argument("--dry-run", action="store_true", help="print the request plan without calling the model")
    parser.add_argument("--json-out", help="also write the result JSON to this path")
    return parser


def resolve_settings(args):
    """Merge CLI args, config file, and environment into one settings dict.

    A config file is used when --config names one, and when ~/.codex/qwen-local-vision.json
    exists it is picked up automatically so a saved provider keeps working without flags.
    """
    cfg_path = args.config
    if cfg_path is None and DEFAULT_CONFIG_PATH.is_file():
        cfg_path = str(DEFAULT_CONFIG_PATH)
    cfg = load_config(cfg_path) if cfg_path else {}
    provider = args.provider
    base_url = args.base_url or cfg.get("base_url") or os.environ.get("VISION_BASE_URL")
    key_env = (args.api_key_env or cfg.get("api_key_env")
               or os.environ.get("VISION_API_KEY_ENV") or "VISION_API_KEY")
    api_key = (args.api_key or os.environ.get(key_env) or user_env_var(key_env)
               or cfg.get("api_key"))

    if provider == "auto":
        provider = "openai" if (base_url or api_key) else "ollama"

    if provider == "ollama":
        model = args.model or cfg.get("model") or DEFAULT_OLLAMA_MODEL
    else:
        model = args.model or cfg.get("model") or DEFAULT_OPENAI_MODEL

    return {
        "provider": provider,
        "model": model,
        "ollama_url": args.api_url or cfg.get("ollama_url") or DEFAULT_OLLAMA_URL,
        "base_url": base_url,
        "api_key": api_key,
        "api_key_env": key_env,
        "chat_url": normalize_chat_url(base_url) if base_url else None,
        "detail": args.image_detail,
        "timeout": args.timeout,
        "retries": args.retries,
        "keep_alive": args.keep_alive,
        "num_ctx": args.num_ctx,
        "temperature": args.temperature,
        "config_path": cfg_path,
    }


def run_check(opener, args, settings):
    if settings["provider"] == "ollama":
        installed = list_ollama_models(opener, settings["ollama_url"])
        best = pick_model(installed)
        print(json.dumps({
            "ok": True,
            "provider": "ollama",
            "api_url": settings["ollama_url"],
            "installed_models": installed,
            "vision_model_detected": best,
            "requested_model": settings["model"],
            "hint": None if best else "ollama pull qwen3-vl:8b  (or minicpm-v:8b on older Ollama)",
        }, ensure_ascii=False, indent=2))
        return 0

    base = settings["base_url"]
    if not base:
        eprint("[error] openai provider needs --base-url (or VISION_BASE_URL in env / config).")
        return 2
    if not settings["api_key"]:
        eprint(f"[error] openai provider needs an API key in ${settings['api_key_env']}, --api-key, or config.")
        return 2
    url = base.rstrip("/") + "/models"
    try:
        data = request_json(opener, url, timeout=30.0, retries=settings["retries"],
                            headers={"Authorization": f"Bearer {settings['api_key']}"})
    except RuntimeError as exc:
        print(json.dumps({
            "ok": False, "provider": "openai", "base_url": base, "error": str(exc),
            "hint": "check the key, the base URL, and whether this host needs a proxy",
        }, ensure_ascii=False, indent=2))
        return 1
    ids = [m.get("id") for m in (data or {}).get("data", []) if isinstance(m, dict)]
    print(json.dumps({
        "ok": True,
        "provider": "openai",
        "base_url": base,
        "model": settings["model"],
        "model_listed": settings["model"] in ids if ids else None,
        "available_models_sample": ids[:25],
    }, ensure_ascii=False, indent=2))
    return 0


def call_ollama(opener, settings, prompt, images, encoded):
    payload = {
        "model": settings["model"],
        "messages": [{"role": "user", "content": prompt, "images": encoded}],
        "stream": False,
        "options": {"num_ctx": settings["num_ctx"], "temperature": settings["temperature"]},
    }
    if settings["keep_alive"]:
        payload["keep_alive"] = settings["keep_alive"]
    response = request_json(opener, f"{settings['ollama_url']}/api/chat", method="POST",
                            payload=payload, timeout=settings["timeout"],
                            retries=settings["retries"]) or {}
    message = response.get("message") or {}
    return (message.get("content") or "").strip(), response


def call_openai(opener, settings, prompt, images, encoded):
    content = [{"type": "text", "text": prompt}]
    for path in images:
        content.append({
            "type": "image_url",
            "image_url": {"url": data_url(path), "detail": settings["detail"]},
        })
    payload = {
        "model": settings["model"],
        "messages": [{"role": "user", "content": content}],
        "temperature": settings["temperature"],
    }
    response = request_json(opener, settings["chat_url"], method="POST", payload=payload,
                            timeout=settings["timeout"],
                            retries=settings["retries"],
                            headers={"Authorization": f"Bearer {settings['api_key']}"}) or {}
    choices = response.get("choices") or [{}]
    message = choices[0].get("message") or {}
    text = message.get("content")
    if isinstance(text, list):
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
    return (text or "").strip(), response


def main(argv=None):
    force_utf8_streams()
    args = build_parser().parse_args(argv)
    opener = make_opener()

    try:
        settings = resolve_settings(args)
    except RuntimeError as exc:
        eprint(f"[error] {exc}")
        return 2

    if args.check:
        return run_check(opener, args, settings)

    if settings["provider"] == "ollama":
        try:
            installed = list_ollama_models(opener, settings["ollama_url"])
        except RuntimeError as exc:
            eprint(f"[error] cannot reach Ollama at {settings['ollama_url']}: {exc}")
            eprint("[hint] start it with:  ollama serve   (or switch to --provider openai)")
            return 2
        if str(settings["model"]).strip().lower() == "auto":
            settings["model"] = pick_model(installed)
            if not settings["model"]:
                eprint("[error] no vision model found in Ollama. install one, e.g.:")
                eprint("         ollama pull qwen3-vl:8b        (best Chinese/OCR, needs a current Ollama)")
                eprint("         ollama pull minicpm-v:8b        (light fallback, works on older Ollama)")
                eprint("       or use a cloud API:  --provider openai --base-url <url>")
                return 3
        elif settings["model"] not in installed:
            eprint(f"[warn] '{settings['model']}' is not in the Ollama model list; trying anyway.")
    elif settings["provider"] == "openai":
        if not settings["base_url"]:
            eprint("[error] openai provider needs --base-url. e.g. https://api.siliconflow.cn/v1")
            return 2
        if not settings["api_key"]:
            eprint(f"[error] missing API key. set {settings['api_key_env']} or pass --api-key.")
            return 2

    image_args = list(args.images)
    if args.images_from_file:
        listing = read_text_source(args.images_from_file)
        image_args.extend(line.strip() for line in listing.splitlines() if line.strip())
    if not image_args:
        eprint("[error] no images given. pass one or more paths, or use --check.")
        return 2

    prompt = args.prompt
    if args.prompt_file:
        prompt = read_text_source(args.prompt_file)
    elif prompt == "-":
        prompt = read_text_source("-")
    if args.mode == "ask" and not prompt:
        eprint("[error] --mode ask needs a question: --prompt-file <file>, --prompt - (stdin), or --prompt \"...\"")
        return 2
    if not prompt:
        prompt = PROMPTS.get(args.mode, PROMPTS["describe"])

    try:
        images = resolve_images(image_args)
    except RuntimeError as exc:
        eprint(f"[error] {exc}")
        return 2

    big = [f"{p.name} ({p.stat().st_size / 1024 / 1024:.1f}MB)"
           for p in images if p.stat().st_size / 1024 / 1024 > args.max_image_mb]
    if big:
        eprint("[warn] large image(s): " + ", ".join(big) + " - the model may reject or slow down.")

    encoded = encode_images(images)

    if args.dry_run:
        print(json.dumps({
            "ok": True,
            "dry_run": True,
            "provider": settings["provider"],
            "model": settings["model"],
            "endpoint": settings["chat_url"] if settings["provider"] == "openai"
                        else f"{settings['ollama_url']}/api/chat",
            "mode": args.mode,
            "images": [{"path": str(p), "bytes": p.stat().st_size} for p in images],
            "base64_chars": [len(e) for e in encoded],
            "sends_image_off_machine": settings["provider"] == "openai",
            "prompt": prompt,
        }, ensure_ascii=False, indent=2))
        return 0

    endpoint = settings["chat_url"] if settings["provider"] == "openai" else f"{settings['ollama_url']}/api/chat"
    eprint(f"[info] provider={settings['provider']} model={settings['model']} images={len(images)} -> {endpoint}")

    try:
        if settings["provider"] == "ollama":
            text, response = call_ollama(opener, settings, prompt, images, encoded)
        else:
            text, response = call_openai(opener, settings, prompt, images, encoded)
    except RuntimeError as exc:
        eprint(f"[error] {exc}")
        if settings["provider"] == "ollama":
            eprint("[hint] if the model is missing run:  ollama pull " + str(settings["model"]))
            eprint("[hint] if it says 'does not support images', that model is text-only - pick a VL model.")
        else:
            eprint(f"[hint] verify {settings['api_key_env']} and that the model id exists for this endpoint.")
            eprint("[hint] use --check to list models the endpoint actually exposes.")
        return 1

    result = {
        "ok": bool(text),
        "provider": settings["provider"],
        "model": response.get("model", settings["model"]),
        "mode": args.mode,
        "images": [str(p) for p in images],
        "prompt": prompt,
        "text": text,
    }
    usage = response.get("usage")
    if usage:
        result["usage"] = usage
    if settings["provider"] == "ollama":
        result["stats"] = {
            "total_duration_s": round((response.get("total_duration") or 0) / 1e9, 3),
            "eval_count": response.get("eval_count"),
        }
    if not text:
        result["raw"] = response

    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.json_out:
        Path(args.json_out).write_text(rendered, encoding="utf-8")
    return 0 if text else 1


if __name__ == "__main__":
    sys.exit(main())