from flask import Blueprint, jsonify, request, Response
import os
import subprocess
import base64
import json
import mimetypes
from PIL import Image
from io import BytesIO
from openai import OpenAI
import piexif
import piexif.helper
try:
    from sd_prompt_reader.image_data_reader import ImageDataReader
except ImportError:
    ImageDataReader = None

def extract_comfy_workflow(image_path):
    """Robustly extract ComfyUI workflow/prompt from PNG and WebP/JPEG images."""
    def extract_json_if_valid(val):
        if not isinstance(val, str):
            val = str(val)
        val = val.strip()
        if val.startswith('{') or val.startswith('['):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, (dict, list)):
                    return val
            except:
                pass
        return None

    try:
        with Image.open(image_path) as img:
            img.load()
            # Check PNG text chunks
            if "workflow" in img.info:
                val = extract_json_if_valid(img.info["workflow"])
                if val: return val
            if "prompt" in img.info:
                val = extract_json_if_valid(img.info["prompt"])
                if val: return val
            
            # Check Exif UserComment for WEBP / JPEG
            if "exif" in img.info:
                try:
                    exif_dict = piexif.load(img.info["exif"])
                    if "Exif" in exif_dict and piexif.ExifIFD.UserComment in exif_dict["Exif"]:
                        user_comment = exif_dict["Exif"][piexif.ExifIFD.UserComment]
                        try:
                            comment_str = piexif.helper.UserComment.load(user_comment)
                        except Exception:
                            if isinstance(user_comment, bytes):
                                comment_str = user_comment.decode("utf-8", errors="ignore")
                                if comment_str.startswith("ASCII\0\0\0"):
                                    comment_str = comment_str[8:]
                                elif comment_str.startswith("UNICODE\0"):
                                    comment_str = comment_str[8:]
                            else:
                                comment_str = str(user_comment)
                        
                        val = extract_json_if_valid(comment_str)
                        if val: return val
                except Exception as e:
                    pass
    except Exception as e:
        print(f"Error extracting workflow from {image_path}: {e}")
    return None

gallery = Blueprint("gallery", __name__)

@gallery.route("/api/browse", methods=["GET"])
def browse_folder():
    """API to browse folders using native filepickers.

    NOTE: this opens a dialog on the *server*. Inside Docker / headless
    servers there is no display (and no zenity/kdialog), so it fails — the
    frontend then falls back to the web-based /api/browse-folders browser,
    which works everywhere.
    """
    try:
        import platform
        import shutil

        if platform.system() == "Windows":
            cmd = ['powershell', '-command', 'Add-Type -AssemblyName System.Windows.Forms; $folderBrowser = New-Object System.Windows.Forms.FolderBrowserDialog; $null = $folderBrowser.ShowDialog(); $folderBrowser.SelectedPath']
        elif platform.system() == "Darwin":
            cmd = ['osascript', '-e', 'tell application "System Events" to return POSIX path of (choose folder)']
        else:
            if shutil.which("zenity"):
                cmd = ["zenity", "--file-selection", "--directory"]
            elif shutil.which("kdialog"):
                cmd = ["kdialog", "--getexistingdirectory"]
            else:
                return jsonify({
                    "error": "Native folder picker unavailable on the server "
                             "(expected inside Docker/headless). Use the folder "
                             "browser or type a container path like /data/downloads.",
                    "fallback": True,
                }), 500

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            folder_path = result.stdout.strip()
            if folder_path:
                return jsonify({"path": folder_path})

        return jsonify({"error": "No folder selected", "fallback": True}), 400
    except Exception as e:
        print(f"exec error: {e}")
        return jsonify({"error": f"Failed to open filepicker: {e}", "fallback": True}), 500


def _in_docker():
    return os.path.exists("/.dockerenv") or os.environ.get("REMBG_OUTPUT", "").startswith("/data")


