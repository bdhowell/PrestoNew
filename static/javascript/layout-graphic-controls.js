(() => {
    "use strict";

    const layout = document.getElementById("layout");
    const canvas = document.getElementById("poster-canvas");
    if (!layout || !canvas) return;
    const safeDpi = Number(layout.dataset.graphicsSafeDpi);
    if (!Number.isFinite(safeDpi) || safeDpi <= 0) {
        console.error("Invalid poster graphic resolution threshold.");
        return;
    }

    let selected = null;
    let overlay = null;
    let operation = null;
    const directions = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
    const placementControls = [
        ["graphics-left", "left", "left-middle"],
        ["graphics-center", "none", "free"],
        ["graphics-right", "right", "right-middle"],
    ].map(([id, mode, placement]) => [document.getElementById(id), mode, placement])
        .filter(([button]) => button);

    function say(text) {
        const paragraph = document.createElement("p");
        paragraph.textContent = text;
        if (window.wikkit?.say) window.wikkit.say(paragraph.outerHTML);
        else document.getElementById("content-save-status").textContent = text;
    }

    function updateOverlay() {
        if (!selected?.isConnected || !overlay) return;
        const rect = selected.getBoundingClientRect();
        Object.assign(overlay.style, {
            left: `${rect.left}px`, top: `${rect.top}px`,
            width: `${rect.width}px`, height: `${rect.height}px`,
        });
    }

    function deselect() {
        overlay?.remove();
        overlay = null;
        selected = null;
        updatePlacementControls();
    }

    function updatePlacementControls() {
        const placement = selected?.dataset.placement || "free";
        const mode = placement === "left-middle" || placement.endsWith("-left")
            ? "left"
            : placement === "right-middle" || placement.endsWith("-right") ? "right" : "none";
        for (const [button, controlMode] of placementControls) {
            button.disabled = !selected?.isConnected;
            button.setAttribute("aria-pressed", String(Boolean(selected?.isConnected && mode === controlMode)));
        }
    }

    function editorFor(element) {
        return element.closest(".content[contenteditable='true'][data-section]");
    }

    function canPlace(graphic, editor) {
        const section = editor.dataset.section;
        if (section === "additional information") return graphic.dataset.type === "qrcode";
        if (graphic.dataset.type === "qrcode") return false;
        if (["materials and methods", "results"].includes(section)) return true;
        return graphic.dataset.type === "math"
            && ["abstract", "introduction", "conclusion", "future plans"].includes(section);
    }

    window.pastePosterGraphic = (editor, range, html) => {
        const documentCopy = new DOMParser().parseFromString(html, "text/html");
        const graphic = documentCopy.body.querySelector(".graphic[data-presto-pid]");
        if (!graphic) return false;
        if (graphic.dataset.prestoPid !== layout.dataset.pid
            || !/^[1-9]\d{0,9}$/.test(graphic.dataset.sid || "")
            || !["svg", "png", "jpg", "jpeg", "gif", "tables", "math", "qrcode"].includes(graphic.dataset.type)
            || !canPlace(graphic, editor)) {
            throw new Error("That graphic cannot be pasted into this poster section.");
        }
        if (graphic.dataset.type === "qrcode" && editor.querySelector('.graphic[data-type="qrcode"]')) {
            throw new Error("This section already contains a QR code.");
        }
        const copy = document.createElement("span");
        copy.className = "graphic";
        copy.contentEditable = "false";
        copy.dataset.sid = graphic.dataset.sid;
        copy.dataset.type = graphic.dataset.type;
        copy.dataset.placement = "free";
        const width = Number(graphic.dataset.width);
        const height = Number(graphic.dataset.height);
        copy.dataset.width = String(Number.isInteger(width) && width >= 1 && width <= 10000 ? width : 240);
        copy.style.width = `${copy.dataset.width}px`;
        if (Number.isInteger(height) && height >= 1 && height <= 10000) {
            copy.dataset.height = String(height);
            copy.style.height = `${height}px`;
        }
        if (copy.dataset.type === "math") {
            for (const expression of graphic.querySelectorAll(".graphic-math")) {
                const math = document.createElement("span");
                math.className = "graphic-math";
                math.textContent = expression.textContent;
                copy.append(math);
            }
        } else {
            const image = document.createElement("img");
            image.src = `/layout/source?sid=${copy.dataset.sid}`;
            image.alt = graphic.querySelector("img")?.alt || "Poster graphic";
            copy.append(image);
        }
        if (!editor.isConnected || !editor.contains(range.startContainer) || !editor.contains(range.endContainer)) {
            throw new Error("Select the destination before pasting the graphic.");
        }
        editor.focus({preventScroll: true});
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        if (!document.execCommand("insertHTML", false, copy.outerHTML)) {
            throw new Error("Your browser could not paste this graphic.");
        }
        editor.dispatchEvent(new Event("input", {bubbles: true}));
        window.mathJaxSource?.typeset(editor);
        return true;
    };

    for (const eventName of ["copy", "cut"]) {
        document.addEventListener(eventName, event => {
            if (!selected?.isConnected || !event.clipboardData || !editorFor(selected)) return;
            const selection = getSelection();
            if (!selection.rangeCount) return;
            const range = selection.getRangeAt(0);
            const expected = document.createRange();
            expected.selectNode(selected);
            if (range.compareBoundaryPoints(Range.START_TO_START, expected) !== 0
                || range.compareBoundaryPoints(Range.END_TO_END, expected) !== 0) return;
            const holder = document.createElement("div");
            holder.innerHTML = window.mathJaxSource?.serialize(selected) ?? selected.innerHTML;
            const clone = selected.cloneNode(false);
            clone.dataset.prestoPid = layout.dataset.pid;
            clone.append(...holder.childNodes);
            event.clipboardData.setData("text/html", clone.outerHTML);
            event.clipboardData.setData("text/plain", selected.querySelector("img")?.alt || "Poster graphic");
            event.preventDefault();
            if (eventName === "cut") {
                const editor = editorFor(selected);
                selected.remove();
                deselect();
                editor.dispatchEvent(new Event("input", {bubbles: true}));
            }
        });
    }

    layout.addEventListener("paste", event => {
        const editor = event.target instanceof Element ? editorFor(event.target) : null;
        const selection = getSelection();
        const html = event.clipboardData?.getData("text/html");
        if (!editor || !html || !selection.rangeCount
            || !html.includes("data-presto-pid")) return;
        event.preventDefault();
        try {
            window.pastePosterGraphic(editor, selection.getRangeAt(0).cloneRange(), html);
        } catch (error) {
            say(error.message);
        }
    });

    function select(graphic) {
        if (selected === graphic) {
            deselect();
            return;
        }
        deselect();
        const editor = editorFor(graphic);
        if (!editor) return;
        selected = graphic;
        updatePlacementControls();
        editor.focus({preventScroll: true});
        const range = document.createRange();
        range.selectNode(graphic);
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        overlay = document.createElement("div");
        overlay.className = "graphic-controls";
        const move = document.createElement("button");
        move.type = "button";
        move.className = "graphic-move";
        move.textContent = "Move";
        move.setAttribute("aria-label", "Move graphic");
        move.addEventListener("pointerdown", event => begin(event, "move"));
        overlay.append(move);
        for (const direction of directions) {
            const handle = document.createElement("button");
            handle.type = "button";
            handle.className = `graphic-handle graphic-handle-${direction}`;
            handle.setAttribute("aria-label", `Resize graphic ${direction}`);
            handle.addEventListener("pointerdown", event => begin(event, direction));
            overlay.append(handle);
        }
        document.body.append(overlay);
        updateOverlay();
    }

    function resized() {
        const image = selected.querySelector("img");
        if (!image?.naturalWidth || !image.naturalHeight) return;
        const canvasScale = canvas.getBoundingClientRect().width / canvas.offsetWidth;
        if (!canvasScale) return;
        const rect = selected.getBoundingClientRect();
        const dpi = Math.min(image.naturalWidth * 96 * canvasScale / rect.width,
            image.naturalHeight * 96 * canvasScale / rect.height);
        if (dpi >= safeDpi) return;
        selected.classList.remove("graphic-low-resolution");
        void selected.offsetWidth;
        selected.classList.add("graphic-low-resolution");
        selected.addEventListener("animationend", () => selected?.classList.remove("graphic-low-resolution"), {once: true});
        say(`${document.body.dataset.userName || "Your"}${document.body.dataset.userName ? ", your" : ""} graphic is likely to be blurry to most viewers.`);
    }

    function drop(event) {
        const destination = document.elementFromPoint(event.clientX, event.clientY)
            ?.closest(".content[contenteditable='true'][data-section]");
        if (!destination || !layout.contains(destination)) return;
        if (!canPlace(selected, destination)) {
            say("That graphic cannot be placed in this poster section.");
            return;
        }
        const original = editorFor(selected);
        const bounds = destination.getBoundingClientRect();
        const x = (event.clientX - bounds.left) / bounds.width;
        const y = (event.clientY - bounds.top) / bounds.height;
        const horizontal = x < 0.28 ? "left" : x > 0.72 ? "right" : "center";
        const vertical = y < 0.18 ? "top" : y > 0.82 ? "bottom" : "middle";
        const placement = vertical === "middle"
            ? horizontal === "center" ? "free" : `${horizontal}-middle`
            : `${vertical}-${horizontal}`;
        selected.dataset.placement = placement;
        if (vertical === "top") destination.prepend(selected);
        else if (vertical === "bottom") destination.append(selected);
        else {
            const range = document.caretRangeFromPoint(event.clientX, event.clientY);
            if (range && destination.contains(range.startContainer)
                && !selected.contains(range.startContainer)) {
                range.collapse(true);
                range.insertNode(selected);
            } else if (original !== destination) {
                destination.append(selected);
            }
        }
        if (original && original !== destination) original.dispatchEvent(new Event("input", {bubbles: true}));
        destination.dispatchEvent(new Event("input", {bubbles: true}));
    }

    function begin(event, direction) {
        if (event.button !== 0 || !selected || operation) return;
        event.preventDefault();
        const graphic = selected;
        const rect = graphic.getBoundingClientRect();
        const width = graphic.offsetWidth;
        const height = graphic.offsetHeight;
        if (!width || !height || !rect.width || !rect.height) return;
        operation = {pointer: event.pointerId, direction, x: event.clientX, y: event.clientY,
            width, height, scaleX: rect.width / width, scaleY: rect.height / height};
        const move = moveEvent => {
            if (!operation || moveEvent.pointerId !== operation.pointer || direction === "move") return;
            const dx = (moveEvent.clientX - operation.x) / operation.scaleX;
            const dy = (moveEvent.clientY - operation.y) / operation.scaleY;
            const newWidth = direction.includes("e") ? width + dx : direction.includes("w") ? width - dx : width;
            const newHeight = direction.includes("s") ? height + dy : direction.includes("n") ? height - dy : height;
            graphic.dataset.width = String(Math.round(Math.max(16, Math.min(10000, newWidth))));
            graphic.dataset.height = String(Math.round(Math.max(16, Math.min(10000, newHeight))));
            graphic.style.width = `${graphic.dataset.width}px`;
            graphic.style.height = `${graphic.dataset.height}px`;
            updateOverlay();
        };
        const end = endEvent => {
            if (!operation || endEvent.pointerId !== operation.pointer) return;
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", end);
            window.removeEventListener("pointercancel", end);
            operation = null;
            if (endEvent.type === "pointercancel") {
                graphic.dataset.width = String(width);
                graphic.dataset.height = String(height);
                graphic.style.width = `${width}px`;
                graphic.style.height = `${height}px`;
                updateOverlay();
                return;
            }
            if (direction === "move") drop(endEvent);
            else {
                editorFor(graphic)?.dispatchEvent(new Event("input", {bubbles: true}));
                resized();
            }
            updateOverlay();
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", end);
        window.addEventListener("pointercancel", end);
    }

    layout.addEventListener("click", event => {
        const graphic = event.target instanceof Element ? event.target.closest(".graphic[data-sid]") : null;
        if (graphic && layout.contains(graphic)) {
            event.preventDefault();
            select(graphic);
        } else if (selected) {
            deselect();
        }
    });
    for (const [button, , placement] of placementControls) {
        button.addEventListener("click", () => {
            if (!selected?.isConnected) {
                deselect();
                return;
            }
            selected.dataset.placement = placement;
            editorFor(selected)?.dispatchEvent(new Event("input", {bubbles: true}));
            updatePlacementControls();
            updateOverlay();
        });
    }
    updatePlacementControls();
    window.addEventListener("resize", updateOverlay);
    document.addEventListener("scroll", updateOverlay, true);
    document.addEventListener("poster-layout-updated", updateOverlay);
})();
