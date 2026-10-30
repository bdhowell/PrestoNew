(() => {
    "use strict";

    const toolbar = document.getElementById("editor-controls");
    if (!toolbar) return;

    const extended = document.getElementById("extended");
    const extendedControls = document.getElementById("extended-controls");
    if (extended && extendedControls) {
        extended.addEventListener("click", () => {
            extendedControls.hidden = !extendedControls.hidden;
            extended.setAttribute("aria-expanded", String(!extendedControls.hidden));
        });
    }
    const commands = {
        bold: "bold",
        italic: "italic",
        underline: "underline",
        strikethrough: "strikeThrough",
        superscript: "superscript",
        subscript: "subscript",
        ullist: "insertUnorderedList",
        ollist: "insertOrderedList",
        code: null,
        plaintext: "removeFormat",
    };
    const clipboardActions = ["cut", "copy", "paste"];
    const buttons = [...Object.keys(commands), ...clipboardActions]
        .map(id => document.getElementById(id));
    let savedRange = null;
    let activeEditor = null;
    let placeholder = null;
    let insertingCode = false;

    function editorFor(range) {
        const element = range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
            ? range.commonAncestorContainer : range.commonAncestorContainer.parentElement;
        const editor = element.closest(".content[contenteditable='true']");
        return editor && editor.contains(range.startContainer)
            && editor.contains(range.endContainer) ? editor : null;
    }

    function codeFor(range) {
        const element = range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
            ? range.commonAncestorContainer : range.commonAncestorContainer.parentElement;
        const code = element.closest("code");
        return code && activeEditor.contains(code) ? code : null;
    }

    function update() {
        const selection = window.getSelection();
        const range = selection.rangeCount ? selection.getRangeAt(0) : null;
        const editor = range && editorFor(range);
        const inToolbar = toolbar.contains(document.activeElement);
        if (editor && (editor.contains(document.activeElement) || inToolbar)) {
            activeEditor = editor;
            savedRange = range.cloneRange();
        } else if (!inToolbar) {
            activeEditor = null;
            savedRange = null;
        }
        for (const button of buttons) {
            button.disabled = !savedRange
                || (["plaintext", "cut", "copy"].includes(button.id) && savedRange.collapsed);
            if (button.id !== "plaintext" && !clipboardActions.includes(button.id)) {
                const pressed = savedRange && (button.id === "code"
                    ? Boolean(codeFor(savedRange)) : document.queryCommandState(commands[button.id]));
                button.setAttribute("aria-pressed", String(Boolean(pressed)));
            }
        }
    }

    function html(node) {
        const container = document.createElement("div");
        container.append(node);
        return container.innerHTML;
    }

    function unwrapCodes(fragment) {
        for (const code of fragment.querySelectorAll("code")) {
            code.replaceWith(...code.childNodes);
        }
    }

    function clipboardHTML(value) {
        const document = new DOMParser().parseFromString(value, "text/html");
        const allowed = new Set([
            "P", "BR", "H2", "H3", "STRONG", "B", "EM", "I", "U", "S", "STRIKE",
            "CODE", "UL", "OL", "LI", "SUB", "SUP", "A",
        ]);
        for (const element of [...document.body.querySelectorAll("*")].reverse()) {
            if (element.tagName === "SPAN") {
                const style = element.style;
                const formats = [
                    ["b", style.fontWeight === "bold" || Number(style.fontWeight) >= 600],
                    ["i", style.fontStyle === "italic"],
                    ["u", style.textDecorationLine.includes("underline")],
                    ["strike", style.textDecorationLine.includes("line-through")],
                    ["sup", style.verticalAlign === "super"],
                    ["sub", style.verticalAlign === "sub"],
                ];
                for (const [tag, enabled] of formats) {
                    if (!enabled) continue;
                    const wrapper = document.createElement(tag);
                    wrapper.append(...element.childNodes);
                    element.append(wrapper);
                }
            }
            if (["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "TEMPLATE"].includes(element.tagName)) {
                element.remove();
                continue;
            }
            if (element.tagName === "DIV") {
                const paragraph = document.createElement("p");
                paragraph.append(...element.childNodes);
                element.replaceWith(paragraph);
                continue;
            }
            if (!allowed.has(element.tagName)) {
                element.replaceWith(...element.childNodes);
                continue;
            }
            for (const attribute of [...element.attributes]) {
                const link = element.tagName === "A" && attribute.name === "href";
                const start = element.tagName === "OL" && attribute.name === "start";
                if ((!link && !start) || (link && !/^(https?:|mailto:)/i.test(attribute.value.trim()))) {
                    element.removeAttribute(attribute.name);
                }
            }
        }
        return document.body.innerHTML;
    }

    async function paste(editor, range) {
        const originalHTML = editor.innerHTML;
        if (!navigator.clipboard) {
            throw new Error("Clipboard access is unavailable. Use your keyboard shortcut to paste.");
        }
        let value = "";
        let command = "insertText";
        if (navigator.clipboard.read) {
            const items = await navigator.clipboard.read();
            const rich = items.find(item => item.types.includes("text/html"));
            const plain = items.find(item => item.types.includes("text/plain"));
            if (rich) {
                value = clipboardHTML(await (await rich.getType("text/html")).text());
                command = "insertHTML";
            } else if (plain) {
                value = await (await plain.getType("text/plain")).text();
            } else {
                throw new Error("The clipboard does not contain supported text.");
            }
        } else {
            value = await navigator.clipboard.readText();
        }
        // A permission prompt can outlive the original selection or editor.
        if (!editor.isConnected || editor.innerHTML !== originalHTML
            || editorFor(range) !== editor || activeEditor !== editor
            || !savedRange || ["startContainer", "startOffset", "endContainer", "endOffset"]
                .some(key => savedRange[key] !== range[key])) {
            throw new Error("The selection changed. Select where to paste and try again.");
        }
        editor.focus({preventScroll: true});
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        if (value && !document.execCommand(command, false, value)) {
            throw new Error("Paste is not supported by this browser. Use your keyboard shortcut.");
        }
        if (value) editor.dispatchEvent(new Event("input", {bubbles: true}));
    }

    function toggleCode(range) {
        const code = codeFor(range);
        const selected = range.cloneContents();
        unwrapCodes(selected);
        const marker = document.createElement("span");
        marker.id = "editor-selection-marker";
        marker.append(selected);
        if (code) {
            let ancestor = range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
                ? range.commonAncestorContainer : range.commonAncestorContainer.parentElement;
            while (ancestor !== code) {
                const wrapper = ancestor.cloneNode(false);
                wrapper.append(...marker.childNodes);
                marker.append(wrapper);
                ancestor = ancestor.parentElement;
            }
        }
        if (range.collapsed) marker.textContent = "\u200b";

        let replacement;
        if (code) {
            // Split the enclosing code so a partial toggle leaves its neighbors intact.
            const before = document.createRange();
            before.selectNodeContents(code);
            before.setEnd(range.startContainer, range.startOffset);
            const after = document.createRange();
            after.selectNodeContents(code);
            after.setStart(range.endContainer, range.endOffset);
            const fragment = document.createDocumentFragment();
            for (const part of [before.cloneContents(), marker, after.cloneContents()]) {
                if (part === marker) {
                    fragment.append(part);
                } else if (part.textContent || part.querySelector("br, img")) {
                    const wrapper = code.cloneNode(false);
                    wrapper.append(part);
                    fragment.append(wrapper);
                }
            }
            replacement = html(fragment);
            range.selectNode(code);
        } else {
            const wrapper = document.createElement("code");
            wrapper.append(marker);
            replacement = html(wrapper);
        }

        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        insertingCode = true;
        try {
            if (!document.execCommand("insertHTML", false, replacement)) {
                throw new Error("Code formatting is not supported by this browser.");
            }
        } finally {
            insertingCode = false;
        }
        const inserted = activeEditor.querySelector("#editor-selection-marker");
        const result = document.createRange();
        result.selectNodeContents(inserted);
        if (result.toString() === "\u200b") placeholder = inserted.firstChild;
        const children = [...inserted.childNodes];
        inserted.replaceWith(...children);
        const first = children[0];
        const last = children[children.length - 1];
        if (first.nodeType === Node.TEXT_NODE) result.setStart(first, 0);
        else result.setStartBefore(first);
        if (last.nodeType === Node.TEXT_NODE) result.setEnd(last, last.length);
        else result.setEndAfter(last);
        selection.removeAllRanges();
        selection.addRange(result);
    }

    toolbar.addEventListener("mousedown", event => {
        if (event.target.closest("button")) event.preventDefault();
    });
    toolbar.addEventListener("click", async event => {
        const button = event.target.closest("button");
        if (!button || (!(button.id in commands) && !clipboardActions.includes(button.id))
            || button.disabled || !savedRange) return;
        activeEditor.focus({preventScroll: true});
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(savedRange);
        try {
            if (button.id === "paste") {
                await paste(activeEditor, savedRange.cloneRange());
            } else if (button.id === "copy" || button.id === "cut") {
                if (!document.execCommand(button.id)) {
                    throw new Error("Clipboard access was denied. Allow access or use your keyboard shortcut.");
                }
                if (button.id === "cut") {
                    activeEditor.dispatchEvent(new Event("input", {bubbles: true}));
                }
            } else if (button.id === "code") {
                toggleCode(savedRange.cloneRange());
            } else {
                if (!document.execCommand(commands[button.id], false, null)) {
                    throw new Error("This formatting action is not supported by this browser.");
                }
                if (button.id === "plaintext") document.execCommand("unlink", false, null);
            }
            if (!clipboardActions.includes(button.id) && !placeholder) {
                activeEditor.dispatchEvent(new Event("input", {bubbles: true}));
            }
        } catch (error) {
            console.error("Could not edit poster content", error);
            document.getElementById("content-save-status").textContent =
                error.name === "NotAllowedError"
                    ? "Clipboard access was denied. Allow access or use your keyboard shortcut."
                    : error.message;
        }
        update();
    });

    document.addEventListener("keydown", event => {
        if (event.key === "Escape" && extended && extendedControls && !extendedControls.hidden) {
            extendedControls.hidden = true;
            extended.setAttribute("aria-expanded", "false");
            if (extendedControls.contains(document.activeElement)) extended.focus();
            event.preventDefault();
        }
    });

    document.addEventListener("input", event => {
        if (insertingCode) {
            event.stopImmediatePropagation();
            return;
        }
        if (placeholder && placeholder.isConnected) {
            const index = placeholder.data.indexOf("\u200b");
            if (index !== -1) placeholder.deleteData(index, 1);
        }
        placeholder = null;
    }, true);
    document.addEventListener("selectionchange", update);
    document.addEventListener("focusin", update);
    update();
})();
