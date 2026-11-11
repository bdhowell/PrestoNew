(() => {
    "use strict";

    const poster = JSON.parse(document.querySelector("#evaluation-poster").textContent);
    const status = document.querySelector("#evaluation-status");
    const output = document.querySelector("#graphic-evaluations");
    const retry = document.querySelector("#retry-evaluation");
    const next = document.querySelector("#continue-review");
    const results = new Map();
    let running = false;
    let leaving = false;
    let terminalEventReceived = false;
    let controller = null;
    const categoryOrder = {uploaded: 0, extracted: 1};
    const ratings = ["none", "weakly", "somewhat", "significantly", "highly", "definitively"];

    function numberToWords(num, over20=true) {
        if (num === 0) return "zero";
        if (num > 20 && over20) return num;

        const ones = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"];
        const tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"];
        const scales = ["", "thousand", "million", "billion"];

        function convertLessThanOneThousand(n) {
            let str = "";

            if (n >= 100) {
                str += ones[Math.floor(n / 100)] + " hundred ";
                n %= 100;
            }

            if (n >= 20) {
                str += tens[Math.floor(n / 10)] + " ";
                n %= 10;
            }

            if (n > 0) {
                str += ones[n] + " ";
            }

            return str.trim();
        }

        let wordResult = "";
        let scaleIndex = 0;

        // Handle negative numbers
        if (num < 0) {
            return "minus " + numberToWords(Math.abs(num));
        }

        while (num > 0) {
            let chunk = num % 1000;
            if (chunk !== 0) {
                let chunkStr = convertLessThanOneThousand(chunk);
                wordResult = chunkStr + (scales[scaleIndex] ? " " + scales[scaleIndex] : "") + " " + wordResult;
            }
            num = Math.floor(num / 1000);
            scaleIndex++;
        }

        return wordResult.trim();
    }

    function element(tag, text, className = "") {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text) node.textContent = text;
        return node;
    }

    function addList(parent, heading, entries) {
        if (!entries.length) return;
        parent.append(element("h4", heading));
        const list = document.createElement("ul");
        for (const entry of entries) list.append(element("li", entry));
        parent.append(list);
    }

    function renderSection(parent, label, value) {
        const section = element("section");
        section.append(element("h4", `${label}: ${value.rating}`));
        section.append(element("p", value.explanation));
        addList(section, "Supported elements", value.supported_elements);
        addList(section, "Limitations", value.limitations);
        addList(section, "Suggested improvements", value.suggested_improvements);
        parent.append(section);
    }

    function suitable(evaluation) {
        const threshold = ratings.indexOf("significantly");
        return [
            evaluation.evaluations.materials_and_methods.rating,
            evaluation.evaluations.results.rating,
        ].some(rating => ratings.indexOf(rating) >= threshold);
    }

    function render() {
        output.replaceChildren();
        const ordered = [...results.values()].sort((first, second) =>
            categoryOrder[first.source.category] - categoryOrder[second.source.category]
                || first.source.sid - second.source.sid);
        for (const category of ["uploaded", "extracted"]) {
            const items = ordered.filter(item => item.source.category === category);
            if (!items.length) continue;
            const group = element(
                "section",
                "",
                "evaluation-category",
            );
            group.append(element("h2", category === "uploaded" ? "Uploaded graphics" : "Extracted graphics"));
            for (const item of items) {
                const isSuitable = suitable(item.evaluation);
                const card = element("article", "", "graphic-evaluation");
                card.append(element("h3", item.source.filename));
                const image = document.createElement("img");
                image.src = `/content/${item.source.sid}/image`;
                image.alt = item.evaluation.image_analysis.description || item.source.filename;
                image.loading = "lazy";
                card.append(image);
                card.append(element(
                    "p",
                    isSuitable ? "Suitable for the poster." : "Not suitable for the poster.",
                    `evaluation-verdict ${isSuitable ? "suitable" : "not-suitable"}`,
                ));
                card.append(element("p", item.evaluation.image_analysis.description));
                renderSection(card, "Materials and Methods", item.evaluation.evaluations.materials_and_methods);
                renderSection(card, "Results", item.evaluation.evaluations.results);
                group.append(card);
            }
            output.append(group);
        }
    }

    async function consumeEvent(event) {
        if (event.type === "empty") {
            terminalEventReceived = true;
            await window.wikkit.say(
                "<p>Well, that was quick! I didn&rsquo;t find any graphics to evaluate. "
                    + "We&rsquo;re going to need some, but we&rsquo;ll get around to graphics shortly.</p>",
            );
            window.wikkit.execute({does: "redirect", payload: "/conferences/query"});
        } else if (event.type === "progress") {
            status.textContent = `I am reviewing image ${event.current} of ${event.total}.`;
            await window.wikkit.say(
                `<p>Processing image ${event.current_word} of ${event.total_word}.</p>`,
            );
        } else if (event.type === "result") {
            results.set(event.source.sid, event);
            render();
        } else if (event.type === "error") {
            throw new Error(event.detail);
        } else if (event.type === "complete") {
            terminalEventReceived = true;
            const count = [...results.values()].filter(item => suitable(item.evaluation)).length;
            const unsuitable = results.size - count;
            status.textContent = `Evaluation complete: ${numberToWords(count)} suitable and ${numberToWords(unsuitable)} not suitable.`;
            next.hidden = false;
            await window.wikkit.say(
                `<p>I&rsquo;ve finished evaluating the graphics. ${count} may be useful for the poster; `
                    + `${unsuitable} may need a different image.</p>`,
            );
        }
    }

    async function evaluate() {
        if (running) return;
        running = true;
        retry.hidden = true;
        next.hidden = true;
        terminalEventReceived = false;
        controller = new AbortController();
        status.textContent = "Wikkit is evaluating your graphics.";
        try {
            const response = await fetch(`/content/evaluate/${poster.pid}`, {
                method: "POST",
                credentials: "same-origin",
                headers: {"Content-Type": "application/json"},
                body: "{}",
                signal: controller.signal,
            });
            if (!response.ok) {
                let detail = "Your graphics could not be evaluated.";
                try {
                    detail = (await response.json()).detail || detail;
                } catch {
                    // The status code and generic message are more useful than invalid error content.
                }
                throw new Error(detail);
            }
            if (!response.body) throw new Error("Your graphics could not be evaluated.");
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";
            while (true) {
                const {done, value} = await reader.read();
                buffer += decoder.decode(value || new Uint8Array(), {stream: !done});
                const lines = buffer.split("\n");
                buffer = lines.pop();
                for (const line of lines) {
                    if (line) await consumeEvent(JSON.parse(line));
                }
                if (done) break;
            }
            if (buffer.trim()) await consumeEvent(JSON.parse(buffer));
            if (!terminalEventReceived) {
                throw new Error("The evaluation stream ended before all graphics were processed.");
            }
        } catch (error) {
            if (!leaving && error.name !== "AbortError") {
                console.error("Graphic evaluation failed", error);
                status.textContent = error.message;
                render();
                retry.hidden = false;
                const escaped = document.createElement("span");
                escaped.textContent = error.message;
                window.wikkit.say(`<p>${escaped.innerHTML}</p>`);
            }
        } finally {
            running = false;
        }
    }

    retry.addEventListener("click", evaluate);
    window.addEventListener("pagehide", () => {
        leaving = true;
        controller?.abort();
    });
    evaluate();
})();
