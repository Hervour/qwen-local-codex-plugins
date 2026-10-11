---
name: qwen-local-vision
description: Read and understand local image files (screenshots, photos, chat attachments, generated artwork) with a local vision-language model served by Ollama, or with an OpenAI-compatible vision API when the user explicitly asks for API/cloud vision or has configured one. Use when the user points at an image file path, asks what is in an image, wants OCR of a local screenshot, or wants an image turned into a description or generation prompt. Never substitute a text-only model.
---

# Local / API Vision

Give Codex eyes on **image files** (QQ/WeChat attachments, screenshots, download folders, previous generation output) rather than images attached to the chat.

Two providers, one interface:

- `ollama` - local vision model at `127.0.0.1:11434`. **Default.** Images never leave the machine.
- `openai` - any OpenAI-compatible `/chat/completions` endpoint (Qwen-VL, GLM-4V, Doubao, Kimi, GPT-4o, Gemini-compatible). Images are uploaded to that endpoint, so use it only when the user asks for API vision or has already configured it.

Resolve `<skill-root>` as the directory containing this `SKILL.md`.

## Step 0 - check what is configured

```powershell
python "<skill-root>\scripts\vision_describe.py" --check
```

A saved config is picked up automatically from `~/.codex/qwen-local-vision.json`; pass `--config <path>` to use another file. Without any config this checks local Ollama. When an API provider is configured, or `--provider openai` / `VISION_BASE_URL`+`VISION_API_KEY` is given, it validates the key against `GET /models` instead.

## Describe an image

```powershell
python "<skill-root>\scripts\vision_describe.py" "<absolute image path>" --model auto --mode describe
```

## OCR - extract text verbatim

```powershell
python "<skill-root>\scripts\vision_describe.py" "<absolute image path>" --model auto --mode ocr
```

## Ask a specific question

Windows PowerShell 5.1 corrupts non-ASCII arguments, so never pass a Chinese question inline. Write it to a UTF-8 file, or pipe it through stdin:

```powershell
python "<skill-root>\scripts\vision_describe.py" "<absolute image path>" --model auto --mode ask --prompt-file "<question file>"
```

```powershell
"<question>" | python "<skill-root>\scripts\vision_describe.py" "<absolute image path>" --model auto --mode ask --prompt -
```

Multiple images are allowed: pass several paths, or `--images-from-file <utf8 list>` for one path per line (use this when paths contain non-ASCII characters).

## When the image only exists on the clipboard

The chat window may refuse image attachments because the active Codex model is text-only, even though vision works through this skill. If the user says they copied a screenshot, or that pasting the image failed, pull it off the clipboard first:

```powershell
powershell -NoProfile -STA -ExecutionPolicy Bypass -File "<skill-root>\scripts\save-clipboard-image.ps1"
```

It prints a single JSON line. When `source` is `file-drop`, the user copied a file in Explorer and `path` is that file. Otherwise the clipboard bitmap was written to a PNG in `%TEMP%` and `path` points at it. Hand that path directly to `vision_describe.py`. Exit code 2 with `ok: false` means the clipboard held text rather than an image.

The script re-launches itself under an STA thread when needed, so always call it exactly as shown.

## Reading the result

- One JSON object is printed on stdout. Read `text` for the answer; `ok: false` or a non-zero exit means failure.
- `provider`, `model`, `images`, and `prompt` echo what was actually sent.
- `--dry-run` validates paths, sizes, and the prompt without calling any model; the `sends_image_off_machine` field states whether the image would leave the machine.
- `--json-out <path>` also writes the JSON to a file; `--keep-alive 10m` keeps a local model resident for consecutive images.

## Using a cloud / API vision model

Local Ollama first. Only switch to an API when the user asks for it, or when no local vision model is installed and the user accepts sending the image out.

```powershell
$env:VISION_API_KEY = "<key>"
python "<skill-root>\scripts\vision_describe.py" "<image>" --provider openai --base-url "https://dashscope.aliyuncs.com/compatible-mode/v1" --model "qwen-vl-max"
```

Anything OpenAI-compatible works: set `--base-url` (the script appends `/chat/completions`) plus the key. `--model` must be a vision-capable model id for that endpoint.

To make it the default, write `~/.codex/qwen-local-vision.json` - it is loaded automatically whenever `--config` is not given:

```json
{
  "provider": "openai",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "model": "qwen-vl-max",
  "api_key_env": "DASHSCOPE_API_KEY"
}
```

Prefer `api_key_env` over an inline `api_key`, so the secret stays in the environment and out of any file that might be committed or archived. Provider base URLs, model ids, and price notes are in `references/vision-models.md`.

Resolution order: `--provider/--base-url/--model/--api-key` > config file (`--config`, else `~/.codex/qwen-local-vision.json` if it exists) > environment (`VISION_BASE_URL`, `VISION_API_KEY`, `VISION_MODEL`) > local Ollama defaults. Keys are read from the env var named by `api_key_env`. `--provider auto` (the default) picks `openai` only when a base URL or API key is configured, otherwise `ollama`.

## Installing a local model

```powershell
ollama pull qwen3-vl:8b
```

Recommended order for this machine (RTX 4060 Laptop, 8 GB VRAM, 16 GB RAM): `qwen3-vl:8b` > `qwen2.5-vl:7b` > `minicpm-v:8b` > `moondream`. Check the Ollama version first: `qwen3-vl`/`qwen2.5-vl` need a recent build, while `minicpm-v:8b` works on the older 0.5.x series.

## Failure handling

- `cannot reach Ollama` - start `ollama serve`, pass `--api-url`, or switch to `--provider openai`.
- `HTTP 401` / `403` on an API - wrong key, expired key, or the key was not loaded into the shell that ran the script.
- `does not support images` / model refuses the image - the model is text-only. Re-run with `--model auto`, or pick a VL model id for that endpoint.
- `model not found` - run `--check` to list what the endpoint actually exposes.
- Out of memory or very slow first local answer - lower `--num-ctx 4096`, stop ComfyUI, or use a smaller/API model.
- A `[warn] ... is not in the Ollama model list` line is not fatal; the request is still attempted.
- Intermittent TLS drops (for example `SSL: UNEXPECTED_EOF_WHILE_READING`) are retried automatically. Adjust with `--retries N` (default 3); a real HTTP response such as 401 is never retried.