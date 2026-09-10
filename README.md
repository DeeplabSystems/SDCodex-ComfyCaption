# SDCodex-ComfyCaption

SDCodex Plugin for Image Captioning and ComfyUI Workflow Integration.

## Features
- **Captioning**: Auto-caption images using vision LLMs and JoyCaption.
- **ComfyUI Nodes**: Custom nodes (`comfyui-sdcodex`) to directly load and browse images in ComfyUI workflows.

> The gallery no longer lives here. SDCodex provides a separate
> **SDCodex Gallery** plugin that reads media, captions, SD prompts and
> ComfyUI workflows directly from disk and image metadata — no database and no
> "save to gallery" flow. The legacy `gallery` table/models, `/api/galleries`,
> `/api/gallery/save*` and the Saved Gallery UI were removed in v2.0.0.

## LM Studio URL resolution inside Docker
The plugin runs server-side inside the `sdcodex` container, so `http://localhost:1234/v1`
would point at the container itself, not the host where LM Studio runs. To fix this,
`api_gallery.py` rewrites any `localhost` / `127.0.0.1` LM Studio URL to the container's
default-gateway IP (the host) whenever the app is running inside Docker. No config change
is needed — the default URL just works.

The LM Studio API URL is also now **persisted** to browser `localStorage`
(`captionLmStudioUrl`) by the captioning page and shared with the gallery's
"Caption this Image" button, so it no longer resets to the localhost default on
every visit.

## Required Volumes (Docker)
- `COMFYUI_CUSTOM_NODES`: ComfyUI custom-addons folder (where `comfyui-sdcodex` nodes are installed).

## Installation
Add this repository (`DeeplabSystems/SDCodex-ComfyCaption`) in SDCodex Settings -> Plugins.