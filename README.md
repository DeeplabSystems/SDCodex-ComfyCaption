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

## Required Volumes (Docker)
- `COMFYUI_CUSTOM_NODES`: ComfyUI custom-addons folder (where `comfyui-sdcodex` nodes are installed).

## Installation
Add this repository (`DeeplabSystems/SDCodex-ComfyCaption`) in SDCodex Settings -> Plugins.