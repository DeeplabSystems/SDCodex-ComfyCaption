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

## ComfyUI Nodes (`comfyui-sdcodex`)
Installed into your ComfyUI `custom_nodes` folder under `comfyui-sdcodex/`:

- **SDCodex Gallery Browser** — folder-only image browser. Set `folder_path` and
  browse the grid; clicking an image (and the Runner) pushes the selection into
  every **SDCodex Gallery Loader** node in the graph. Reads captions from
  `<image>.txt` sidecars and SD prompts from image metadata, all from disk.
- **SDCodex Gallery Loader** — turns the current selection into `IMAGE` /
  `STRING` (`caption`, `sd_prompt`, `sd_negative`) outputs for your generation
  workflow.
- **SDCodex Gallery Runner** — batch/loop runner. Wire the Browser's
  `selected_image` into the Runner's `current_image` input, then press **▶ Start**:
  it runs the workflow on the currently selected image, advances to the next
  image in the folder, and keeps going until **■ Stop** or the limits below:
  - `executions_per_image` — how many times to run the workflow on each image.
  - `max_images` — how many images to cycle through before auto-stopping
    (`0` / blank = until the end of the list).

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