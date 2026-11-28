const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { test } = require("node:test");
const vm = require("node:vm");

const common = readFileSync(new URL("../static/javascript/common.js", `file://${__filename}`), "utf8");
const evaluation = readFileSync(new URL("../static/javascript/pages/content-evaluate.js", `file://${__filename}`), "utf8");

function node() {
    return {
        children: [],
        textContent: "",
        hidden: false,
        append(...children) { this.children.push(...children); },
        replaceChildren(...children) { this.children = children; },
        addEventListener() {},
        setAttribute() {},
        style: {setProperty() {}},
        get childElementCount() { return this.children.length; },
    };
}

async function evaluate(suitableCount) {
    const nodes = {
        "#evaluation-poster": {...node(), textContent: '{"pid":1}'},
        "#evaluation-status": node(),
        "#graphic-evaluations": node(),
        "#evaluation-progress": node(),
        "#evaluation-progress-meter": node(),
        "#evaluation-progress-count": node(),
        "#evaluation-remaining": node(),
        "#evaluation-summary": node(),
        "#evaluation-panes": node(),
        "#evaluation-preview": node(),
        "#evaluation-divider": node(),
        "#evaluation-details": node(),
        "#retry-evaluation": node(),
        "#continue-review": node(),
    };
    const events = Array.from({length: suitableCount + 1}, (_, index) => {
        const rating = index < suitableCount ? "significantly" : "none";
        const section = {
            rating, explanation: "", supported_elements: [], limitations: [], suggested_improvements: [],
        };
        return {
            type: "result",
            source: {sid: index, category: "uploaded", filename: `image${index}.png`},
            evaluation: {
                image_analysis: {description: "", classification: "figure"},
                evaluations: {materials_and_methods: section, results: section},
            },
        };
    });
    events.push({type: "complete"});
    const bytes = new TextEncoder().encode(events.map(event => JSON.stringify(event)).join("\n") + "\n");
    let read = false;
    const messages = [];
    const errors = [];
    const context = vm.createContext({
        document: {querySelector: selector => nodes[selector], createElement: node},
        window: {
            addEventListener() {},
            wikkit: {
                say: async message => { messages.push(message); },
                requestResponse: () => new Promise(() => {}),
            },
        },
        console: {error: (...args) => errors.push(args)},
        fetch: async () => ({
            ok: true,
            body: {getReader: () => ({
                read: async () => {
                    if (read) return {done: true};
                    read = true;
                    return {done: false, value: bytes};
                },
            })},
        }),
        AbortController,
        TextDecoder,
        Uint8Array,
        performance,
        setInterval,
        clearInterval,
    });
    vm.runInContext(common, context);
    vm.runInContext(evaluation, context);
    for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve));
    return {nodes, messages, errors};
}

test("evaluation completion handles suitable counts at and above the word threshold", async () => {
    for (const [count, display] of [[0, "Zero"], [20, "Twenty"], [21, "21"]]) {
        const {nodes, messages, errors} = await evaluate(count);
        assert.deepEqual(errors, []);
        assert.equal(nodes["#retry-evaluation"].hidden, true);
        assert.equal(nodes["#continue-review"].hidden, false);
        assert.match(nodes["#evaluation-status"].textContent, /Graphic review complete:/);
        assert.ok(messages.some(message => message.includes(`${display} may be useful for your poster; one are not suitable`)));
    }
});

test("numberToWords returns text for counts above 20 and supports spelling them out", () => {
    const context = vm.createContext({});
    vm.runInContext(common, context);
    assert.equal(context.numberToWords(21), "21");
    assert.equal(context.numberToWords(21, false), "twenty one");
});
