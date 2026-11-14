const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { test } = require("node:test");
const vm = require("node:vm");
const source = readFileSync(new URL("../static/javascript/sessions.js", `file://${__filename}`), "utf8");

async function flush() {
    for (let i = 0; i < 10; i++) await Promise.resolve();
}

async function harness(seconds, name = "Ada") {
    let now = 1000000;
    let nextId = 0;
    let expiry = now + seconds * 1000;
    const timers = new Map();
    const messages = [];
    const redirects = [];
    const errors = [];
    const events = {};
    const requestHeaders = [];
    let requests = 0;
    let fail = false;
    function timer(fn, delay, interval = false) {
        const id = ++nextId;
        timers.set(id, { fn, due: now + delay, delay, interval });
        return id;
    }
    const document = {
        visibilityState: "visible",
        addEventListener: (event, fn) => { events[event] = fn; },
        createElement: () => ({
            textContent: "",
            get innerHTML() {
                return this.textContent.replaceAll("&", "&amp;").replaceAll("<", "&lt;")
                    .replaceAll(">", "&gt;");
            },
        }),
    };
    vm.runInNewContext(source, {
        Date: { now: () => now },
        AbortSignal,
        URL,
        document,
        window: {
            location: { assign: (url) => redirects.push(url) },
            wikkit: { say: (message) => messages.push({ message, at: now }) },
            addEventListener: (event, fn) => { events[event] = fn; },
        },
        console: { error: (...args) => errors.push(args) },
        numberToWords: (number) => String(number),
        setTimeout: (fn, delay) => timer(fn, delay),
        clearTimeout: (id) => timers.delete(id),
        setInterval: (fn, delay) => timer(fn, delay, true),
        clearInterval: (id) => timers.delete(id),
        fetch: async (url, options) => {
            requests++;
            requestHeaders.push({ ...options.headers });
            assert.equal(url, "/system/timecheck");
            assert.equal(options.credentials, "same-origin");
            assert.equal(options.cache, "no-store");
            if (fail) throw new Error("offline");
            if (now >= expiry) return {
                redirected: true, url: "https://presto.test/account/signout",
            };
            return {
                ok: true,
                json: async () => ({
                    remaining_seconds: (expiry - now) / 1000,
                    expires_at: expiry / 1000,
                    name,
                }),
            };
        },
    });
    await flush();
    return {
        timers, messages, redirects, errors, events, requestHeaders,
        get requests() { return requests; },
        renew: (seconds) => { expiry = now + seconds * 1000; },
        fail: () => { fail = true; },
        async advance(seconds) {
            const target = now + seconds * 1000;
            while (true) {
                const next = [...timers.entries()].filter(([, value]) => value.due <= target)
                    .sort((a, b) => a[1].due - b[1].due || a[0] - b[0])[0];
                if (!next) break;
                const [id, value] = next;
                now = value.due;
                if (value.interval) value.due += value.delay;
                else timers.delete(id);
                value.fn();
                await flush();
            }
            now = target;
            await flush();
        },
    };
}

test("polls every minute, warns at exactly five and two minutes, then redirects", async () => {
    const h = await harness(600);
    await h.advance(299);
    assert.equal(h.messages.length, 0);
    await h.advance(1);
    assert.match(h.messages[0].message, /Ada, your session will expire in 5 minutes/);
    assert.equal(h.messages[0].at, 1300000);
    await h.advance(180);
    assert.match(h.messages[1].message, /Ada, your session will expire in 2 minutes/);
    assert.equal(h.messages[1].at, 1480000);
    await h.advance(120);
    assert.deepEqual(h.redirects, ["/account/signout"]);
    assert.equal(h.requests, 11);
});

test("repeated checks within either window do not replace the scheduled warning", async () => {
    for (const [seconds, minutes] of [[419, 5], [239, 2]]) {
        const h = await harness(seconds);
        const warning = [...h.timers.entries()].find(([, t]) => t.delay === (seconds - minutes * 60) * 1000);
        assert.ok(warning);
        await h.advance(60);
        h.events.visibilitychange();
        await flush();
        assert.equal(h.timers.get(warning[0]), warning[1]);
        await h.advance(seconds - minutes * 60 - 60);
        assert.equal(h.messages.length, 1);
        assert.match(h.messages[0].message, new RegExp(`in ${minutes} minutes`));
        h.events.visibilitychange();
        await flush();
        assert.equal(h.messages.length, 1);
    }
});

test("warning windows exclude their exact boundaries", async () => {
    for (const seconds of [420, 300, 240, 120]) {
        const h = await harness(seconds);
        assert.equal(h.timers.size, 2);
        assert.equal(h.messages.length, 0);
    }
});

test("actual activity cancels obsolete warnings and begins a new expiry cycle", async () => {
    const h = await harness(360);
    await h.advance(30);
    h.renew(600);
    h.events.keydown();
    await h.advance(30);
    assert.equal(h.requestHeaders.at(-1)["X-Session-Activity"], "1");
    assert.equal(h.messages.length, 0);
    await h.advance(270);
    assert.equal(h.messages.length, 1);
    assert.match(h.messages[0].message, /in 5 minutes/);
});

test("user activity marks the next timecheck to renew the session", async () => {
    for (const event of ["mousemove", "click", "touchstart", "keydown"]) {
        const h = await harness(600);
        h.events[event]();
        await h.advance(60);
        assert.equal(h.requestHeaders.at(-1)["X-Session-Activity"], "1");
    }
});

test("deadline check honors renewal in another tab instead of logging out", async () => {
    const h = await harness(10);
    await h.advance(5);
    h.renew(600);
    await h.advance(5);
    assert.equal(h.redirects.length, 0);
    assert.equal(h.requests, 2);
});

test("warning names are escaped and page teardown cancels all timers", async () => {
    const h = await harness(301, "<script>Ada</script>");
    await h.advance(1);
    assert.match(h.messages[0].message, /&lt;script&gt;Ada&lt;\/script&gt;/);
    h.events.pagehide();
    assert.equal(h.timers.size, 0);
    await h.advance(600);
    assert.equal(h.redirects.length, 0);
});

test("poll failures are visible and do not manufacture a renewed deadline", async () => {
    const h = await harness(65);
    h.fail();
    await h.advance(60);
    assert.equal(h.errors.length, 1);
    assert.equal(h.redirects.length, 0);
    await h.advance(5);
    assert.equal(h.errors.length, 2);
    assert.deepEqual(h.redirects, ["/account/signout"]);
});
