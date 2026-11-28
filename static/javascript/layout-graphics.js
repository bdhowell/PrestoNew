(() => {
    "use strict";

    const imageButton = document.getElementById("image");
    const dialog = document.getElementById("graphics-dialog");
    const preview = document.getElementById("graphics-preview");
    const grid = document.getElementById("graphics-grid");
    const status = document.getElementById("graphics-status");
    const insert = document.getElementById("graphics-insert");
    const layout = document.getElementById("layout");
    if (!imageButton || !dialog || !preview || !grid || !status || !insert || !layout) return;

    let target = null;
    let selected = null;
    let qrBusy = false;

    function say(message) {
        const paragraph = document.createElement("p");
        paragraph.textContent = message;
        if (window.wikkit?.say) window.wikkit.say(paragraph.outerHTML);
        else document.getElementById("content-save-status").textContent = message;
    }

    window.posterGraphicSectionAvailable = editor => {
        if (!editor.querySelector(".graphic")) return true;
        const name = document.body.dataset.userName || "Friend";
        say(`${name}, you must first cut or otherwise delete the existing graphic in the ${editor.dataset.section} section before you insert a new graphic.`);
        return false;
    };

    function graphicContents(graphic) {
        if (graphic.type === "math") {
            const contents = document.createDocumentFragment();
            for (const expression of graphic.math_expressions) {
                const math = document.createElement("span");
                math.className = "graphic-math";
                math.textContent = `\\[${expression}\\]`;
                contents.append(math);
            }
            if (!graphic.math_expressions.length) {
                const message = document.createElement("span");
                message.textContent = "No LaTeX is available for this source.";
                contents.append(message);
            }
            return contents;
        }
        const image = document.createElement("img");
        image.src = `/layout/source?sid=${encodeURIComponent(graphic.sid)}`;
        image.alt = graphic.filename;
        return image;
    }

    function select(graphic, button) {
        selected = graphic;
        for (const item of grid.querySelectorAll("button")) {
            item.setAttribute("aria-pressed", String(item === button));
        }
        insert.disabled = graphic.type === "math" && !graphic.math_expressions.length;
    }

    function showPreview(graphic) {
        const body = document.getElementById("graphics-preview-content");
        body.replaceChildren(graphicContents(graphic));
        document.getElementById("graphics-preview-title").textContent = graphic.filename;
        preview.showModal();
        window.mathJaxSource?.typeset(body);
    }

    async function createQr(editor) {
        if (qrBusy) return;
        qrBusy = true;
        let locked = false;
        try {
            if (!window.wikkit?.requestResponse) throw new Error("Wikkit is unavailable. Please try again.");
            const response = await fetch(`/layout/qrcode/status?pid=${encodeURIComponent(layout.dataset.pid)}`);
            const result = await response.json();
            if (!response.ok || result.ok !== true || typeof result.exists !== "boolean") {
                throw new Error(result.detail || "Your QR code could not be checked.");
            }
            const question = result.exists
                ? "A QR code already exists for this poster. Would you like to replace it?"
                : "Would you like to insert an internally generated QR code?";
            window.wikkit.say(`<p>Only internally generated QR codes can be inserted into Additional Information. ${question}</p>`
                + '<p><button data-sends="yes">Yes</button> <button data-sends="no">No</button></p>');
            let answer = "";
            for (let attempt = 0; attempt < 3; attempt++) {
                answer = (await window.wikkit.requestResponse()).trim().toLowerCase();
                if (answer === "yes" || answer === "no") break;
                say("Please answer yes or no.");
            }
            if (answer !== "yes") {
                if (result.exists && answer === "no") say("Okay, we won’t replace the existing QR code.");
                else if (answer !== "no") say("I couldn't confirm your choice, so I didn't change the QR code.");
                return;
            }
            window.wikkit.say("<p>Please provide the URL for the QR code.</p>");
            const url = (await window.wikkit.requestResponse()).trim();
            if (!url) return;
            if (!editor.isConnected) throw new Error("The section is no longer available. Please reload.");
            if (typeof window.flushPosterContent !== "function") {
                throw new Error("Poster changes cannot be saved. Please reload and try again.");
            }
            await window.flushPosterContent();
            editor.contentEditable = "false";
            locked = true;
            const saved = await fetch("/layout/qrcode", {
                method: "POST",
                credentials: "same-origin",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({pid: Number(layout.dataset.pid), url, replace: result.exists}),
            });
            const body = await saved.json();
            if (!saved.ok || body.ok !== true || typeof body.content !== "string") {
                throw new Error(body.detail || "Your QR code could not be saved.");
            }
            const container = document.createElement("div");
            container.innerHTML = body.content;
            if (container.firstElementChild?.matches("h2, h3")) container.firstElementChild.remove();
            editor.replaceChildren(...container.childNodes);
            document.dispatchEvent(new Event("poster-layout-updated"));
            say("Your QR code has been added to Additional Information.");
        } catch (error) {
            console.error("Could not insert poster QR code", error);
            say(error.message || "Your QR code could not be inserted. Please try again.");
        } finally {
            if (locked) editor.contentEditable = "true";
            qrBusy = false;
        }
    }

    imageButton.addEventListener("click", async () => {
        const selection = window.posterEditorSelection?.();
        const editor = selection?.editor;
        if (!editor || !selection.range || !layout.contains(editor)) {
            say("Please select a poster section into which to insert a graphic.");
            return;
        }
        const section = editor.dataset.section;
        if (["literature cited", "acknowledgements"].includes(section)) {
            say("Graphics cannot be inserted into Literature Cited or Acknowledgements. Those sections are reserved for specific content and should be kept as brief as possible.");
            return;
        }
        if (section === "additional information") {
            const existing = editor.querySelector(".graphic");
            if (existing && existing.dataset.type !== "qrcode") {
                window.posterGraphicSectionAvailable(editor);
                return;
            }
            await createQr(editor);
            return;
        }
        if (!window.posterGraphicSectionAvailable(editor)) return;
        target = {editor, range: selection.range, html: editor.innerHTML};
        selected = null;
        insert.disabled = true;
        grid.replaceChildren();
        status.textContent = "Loading your poster's source graphics...";
        dialog.showModal();
        try {
            const query = new URLSearchParams({pid: layout.dataset.pid, section});
            const response = await fetch(`/layout/graphics?${query}`, {credentials: "same-origin"});
            const result = await response.json();
            if (!response.ok || result.ok !== true || !Array.isArray(result.graphics)) {
                throw new Error(result.detail || "Your graphics could not be loaded.");
            }
            if (!dialog.open || target?.editor !== editor) return;
            status.textContent = result.graphics.length ? "" : "No suitable source graphics are available for this section.";
            for (const graphic of result.graphics) {
                const button = document.createElement("button");
                button.type = "button";
                button.className = "graphics-choice";
                button.setAttribute("aria-pressed", "false");
                const caption = document.createElement("span");
                caption.textContent = graphic.filename;
                button.append(graphicContents(graphic), caption);
                button.addEventListener("click", () => select(graphic, button));
                button.addEventListener("dblclick", () => showPreview(graphic));
                grid.append(button);
            }
            window.mathJaxSource?.typeset(grid);
        } catch (error) {
            console.error("Could not load poster graphics", error);
            status.textContent = error.message || "Your graphics could not be loaded. Please try again.";
        }
    });

    insert.addEventListener("click", () => {
        if (!target || !selected) return;
        const {editor, range, html} = target;
        if (!window.posterGraphicSectionAvailable(editor)) return;
        if (!editor.isConnected || editor.innerHTML !== html
            || !editor.contains(range.startContainer) || !editor.contains(range.endContainer)) {
            status.textContent = "The section changed. Close this dialog and select the insertion point again.";
            return;
        }
        const graphic = document.createElement("span");
        graphic.className = "graphic";
        graphic.contentEditable = "false";
        graphic.dataset.sid = String(selected.sid);
        graphic.dataset.type = selected.type;
        graphic.dataset.placement = "free";
        graphic.dataset.width = "240";
        graphic.style.width = "240px";
        graphic.append(graphicContents(selected));
        dialog.close();
        editor.focus({preventScroll: true});
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        if (!document.execCommand("insertHTML", false, graphic.outerHTML)) {
            say("Your browser could not insert this graphic. Please try again.");
            return;
        }
        editor.dispatchEvent(new Event("input", {bubbles: true}));
        window.mathJaxSource?.typeset(editor);
    });

    document.getElementById("graphics-cancel").addEventListener("click", () => dialog.close());
    document.getElementById("graphics-preview-close").addEventListener("click", () => preview.close());
    dialog.addEventListener("close", () => { target = null; });
})();
