(() => {
    "use strict";

    const status = document.getElementById("content-save-status");
    let statusVersion = 0;

    function save(element, state) {
        if (state.saving || !state.dirty || !state.ready) {
            return;
        }

        const version = state.version;
        const value = element.innerHTML;
        state.dirty = false;
        state.ready = false;
        state.saving = true;

        fetch("/layout/content", {
            method: "POST",
            credentials: "same-origin",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({section: element.dataset.section, content: value}),
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
            if (version === statusVersion) {
                status.textContent = "Changes saved.";
            }
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
    }

    for (const element of document.querySelectorAll(".content[contenteditable='true'][data-section]")) {
        const state = {
            dirty: false,
            ready: false,
            saving: false,
            timer: null,
            version: 0,
        };
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
    }
})();