@gallery.route("/api/browse-folders", methods=["GET"])
def browse_folders():
    """Web-based directory browser (works in Docker/headless).

    Query: ?path=/data/downloads → {cwd, parent, dirs:[{name,path}], isDocker}
    Paths are container/server-side — the same paths /api/images expects.
    """
    req_path = request.args.get("path") or "/data"
    req_path = os.path.abspath(os.path.expanduser(req_path.strip('"\'')))
    if not os.path.exists(req_path):
        return jsonify({"error": f"Folder does not exist: {req_path}"}), 400
    if not os.path.isdir(req_path):
        req_path = os.path.dirname(req_path)
    try:
        entries = sorted(os.listdir(req_path))
    except PermissionError:
        return jsonify({"error": f"Permission denied: {req_path}"}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    dirs = []
    for name in entries:
        if name.startswith(".") and name not in (".", ".."):
            continue
        full = os.path.join(req_path, name)
        try:
            if os.path.isdir(full):
                dirs.append({"name": name, "path": full})
        except Exception:
            continue
        if len(dirs) >= 500:
            break
    parent = os.path.dirname(req_path.rstrip(os.sep)) or os.sep
    return jsonify({
        "cwd": req_path,
        "parent": parent,
        "dirs": dirs,
        "isDocker": _in_docker(),
    })


@gallery.route("/api/browse-roots", methods=["GET"])
def browse_roots():
    """Quick-pick roots for the folder browser (container-side paths)."""
    candidates = [
        "/data", "/data/downloads", "/data/tasks", "/data/config",
        os.path.expanduser("~"), "/app", "/",
    ]
    seen = []
    for p in candidates:
        try:
            ap = os.path.abspath(p)
            if ap not in seen and os.path.isdir(ap):
                seen.append(ap)
        except Exception:
            continue
    return jsonify({"roots": seen, "isDocker": _in_docker()})


@gallery.route("/api/check-connection", methods=["POST"])
def check_connection():
    """API to check connection to LLM Studio"""
    data = request.json or {}
    lm_studio_url = data.get("lmStudioUrl", "http://localhost:1234/v1")
    print(f"Checking connection to: {lm_studio_url}")

    try:
        # Attempt to list models via openai library
        client = OpenAI(api_key="not-needed", base_url=lm_studio_url, timeout=5.0)
        models = client.models.list()
        model_list = [{"id": m.id} for m in models.data]
        
        print(f"Connection successful via models.list(). Models found: {len(model_list)}")
        return jsonify({
            "connected": True, 
            "models": model_list
        })
    except Exception as e:
        import requests
        print(f"openai.models.list() failed for {lm_studio_url}: {e}. Trying direct probe...")
        try:
            # Fallback direct probe
            response = requests.get(lm_studio_url, timeout=5.0)
            print(f"Direct probe to {lm_studio_url} returned status: {response.status_code}")
            return jsonify({
                "connected": True, 
                "warning": "Server responded but models.list() failed"
            })
        except requests.exceptions.RequestException as fetch_err:
            print(f"All connection attempts failed for {lm_studio_url}: {fetch_err}")
            return jsonify({
                "connected": False, 
                "error": str(fetch_err)
            })

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}

@gallery.route("/api/images", methods=["GET"])
def get_images():
    """Optimized API to get images and existing captions"""
    folder_path = request.args.get("folderPath")
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 20))

    if not folder_path:
        return jsonify({"error": "Folder path is required"}), 400
        
    folder_path = folder_path.strip('"\'')
    page = max(1, page)
    limit = max(1, limit)

    if not os.path.exists(folder_path):
        return jsonify({"error": f"Folder does not exist: {folder_path}"}), 400
        
    if not os.path.isdir(folder_path):
        return jsonify({"error": "Path is not a directory"}), 400

    try:
        all_files = os.listdir(folder_path)
        
        # Filter image files
        image_files = []
        for file in all_files:
            ext = os.path.splitext(file)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                image_files.append(file)
                
        # Sort files to ensure stable pagination
        image_files.sort()

        total = len(image_files)
        # Compute proper ceiling
        total_pages = (total + limit - 1) // limit if total > 0 else 1
        
        start_index = (page - 1) * limit
        page_files = image_files[start_index : start_index + limit]

        results = []
        for file in page_files:
            image_path = os.path.join(folder_path, file)
            txt_path = os.path.join(folder_path, os.path.splitext(file)[0] + '.txt')
            
            caption = ""
            if os.path.exists(txt_path):
                try:
                    with open(txt_path, 'r', encoding='utf-8') as f:
                        caption = f.read()
                except Exception as e:
                    print(f"Could not read caption for {file}: {e}")
            
            sd_prompt = ""
            sd_negative = ""
            sd_setting = ""
            if ImageDataReader:
                try:
                    # Initialize ImageDataReader with the image path
                    reader = ImageDataReader(image_path)
                    # Use the raw string representing positive prompt if exists
                    if reader.positive:
                        sd_prompt = str(reader.positive).strip()
                    if reader.negative:
                        sd_negative = str(reader.negative).strip()
                    if reader.setting:
                        sd_setting = str(reader.setting).strip()
                except Exception as e:
                    print(f"Failed to read SD info for {file}: {e}")
            
            base64_img = ""
            has_workflow = extract_comfy_workflow(image_path) is not None
            try:
                with Image.open(image_path) as img:
                    img.load()
                
                with open(image_path, "rb") as image_file:
                    encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                    m, _ = mimetypes.guess_type(image_path)
                    m = m or 'image/jpeg'
                    base64_img = f"data:{m};base64,{encoded_string}"
            except Exception as e:
                print(f"Could not read image {file}: {e}")

            results.append({
                "file": file,
                "caption": caption,
                "sdPrompt": sd_prompt,
                "sdNegative": sd_negative,
                "sdSetting": sd_setting,
                "image": base64_img,
                "hasCaption": len(caption) > 0,
                "hasWorkflow": has_workflow
            })

        return jsonify({
            "images": results,
            "total": total,
            "page": page,
            "totalPages": total_pages,
            "limit": limit
        })
    except Exception as e:
        print(f"Error in /api/images: {e}")
        return jsonify({"error": str(e)}), 500

