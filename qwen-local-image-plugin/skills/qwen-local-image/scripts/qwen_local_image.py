#!/usr/bin/env python3
"""Run the local Qwen-Image 2.1 ComfyUI workflows."""

from __future__ import annotations

import argparse
import json
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib import error, parse, request

DEFAULT_COMFY_ROOT = Path(
    r"C:\Users\LHW66\Documents\Codex\2026-09-23\abenzerps-qwen-image-2-1-uncensored\outputs\ComfyUI"
)
DEFAULT_COMFY_PYTHON = DEFAULT_COMFY_ROOT.parent / ".venv" / "Scripts" / "python.exe"
DEFAULT_API_URL = "http://127.0.0.1:8188"
DEFAULT_UNET = "qwen-image-2.1-UC-Q4_K_M.gguf"
DEFAULT_CLIP = "qwen3vl_8b_int8_convrot.safetensors"
DEFAULT_VAE = "qwen_image_2.1_vae_bf16.safetensors"
MODES = ("text2image", "edit", "manga")


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr, flush=True)


def make_opener():
    # Loopback must not be routed through a system proxy.
    return request.build_opener(request.ProxyHandler({}))


def request_json(opener, url: str, *, method: str = "GET", payload=None, timeout: float = 20.0):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=timeout) as response:
            raw = response.read()
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body[:4000]}") from exc
    except Exception as exc:
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from {url}: {raw[:1000]!r}") from exc


def server_status(opener, api_url: str):
    try:
        return request_json(opener, f"{api_url}/system_stats", timeout=2.0)
    except RuntimeError:
        return None


def port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def tail_file(path: Path, limit: int = 8000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:]


def start_comfyui(args, api_url: str):
    root = Path(args.comfy_root)
    python_exe = Path(args.comfy_python)
    if not root.is_dir():
        raise RuntimeError(f"ComfyUI directory does not exist: {root}")
    if not python_exe.is_file():
        raise RuntimeError(f"ComfyUI Python does not exist: {python_exe}")

    parsed = parse.urlparse(api_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8188
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Automatic startup is only supported for a local ComfyUI server")
    if port_is_open(host, port):
        raise RuntimeError(
            f"Port {port} is already in use, but it is not the expected ComfyUI API. "
            "No process was stopped."
        )

    log_path = Path(tempfile.gettempdir()) / "qwen-local-image-comfyui.log"
    command = [
        str(python_exe),
        "main.py",
        "--disable-dynamic-vram",
        "--lowvram",
        "--disable-smart-memory",
        "--cache-none",
        "--listen",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )

    eprint(f"Starting ComfyUI: {python_exe} main.py --lowvram")
    with log_path.open("ab") as log_handle:
        process = subprocess.Popen(
            command,
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            close_fds=True,
        )

    deadline = time.monotonic() + args.server_start_timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"ComfyUI exited during startup with code {process.returncode}.\n"
                f"Log: {log_path}\n{tail_file(log_path)}"
            )
        if server_status(make_opener(), api_url) is not None:
            eprint(f"ComfyUI is ready (PID {process.pid})")
            return process, log_path
        time.sleep(2.0)

    raise RuntimeError(
        f"ComfyUI did not become ready within {args.server_start_timeout:.0f}s.\n"
        f"Log: {log_path}\n{tail_file(log_path)}"
    )


def validate_dimensions(width: int, height: int) -> None:
    if width < 256 or height < 256:
        raise RuntimeError("Width and height must both be at least 256")
    if width % 32 or height % 32:
        raise RuntimeError("Width and height must be multiples of 32")


def sanitize_prefix(value: str) -> str:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    cleaned = "".join(ch if ch in allowed else "_" for ch in value).strip("._")
    return cleaned or "Qwen-Image-2.1-Uncensored"


def base_nodes(args):
    return {
        "1": {
            "class_type": "UnetLoaderGGUF",
            "inputs": {"unet_name": args.unet_name},
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": args.clip_name,
                "type": "qwen_image",
                "device": args.clip_device,
            },
        },
        "3": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": args.vae_name},
        },
    }


def build_text2image_workflow(args, seed: int, prefix: str):
    workflow = base_nodes(args)
    resolution = max(args.width, args.height)
    workflow.update(
        {
            "4": {
                "class_type": "TextEncodeQwenImage21",
                "inputs": {
                    "clip": ["2", 0],
                    "prompt": args.prompt,
                    "negative_prompt": args.negative_prompt,
                    "resolution": resolution,
                },
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {
                    "width": args.width,
                    "height": args.height,
                    "batch_size": 1,
                },
            },
            "6": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["1", 0],
                    "positive": ["4", 0],
                    "negative": ["4", 1],
                    "latent_image": ["5", 0],
                    "seed": seed,
                    "steps": args.steps,
                    "cfg": args.cfg,
                    "sampler_name": args.sampler,
                    "scheduler": args.scheduler,
                    "denoise": 1.0,
                },
            },
            "7": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["6", 0], "vae": ["3", 0]},
            },
            "8": {
                "class_type": "SaveImage",
                "inputs": {"images": ["7", 0], "filename_prefix": prefix},
            },
        }
    )
    return workflow


