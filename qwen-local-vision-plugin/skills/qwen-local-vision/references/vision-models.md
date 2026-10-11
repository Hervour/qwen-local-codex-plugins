# Choosing a local vision model (Ollama)

Verified on this machine on 2026-10-07: Ollama **0.5.7**, RTX 4060 Laptop **8 GB VRAM**, ~16 GB RAM, GPU driver 566.07.
Disk sizes below are the Q4-ish default tags and are approximate.

## Quick pick

| Priority | Ollama tag | Size | Fits 8 GB VRAM | Chinese | Min Ollama |
| --- | --- | --- | --- | --- | --- |
| Best overall | `qwen3-vl:8b` | ~6 GB | yes, tight | excellent | ~0.12+ |
| Safe second | `qwen2.5-vl:7b` | ~6 GB | yes, tight | very good | ~0.6+ |
| Works on old builds | `minicpm-v:8b` | ~5.5 GB | yes | good | 0.5.x |
| Fast / low RAM | `moondream:v2` | ~1.7 GB | easily | weak (English-first) | 0.5.x |
| Legacy | `llava:7b` | ~4.7 GB | yes | poor | any |
| Heavy | `llama3.2-vision:11b` | ~7.8 GB | no, spills to RAM | poor | 0.4+ |
| Newer alternative | `gemma3:4b` / `gemma3:12b` | ~3.3 / ~8 GB | 4b yes | fair | ~0.6+ |

## Why Qwen3-VL first

- Strongest Chinese and mixed Chinese/English OCR of the local options, which matters for screenshots, chat logs, and manga text.
- Handles grounding ("where is the button"), charts, documents, UI screenshots, and handwriting better than the llava-era models.
- 8B Q4 is the practical ceiling for 8 GB VRAM; it fits, but expect the first token to take a while because the weights load once per `keep_alive` window.

## When to prefer something else

- **Old Ollama build.** `qwen2.5-vl` and `qwen3-vl` must be served by a recent Ollama. On 0.5.x the working choices are `minicpm-v:8b`, `moondream:v2`, or `llava:7b`. Upgrading Ollama is the better fix.
- **Speed over accuracy.** `moondream:v2` answers in a second or two, useful for "just tell me what this is".
- **Long documents or multi-page screenshots.** `minicpm-v:8b` handles high resolution tiles well and is lighter than Qwen3-VL.

## Cloud options (only if the user explicitly wants non-local)

`qwen-vl-max` (DashScope), `glm-4v` (Zhipu), Gemini 2.5 Flash, and GPT-4o/4.1 vision are all stronger than an 8B local model, and none of them need VRAM. They send the image off the machine, which contradicts the local-only premise of this plugin - ask before using one.

## Operational notes

- Loading an 8B Q4 vision model takes 3-10 GB of VRAM plus KV cache. With ComfyUI already holding VRAM, expect swapping; stop ComfyUI or lower `--num-ctx` first.
- Keep `--num-ctx` at 8192 or below on 16 GB RAM. A large context plus an 8B model is what usually triggers OOM.
- `ollama ps` shows what is currently resident; `ollama stop <model>` frees VRAM.
- Images are sent to `127.0.0.1` only, and the script bypasses the system HTTP proxy so a proxy setting cannot break loopback.
---

# Cloud / API vision endpoints (OpenAI-compatible)

Use these when the user asks for API vision or no local model is available. All of them speak the same `/chat/completions` shape, so the only changes are `--base-url`, `--model`, and the API key. Verify ids in the provider console before relying on them.

| Provider | Base URL (`--base-url`) | Example vision model | Typical key env var |
| --- | --- | --- | --- |
| Alibaba Bailian / DashScope | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-vl-max`, `qwen-vl-plus` | `DASHSCOPE_API_KEY` |
| Zhipu AI | `https://open.bigmodel.cn/api/paas/v4` | `glm-4v-plus` | `ZHIPUAI_API_KEY` |
| SiliconFlow | `https://api.siliconflow.cn/v1` | `Qwen/Qwen2.5-VL-72B-Instruct` | `SILICONFLOW_API_KEY` |
| Volcengine Ark | `https://ark.cn-beijing.volces.com/api/v3` | an `ep-...` endpoint id | `ARK_API_KEY` |
| Moonshot (Kimi) | `https://api.moonshot.cn/v1` | `moonshot-v1-32k-vision-preview` | `MOONSHOT_API_KEY` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o`, `gpt-4.1-mini` | `OPENAI_API_KEY` |
| Google Gemini (OpenAI compat) | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-2.5-flash` | `GEMINI_API_KEY` |

## Notes

- **Which one to pick.** For Chinese OCR and chat screenshots, `qwen-vl-max` (DashScope) is the strongest and simplest from mainland China; `glm-4v-plus` is a close second. OpenAI and Gemini usually need a proxy from this machine.
- **Cost shape.** Images are billed as input tokens, typically a low thousands of tokens per image depending on resolution. Set `--image-detail low` to cut cost when fine detail is not needed.
- **Privacy.** Everything except the local Ollama path uploads the image to a third party. Check with the user before switching, and say so in the answer when an API provider was used.
- **Keys.** Keep keys in an environment variable and reference them with `api_key_env` in the config file. Never write a key into this plugin, the workspace, or a final answer.
- **Proxy.** The script bypasses the system proxy for loopback only; remote endpoints still use whatever `HTTP_PROXY`/`HTTPS_PROXY` is set, so a working Clash/V2Ray setup is enough for OpenAI/Gemini.