@gallery.route("/api/workflow", methods=["GET"])
def download_workflow():
    """API to download ComfyUI workflow JSON from image metadata"""
    folder_path = request.args.get("folderPath")
    file_name = request.args.get("file")

    if not folder_path or not file_name:
        return jsonify({"error": "Folder path and file name are required"}), 400
        
    folder_path = folder_path.strip('"\'')
    image_path = os.path.join(folder_path, file_name)

    if not is_safe_path(folder_path, image_path):
        return jsonify({"error": "Invalid file name"}), 400

    if not os.path.exists(image_path):
        return jsonify({"error": "Image file does not exist"}), 400

    try:
        workflow_data = extract_comfy_workflow(image_path)
            
        if not workflow_data:
            return jsonify({"error": "No ComfyUI workflow found in image metadata"}), 404

        # Ensure workflow_data is valid JSON format for the file instead of a plain string
        try:
            parsed_json = json.loads(workflow_data)
            formatted_data = json.dumps(parsed_json, indent=2)
        except json.JSONDecodeError:
            # Fallback to raw string if it's not valid JSON
            formatted_data = workflow_data

        return Response(
            formatted_data,
            mimetype="application/json",
            headers={
                "Content-disposition": f"attachment; filename={os.path.splitext(file_name)[0]}_workflow.json"
            }
        )
    except Exception as e:
        print(f"Error in /api/workflow: {e}")
        return jsonify({"error": str(e)}), 500

def is_safe_path(base, target):
    """Basic path traversal protection"""
    resolved_base = os.path.abspath(base)
    resolved_target = os.path.abspath(target)
    return resolved_target.startswith(resolved_base)

def process_image_for_llm(image_path):
    """Converts image to JPEG and resizes to 1024 max dimension for compatibility"""
    try:
        with Image.open(image_path) as img:
            # Convert to RGB if necessary (e.g., for PNG with alpha)
            if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                bg.paste(img, mask=img.split()[3])
                img = bg
            elif img.mode != 'RGB':
                img = img.convert('RGB')
                
            # Resize logic (fit inside 1024x1024 without enlargement)
            img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            
            # Save to BytesIO
            buffered = BytesIO()
            img.save(buffered, format="JPEG")
            img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
            return f"data:image/jpeg;base64,{img_str}"
    except Exception as e:
        print(f"Error processing image for LLM: {e}")
        raise e

import re

@gallery.route("/api/caption-single", methods=["POST"])
def caption_single():
    """API to caption a single image"""
    data = request.json or {}
    folder_path = data.get("folderPath")
    file_name = data.get("fileName")
    prompt = data.get("prompt", "Caption this image")
    lm_studio_url = data.get("lmStudioUrl", "http://localhost:1234/v1")
    model = data.get("model", "model-identifier")
    trigger_tag = data.get("triggerTag", "")

    if not folder_path or not file_name:
        return jsonify({"error": "Folder path and file name are required"}), 400
        
    folder_path = folder_path.strip('"\'')
    image_path = os.path.join(folder_path, file_name)
    text_file_path = os.path.join(folder_path, os.path.splitext(file_name)[0] + '.txt')

    if not is_safe_path(folder_path, image_path):
        return jsonify({"error": "Invalid file name"}), 400

    if not os.path.exists(image_path):
        return jsonify({"error": "Image file does not exist"}), 400

    client = OpenAI(api_key="not-needed", base_url=lm_studio_url)

    try:
        # Read full image for display
        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
            m, _ = mimetypes.guess_type(image_path)
            m = m or 'image/jpeg'
            display_image_data = f"data:{m};base64,{encoded_string}"

        # Get resized JPEG for LLM
        llm_image_data = process_image_for_llm(image_path)

        # Call OpenAI
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": llm_image_data},
                        },
                    ],
                }
            ],
        )

        full_content = response.choices[0].message.content
        
        # Remove think tags if generated
        caption = re.sub(r'<think>.*?</think>', '', full_content, flags=re.DOTALL).strip()
        
        if trigger_tag and trigger_tag.strip():
            caption = f"{trigger_tag.strip()}, {caption}"
            
        # Extract and append SD Prompt if available
        sd_prompt = ""
        sd_negative = ""
        sd_setting = ""
        if ImageDataReader:
            try:
                reader = ImageDataReader(image_path)
                if reader.positive:
                    sd_prompt = str(reader.positive).strip()
                if reader.negative:
                    sd_negative = str(reader.negative).strip()
                if reader.setting:
                    sd_setting = str(reader.setting).strip()
                    
                # Append them behind the caption
                appended_text = ""
                if sd_prompt or sd_negative or sd_setting:
                    appended_text += "\n\n"
                    if sd_prompt:
                        appended_text += f"{sd_prompt}\n"
                    if sd_negative:
                        appended_text += f"Negative prompt: {sd_negative}\n"
                    if sd_setting:
                        appended_text += f"{sd_setting}"
                        
                if appended_text:
                    caption = f"{caption}{appended_text}"
            except Exception as e:
                print(f"Failed to read SD info for {file_name}: {e}")

        # Save to disk
        with open(text_file_path, "w", encoding="utf-8") as f:
            f.write(caption)

        return jsonify({
            "status": "success",
            "file": file_name,
            "caption": caption,
            "sdPrompt": sd_prompt,
            "sdNegative": sd_negative,
            "sdSetting": sd_setting,
            "image": display_image_data,
            "hasWorkflow": extract_comfy_workflow(image_path) is not None
        })
    except Exception as e:
        print(f"Error in /api/caption-single: {e}")
        return jsonify({"error": str(e)}), 500


