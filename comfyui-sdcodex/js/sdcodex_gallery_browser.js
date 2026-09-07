import { app } from "../../scripts/app.js";

const style = document.createElement("style");
style.innerHTML = `
    .sdcodex-gallery-container {
        display: flex;
        flex-direction: column;
        gap: 6px;
        background: #1e1e24;
        border: 1px solid #3e3e4a;
        border-radius: 6px;
        padding: 8px;
        font-family: Arial, sans-serif;
        color: #eee;
        width: 100%;
        box-sizing: border-box;
        margin: 5px 0;
        overflow: hidden;
    }
    .sdcodex-gallery-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 6px;
    }
    .sdcodex-gallery-select {
        background: #111;
        border: 1px solid #444;
        color: #fff;
        border-radius: 4px;
        padding: 3px 6px;
        font-size: 11px;
    }
    .sdcodex-gallery-select-row {
        display: flex;
        align-items: center;
        gap: 6px;
    }
    .sdcodex-gallery-search {
        flex: 1;
        background: #111;
        border: 1px solid #444;
        color: #fff;
        border-radius: 4px;
        padding: 3px 6px;
        font-size: 11px;
    }
    .sdcodex-gallery-btn {
        background: #3a3a4a;
        border: 1px solid #555;
        color: #fff;
        border-radius: 4px;
        padding: 3px 8px;
        cursor: pointer;
        font-size: 11px;
        transition: background 0.2s;
    }
    .sdcodex-gallery-btn:hover {
        background: #4e4e60;
    }
    .sdcodex-gallery-btn:disabled {
        opacity: 0.4;
        cursor: not-allowed;
    }
    .sdcodex-gallery-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(115px, 1fr));
        grid-auto-rows: 1fr;
        gap: 6px;
        min-height: 0;
        flex: 1;
        overflow-y: auto;
        background: #101014;
        border: 1px solid #2a2a30;
        border-radius: 4px;
        padding: 4px;
    }
    .sdcodex-gallery-item {
        min-height: 0;
        background: #25252b;
        border-radius: 4px;
        overflow: hidden;
        cursor: pointer;
        border: 2px solid transparent;
        position: relative;
        transition: border-color 0.15s, transform 0.1s;
    }
    .sdcodex-gallery-item:hover {
        border-color: #555;
        transform: scale(1.02);
    }
    .sdcodex-gallery-item.selected {
        border-color: #007acc;
        box-shadow: 0 0 5px rgba(0, 122, 204, 0.6);
    }
    .sdcodex-gallery-item img {
        width: 100%;
        height: 100%;
        object-fit: cover;
    }
    .sdcodex-gallery-footer {
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 10px;
        color: #aaa;
    }
    .sdcodex-gallery-status {
        color: #888;
    }
`;
document.head.appendChild(style);