def build_edit_workflow(args, seed: int, prefix: str, uploaded_name: str):
    workflow = base_nodes(args)
    resolution = max(args.width, args.height)
    workflow.update(
        {
            "4": {
                "class_type": "LoadImage",
                "inputs": {"image": uploaded_name},
            },
            "5": {
                "class_type": "TextEncodeQwenImage21",
                "inputs": {
                    "clip": ["2", 0],
                    "images.image_1": ["4", 0],
                    "vae": ["3", 0],
                    "prompt": args.prompt,
                    "negative_prompt": args.negative_prompt,
                    "resolution": resolution,
                },
            },
            "6": {
                "class_type": "QwenImage21Cache",
                "inputs": {
                    "model": ["1", 0],
                    "device": args.cache_device,
                    "dtype": args.cache_dtype,
                },
            },
            "7": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["6", 0],
                    "positive": ["5", 0],
                    "negative": ["5", 1],
                    "latent_image": ["5", 2],
                    "seed": seed,
                    "steps": args.steps,
                    "cfg": args.cfg,
                    "sampler_name": args.sampler,
                    "scheduler": args.scheduler,
                    "denoise": 1.0,
                },
            },
            "8": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["7", 0], "vae": ["3", 0]},
            },
            "9": {
                "class_type": "SaveImage",
                "inputs": {"images": ["8", 0], "filename_prefix": prefix},
            },
        }
    )
    return workflow


def build_manga_workflow(args, prefix: str):
    workflow = base_nodes(args)
    workflow.update(
        {
            "1": {
                "class_type": "MangaStoryboardLoader",
                "inputs": {
                    "storyboard_path": args.storyboard,
                    "output_dir": prefix,
                    "default_width": args.width,
                    "default_height": args.height,
                },
            },
            "2": {
                "class_type": "MangaImageLoader",
                "inputs": {"image_path": ["1", 4]},
            },
            "3": {
                "class_type": "MangaImageLoader",
                "inputs": {"image_path": ["1", 5]},
            },
            "4": {
                "class_type": "UnetLoaderGGUF",
                "inputs": {"unet_name": args.unet_name},
            },
            "5": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": args.clip_name,
                    "type": "qwen_image",
                    "device": args.clip_device,
                },
            },
            "6": {
                "class_type": "VAELoader",
                "inputs": {"vae_name": args.vae_name},
            },
            "7": {
                "class_type": "TextEncodeQwenImage21",
                "inputs": {
                    "clip": ["5", 0],
                    "images.image_1": ["2", 0],
                    "images.image_2": ["3", 0],
                    "vae": ["6", 0],
                    "prompt": ["1", 0],
                    "negative_prompt": ["1", 1],
                    "resolution": ["1", 3],
                },
            },
            "8": {
                "class_type": "QwenImage21Cache",
                "inputs": {
                    "model": ["4", 0],
                    "device": args.cache_device,
                    "dtype": args.cache_dtype,
                },
            },
            "9": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["8", 0],
                    "positive": ["7", 0],
                    "negative": ["7", 1],
                    "latent_image": ["7", 2],
                    "seed": ["1", 2],
                    "steps": args.steps,
                    "cfg": args.cfg,
                    "sampler_name": args.sampler,
                    "scheduler": args.scheduler,
                    "denoise": 1.0,
                },
            },
            "10": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["9", 0], "vae": ["6", 0]},
            },
            "11": {
                "class_type": "MangaPageComposer",
                "inputs": {
                    "image": ["10", 0],
                    "panel_index": ["1", 7],
                    "panel_id": ["1", 6],
                    "dialogue_json": ["1", 9],
                    "total_panels": ["1", 8],
                    "output_dir": prefix,
                    "rows": args.manga_rows,
                    "cols": args.manga_cols,
                    "gutter": args.manga_gutter,
                    "margin": args.manga_margin,
                    "page_width": args.manga_page_width,
                    "add_bubbles": not args.no_bubbles,
                },
            },
        }
    )
    return workflow