@gallery.route("/api/caption", methods=["POST"])
def caption_batch():
    """API to process captions using Server-Sent Events"""
    data = request.json or {}
    folder_path = data.get("folderPath")
    prompt = data.get("prompt", "Caption this image")
    lm_studio_url = data.get("lmStudioUrl", "http://localhost:1234/v1")
    model = data.get("model", "model-identifier")
    mode = data.get("mode")
    trigger_tag = data.get("triggerTag", "")

    if not folder_path:
        return jsonify({"error": "Folder path is required"}), 400
        
    folder_path = folder_path.strip('"\'')

    if not os.path.exists(folder_path):
        return jsonify({"error": "Invalid folder path"}), 400

    client = OpenAI(api_key="not-needed", base_url=lm_studio_url)

    # Pre-check connection
    try:
        client.models.list(timeout=5.0)
    except Exception as err:
        return jsonify({"error": f"LLM instance not connected: {err}"}), 500

    try:
        all_files = os.listdir(folder_path)
        image_files = []
        for file in all_files:
            ext = os.path.splitext(file)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                image_files.append(file)

        if not image_files:
            return jsonify({"error": "No images found"}), 400

        total = len(image_files)

        def generate():
            current = 0
            
            for image_file in image_files:
                current += 1
                image_path = os.path.join(folder_path, image_file)
                text_file_path = os.path.join(folder_path, os.path.splitext(image_file)[0] + '.txt')
                
                exists = os.path.exists(text_file_path)

                if mode == 'missing' and exists:
                    try:
                        with open(text_file_path, 'r', encoding='utf-8') as f:
                            existing_caption = f.read()
                        
                        m, _ = mimetypes.guess_type(image_path)
                        m = m or 'image/jpeg'
                        
                        with open(image_path, "rb") as bf:
                            encoded_string = base64.b64encode(bf.read()).decode('utf-8')
                        
                        sd_prompt_existing = ""
                        sd_negative_existing = ""
                        sd_setting_existing = ""
                        if ImageDataReader:
                            try:
                                reader = ImageDataReader(image_path)
                                if reader.positive:
                                    sd_prompt_existing = str(reader.positive).strip()
                                if reader.negative:
                                    sd_negative_existing = str(reader.negative).strip()
                                if reader.setting:
                                    sd_setting_existing = str(reader.setting).strip()
                            except Exception:
                                pass
                                
                        yield f"data: {json.dumps({ 'status': 'success', 'current': current, 'total': total, 'file': image_file, 'caption': existing_caption, 'sdPrompt': sd_prompt_existing, 'sdNegative': sd_negative_existing, 'sdSetting': sd_setting_existing, 'image': f'data:{m};base64,{encoded_string}', 'skipped': True, 'hasWorkflow': extract_comfy_workflow(image_path) is not None })}\n\n"
                    except Exception as e:
                        print(f"Error reading existing file {image_file}: {e}")
                    continue

                try:
                    with open(image_path, "rb") as image_f:
                        encoded_string = base64.b64encode(image_f.read()).decode('utf-8')
                        m, _ = mimetypes.guess_type(image_path)
                        m = m or 'image/jpeg'
                        display_image_data = f"data:{m};base64,{encoded_string}"

                    llm_image_data = process_image_for_llm(image_path)

                    yield f"data: {json.dumps({ 'status': 'started', 'current': current, 'total': total, 'file': image_file, 'image': display_image_data })}\n\n"

                    response = client.chat.completions.create(
                        model=model,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": llm_image_data},
                                    },
                                ],
                            }
                        ],
                    )

                    full_content = response.choices[0].message.content
                    caption = re.sub(r'<think>.*?</think>', '', full_content, flags=re.DOTALL).strip()
                    
                    if trigger_tag and trigger_tag.strip():
                        caption = f"{trigger_tag.strip()}, {caption}"
                        
                    # Extract and append SD Prompt if available
                    sd_prompt = ""
                    sd_negative = ""
                    sd_setting = ""
                    if ImageDataReader:
                        try:
                            reader = ImageDataReader(image_path)
                            if reader.positive:
                                sd_prompt = str(reader.positive).strip()
                            if reader.negative:
                                sd_negative = str(reader.negative).strip()
                            if reader.setting:
                                sd_setting = str(reader.setting).strip()
                                
                            # Append them behind the caption
                            appended_text = ""
                            if sd_prompt or sd_negative or sd_setting:
                                appended_text += "\n\n"
                                if sd_prompt:
                                    appended_text += f"{sd_prompt}\n"
                                if sd_negative:
                                    appended_text += f"Negative prompt: {sd_negative}\n"
                                if sd_setting:
                                    appended_text += f"{sd_setting}"
                                    
                            if appended_text:
                                caption = f"{caption}{appended_text}"
                        except Exception as e:
                            print(f"Failed to read SD info for {image_file}: {e}")

                    with open(text_file_path, "w", encoding="utf-8") as f:
                        f.write(caption)

                    yield f"data: {json.dumps({ 'status': 'success', 'current': current, 'total': total, 'file': image_file, 'caption': caption, 'sdPrompt': sd_prompt, 'sdNegative': sd_negative, 'sdSetting': sd_setting, 'image': display_image_data, 'hasWorkflow': extract_comfy_workflow(image_path) is not None })}\n\n"

                except Exception as err:
                    yield f"data: {json.dumps({ 'status': 'error', 'file': image_file, 'error': str(err) })}\n\n"

            yield f"data: {json.dumps({ 'status': 'complete', 'total': total })}\n\n"

        return Response(generate(), mimetype='text/event-stream')

    except Exception as err:
        print(f"Error in /api/caption: {err}")
        return jsonify({"error": str(err)}), 500

