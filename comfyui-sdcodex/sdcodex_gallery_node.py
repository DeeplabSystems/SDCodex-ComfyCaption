import os
import sqlite3
import mimetypes
import json
from aiohttp import web
from server import PromptServer

# Determine SDCodex root directory default
current_dir = os.path.dirname(os.path.abspath(__file__))
# Check if parent is the SDCodex root (e.g. when run inside the workspace)
parent_dir = os.path.dirname(current_dir)
if os.path.exists(os.path.join(parent_dir, "app", "civitr.db")) or os.path.exists(os.path.join(parent_dir, "run.py")):
    default_sdcodex_root = parent_dir
else:
    default_sdcodex_root = "/home/naked/dev/SDCodex"

# Helper function to check safe paths
def is_safe_path(base, target):
    try:
        resolved_base = os.path.realpath(base)
        resolved_target = os.path.realpath(target)
        return resolved_target.startswith(resolved_base)
    except Exception:
        return False

# Register API Routes in ComfyUI
@PromptServer.instance.routes.get("/sdcodex/images")
async def get_sdcodex_images(request):
    params = request.query
    mode = params.get("mode", "Saved Gallery")
    folder_path = params.get("folder_path", "").strip()
    sdcodex_root = params.get("sdcodex_root", "").strip()
    page = int(params.get("page", 1))
    limit = int(params.get("limit", 24))
    
    if not sdcodex_root:
        sdcodex_root = default_sdcodex_root
        
    page = max(1, page)
    limit = max(1, limit)
    
    results = []
    total = 0
    total_pages = 1
    
    try:
        if mode == "Folder Path":
            if not folder_path:
                return web.json_response({"error": "Folder path is required in Folder Path mode"}, status=400)
            
            if not os.path.exists(folder_path) or not os.path.isdir(folder_path):
                return web.json_response({"error": f"Folder does not exist or is not a directory: {folder_path}"}, status=400)
                
            all_files = os.listdir(folder_path)
            image_files = []
            image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
            for file in all_files:
                ext = os.path.splitext(file)[1].lower()
                if ext in image_extensions:
                    image_files.append(file)
                    
            image_files.sort()
            total = len(image_files)
            total_pages = (total + limit - 1) // limit if total > 0 else 1
            
            start_index = (page - 1) * limit
            page_files = image_files[start_index : start_index + limit]
            
            for file in page_files:
                img_abs_path = os.path.join(folder_path, file)
                txt_path = os.path.join(folder_path, os.path.splitext(file)[0] + '.txt')
                
                caption = ""
                if os.path.exists(txt_path):
                    try:
                        with open(txt_path, 'r', encoding='utf-8') as f:
                            caption = f.read().strip()
                    except Exception:
                        pass
                
                results.append({
                    "file_name": file,
                    "image_path": img_abs_path,
                    "caption": caption,
                    "sd_prompt": "",
                    "sd_negative": ""
                })
        
        else: # Saved Gallery
            db_path = os.path.join(sdcodex_root, "app", "civitr.db")
            if not os.path.exists(db_path):
                db_path = os.path.join(sdcodex_root, "civitr.db")
                
            if not os.path.exists(db_path):
                return web.json_response({"error": f"SDCodex database not found at {db_path}"}, status=404)
                
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM gallery_image")
            total = cursor.fetchone()[0]
            total_pages = (total + limit - 1) // limit if total > 0 else 1
            
            offset = (page - 1) * limit
            cursor.execute(
                "SELECT id, file_name, image_path, caption, sd_prompt, sd_negative FROM gallery_image ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset)
            )
            rows = cursor.fetchall()
            conn.close()
            
            for row in rows:
                img_rel = row["image_path"]
                if img_rel.startswith("/static/"):
                    img_rel = img_rel[8:]
                elif img_rel.startswith("static/"):
                    img_rel = img_rel[7:]
                    
                img_abs_path = os.path.join(sdcodex_root, "app", "static", img_rel)
                
                results.append({
                    "id": row["id"],
                    "file_name": row["file_name"],
                    "image_path": img_abs_path,
                    "caption": row["caption"] or "",
                    "sd_prompt": row["sd_prompt"] or "",
                    "sd_negative": row["sd_negative"] or ""
                })
                
        return web.json_response({
            "images": results,
            "total": total,
            "page": page,
            "totalPages": total_pages,
            "limit": limit
        })
        
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

@PromptServer.instance.routes.get("/sdcodex/image")
async def get_sdcodex_image_content(request):
    params = request.query
    path = params.get("path", "").strip()
    if not path:
        return web.Response(status=400, text="Path parameter is required")
        
    if not os.path.exists(path):
        return web.Response(status=404, text="File not found")
        
    ext = os.path.splitext(path)[1].lower()
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
    if ext not in image_extensions:
        return web.Response(status=400, text="File requested is not a valid image format")
        
    try:
        with open(path, "rb") as f:
            content = f.read()
            
        mime, _ = mimetypes.guess_type(path)
        mime = mime or "image/jpeg"
        return web.Response(body=content, content_type=mime)
    except Exception as e:
        return web.Response(status=500, text=str(e))


