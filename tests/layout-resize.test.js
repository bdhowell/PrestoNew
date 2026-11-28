const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const {test} = require("node:test");
const vm = require("node:vm");

const source = readFileSync(new URL("../static/javascript/layout-resize.js", `file://${__filename}`), "utf8");

async function harness() {
    const timers = new Map();
    const requests = [];
    const updates = [];
    const document = {activeElement: null};
    let timerId = 0;
    function element(rect = {}) {
        const listeners = {};
        const captures = new Set();
        return {
            children: [], dataset: {}, style: {setProperty() {}},
            offsetWidth: 1000, clientWidth: 1000,
            append(...children) { this.children.push(...children); },
            setAttribute() {},
            querySelector() { return null; },
            getBoundingClientRect: () => rect,
            addEventListener(name, handler) { listeners[name] = handler; },
            setPointerCapture: id => captures.add(id),
            hasPointerCapture: id => captures.has(id),
            releasePointerCapture: id => captures.delete(id),
            focus() { document.activeElement = this; },
            emit(type, properties = {}) {
                listeners[type]?.({
                    type, button: 0, pointerId: 1, clientX: 300, clientY: 100,
                    currentTarget: this, preventDefault() {}, ...properties,
                });
            },
        };
    }
    const layout = element({left: 0, top: 0, width: 1000, height: 600});
    layout.dataset = {
        resizeSettings: '{"left":0,"right":0,"sections":{}}',
        pid: "1", template: "central",
    };
    const panes = [0, 1, 2].map(index => ({
        ...element({
            left: index * 350, right: index * 350 + 300,
            top: 0, bottom: 600, width: 300, height: 600,
        }),
        parentElement: layout,
    }));
    Object.assign(document, {
        body: {dataset: {}},
        querySelector: () => layout,
        getElementById: id => id.startsWith("pane") ? panes[Number(id.slice(4))] : {textContent: ""},
        createElement: () => element(),
        dispatchEvent: event => updates.push(event.type),
    });
    const window = {cancelFitQueue() {}};
    vm.runInNewContext(source, {
        window, document, Event,
        console,
        getComputedStyle: () => ({
            gridTemplateColumns: "300 300 300", gridTemplateRows: "600",
            columnGap: "50", rowGap: "50",
        }),
        ResizeObserver: class {observe() {}},
        setTimeout: handler => {
            timers.set(++timerId, handler);
            return timerId;
        },
        clearTimeout: id => timers.delete(id),
        fetch: async (url, options) => {
            requests.push({url, body: JSON.parse(options.body)});
            return {ok: true, json: async () => ({ok: true})};
        },
    });
    await window.posterSizingReady;
    const gutters = layout.children[0].children;
    return {
        gutters, timers, requests, updates,
        async runTimers() {
            const callbacks = [...timers.values()];
            timers.clear();
            for (const callback of callbacks) await callback();
        },
    };
}

test("a held gutter drag shows only its handle and saves final sizing on release", async () => {
    const h = await harness();
    const [surface, handle] = h.gutters[0].children;
    const otherHandle = h.gutters[1].children[1];
    surface.emit("pointerdown");
    assert.equal(handle.hidden, false);
    assert.equal(otherHandle.hidden, true);
    surface.emit("pointermove", {clientX: 320});
    await h.runTimers();
    assert.deepEqual(h.requests, []);
    surface.emit("pointermove", {clientX: 340});
    await h.runTimers();
    assert.deepEqual(h.updates, []);
    surface.emit("pointerup", {clientX: 340});
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(handle.hidden, true);
    assert.equal(h.requests.length, 1);
    assert.equal(h.requests[0].url, "/layout/sizing");
    assert.equal(h.requests[0].body.sizing.left, 0.04);
    assert.deepEqual(h.updates, ["poster-layout-updated"]);
});

test("cancelling a drag saves once, while a press without movement does not save", async () => {
    const h = await harness();
    const [surface, handle] = h.gutters[0].children;
    surface.emit("pointerdown");
    surface.emit("pointerup");
    assert.equal(handle.hidden, true);
    assert.equal(h.requests.length, 0);
    surface.emit("pointerdown");
    surface.emit("pointermove", {clientX: 320});
    surface.emit("pointercancel");
    surface.emit("lostpointercapture");
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(handle.hidden, true);
    assert.equal(h.requests.length, 1);
});

test("keyboard resizing retains its debounced save", async () => {
    const h = await harness();
    const [surface] = h.gutters[0].children;
    surface.emit("keydown", {key: "End"});
    assert.equal(h.requests.length, 0);
    await h.runTimers();
    assert.equal(h.requests.length, 1);
    assert.equal(h.requests[0].body.sizing.left, 0.15);
});