import shutil

@gallery.route("/api/galleries", methods=["GET"])
def get_galleries():
    """API to retrieve all available galleries"""
    from app import db
    from app.models import Gallery, GalleryImage
    try:
        galleries = Gallery.query.order_by(Gallery.created_at.asc()).all()
        if not galleries:
            default_gal = Gallery(name="Default Gallery")
            db.session.add(default_gal)
            db.session.commit()
            galleries = [default_gal]

        res = []
        for g in galleries:
            count = GalleryImage.query.filter_by(gallery_id=g.id).count()
            res.append({"id": g.id, "name": g.name, "count": count})
        return jsonify(res)
    except Exception as e:
        print(f"Error fetching galleries: {e}")
        return jsonify({"error": str(e)}), 500

@gallery.route("/api/galleries", methods=["POST"])
def create_gallery():
    """API to create a new named gallery"""
    from app import db
    from app.models import Gallery
    data = request.json or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Gallery name is required"}), 400

    try:
        existing = Gallery.query.filter(Gallery.name.ilike(name)).first()
        if existing:
            return jsonify({"id": existing.id, "name": existing.name, "status": "exists"})

        new_gal = Gallery(name=name)
        db.session.add(new_gal)
        db.session.commit()
        return jsonify({"id": new_gal.id, "name": new_gal.name, "status": "created"})
    except Exception as e:
        db.session.rollback()
        print(f"Error creating gallery: {e}")
        return jsonify({"error": str(e)}), 500

@gallery.route("/api/galleries/<int:gallery_id>", methods=["DELETE"])
def delete_gallery(gallery_id):
    """API to delete a gallery and its associated saved images"""
    from app import db
    from app.models import Gallery, GalleryImage
    from flask import current_app
    try:
        gal = Gallery.query.get(gallery_id)
        if not gal:
            return jsonify({"error": "Gallery not found"}), 404
        
        images = GalleryImage.query.filter_by(gallery_id=gallery_id).all()
        for img in images:
            file_path = os.path.join(current_app.root_path, "static", img.image_path)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass
            db.session.delete(img)
        db.session.delete(gal)
        db.session.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@gallery.route("/api/gallery/save", methods=["POST"])