app.registerExtension({
    name: "SDCodex.GalleryBrowser",
    async nodeCreated(node) {
        if (node.comfyClass !== "SDCodexGalleryBrowser") return;

        const container = document.createElement("div");
        container.className = "sdcodex-gallery-container";

        container.innerHTML = `
            <div class="sdcodex-gallery-header">
                <input type="text" class="sdcodex-gallery-search" placeholder="Search gallery...">
                <button class="sdcodex-gallery-btn refresh-btn">↻</button>
            </div>
            <div class="sdcodex-gallery-select-row" style="display:none;">
                <label class="sdcodex-gallery-status" style="white-space:nowrap;">Gallery:</label>
                <select class="sdcodex-gallery-select" style="flex:1;">
                    <option value="">All Galleries</option>
                </select>
            </div>
            <div class="sdcodex-gallery-grid"></div>
            <div class="sdcodex-gallery-footer">
                <button class="sdcodex-gallery-btn prev-btn">◀ Prev</button>
                <span class="sdcodex-gallery-status">Page 1 of 1</span>
                <button class="sdcodex-gallery-btn next-btn">Next ▶</button>
            </div>
        `;

        const grid = container.querySelector(".sdcodex-gallery-grid");
        const prevBtn = container.querySelector(".prev-btn");
        const nextBtn = container.querySelector(".next-btn");
        const statusSpan = container.querySelector(".sdcodex-gallery-status");
        const refreshBtn = container.querySelector(".refresh-btn");
        const searchInput = container.querySelector(".sdcodex-gallery-search");
        const gallerySelectRow = container.querySelector(".sdcodex-gallery-select-row");
        const gallerySelect = container.querySelector(".sdcodex-gallery-select");

        let currentPage = 1;
        let totalPages = 1;
        let imagesData = [];
        let searchQuery = "";
        let galleriesCache = [];

        // A stale workflow may persist an image file path in the sdcodex_root
        // widget (e.g. ".../static/saved_gallery/foo.jpeg"). Only accept values
        // that look like a directory; anything else → "" so the server uses its
        // detected default root.
        const sanitizeRoot = (p) => {
            if (!p) return "";
            const s = String(p).trim();
            if (!s) return "";
            if (/\.(jpe?g|png|gif|webp|bmp)$/i.test(s)) return "";
            if (/\/static\/(saved_gallery|downloads?)\//i.test(s)) return "";
            if (/\/db\/sdcodex\.db/i.test(s)) return "";
            if (!/^\/|^[A-Za-z]:[\\/]/.test(s)) return "";   // must be absolute
            return s;
        };

        const getRootPath = () => {
            const rootWidget = node.widgets?.find(w => w.name === "sdcodex_root");
            return sanitizeRoot(rootWidget ? rootWidget.value : "");
        };

        const loadGalleries = async () => {
            const rootPath = getRootPath();
            const url = `/sdcodex/galleries?sdcodex_root=${encodeURIComponent(rootPath)}`;
            try {
                const response = await fetch(url);
                if (!response.ok) return;
                const data = await response.json();
                galleriesCache = data.galleries || [];
            } catch (err) {
                console.error("Error loading galleries:", err);
            }
        };

        const renderGallerySelect = () => {
            const galleryWidget = node.widgets?.find(w => w.name === "gallery");
            const currentGallery = galleryWidget ? (galleryWidget.value || "") : "";
            gallerySelect.innerHTML = "";
            const allOpt = document.createElement("option");
            allOpt.value = "";
            allOpt.textContent = "All Galleries";
            gallerySelect.appendChild(allOpt);
            galleriesCache.forEach(g => {
                const opt = document.createElement("option");
                opt.value = g.name;
                opt.textContent = `${g.name} (${g.image_count})`;
                if (g.name === currentGallery) opt.selected = true;
                gallerySelect.appendChild(opt);
            });
        };

        const syncGalleryWidget = () => {
            const galleryWidget = node.widgets?.find(w => w.name === "gallery");
            if (!galleryWidget) return;
            const val = gallerySelect.value || "";
            galleryWidget.value = val;
            if (galleryWidget.inputEl) galleryWidget.inputEl.value = val;
            if (galleryWidget.callback) galleryWidget.callback(val);
        };

        const loadGallery = async () => {
            const modeWidget = node.widgets?.find(w => w.name === "mode");
            const folderPathWidget = node.widgets?.find(w => w.name === "folder_path");
            const galleryWidget = node.widgets?.find(w => w.name === "gallery");

            const mode = modeWidget ? modeWidget.value : "Saved Gallery";
            const folderPath = folderPathWidget ? folderPathWidget.value : "";
            const rootPath = getRootPath();
            const gallery = gallerySelect.value || (galleryWidget ? galleryWidget.value : "") || "";

            gallerySelectRow.style.display = mode === "Saved Gallery" ? "flex" : "none";

            grid.innerHTML = `<div style="grid-column: 1 / -1; display: flex; align-items: center; justify-content: center; height: 100%; color: #888; font-size: 11px;">Loading...</div>`;

            try {
                const url = `/sdcodex/images?mode=${encodeURIComponent(mode)}&folder_path=${encodeURIComponent(folderPath)}&sdcodex_root=${encodeURIComponent(rootPath)}&gallery=${encodeURIComponent(gallery)}&page=${currentPage}&limit=24`;
                const response = await fetch(url);

                if (!response.ok) {
                    const errText = await response.text();
                    throw new Error(errText || "Error loading images");
                }

                const data = await response.json();
                imagesData = data.images || [];
                totalPages = data.totalPages || 1;
                currentPage = data.page || 1;

                renderGrid();
            } catch (err) {
                console.error(err);
                grid.innerHTML = `<div style="grid-column: 1 / -1; color: #ff5555; font-size: 11px; padding: 10px;">Error: ${err.message}</div>`;
            }
        };

        const renderGrid = () => {
            grid.innerHTML = "";

            const selectedImageWidget = node.widgets?.find(w => w.name === "selected_image");
            const currentSelectedPath = selectedImageWidget ? selectedImageWidget.value : "";

            const filteredImages = imagesData.filter(img => {
                if (!searchQuery) return true;
                const q = searchQuery.toLowerCase();
                const nameMatch = img.file_name.toLowerCase().includes(q);
                const captionMatch = img.caption && img.caption.toLowerCase().includes(q);
                return nameMatch || captionMatch;
            });

            if (filteredImages.length === 0) {
                grid.innerHTML = `<div style="grid-column: 1 / -1; display: flex; align-items: center; justify-content: center; height: 100%; color: #666; font-size: 11px;">No images found</div>`;
            } else {
                filteredImages.forEach(img => {
                    const item = document.createElement("div");
                    item.className = "sdcodex-gallery-item";

                    if (currentSelectedPath === img.image_path) {
                        item.classList.add("selected");
                    }

                    const thumbUrl = `/sdcodex/image?path=${encodeURIComponent(img.image_path)}`;
                    item.innerHTML = `<img src="${thumbUrl}" alt="${img.file_name}" loading="lazy" style="width: 100%; height: 100%; object-fit: cover;">`;

                    item.addEventListener("click", () => {
                        grid.querySelectorAll(".sdcodex-gallery-item").forEach(el => el.classList.remove("selected"));
                        item.classList.add("selected");

                        const setImagePath = img.image_path || "";
                        const setCaption = img.caption || "";
                        const setSdPrompt = img.sd_prompt || "";
                        const setSdNegative = img.sd_negative || "";

                        const setNodeWidget = (n, name, val) => {
                            if (!n || !n.widgets) return;
                            const w = n.widgets.find(widget => widget.name === name);
                            if (w) {
                                w.value = val;
                                if (w.inputEl) {
                                    w.inputEl.value = val;
                                }
                                if (w.callback) {
                                    w.callback(val);
                                }
                            }
                        };

                        // 1. Update current browser node widgets
                        setNodeWidget(node, "selected_image", setImagePath);
                        setNodeWidget(node, "caption", setCaption);
                        setNodeWidget(node, "sd_prompt", setSdPrompt);
                        setNodeWidget(node, "sd_negative", setSdNegative);

                        // 2. Update connected or target SDCodexGalleryLoader nodes in the graph
                        if (app.graph && app.graph._nodes) {
                            for (const targetNode of app.graph._nodes) {
                                if (targetNode.comfyClass === "SDCodexGalleryLoader") {
                                    setNodeWidget(targetNode, "selected_image", setImagePath);
                                    setNodeWidget(targetNode, "caption", setCaption);
                                    setNodeWidget(targetNode, "sd_prompt", setSdPrompt);
                                    setNodeWidget(targetNode, "sd_negative", setSdNegative);
                                }
                            }
                        }

                        app.graph.setDirtyCanvas(true, true);
                    });

                    grid.appendChild(item);
                });
            }

            statusSpan.innerText = `Page ${currentPage} of ${totalPages}`;
            prevBtn.disabled = currentPage <= 1;
            nextBtn.disabled = currentPage >= totalPages;
        };

        prevBtn.addEventListener("click", () => {
            if (currentPage > 1) {
                currentPage--;
                loadGallery();
            }
        });

        nextBtn.addEventListener("click", () => {
            if (currentPage < totalPages) {
                currentPage++;
                loadGallery();
            }
        });

        refreshBtn.addEventListener("click", () => {
            loadGalleries().then(() => {
                renderGallerySelect();
                loadGallery();
            });
        });

        searchInput.addEventListener("input", (e) => {
            searchQuery = e.target.value;
            renderGrid();
        });

        gallerySelect.addEventListener("change", () => {
            syncGalleryWidget();
            currentPage = 1;
            loadGallery();
        });

        const galleryWidget = node.addDOMWidget("sdcodex_gallery_widget", "custom_gallery", container);
        galleryWidget.serializeValue = () => undefined;

        setTimeout(() => {
            const modeWidget = node.widgets?.find(w => w.name === "mode");
            const folderPathWidget = node.widgets?.find(w => w.name === "folder_path");
            const rootWidget = node.widgets?.find(w => w.name === "sdcodex_root");
            const galleryWidget = node.widgets?.find(w => w.name === "gallery");

            const onWidgetChanged = async () => {
                currentPage = 1;
                await loadGalleries();
                renderGallerySelect();
                loadGallery();
            };

            if (modeWidget) {
                const origCallback = modeWidget.callback;
                modeWidget.callback = function(...args) {
                    if (origCallback) origCallback.apply(this, args);
                    onWidgetChanged();
                };
            }

            if (folderPathWidget) {
                const origCallback = folderPathWidget.callback;
                folderPathWidget.callback = function(...args) {
                    if (origCallback) origCallback.apply(this, args);
                    onWidgetChanged();
                };
            }

            if (rootWidget) {
                const origCallback = rootWidget.callback;
                rootWidget.callback = function(...args) {
                    if (origCallback) origCallback.apply(this, args);
                    onWidgetChanged();
                };
            }

            if (galleryWidget) {
                const origCallback = galleryWidget.callback;
                galleryWidget.callback = function(...args) {
                    if (origCallback) origCallback.apply(this, args);
                    gallerySelect.value = galleryWidget.value || "";
                    currentPage = 1;
                    loadGallery();
                };
            }

            // Initial gallery list + select population, then first load.
            loadGalleries().then(() => {
                renderGallerySelect();
                loadGallery();
            });
            setTimeout(() => {
                node.onResize?.(node.size);
            }, 50);
        }, 100);

        const origOnResize = node.onResize;
        node.onResize = function(size) {
            if (origOnResize) {
                origOnResize.apply(this, arguments);
            }

            const headerFooterApprox = 100;
            const galleryHeight = Math.max(200, size[1] - headerFooterApprox);

            const gridEl = container.querySelector(".sdcodex-gallery-grid");
            if (gridEl) {
                gridEl.style.height = "100%";
            }

            galleryWidget.height = galleryHeight;
            container.style.height = galleryHeight + "px";
        };

        node.min_size = [350, 400];
        node.size = [500, 700];
        node.onResize?.(node.size);
    }
});
