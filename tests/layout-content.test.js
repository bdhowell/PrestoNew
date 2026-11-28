const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const {test} = require("node:test");
const vm = require("node:vm");

const source = readFileSync(new URL("../static/javascript/layout-content.js", `file://${__filename}`), "utf8");

async function settle() {
    for (let i = 0; i < 20; i++) await Promise.resolve();
}

function deferred() {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return {promise, resolve};
}

function harness() {
    const events = {};
    const timers = new Map();
    const saves = [];
    const requests = [];
    const errors = [];
    const status = {textContent: ""};
    const section = {querySelector: () => editor, hasAttribute: () => false};
    const editor = {
        dataset: {section: "methods"},
        innerHTML: "Original text",
        addEventListener: (name, handler) => { events[name] = handler; },
        closest: () => section,
    };
    const layout = {dataset: {pid: "1"}};
    const window = {addEventListener() {}};
    let timerId = 0;
    vm.runInNewContext(source, {
        window,
        document: {
            getElementById: id => id === "layout" ? layout
                : id === "methods" ? section : id === "content-save-status" ? status : null,
            querySelectorAll: () => [editor],
            dispatchEvent() {},
        },
        Event,
        AbortController,
        console: {error: (...args) => errors.push(args)},
        setTimeout: handler => {
            timers.set(++timerId, handler);
            return timerId;
        },
        clearTimeout: id => timers.delete(id),
        fetch: async (url, options) => {
            const body = JSON.parse(options.body);
            requests.push({url, body});
            if (url === "/layout/content") {
                const response = deferred();
                saves.push({body, ...response});
                return response.promise;
            }
            return {ok: true, json: async () => url.endsWith("/start")
                ? {ok: true, run_id: "run-1"}
                : {ok: true, conflicts: {}, command: {payload: {revisions: {}}}}};
        },
    });
    return {
        window, saves, requests, errors, timers,
        input(value) {
            editor.innerHTML = value;
            events.input();
        },
        saveResponse(index, ok = true) {
            saves[index].resolve({
                ok,
                json: async () => ok ? {ok: true, content: saves[index].body.content}
                    : {ok: false, detail: "Text save failed"},
            });
        },
    };
}

test("flushing waits for a follow-up save started by an in-flight save", async () => {
    const h = harness();
    h.input("First edit");
    const firstFlush = h.window.flushPosterContent();
    h.input("Second edit");
    const secondFlush = h.window.flushPosterContent();
    h.saveResponse(0);
    await settle();
    assert.equal(h.saves.length, 2);
    assert.equal(h.saves[1].body.content, "Second edit");
    let finished = false;
    secondFlush.then(() => { finished = true; });
    await settle();
    assert.equal(finished, false);
    h.saveResponse(1);
    await Promise.all([firstFlush, secondFlush]);
    assert.deepEqual(h.errors, []);
});

test("failed cancellation remains observable but does not poison later cancellation and profiling", async () => {
    const h = harness();
    const profile = {overflowing_sections: ["methods"]};
    await h.window.queueFitProfile(profile);
    await settle();
    h.input("Edited text");
    const failed = h.window.cancelFitQueue();
    const rejected = assert.rejects(failed, /Save your text changes/);
    await settle();
    h.saveResponse(0, false);
    await rejected;
    await assert.rejects(h.window.waitFitCancellation(), /Save your text changes/);
    assert.equal(h.errors.length, 2);

    const retry = h.window.flushPosterContent();
    h.saveResponse(1);
    await retry;
    await h.window.cancelFitQueue();
    await h.window.waitFitCancellation();
    await h.window.queueFitProfile(profile);
    assert.equal(h.requests.filter(request => request.url.endsWith("/start")).length, 2);
    assert.equal(h.errors.length, 2);
});