def save_gallery_image():
    """API to save a captioned image to the persistent gallery"""
    from app import db
    from app.models import GalleryImage, Gallery
    from flask import current_app
    
    data = request.json or {}
    caption = data.get("caption", "")
    sd_prompt = data.get("sdPrompt", "")
    sd_negative = data.get("sdNegative", "")
    sd_setting = data.get("sdSetting", "")
    folder_path = data.get("folderPath")
    file_name = data.get("fileName")
    gallery_id = data.get("galleryId") or data.get("gallery_id")
    
    if not folder_path or not file_name:
        return jsonify({"error": "Folder path and file name are required"}), 400
        
    folder_path = folder_path.strip('"\'')
    source_path = os.path.join(folder_path, file_name)
    
    if not os.path.exists(source_path):
        return jsonify({"error": "Original image not found"}), 400

    target_gal_id = None
    if gallery_id:
        gal = Gallery.query.get(int(gallery_id))
        if gal:
            target_gal_id = gal.id
    if not target_gal_id:
        gal = Gallery.query.filter_by(name="Default Gallery").first()
        if not gal:
            gal = Gallery(name="Default Gallery")
            db.session.add(gal)
            db.session.commit()
        target_gal_id = gal.id
        
    # ensure saved_gallery exists
    gallery_dir = os.path.join(current_app.root_path, "static", "saved_gallery")
    if not os.path.exists(gallery_dir):
        os.makedirs(gallery_dir, exist_ok=True)
        
    # Handle filename collision
    base, ext = os.path.splitext(file_name)
    dest_file = file_name
    dest_path = os.path.join(gallery_dir, dest_file)
    counter = 1
    while os.path.exists(dest_path):
        dest_file = f"{base}_{counter}{ext}"
        dest_path = os.path.join(gallery_dir, dest_file)
        counter += 1
        
    try:
        shutil.copy2(source_path, dest_path)
        
        rel_path = f"saved_gallery/{dest_file}"
        
        new_image = GalleryImage(
            gallery_id=target_gal_id,
            file_name=dest_file,
            image_path=rel_path,
            caption=caption,
            sd_prompt=sd_prompt,
            sd_negative=sd_negative,
            sd_setting=sd_setting
        )
        db.session.add(new_image)
        db.session.commit()
        
        return jsonify({"status": "success", "id": new_image.id})
    except Exception as e:
        print(f"Error saving to gallery: {e}")
        return jsonify({"error": str(e)}), 500

