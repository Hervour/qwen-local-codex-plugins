---
name: qwen-local-image
description: Generate or edit raster images locally with Qwen-Image 2.1 through ComfyUI, including text-to-image, reference-image editing, and Manga Drawer storyboard-to-page workflows. Use when the user asks for a local Qwen image, wants to transform a source image, or wants a manga page generated from a storyboard. Do not use for diagrams, screenshots, or vector/code-native artwork.
---

# Local Qwen Image

Use the user's local Qwen-Image 2.1 Uncensored model and ComfyUI. Keep generation on this computer; do not silently substitute a cloud image model when the user asks for local Qwen.

Resolve `<skill-root>` as the directory containing this `SKILL.md`. The script auto-starts the known ComfyUI installation with `--lowvram` when port `8188` is offline.

## Text to image

```powershell
python "<skill-root>\scripts\qwen_local_image.py" --mode text2image --prompt "<English prompt>" --width 1024 --height 1024 --steps 25 --output-dir "<current workspace>\qwen-images"
```

Use this for new images, illustrations, avatars, posters, and concept art.

## Reference image edit

```powershell
python "<skill-root>\scripts\qwen_local_image.py" --mode edit --input-image "<absolute image path>" --prompt "<edit instruction>" --width 1024 --height 1024 --steps 25 --output-dir "<current workspace>\qwen-images"
```

Use this when the user supplies an image and asks to transform, restyle, replace the background, or preserve a subject while changing other details. The source image is uploaded only to local ComfyUI.

## Manga Drawer

Create a neutral storyboard JSON in the workspace from the user's request, using the schema in `references/storyboard.md`, then run:

```powershell
python "<skill-root>\scripts\qwen_local_image.py" --mode manga --storyboard "<absolute storyboard path>" --width 768 --height 768 --steps 12 --output-dir "<current workspace>\qwen-images"
```

The Manga Drawer workflow reads each panel, generates panels sequentially, draws dialogue bubbles, and composes the final page. `MangaStoryboardLoader` supports per-panel seeds and optional frame/reference images.

Use only the storyboard path explicitly supplied by the user or one created for the current request. Do not scan or reuse unrelated storyboard files.

## Prompting and delivery

- Translate the user's idea into a concrete English prompt while preserving identity, requested text, and explicit constraints.
- Defaults: `1024x1024`, `25` steps, `cfg 1`, sampler `euler`, scheduler `simple`, random seed for text-to-image and edit.
- Draft images: `768x768` with `12` steps. Manga panels default to `768x768` with `12` steps for drafts.
- Width and height must be multiples of 32.
- Parse the final JSON printed to stdout; do not infer success from ComfyUI queue messages.
- Show every generated image using an absolute Markdown path, and report absolute paths and the seed used.

## Failure handling

- If ComfyUI returns CUDA out-of-memory, retry once at `768x768`; for edit mode, also try `--cache-dtype int4`.
- If another process occupies port `8188`, do not kill it; report the conflict.
- If the ComfyUI startup or generation fails, report the JSON error and the server log path. The first cold run can take several minutes while the model loads.
- The canonical UI workflow copies are under `workflows/`; the runtime script builds equivalent API prompts.
