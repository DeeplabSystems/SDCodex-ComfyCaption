from .sdcodex_gallery_browser import SDCodexGalleryBrowser
from .sdcodex_gallery_loader import SDCodexGalleryLoader

NODE_CLASS_MAPPINGS = {
    "SDCodexGalleryBrowser": SDCodexGalleryBrowser,
    "SDCodexGalleryLoader": SDCodexGalleryLoader,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "SDCodexGalleryBrowser": "SDCodex Gallery Browser",
    "SDCodexGalleryLoader": "SDCodex Gallery Loader",
}

WEB_DIRECTORY = "./js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