@gallery.route("/api/gallery/save-all", methods=["POST"])
def save_all_gallery_images():
    """API to save all captioned images in a folder to the persistent gallery"""
    from app import db
    from app.models import GalleryImage, Gallery
    from flask import current_app
    
    data = request.json or {}
    folder_path = data.get("folderPath")
    gallery_id = data.get("galleryId") or data.get("gallery_id")
    
    if not folder_path:
        return jsonify({"error": "Folder path is required"}), 400
        
    folder_path = folder_path.strip('"\'')
    
    if not os.path.exists(folder_path):
        return jsonify({"error": "Folder not found"}), 400

    target_gal_id = None
    if gallery_id:
        gal = Gallery.query.get(int(gallery_id))
        if gal:
            target_gal_id = gal.id
    if not target_gal_id:
        gal = Gallery.query.filter_by(name="Default Gallery").first()
        if not gal:
            gal = Gallery(name="Default Gallery")
            db.session.add(gal)
            db.session.commit()
        target_gal_id = gal.id
        
    gallery_dir = os.path.join(current_app.root_path, "static", "saved_gallery")
    if not os.path.exists(gallery_dir):
        os.makedirs(gallery_dir, exist_ok=True)

    saved_count = 0
    try:
        all_files = os.listdir(folder_path)
        for file_name in all_files:
            ext = os.path.splitext(file_name)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                txt_path = os.path.join(folder_path, os.path.splitext(file_name)[0] + '.txt')
                if os.path.exists(txt_path):
                    with open(txt_path, 'r', encoding='utf-8') as f:
                        caption = f.read().strip()
                    
                    if caption:
                        image_path = os.path.join(folder_path, file_name)
                        
                        sd_prompt = ""
                        sd_negative = ""
                        sd_setting = ""
                        if ImageDataReader:
                            try:
                                reader = ImageDataReader(image_path)
                                if reader.positive: sd_prompt = str(reader.positive).strip()
                                if reader.negative: sd_negative = str(reader.negative).strip()
                                if reader.setting: sd_setting = str(reader.setting).strip()
                            except:
                                pass
                                
                        base, iext = os.path.splitext(file_name)
                        dest_file = file_name
                        dest_path = os.path.join(gallery_dir, dest_file)
                        counter = 1
                        while os.path.exists(dest_path):
                            dest_file = f"{base}_{counter}{iext}"
                            dest_path = os.path.join(gallery_dir, dest_file)
                            counter += 1
                            
                        shutil.copy2(image_path, dest_path)
                        rel_path = f"saved_gallery/{dest_file}"
                        
                        new_image = GalleryImage(
                            gallery_id=target_gal_id,
                            file_name=dest_file,
                            image_path=rel_path,
                            caption=caption,
                            sd_prompt=sd_prompt,
                            sd_negative=sd_negative,
                            sd_setting=sd_setting
                        )
                        db.session.add(new_image)
                        saved_count += 1
                        
        db.session.commit()
        return jsonify({"status": "success", "saved_count": saved_count})
        
    except Exception as e:
        print(f"Error saving all to gallery: {e}")
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@gallery.route("/api/gallery/save-selected", methods=["POST"])
def save_selected_gallery_images():
    """API to save selected captioned images in a folder to a specific gallery"""
    from app import db
    from app.models import GalleryImage, Gallery
    from flask import current_app
    
    data = request.json or {}
    folder_path = data.get("folderPath")
    files = data.get("files", [])
    gallery_id = data.get("galleryId") or data.get("gallery_id")
    
    if not folder_path or not files:
        return jsonify({"error": "Folder path and file list are required"}), 400
        
    folder_path = folder_path.strip('"\'')
    if not os.path.exists(folder_path):
        return jsonify({"error": "Folder not found"}), 400

    target_gal_id = None
    if gallery_id:
        gal = Gallery.query.get(int(gallery_id))
        if gal:
            target_gal_id = gal.id
    if not target_gal_id:
        gal = Gallery.query.filter_by(name="Default Gallery").first()
        if not gal:
            gal = Gallery(name="Default Gallery")
            db.session.add(gal)
            db.session.commit()
        target_gal_id = gal.id
        
    gallery_dir = os.path.join(current_app.root_path, "static", "saved_gallery")
    if not os.path.exists(gallery_dir):
        os.makedirs(gallery_dir, exist_ok=True)

    saved_count = 0
    try:
        for file_name in files:
            image_path = os.path.join(folder_path, file_name)
            if not os.path.exists(image_path):
                continue
            
            caption = ""
            txt_path = os.path.join(folder_path, os.path.splitext(file_name)[0] + '.txt')
            if os.path.exists(txt_path):
                with open(txt_path, 'r', encoding='utf-8') as f:
                    caption = f.read().strip()
                
            sd_prompt = ""
            sd_negative = ""
            sd_setting = ""
            if ImageDataReader:
                try:
                    reader = ImageDataReader(image_path)
                    if reader.positive: sd_prompt = str(reader.positive).strip()
                    if reader.negative: sd_negative = str(reader.negative).strip()
                    if reader.setting: sd_setting = str(reader.setting).strip()
                except:
                    pass
                    
            base, iext = os.path.splitext(file_name)
            dest_file = file_name
            dest_path = os.path.join(gallery_dir, dest_file)
            counter = 1
            while os.path.exists(dest_path):
                dest_file = f"{base}_{counter}{iext}"
                dest_path = os.path.join(gallery_dir, dest_file)
                counter += 1
                
            shutil.copy2(image_path, dest_path)
            rel_path = f"saved_gallery/{dest_file}"
            
            new_image = GalleryImage(
                gallery_id=target_gal_id,
                file_name=dest_file,
                image_path=rel_path,
                caption=caption,
                sd_prompt=sd_prompt,
                sd_negative=sd_negative,
                sd_setting=sd_setting
            )
            db.session.add(new_image)
            saved_count += 1
                        
        db.session.commit()
        return jsonify({"status": "success", "saved_count": saved_count})
        
    except Exception as e:
        print(f"Error saving selected to gallery: {e}")
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@gallery.route("/api/gallery/saved", methods=["GET"])
def get_saved_gallery():
    """API to retrieve saved gallery images"""
    from app.models import GalleryImage
    try:
        page = int(request.args.get("page", 1))
        limit = int(request.args.get("limit", 20))
        page = max(1, page)
        limit = max(1, limit)

        gallery_id = request.args.get("gallery_id") or request.args.get("galleryId")
        query = GalleryImage.query
        if gallery_id and gallery_id != "all":
            try:
                query = query.filter_by(gallery_id=int(gallery_id))
            except ValueError:
                pass

        pagination = query.order_by(GalleryImage.created_at.desc()).paginate(page=page, per_page=limit, error_out=False)
        
        results = []
        for img in pagination.items:
            results.append({
                "id": img.id,
                "gallery_id": img.gallery_id,
                "file_name": img.file_name,
                "image_path": f"/static/{img.image_path}",
                "caption": img.caption or "",
                "sd_prompt": img.sd_prompt or "",
                "sd_negative": img.sd_negative or "",
                "sd_setting": img.sd_setting or "",
                "created_at": img.created_at.isoformat() if img.created_at else None
            })
            
        return jsonify({
            "images": results,
            "total": pagination.total,
            "page": pagination.page,
            "totalPages": pagination.pages or 1,
            "limit": limit
        })
    except Exception as e:
        print(f"Error retrieving saved gallery: {e}")
        return jsonify({"error": str(e)}), 500

