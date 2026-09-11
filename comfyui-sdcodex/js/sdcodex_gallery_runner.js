import { app } from "../../scripts/app.js";

// SDCodex Gallery Runner
// -----------------------
// A control node that cycle-runs the workflow across the images displayed by a
// connected SDCodexGalleryBrowser node. Usage:
//   1. Drop a Gallery Runner onto the canvas.
//   2. Connect the Gallery Browser's `selected_image` output into the Runner's
//      `current_image` input (or just have a Gallery Browser in the graph).
//   3. Set `executions_per_image` (how many times to run on each image) and
//      `max_images` (images to cycle; 0/blank = until end of list).
//   4. Press ▶ Start to run the workflow for the currently selected image,
//      then advance to the next image and run again until Stop / max reached.
//
// The loop is driven here on the frontend: for each image (and each execution
// repeat) it syncs the selection onto the Gallery Browser + Gallery Loader
// nodes, serializes the current graph, queues it via the ComfyUI API, and
// waits for that exact prompt_id to finish before advancing.

app.registerExtension({
    name: "SDCodex.GalleryRunner",
    async nodeCreated(node) {
        if (node.comfyClass !== "SDCodexGalleryRunner") return;

        const helpers = window.SDCodexGalleryHelpers || {};
        let running = false;
        let stopRequested = false;

        const getWidget = (n, name) => n?.widgets?.find(w => w.name === name);

        const findBrowserNode = () => {
            // Prefer the node wired into `current_image`.
            const inp = node.inputs?.find(i => i.name === "current_image");
            if (inp && inp.link !== null && inp.link !== undefined) {
                for (const n of app.graph?._nodes || []) {
                    for (const o of n.outputs || []) {
                        if (o.links && o.links.includes(inp.link)) return n;
                    }
                }
            }
            // Fall back to the first Gallery Browser in the graph.
            return (app.graph?._nodes || []).find(n => n.comfyClass === "SDCodexGalleryBrowser");
        };

        const readWidgetStr = (n, name) => {
            const w = getWidget(n, name);
            return w ? String(w.value ?? "") : "";
        };

        const loadFolderImages = async (folderPath) => {
            const resp = await fetch(
                `/sdcodex/images?folder_path=${encodeURIComponent(folderPath)}&page=1&limit=100000`
            );
            if (!resp.ok) {
                throw new Error((await resp.text()) || "Failed to load images from folder");
            }
            const data = await resp.json();
            return data.images || [];
        };

        const setStatus = (text) => {
            const statusWidget = getWidget(node, "runner_status");
            if (statusWidget) {
                statusWidget.value = text;
                if (statusWidget.inputEl) statusWidget.inputEl.value = text;
            }
            if (node.setDirtyCanvas) node.setDirtyCanvas(true, true);
        };

        // Await completion (success / interrupted / error) of one prompt_id.
        const waitForPrompt = (promptId) => {
            if (!app.api) return;
            return new Promise((resolve) => {
                const finish = () => {
                    app.api.removeEventListener("execution_success", onSuccess);
                    app.api.removeEventListener("execution_interrupted", onInterrupt);
                    app.api.removeEventListener("execution_error", onError);
                    resolve();
                };
                const matches = (evt) => {
                    const data = evt && evt.detail;
                    const pid = data && data.prompt_id;
                    return !pid || pid === promptId;
                };
                const onSuccess = (evt) => { if (matches(evt)) finish(); };
                const onInterrupt = (evt) => { if (matches(evt)) finish(); };
                const onError = (evt) => { if (matches(evt)) finish(); };
                app.api.addEventListener("execution_success", onSuccess);
                app.api.addEventListener("execution_interrupted", onInterrupt);
                app.api.addEventListener("execution_error", onError);
            });
        };

        const queueCurrentGraph = async () => {
            if (typeof app.graphToPrompt !== "function" || !app.api) {
                throw new Error("ComfyUI queue API unavailable");
            }
            const p = await app.graphToPrompt();
            const res = await app.api.queuePrompt(0, p);
            return res && res.prompt_id;
        };

        const runLoop = async () => {
            if (running) {
                setStatus("Already running — press ■ Stop first");
                return;
            }
            running = true;
            stopRequested = false;
            try {
                const browser = findBrowserNode();
                if (!browser) {
                    setStatus("Connect a Gallery Browser (or add one to the graph)");
                    return;
                }
                const folderPath = readWidgetStr(browser, "folder_path");
                if (!folderPath) {
                    setStatus("Set folder_path on the Gallery Browser first");
                    return;
                }
                const images = await loadFolderImages(folderPath);
                if (images.length === 0) {
                    setStatus("No images in the folder");
                    return;
                }
                const selected = readWidgetStr(browser, "selected_image");
                let startIdx = images.findIndex(img => img.image_path === selected);
                if (startIdx < 0) startIdx = 0;

                const maxImages = parseInt(readWidgetStr(node, "max_images") || "0", 10);
                const perImage = Math.max(1, parseInt(readWidgetStr(node, "executions_per_image") || "1", 10));
                const endIdx = (maxImages > 0)
                    ? Math.min(images.length, startIdx + maxImages)
                    : images.length;

                let cycled = 0;
                for (let i = startIdx; i < endIdx && !stopRequested; i++) {
                    const img = images[i];
                    const label = `${i + 1}/${endIdx}: ${img.file_name}`;
                    setStatus(`▶ Running ${label}`);

                    if (helpers.syncSelectionNodes) {
                        helpers.syncSelectionNodes(
                            img.image_path || "",
                            img.caption || "",
                            img.sd_prompt || "",
                            img.sd_negative || "",
                            app.graph
                        );
                    }
                    if (app.graph?.setDirtyCanvas) app.graph.setDirtyCanvas(true, true);

                    for (let e = 0; e < perImage && !stopRequested; e++) {
                        setStatus(`▶ ${label}  —  run ${e + 1}/${perImage}`);
                        try {
                            const promptId = await queueCurrentGraph();
                            if (promptId) await waitForPrompt(promptId);
                        } catch (err) {
                            console.error("Queue error:", err);
                            setStatus(`! ${err.message}`);
                        }
                        if (stopRequested) break;
                    }
                    cycled++;
                }

                if (stopRequested) setStatus("■ Stopped");
                else if (maxImages > 0 && cycled >= maxImages) setStatus("■ Done — max images reached");
                else setStatus("■ Done — reached end of list");
            } catch (err) {
                console.error("Runner error:", err);
                setStatus(`! ${err.message}`);
            } finally {
                running = false;
            }
        };

        const stopLoop = async () => {
            stopRequested = true;
            try {
                if (app.api && typeof app.api.interrupt === "function") {
                    await app.api.interrupt();
                }
            } catch (err) {
                console.error("Interrupt error:", err);
            }
            setStatus("■ Stopping…");
        };

        // Buttons + status.
        node.addWidget("button", "▶ Start", null, () => { runLoop(); });
        node.addWidget("button", "■ Stop", null, () => { stopLoop(); });
        node.addWidget("string", "runner_status", "Idle");

        node.min_size = [340, 150];
        if (node.setSize) node.setSize([380, 170]);
        else node.size = [380, 170];
    }
});