class SDCodexGalleryNode:
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "mode": (["Saved Gallery", "Folder Path"], {"default": "Saved Gallery"}),
                "folder_path": ("STRING", {"default": ""}),
                "selected_image": ("STRING", {"default": ""}),
                "prefix": ("STRING", {"default": "", "multiline": True}),
                "caption": ("STRING", {"default": "", "multiline": True}),
                "suffix": ("STRING", {"default": "", "multiline": True}),
                "sdcodex_root": ("STRING", {"default": ""}),
            }
        }

    RETURN_TYPES = ("STRING", "IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("caption", "image", "sd_prompt", "sd_negative")
    FUNCTION = "execute"
    CATEGORY = "SDCodex"

    def execute(self, mode, folder_path, selected_image, prefix, caption, suffix, sdcodex_root=""):
        import torch
        import numpy as np
        from PIL import Image, ImageOps

        # Fallback empty outputs
        out_image = torch.zeros((1, 512, 512, 3), dtype=torch.float32)
        out_sd_prompt = ""
        out_sd_negative = ""

        # Determine actual file path
        img_path = None
        if selected_image:
            # If selected_image is an absolute path, use it directly
            if os.path.isabs(selected_image):
                img_path = selected_image
            else:
                if mode == "Folder Path" and folder_path:
                    img_path = os.path.join(folder_path, selected_image)

        # If we couldn't resolve the path directly and it's Saved Gallery mode
        if not img_path or not os.path.exists(img_path):
            if mode == "Saved Gallery":
                # Find SDCodex root
                root = sdcodex_root.strip() if sdcodex_root else default_sdcodex_root
                db_path = os.path.join(root, "app", "civitr.db")
                if not os.path.exists(db_path):
                    db_path = os.path.join(root, "civitr.db")
                
                if os.path.exists(db_path) and selected_image:
                    try:
                        conn = sqlite3.connect(db_path)
                        conn.row_factory = sqlite3.Row
                        cursor = conn.cursor()
                        # Query by file_name
                        cursor.execute(
                            "SELECT image_path, sd_prompt, sd_negative FROM gallery_image WHERE file_name = ? LIMIT 1",
                            (os.path.basename(selected_image),)
                        )
                        row = cursor.fetchone()
                        conn.close()
                        if row:
                            img_rel = row["image_path"]
                            if img_rel.startswith("/static/"):
                                img_rel = img_rel[8:]
                            elif img_rel.startswith("static/"):
                                img_rel = img_rel[7:]
                            img_path = os.path.join(root, "app", "static", img_rel)
                            out_sd_prompt = row["sd_prompt"] or ""
                            out_sd_negative = row["sd_negative"] or ""
                    except Exception as e:
                        print(f"Error loading image path from database: {e}")

        # Load image if file path is valid
        if img_path and os.path.exists(img_path):
            try:
                img = Image.open(img_path)
                img = ImageOps.exif_transpose(img)
                img_rgb = img.convert("RGB")
                
                # Convert to ComfyUI format tensor: shape (1, H, W, 3) normalized to [0, 1]
                np_img = np.array(img_rgb).astype(np.float32) / 255.0
                out_image = torch.from_numpy(np_img)[None,]

                # Try parsing SD prompt parameters from metadata if we don't have them yet (especially for Folder Path mode)
                if not out_sd_prompt:
                    # Look for positive/negative prompt in Exif or PNG text
                    if "workflow" not in img.info and "prompt" in img.info:
                        try:
                            # Try parsing A1111/Stable Diffusion metadata format
                            metadata = img.info["prompt"]
                            # Often it is a JSON or raw string. Let's check.
                            if isinstance(metadata, str):
                                # If it's standard A1111 metadata format, we can parse it
                                # Line 1: Positive Prompt
                                # Line 2 (Negative Prompt): Negative prompt: ...
                                # Line 3: Steps: ...
                                lines = [line.strip() for line in metadata.split("\n") if line.strip()]
                                if lines:
                                    out_sd_prompt = lines[0]
                                    for line in lines[1:]:
                                        if line.lower().startswith("negative prompt:"):
                                            out_sd_negative = line[16:].strip()
                                            break
                        except Exception:
                            pass
            except Exception as e:
                print(f"Error loading image file {img_path}: {e}")

        # Process prefix and suffix formatting
        final_caption = caption or ""
        if prefix and prefix.strip():
            p = prefix.strip()
            if final_caption:
                # Add a space between prefix and caption unless prefix ends with space/comma
                sep = " " if not (p.endswith(",") or p.endswith(" ")) else ""
                final_caption = f"{p}{sep}{final_caption}"
            else:
                final_caption = p

        if suffix and suffix.strip():
            s = suffix.strip()
            if final_caption:
                # Add a space between caption and suffix unless suffix starts with space/comma
                sep = " " if not (s.startswith(",") or s.startswith(" ")) else ""
                final_caption = f"{final_caption}{sep}{s}"
            else:
                final_caption = s

        return (final_caption, out_image, out_sd_prompt, out_sd_negative)