def upload_image(opener, api_url: str, image_path: Path):
    if not image_path.is_file():
        raise RuntimeError(f"Input image does not exist: {image_path}")
    boundary = "----qwen-local-image-" + uuid.uuid4().hex
    upload_name = f"qwen_input_{uuid.uuid4().hex[:12]}{image_path.suffix.lower() or '.png'}"
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{upload_name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = header + image_path.read_bytes() + footer
    req = request.Request(
        f"{api_url}/upload/image",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with opener.open(req, timeout=120.0) as response:
            result = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Image upload failed with HTTP {exc.code}: {body_text[:4000]}") from exc
    except Exception as exc:
        raise RuntimeError(f"Image upload failed: {exc}") from exc
    name = str(result.get("name", "")).strip()
    if not name:
        raise RuntimeError(f"ComfyUI upload returned no filename: {result}")
    subfolder = str(result.get("subfolder", "")).strip()
    return f"{subfolder}/{name}" if subfolder else name


def queue_prompt(opener, api_url: str, workflow, client_id: str):
    response = request_json(
        opener,
        f"{api_url}/prompt",
        method="POST",
        payload={"prompt": workflow, "client_id": client_id},
    )
    if not isinstance(response, dict) or not response.get("prompt_id"):
        raise RuntimeError(f"ComfyUI rejected the workflow: {response}")
    return str(response["prompt_id"])


def outputs_have_images(outputs) -> bool:
    if not isinstance(outputs, dict):
        return False
    for node_output in outputs.values():
        if isinstance(node_output, dict) and node_output.get("images"):
            return True
    return False


def wait_for_history(opener, api_url: str, prompt_id: str, timeout: float):
    deadline = time.monotonic() + timeout
    last_notice = 0.0
    while time.monotonic() < deadline:
        history = request_json(opener, f"{api_url}/history/{prompt_id}", timeout=20.0)
        entry = history.get(prompt_id) if isinstance(history, dict) else None
        if entry:
            status = entry.get("status") or {}
            status_str = str(status.get("status_str", "")).lower()
            if status_str in {"error", "failed"}:
                raise RuntimeError(f"ComfyUI generation failed: {json.dumps(status, ensure_ascii=False)}")
            outputs = entry.get("outputs") or {}
            if outputs_have_images(outputs):
                return outputs
            if status_str in {"success", "completed"}:
                raise RuntimeError(
                    "ComfyUI completed but returned no downloadable images: "
                    f"{json.dumps(outputs, ensure_ascii=False)}"
                )
        now = time.monotonic()
        if now - last_notice >= 15.0:
            eprint(f"Waiting for ComfyUI prompt {prompt_id}...")
            last_notice = now
        time.sleep(2.0)
    raise RuntimeError(f"Generation timed out after {timeout:.0f}s")


def download_outputs(opener, api_url: str, outputs, output_dir: Path, prompt_id: str):
    output_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for node_output in outputs.values():
        for image in node_output.get("images", []):
            filename = str(image.get("filename", "")).strip()
            if not filename:
                continue
            subfolder = str(image.get("subfolder", ""))
            image_type = str(image.get("type", "output"))
            query = parse.urlencode(
                {"filename": filename, "subfolder": subfolder, "type": image_type}
            )
            url = f"{api_url}/view?{query}"
            try:
                with opener.open(url, timeout=120.0) as response:
                    content = response.read()
            except Exception as exc:
                raise RuntimeError(f"Could not download generated image {filename}: {exc}") from exc

            safe_name = Path(filename).name
            destination = output_dir / safe_name
            if destination.exists():
                destination = output_dir / f"{prompt_id[:8]}_{safe_name}"
            destination.write_bytes(content)
            saved.append(str(destination.resolve()))
    if not saved:
        raise RuntimeError("ComfyUI completed but returned no image outputs")
    return saved


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=MODES, default="text2image")
    parser.add_argument("--prompt", help="Prompt for text-to-image or image editing")
    parser.add_argument("--negative-prompt", default="", help="Negative prompt for text-to-image or editing")
    parser.add_argument("--input-image", help="Source image for --mode edit")
    parser.add_argument("--storyboard", help="Storyboard JSON for --mode manga")
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("--steps", type=int, default=None)
    parser.add_argument("--cfg", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=-1, help="-1 chooses a random seed")
    parser.add_argument("--sampler", default="euler")
    parser.add_argument("--scheduler", default="simple")
    parser.add_argument("--clip-device", default="default", choices=["default", "cpu"])
    parser.add_argument("--cache-device", default="auto", choices=["auto", "gpu", "cpu", "off"])
    parser.add_argument("--cache-dtype", default=None, choices=["default", "int8", "int4"])
    parser.add_argument("--filename-prefix", default=None)
    parser.add_argument("--output-dir", default=str(Path.cwd() / "qwen-images"))
    parser.add_argument("--manga-rows", type=int, default=2)
    parser.add_argument("--manga-cols", type=int, default=2)
    parser.add_argument("--manga-gutter", type=int, default=20)
    parser.add_argument("--manga-margin", type=int, default=40)
    parser.add_argument("--manga-page-width", type=int, default=1600)
    parser.add_argument("--no-bubbles", action="store_true")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--comfy-root", default=str(DEFAULT_COMFY_ROOT))
    parser.add_argument("--comfy-python", default=str(DEFAULT_COMFY_PYTHON))
    parser.add_argument("--server-start-timeout", type=float, default=180.0)
    parser.add_argument("--timeout", type=float, default=1800.0)
    parser.add_argument("--no-auto-start", action="store_true")
    parser.add_argument("--start-only", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--unet-name", default=DEFAULT_UNET)
    parser.add_argument("--clip-name", default=DEFAULT_CLIP)
    parser.add_argument("--vae-name", default=DEFAULT_VAE)
    args = parser.parse_args()

    if args.mode == "manga":
        args.width = 768 if args.width is None else args.width
        args.height = 768 if args.height is None else args.height
        args.steps = 25 if args.steps is None else args.steps
        args.cache_dtype = "int8" if args.cache_dtype is None else args.cache_dtype
    else:
        args.width = 1024 if args.width is None else args.width
        args.height = 1024 if args.height is None else args.height
        args.steps = 25 if args.steps is None else args.steps
        args.cache_dtype = "default" if args.cache_dtype is None else args.cache_dtype
    return args


def main() -> int:
    args = parse_args()
    api_url = args.api_url.rstrip("/")
    opener = make_opener()

    if args.status:
        status = server_status(opener, api_url)
        if status is None:
            print(json.dumps({"status": "offline", "api_url": api_url}, ensure_ascii=False))
            return 1
        print(json.dumps({"status": "online", "api_url": api_url, "system": status}, ensure_ascii=False))
        return 0

    server_process = None
    server_log = None
    if server_status(opener, api_url) is None:
        if args.no_auto_start:
            raise RuntimeError(f"ComfyUI is offline at {api_url}")
        server_process, server_log = start_comfyui(args, api_url)
    else:
        eprint(f"Using running ComfyUI at {api_url}")

    if args.start_only:
        print(
            json.dumps(
                {"status": "ready", "api_url": api_url, "server_started": bool(server_process)},
                ensure_ascii=False,
            )
        )
        return 0

    validate_dimensions(args.width, args.height)
    if args.steps < 1:
        raise RuntimeError("--steps must be at least 1")

    seed = secrets.randbits(63) if args.seed < 0 else args.seed
    default_prefixes = {
        "text2image": "Qwen-Image-2.1-Uncensored",
        "edit": "Qwen-Image-2.1-Edit",
        "manga": "Qwen-Manga",
    }
    prefix = sanitize_prefix(args.filename_prefix or default_prefixes[args.mode])
    client_id = str(uuid.uuid4())
    started = time.monotonic()

    if args.mode == "text2image":
        if not args.prompt or not args.prompt.strip():
            raise RuntimeError("--prompt is required for --mode text2image")
        workflow = build_text2image_workflow(args, seed, prefix)
        seed_result = seed
    elif args.mode == "edit":
        if not args.prompt or not args.prompt.strip():
            raise RuntimeError("--prompt is required for --mode edit")
        if not args.input_image:
            raise RuntimeError("--input-image is required for --mode edit")
        uploaded_name = upload_image(opener, api_url, Path(args.input_image).expanduser().resolve())
        workflow = build_edit_workflow(args, seed, prefix, uploaded_name)
        seed_result = seed
    else:
        if not args.storyboard:
            raise RuntimeError("--storyboard is required for --mode manga")
        storyboard = Path(args.storyboard).expanduser().resolve()
        if not storyboard.is_file():
            raise RuntimeError(f"Storyboard does not exist: {storyboard}")
        args.storyboard = str(storyboard)
        workflow = build_manga_workflow(args, prefix)
        seed_result = "storyboard"

    prompt_id = queue_prompt(opener, api_url, workflow, client_id)
    eprint(f"Queued {args.mode} prompt {prompt_id} with seed {seed_result}")
    outputs = wait_for_history(opener, api_url, prompt_id, args.timeout)
    images = download_outputs(
        opener,
        api_url,
        outputs,
        Path(args.output_dir).expanduser().resolve(),
        prompt_id,
    )

    result = {
        "status": "ok",
        "mode": args.mode,
        "prompt_id": prompt_id,
        "seed": seed_result,
        "width": args.width,
        "height": args.height,
        "steps": args.steps,
        "cfg": args.cfg,
        "sampler": args.sampler,
        "scheduler": args.scheduler,
        "images": images,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "server_started": bool(server_process),
        "server_log": str(server_log) if server_log else None,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)