@gallery.route("/api/gallery/saved/<int:image_id>", methods=["DELETE"])
def delete_saved_gallery_image(image_id):
    """API to delete a saved gallery image"""
    from app import db
    from app.models import GalleryImage
    from flask import current_app
    try:
        img = GalleryImage.query.get(image_id)
        if not img:
            return jsonify({"error": "Image not found"}), 404
            
        file_path = os.path.join(current_app.root_path, "static", img.image_path)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError as e:
                print(f"Could not remove file {file_path}: {e}")
            
        db.session.delete(img)
        db.session.commit()
        
        return jsonify({"status": "success"})
    except Exception as e:
        print(f"Error deleting saved image: {e}")
        return jsonify({"error": str(e)}), 500


@gallery.route("/api/gallery/saved/<int:image_id>/caption", methods=["POST"])
def caption_saved_gallery_image(image_id):
    """API to re-caption a saved gallery image using LM Studio"""
    from app import db
    from app.models import GalleryImage
    from flask import current_app
    from openai import OpenAI

    data = request.json or {}
    prompt = data.get("prompt", "Caption this image")
    lm_studio_url = data.get("lmStudioUrl", "http://localhost:1234/v1")
    model = data.get("model", "model-identifier")
    trigger_tag = data.get("triggerTag", "")

    try:
        img = GalleryImage.query.get(image_id)
        if not img:
            return jsonify({"error": "Image not found"}), 404

        file_path = os.path.join(current_app.root_path, "static", img.image_path)
        if not os.path.exists(file_path):
            file_path = img.image_path

        if not os.path.exists(file_path):
            return jsonify({"error": "Image file does not exist on server"}), 404

        client = OpenAI(api_key="not-needed", base_url=lm_studio_url)
        llm_image_data = process_image_for_llm(file_path)

        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": llm_image_data},
                        },
                    ],
                }
            ],
            max_tokens=300,
        )

        raw_caption = response.choices[0].message.content.strip()
        final_caption = clean_llm_caption(raw_caption)

        if trigger_tag:
            tag = trigger_tag.strip()
            if not final_caption.startswith(tag):
                final_caption = f"{tag}, {final_caption}"

        img.caption = final_caption
        db.session.commit()

        return jsonify({"status": "success", "caption": final_caption})
    except Exception as e:
        print(f"Error captioning saved image {image_id}: {e}")
        return jsonify({"error": str(e)}), 500



@gallery.route("/api/gallery/move-selected", methods=["POST"])
def move_selected_gallery_images():
    """API to move selected saved gallery images to another gallery"""
    from app import db
    from app.models import GalleryImage, Gallery
    
    data = request.json or {}
    image_ids = data.get("image_ids") or data.get("imageIds") or []
    target_gallery_id = data.get("target_gallery_id") or data.get("targetGalleryId")
    
    if not image_ids or not target_gallery_id:
        return jsonify({"error": "image_ids and target_gallery_id are required"}), 400
        
    try:
        gal = Gallery.query.get(int(target_gallery_id))
        if not gal:
            return jsonify({"error": "Target gallery not found"}), 404
            
        moved_count = GalleryImage.query.filter(GalleryImage.id.in_([int(i) for i in image_ids])).update(
            {GalleryImage.gallery_id: gal.id},
            synchronize_session=False
        )
        db.session.commit()
        return jsonify({"status": "success", "moved_count": moved_count})
    except Exception as e:
        db.session.rollback()
        print(f"Error moving selected images: {e}")
        return jsonify({"error": str(e)}), 500


@gallery.route("/api/gallery/delete-selected", methods=["POST"])
def delete_selected_gallery_images():
    """API to bulk delete selected saved gallery images"""
    from app import db
    from app.models import GalleryImage
    from flask import current_app
    
    data = request.json or {}
    image_ids = data.get("image_ids") or data.get("imageIds") or []
    
    if not image_ids:
        return jsonify({"error": "image_ids is required"}), 400
        
    try:
        deleted_count = 0
        images = GalleryImage.query.filter(GalleryImage.id.in_([int(i) for i in image_ids])).all()
        for img in images:
            file_path = os.path.join(current_app.root_path, "static", img.image_path)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError as e:
                    print(f"Could not remove file {file_path}: {e}")
            db.session.delete(img)
            deleted_count += 1
            
        db.session.commit()
        return jsonify({"status": "success", "deleted_count": deleted_count})
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting selected images: {e}")
        return jsonify({"error": str(e)}), 500


