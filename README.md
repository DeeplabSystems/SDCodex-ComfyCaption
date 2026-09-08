# SDCodex-ComfyCaption

SDCodex Plugin for Image Captioning, Saved Galleries, and ComfyUI Workflow Integration.

## Features
- **Gallery**: Manage saved galleries, browse image directories, view generation parameters and workflows.
- **Captioning**: Auto-caption images using vision LLMs and JoyCaption.
- **ComfyUI Nodes**: Custom nodes (`comfyui-sdcodex`) to directly load and browse SDCodex galleries in ComfyUI workflows.

## Required Volumes (Docker)
- `GALLERY`: Host path for saved gallery images (defaults to `./app/static/saved_gallery`).

## Installation
Add this repository (`DeeplabSystems/SDCodex-ComfyCaption`) in SDCodex Settings -> Plugins.
