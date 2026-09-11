import os
import sys
from flask import render_template

plugin_dir = os.path.dirname(os.path.abspath(__file__))
if plugin_dir not in sys.path:
    sys.path.insert(0, plugin_dir)

from api_gallery import gallery as gallery_bp
from caption_models import caption_models_bp, init_caption_models

gallery_bp.template_folder = os.path.join(plugin_dir, "templates")
caption_models_bp.template_folder = os.path.join(plugin_dir, "templates")

@gallery_bp.route("/captioning", endpoint="captioning")
def captioning():
    return render_template("captioning.html")

def init_plugin(app, db, plugin_info=None):
    """Initialize the ComfyUI Caption plugin.

    The gallery is provided by the separate SDCodex Gallery plugin
    (reads directly from disk and image metadata), so this plugin no longer
    manages saved galleries or touches the database. ``db`` is accepted only
    for plugin-manager API compatibility and is intentionally unused.
    """
    if "gallery" not in app.blueprints:
        app.register_blueprint(gallery_bp)
    init_caption_models(app)

    # Install / sync ComfyUI custom nodes into custom-addons folder
    custom_nodes_dirs = [
        os.environ.get("COMFYUI_CUSTOM_NODES"),
        os.environ.get("COMFY_CUSTOM_NODES"),
        "/data/custom_nodes",
        "/data/comfyui_custom_nodes",
    ]
    for cdir in custom_nodes_dirs:
        if cdir and os.path.exists(cdir):
            src_nodes = os.path.join(plugin_dir, "comfyui-sdcodex")
            dest_nodes = os.path.join(cdir, "comfyui-sdcodex")
            if os.path.exists(src_nodes):
                try:
                    import shutil
                    shutil.copytree(src_nodes, dest_nodes, dirs_exist_ok=True)
                    app.logger.info("Installed/updated ComfyUI custom nodes to %s", dest_nodes)
                except Exception as err:
                    app.logger.warning("Could not copy ComfyUI nodes to %s: %s", dest_nodes, err)
            break