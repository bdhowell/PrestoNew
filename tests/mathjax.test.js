const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { test } = require("node:test");
const vm = require("node:vm");
const source = readFileSync(new URL("../static/javascript/mathjax.js", `file://${__filename}`), "utf8");

function harness() {
    let now = 0;
    let nextId = 0;
    const timers = new Map();
    const listeners = {};
    class Element {
        constructor(selector = ".graphic-math") {
            this.selector = selector;
            this.scrollLeft = 0;
            this.scrollTop = 0;
            this.isConnected = true;
            this.calls = [];
        }
        matches(selectors) {
            return selectors.split(", ").includes(this.selector);
        }
        scrollTo(options) {
            this.calls.push(options);
        }
    }
    vm.runInNewContext(source, {
        Element,
        window: {},
        document: {
            addEventListener(event, listener, options) {
                listeners[event] = listener;
                if (event === "scroll") {
                    assert.equal(options.capture, true);
                    assert.equal(options.passive, true);
                }
            },
        },
        setTimeout(fn, delay) {
            const id = ++nextId;
            timers.set(id, {fn, due: now + delay});
            return id;
        },
        clearTimeout: id => timers.delete(id),
    });
    return {
        Element,
        timers,
        scroll(element, left, top) {
            element.scrollLeft = left;
            element.scrollTop = top;
            listeners.scroll({target: element});
        },
        dispatch: target => listeners.scroll({target}),
        advance(milliseconds) {
            now += milliseconds;
            for (const [id, timer] of timers) {
                if (timer.due <= now) {
                    timers.delete(id);
                    timer.fn();
                }
            }
        },
    };
}

test("math graphics return smoothly on both axes only after five idle seconds", () => {
    for (const selector of [".graphic-math", ".review-graphic-math", "mjx-container"]) {
        for (const [left, top] of [[120, 80], [120, 0], [0, 80], [-120, 0]]) {
            const h = harness();
            const element = new h.Element(selector);
            h.scroll(element, left, top);
            h.advance(4999);
            assert.equal(element.calls.length, 0);
            assert.equal(element.scrollLeft, left);
            assert.equal(element.scrollTop, top);
            h.advance(1);
            assert.equal(element.calls.length, 1);
            assert.equal(element.calls[0].left, 0);
            assert.equal(element.calls[0].top, 0);
            assert.equal(element.calls[0].behavior, "smooth");
        }
    }
});

test("further scrolling restarts the full five-second delay", () => {
    const h = harness();
    const element = new h.Element();
    h.scroll(element, 100, 50);
    h.advance(4000);
    h.scroll(element, 200, 80);
    h.advance(4999);
    assert.equal(element.calls.length, 0);
    h.advance(1);
    assert.equal(element.calls.length, 1);
});

test("graphics added later have independent idle timers", () => {
    const h = harness();
    const first = new h.Element();
    h.scroll(first, 100, 50);
    h.advance(2000);
    const second = new h.Element(".review-graphic-math");
    h.scroll(second, 20, 30);
    h.advance(3000);
    assert.equal(first.calls.length, 1);
    assert.equal(second.calls.length, 0);
    h.advance(2000);
    assert.equal(second.calls.length, 1);
});

test("returning to the origin cancels pending resets, including smooth-scroll events", () => {
    const h = harness();
    const element = new h.Element();
    h.scroll(element, 100, 50);
    h.scroll(element, 0, 0);
    h.advance(5000);
    assert.equal(element.calls.length, 0);
    assert.equal(h.timers.size, 0);

    h.scroll(element, 100, 50);
    h.advance(5000);
    h.scroll(element, 50, 25);
    h.scroll(element, 0, 0);
    h.advance(5000);
    assert.equal(element.calls.length, 1);
    assert.equal(h.timers.size, 0);
});

test("unrelated scrolling and document events are ignored", () => {
    const h = harness();
    const panel = new h.Element("#review-graphics");
    h.scroll(panel, 100, 50);
    h.dispatch({});
    h.advance(5000);
    assert.equal(panel.calls.length, 0);
    assert.equal(h.timers.size, 0);
});

test("removed graphics are not scrolled", () => {
    const h = harness();
    const element = new h.Element();
    h.scroll(element, 100, 50);
    element.isConnected = false;
    h.advance(5000);
    assert.equal(element.calls.length, 0);
    assert.equal(h.timers.size, 0);
});
