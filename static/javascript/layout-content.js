(() => {
    "use strict";

    const status = document.getElementById("content-save-status");
    const layout = document.getElementById("layout");
    let statusVersion = 0;
    const states = new Map();
    let fitting = false;

    function save(element, state) {
        if (state.saving || !state.dirty || !state.ready) {
            return state.pending;
        }

        const version = state.version;
        const value = window.mathJaxSource?.serialize(element) ?? element.innerHTML;
        state.dirty = false;
        state.ready = false;
        state.saving = true;

        state.pending = fetch("/layout/content", {
            method: "POST",
            credentials: "same-origin",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                ...(layout ? {pid: Number(layout.dataset.pid)} : {}),
                section: element.dataset.section, content: value,
            }),
        }).then(async response => {
            let result;
            try {
                result = await response.json();
            } catch {
                throw new Error("The server returned an invalid response.");
            }
            if (!response.ok || result.ok !== true) {
                throw new Error(result.detail || "The section could not be saved.");
            }
            if (typeof result.content !== "string") {
                throw new Error("The server returned an invalid saved section.");
            }
            state.savedLocal = value;
            state.savedContent = result.content;
            if (version === statusVersion) {
                status.textContent = "Changes saved.";
            }
            document.dispatchEvent(new Event("poster-layout-updated"));
        }).catch(error => {
            state.dirty = true;
            console.error(`Could not save poster section ${element.dataset.section}`, error);
            if (version === statusVersion) {
                status.textContent = "Changes could not be saved. Please try again.";
            }
        }).finally(() => {
            state.saving = false;
            if (state.dirty && state.ready) {
                save(element, state);
            }
        });
        return state.pending;
    }

    for (const element of document.querySelectorAll(".content[contenteditable='true'][data-section]")) {
        const state = {
            dirty: false,
            ready: false,
            saving: false,
            timer: null,
            version: 0,
            pending: null,
            savedLocal: null,
            savedContent: null,
        };
        states.set(element, state);
        element.addEventListener("input", () => {
            state.dirty = true;
            state.ready = false;
            state.version = ++statusVersion;
            status.textContent = "Saving changes...";
            clearTimeout(state.timer);
            state.timer = setTimeout(() => {
                state.timer = null;
                state.ready = true;
                save(element, state);
            }, 300);
        });
        element.addEventListener("focusin", () => window.mathJaxSource?.restore(element));
        element.addEventListener("focusout", () => window.mathJaxSource?.typeset(element));
    }

    async function flush() {
        for (const [element, state] of states) {
            clearTimeout(state.timer);
            state.ready = true;
            if (state.saving) await state.pending;
            await save(element, state);
            if (state.dirty || state.saving) {
                throw new Error("Save your text changes before using auto-fit. Please try again.");
            }
        }
    }

    function displayed(value) {
        const body = document.createElement("div");
        body.innerHTML = value;
        if (body.firstElementChild?.matches("h2, h3")) body.firstElementChild.remove();
        return body.innerHTML;
    }

    async function applyRevisions(payload) {
        const layout = document.getElementById("layout");
        if (!payload || payload.pid !== Number(layout?.dataset.pid)
            || !payload.revisions || typeof payload.revisions !== "object" || Array.isArray(payload.revisions)
            || !payload.expected || typeof payload.expected !== "object" || Array.isArray(payload.expected)) {
            throw new Error("Auto-fit returned an invalid poster update.");
        }
        const updates = [];
        for (const [name, text] of Object.entries(payload.revisions)) {
            const element = [...states.keys()].find(item => item.dataset.section === name);
            const state = states.get(element);
            const current = element && (window.mathJaxSource?.serialize(element) ?? element.innerHTML);
            const expected = typeof payload.expected[name] === "string"
                ? displayed(payload.expected[name]) : null;
            if (typeof text !== "string" || typeof payload.expected[name] !== "string"
                || !element || state.dirty || state.saving
                || (current !== expected && !(current === state.savedLocal
                    && typeof state.savedContent === "string"
                    && displayed(state.savedContent) === expected))) {
                throw new Error("Your text changed during auto-fit. Copy your edits and reload to see the saved revision.");
            }
            updates.push([element, text]);
        }
        for (const [element, text] of updates) {
            element.innerHTML = displayed(text);
            const state = states.get(element);
            state.savedLocal = null;
            state.savedContent = null;
            await window.mathJaxSource?.typeset(element);
        }
        if (updates.length) document.dispatchEvent(new Event("poster-layout-updated"));
    }

    window.registerWikkitCommands?.({
        commands: {"poster-revisions": {does: applyRevisions}},
    });

    window.autoFitPoster = async function autoFitPoster(sections = null) {
        if (fitting) return;
        const layout = document.getElementById("layout");
        const controls = document.getElementById("controls");
        fitting = true;
        const inert = layout.inert;
        const controlsInert = controls?.inert;
        layout.inert = true;
        if (controls) controls.inert = true;
        try {
            await flush();
            status.textContent = "Wikkit is fitting your text...";
            const response = await fetch(`/magic/${layout.dataset.pid}/auto-fit`, {
                method: "POST",
                credentials: "same-origin",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({sections}),
            });
            const result = await response.json();
            if (!response.ok || result.ok !== true) {
                throw new Error(result.detail || "Your poster could not be auto-fitted.");
            }
            if (result.command?.does !== "poster-revisions") {
                throw new Error("Auto-fit returned an invalid command. Reload to check your saved text.");
            }
            await applyRevisions(result.command.payload);
            if (result.say) window.wikkit?.say(result.say);
            status.textContent = result.cribbed?.length
                ? "Some sections could not be shortened safely. See Wikkit's suggestions."
                : Object.keys(result.command.payload.revisions).length
                    ? "Auto-fitted text saved." : "Your selected sections already fit.";
        } catch (error) {
            console.error("Could not auto-fit poster", error);
            status.textContent = error.message;
            window.wikkit?.say("<p>I couldn&rsquo;t finish auto-fit. Please check the editor status and try again.</p>");
        } finally {
            layout.inert = inert;
            if (controls) controls.inert = controlsInert;
            fitting = false;
        }
    };

    document.getElementById("auto-fit")?.addEventListener("click", () => window.autoFitPoster());
    window.addEventListener("beforeunload", event => {
        if (fitting) {
            event.preventDefault();
            event.returnValue = "";
        }
    });
})();
