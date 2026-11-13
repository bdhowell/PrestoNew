const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { test } = require("node:test");
const vm = require("node:vm");
const source = readFileSync(new URL("../static/javascript/mathjax.js", `file://${__filename}`), "utf8");

function harness() {
    let now = 0;
    let nextId = 0;
    const timers = new Map();
    const frames = new Map();
    const scrollEvents = new Set();
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
        closest(selectors) {
            return this.matches(selectors) ? this : null;
        }
        scrollTo(options) {
            this.calls.push(options);
            this.scrollLeft = options.left;
            this.scrollTop = options.top;
            scrollEvents.add(this);
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
        performance: {now: () => now},
        requestAnimationFrame(fn) {
            const id = ++nextId;
            frames.set(id, fn);
            return id;
        },
        cancelAnimationFrame: id => frames.delete(id),
    });
    return {
        Element,
        timers,
        frames,
        scroll(element, left, top) {
            element.scrollLeft = left;
            element.scrollTop = top;
            listeners.scroll({target: element});
        },
        dispatch: target => listeners.scroll({target}),
        interact: (event, target) => listeners[event]({target}),
        frame(milliseconds = 0) {
            now += milliseconds;
            const pending = [...frames.values()];
            frames.clear();
            for (const fn of pending) fn(now);
            for (const target of scrollEvents) listeners.scroll({target});
            scrollEvents.clear();
        },
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
            assert.equal(h.frames.size, 1);
            h.frame(600);
            assert.equal(element.calls.length, 1);
            assert.equal(element.calls[0].left, 0);
            assert.equal(element.calls[0].top, 0);
            assert.equal(element.calls[0].behavior, "instant");
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
    h.frame(600);
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
    h.frame(600);
    assert.equal(first.calls.length, 1);
    assert.equal(second.calls.length, 0);
    h.advance(1400);
    h.frame(600);
    assert.equal(second.calls.length, 1);
});

test("returning to the origin cancels pending resets", () => {
    const h = harness();
    const element = new h.Element();
    h.scroll(element, 100, 50);
    h.scroll(element, 0, 0);
    h.advance(5000);
    assert.equal(element.calls.length, 0);
    assert.equal(h.timers.size, 0);
});

test("return follows jQuery swing easing over 600ms without restarting its idle timer", () => {
    const h = harness();
    const element = new h.Element();
    h.scroll(element, 200, 100);
    h.advance(5000);
    for (const progress of [0, 0.25, 0.5, 0.75, 1]) {
        h.frame(progress === 0 ? 0 : 150);
        const remaining = (1 + Math.cos(Math.PI * progress)) / 2;
        assert.ok(Math.abs(element.scrollLeft - 200 * remaining) < 1e-8);
        assert.ok(Math.abs(element.scrollTop - 100 * remaining) < 1e-8);
        assert.equal(h.timers.size, 0);
    }
    assert.equal(element.scrollLeft, 0);
    assert.equal(element.scrollTop, 0);
    assert.equal(h.frames.size, 0);
});

test("user input interrupts the animation and restarts the idle delay", () => {
    for (const event of ["wheel", "pointerdown", "touchstart", "keydown"]) {
        const h = harness();
        const element = new h.Element();
        h.scroll(element, 200, 100);
        h.advance(5000);
        h.frame(150);
        h.interact(event, element);
        assert.equal(h.frames.size, 0);
        h.advance(4999);
        assert.equal(element.calls.length, 1);
        h.advance(1);
        h.frame(600);
        assert.equal(element.scrollLeft, 0);
        assert.equal(element.scrollTop, 0);
    }
});

test("external scrolling during the animation interrupts it", () => {
    const h = harness();
    const element = new h.Element();
    h.scroll(element, 200, 100);
    h.advance(5000);
    h.frame(150);
    h.scroll(element, 300, 200);
    assert.equal(h.frames.size, 0);
    h.advance(4999);
    assert.equal(element.scrollLeft, 300);
    h.advance(1);
    h.frame(600);
    assert.equal(element.scrollLeft, 0);
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

test("removing a graphic during its animation stops further frames", () => {
    const h = harness();
    const element = new h.Element();
    h.scroll(element, 100, 50);
    h.advance(5000);
    h.frame(150);
    element.isConnected = false;
    h.frame(150);
    assert.equal(element.calls.length, 1);
    assert.equal(h.frames.size, 0);
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
