(() => {
    "use strict";

    const status = document.getElementById("content-save-status");
    const layout = document.getElementById("layout");
    let statusVersion = 0;
    const states = new Map();
    let generation = 0;
    let currentRun = null;
    let queue = [];
    let active = null;
    let controller = null;
    let starting = null;
    let cancellation = Promise.resolve();
    const suggestions = new Map();
    const requested = new Set();
    const bulkRequested = new Set();

    window.explainManualPosterAdjustment = function explainManualPosterAdjustment(section) {
        const message = section?.dataset.manualAdjustment;
        if (!message) return false;
        const paragraph = document.createElement("p");
        paragraph.textContent = message;
        if (typeof window.wikkit?.say === "function") {
            window.wikkit.say(paragraph.outerHTML);
        } else if (status) {
            status.textContent = message;
        }
        return true;
    };

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
            if (state.version === version) state.ready = false;
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
    window.flushPosterContent = flush;

    function displayed(value) {
        const body = document.createElement("div");
        body.innerHTML = value;
        if (body.firstElementChild?.matches("h2, h3")) body.firstElementChild.remove();
        return body.innerHTML;
    }

    async function applyRevisions(payload, notify = true) {
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
        if (updates.length && notify) document.dispatchEvent(new Event("poster-layout-updated"));
    }

    window.registerWikkitCommands?.({
        commands: {"poster-revisions": {does: applyRevisions}},
    });

    async function post(action, body, signal) {
        const response = await fetch(`/magic/${layout.dataset.pid}/auto-fit${action}`, {
            method: "POST", credentials: "same-origin",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(body), signal,
        });
        const result = await response.json();
        if (!response.ok || result.ok !== true) {
            const detail = result.detail;
            const error = new Error(typeof detail === "string" ? detail
                : detail?.message || "Your poster could not be auto-fitted.");
            error.detail = detail;
            throw error;
        }
        return result;
    }

    function report(error) {
        console.error("Could not auto-fit poster", error);
        status.textContent = error.message;
        window.wikkit?.say("<p>I couldn&rsquo;t finish auto-fit. Please check the editor status and try again.</p>");
    }

    window.cancelFitQueue = function cancelFitQueue({preserveApplied = false} = {}) {
        ++generation;
        queue = [];
        requested.clear();
        if (!preserveApplied) bulkRequested.clear();
        suggestions.clear();
        controller?.abort();
        const run = currentRun;
        const pending = starting;
        currentRun = null;
        starting = null;
        cancellation = cancellation.then(async () => {
            const id = run || (pending && await pending);
            if (!id) return;
            await post("/cancel", {run_id: id, cancel_only: true});
            if (preserveApplied) return;
            await flush();
            const local = new Map([...states].map(([element]) => [
                element.dataset.section,
                window.mathJaxSource?.serialize(element) ?? element.innerHTML,
            ]));
            let result = await post("/cancel", {run_id: id});
            const decisions = {};
            for (const [name, expected] of Object.entries(result.conflicts)) {
                decisions[name] = {
                    expected,
                    overwrite: window.confirm(`The ${name} section has changed. Overwrite your edits to undo its auto-fit?`),
                };
            }
            if (Object.keys(decisions).length) {
                result = await post("/cancel", {run_id: id, decisions});
            }
            if (Object.keys(result.command.payload.revisions).length) {
                for (const name of Object.keys(result.command.payload.revisions)) {
                    result.command.payload.expected[name] = local.get(name);
                }
                await applyRevisions(result.command.payload, false);
            }
        });
        cancellation.catch(report);
        return cancellation;
    };
    window.waitFitCancellation = () => cancellation;

    async function applySuggestion(name, result, epoch) {
        await flush();
        if (epoch !== generation) return;
        const element = [...states.keys()].find(item => item.dataset.section === name);
        const state = states.get(element);
        if (!element || !state) throw new Error("The fitted section is no longer available.");
        const local = window.mathJaxSource?.serialize(element) ?? element.innerHTML;
        const original = result.command.payload.expected[name];
        const current = local === state.savedLocal && state.savedContent !== null
            ? state.savedContent
            : displayed(local) === displayed(original) ? result.sizings[name].expected_text : local;
        const changed = displayed(current) !== displayed(original);
        if (changed && !window.confirm(`The ${name} section has changed. Overwrite your edits with the auto-fit suggestion?`)) return;
        let saved;
        try {
            saved = await post("/apply", {
                run_id: currentRun, sizing_id: result.sizings[name].sizing_id,
                expected: current, overwrite: changed,
            });
        } catch (error) {
            const remote = error.detail?.current;
            if (typeof remote !== "string") throw error;
            if (!window.confirm(`The ${name} section has changed elsewhere. Overwrite those edits with the auto-fit suggestion?`)) return;
            if (epoch !== generation) return;
            saved = await post("/apply", {
                run_id: currentRun, sizing_id: result.sizings[name].sizing_id,
                expected: remote, overwrite: true,
            });
            saved.command.payload.expected[name] = local;
        }
        if (epoch !== generation) return;
        await applyRevisions(saved.command.payload, false);
        suggestions.delete(name);
        bulkRequested.delete(name);
        status.textContent = "Auto-fitted text saved.";
        if (epoch !== generation) return;
        await window.cancelFitQueue({preserveApplied: true});
        document.dispatchEvent(new CustomEvent("poster-layout-updated", {
            detail: {preserveFitRequests: true},
        }));
    }

    async function processQueue(epoch) {
        if (active) return;
        while (epoch === generation && queue.length) {
            const name = queue.shift();
            active = name;
            controller = new AbortController();
            try {
                const result = await post("", {run_id: currentRun, sections: [name]}, controller.signal);
                if (epoch !== generation) break;
                if (result.sizings?.[name]) {
                    suggestions.set(name, result);
                    if (requested.delete(name)) await applySuggestion(name, result, epoch);
                } else if ((result.cribbed?.length || requested.delete(name)) && result.say) {
                    window.wikkit?.say(result.say);
                }
                if (!result.sizings?.[name]) {
                    bulkRequested.delete(name);
                    requested.delete(name);
                }
            } catch (error) {
                if (epoch === generation && error.name !== "AbortError") report(error);
            } finally {
                active = null;
                controller = null;
            }
        }
        if (epoch !== generation && queue.length) processQueue(generation);
    }

    async function enqueue(name) {
        const epoch = generation;
        await cancellation;
        if (epoch !== generation) return;
        await flush();
        if (epoch !== generation) return;
        queue = [name, ...queue.filter(item => item !== name)];
        if (!currentRun) {
            starting ||= post("/start", {}).then(result => result.run_id);
            const id = await starting;
            if (epoch !== generation) return;
            starting = null;
            currentRun = id;
        }
        processQueue(epoch);
    }

    window.queueFitProfile = async function queueFitProfile(profile) {
        const epoch = generation;
        await cancellation;
        if (epoch !== generation) return;
        await flush();
        if (epoch !== generation) return;
        const sections = [...new Set([...profile.overflowing_sections, ...(profile.sparse_sections ?? [])].map(name => {
            const section = document.getElementById(name === "conclusion" ? "conclusions" : name);
            if (section?.hasAttribute("data-manual-adjustment")) return null;
            return section?.querySelector(".content[contenteditable='true'][data-section]")?.dataset.section;
        }).filter(Boolean))];
        if (!sections.length) return;
        queue = sections;
        for (const name of bulkRequested) {
            if (sections.includes(name)) requested.add(name);
            else bulkRequested.delete(name);
        }
        starting = post("/start", {}).then(result => result.run_id);
        const id = await starting;
        if (epoch !== generation) return;
        starting = null;
        currentRun = id;
        processQueue(epoch);
    };

    window.autoFitPoster = async function autoFitPoster(sections = null) {
        try {
            const names = sections || [...suggestions.keys(), ...queue, ...(active ? [active] : [])];
            if (sections === null) {
                for (const name of names) bulkRequested.add(name);
            }
            for (const name of new Set(names)) {
                const section = [...states.keys()].find(item => item.dataset.section === name)?.closest(".section");
                if (window.explainManualPosterAdjustment(section)) continue;
                const result = suggestions.get(name);
                if (result) {
                    await applySuggestion(name, result, generation);
                    break;
                } else if (name === active || queue.includes(name)) {
                    requested.add(name);
                    if (name !== active) {
                        queue = [name, ...queue.filter(item => item !== name)];
                    }
                } else if (sections !== null) {
                    // Sections the profile did not flag can still be fitted on request.
                    requested.add(name);
                    await enqueue(name);
                }
            }
        } catch (error) {
            report(error);
        }
    };

    document.getElementById("auto-fit")?.addEventListener("click", () => window.autoFitPoster());
    window.addEventListener("beforeunload", event => {
        if (active || queue.length) {
            event.preventDefault();
            event.returnValue = "";
        }
    });
})();
