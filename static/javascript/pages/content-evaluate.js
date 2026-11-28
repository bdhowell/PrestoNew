(() => {
    "use strict";

    const poster = JSON.parse(document.querySelector("#evaluation-poster").textContent);
    const status = document.querySelector("#evaluation-status");
    const output = document.querySelector("#graphic-evaluations");
    const progress = document.querySelector("#evaluation-progress");
    const meter = document.querySelector("#evaluation-progress-meter");
    const progressCount = document.querySelector("#evaluation-progress-count");
    const remaining = document.querySelector("#evaluation-remaining");
    const summary = document.querySelector("#evaluation-summary");
    const panes = document.querySelector("#evaluation-panes");
    const preview = document.querySelector("#evaluation-preview");
    const divider = document.querySelector("#evaluation-divider");
    const details = document.querySelector("#evaluation-details");
    const retry = document.querySelector("#retry-evaluation");
    const next = document.querySelector("#continue-review");
    const results = new Map();
    let running = false;
    let leaving = false;
    let terminalEventReceived = false;
    let completionMessage = null;
    let controller = null;
    let timer = null;
    let deadline = 0;
    let total = 0;
    let completed = 0;
    let processing = null;
    let processingTime = 0;
    let resizePointer = null;
    class EvaluationInterruptedError extends Error {}
    function rethrowConnectionError(error) {
        if (error instanceof TypeError) {
            throw new EvaluationInterruptedError("Evaluation connection was interrupted.", {cause: error});
        }
        throw error;
    }
    const ratings = ["none", "weakly", "somewhat", "significantly", "highly", "definitively"];

    function resizePanes(value) {
        const share = Math.max(20, Math.min(80, value));
        panes.style.setProperty("--preview-size", `${share}fr`);
        panes.style.setProperty("--details-size", `${100 - share}fr`);
        divider.setAttribute("aria-valuenow", String(Math.round(share)));
        divider.setAttribute("aria-valuetext", `Graphic ${Math.round(share)}%, details ${100 - Math.round(share)}%`);
    }

    divider.addEventListener("pointerdown", event => {
        if (event.button !== 0 || resizePointer !== null) return;
        resizePointer = event.pointerId;
        divider.setPointerCapture(event.pointerId);
        divider.focus();
        event.preventDefault();
    });
    divider.addEventListener("pointermove", event => {
        if (resizePointer !== event.pointerId) return;
        const height = panes.clientHeight - divider.offsetHeight;
        const offset = event.clientY - panes.getBoundingClientRect().top - panes.clientTop
            - divider.offsetHeight / 2;
        resizePanes(offset / height * 100);
    });
    function finishResize(event) {
        if (resizePointer !== event.pointerId) return;
        resizePointer = null;
        if (divider.hasPointerCapture(event.pointerId)) divider.releasePointerCapture(event.pointerId);
    }
    divider.addEventListener("pointerup", finishResize);
    divider.addEventListener("pointercancel", finishResize);
    divider.addEventListener("lostpointercapture", finishResize);
    divider.addEventListener("keydown", event => {
        if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
        const current = Number(divider.getAttribute("aria-valuenow"));
        const step = event.shiftKey ? 10 : 1;
        resizePanes(event.key === "Home" ? 20 : event.key === "End" ? 80
            : current + (event.key === "ArrowUp" ? -step : step));
        event.preventDefault();
    });

    function updateCountdown() {
        const seconds = Math.max(0, Math.ceil((deadline - performance.now()) / 1000));
        remaining.textContent = `(${seconds} second${seconds === 1 ? "" : "s"} estimated remaining)`;
    }

    function estimateRemaining(seconds) {
        deadline = performance.now() + seconds * 1000;
        updateCountdown();
    }

    function stopCountdown() {
        if (timer !== null) clearInterval(timer);
        timer = null;
    }

    function updateProgress() {
        meter.value = completed;
        progressCount.textContent = `${completed} of ${total}`;
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

    function suitable(evaluation, complexity, legibility) {
        if (!evaluation || complexity?.too_complex || legibility?.below_threshold) return false;
        const threshold = ratings.indexOf("significantly");
        return [
            evaluation.evaluations.materials_and_methods.rating,
            evaluation.evaluations.results.rating,
        ].some(rating => ratings.indexOf(rating) >= threshold);
    }

    function render(item) {
        const isSuitable = suitable(item.evaluation, item.complexity, item.legibility);
        const evaluation = item.evaluation;
        output.hidden = false;
        summary.replaceChildren();
        preview.replaceChildren();
        details.replaceChildren();
        output.scrollTop = 0;
        output.scrollLeft = 0;
        summary.append(element(
            "p",
            evaluation ? `Classification: ${evaluation.image_analysis.classification}` : "Classification: not evaluated",
            "graphic-classification",
        ));
        for (const [label, metric] of [["Complexity", item.complexity], ["Legibility", item.legibility]]) {
            const score = typeof metric?.score === "number" ? `${(metric.score * 100).toFixed(1)}%` : "not available";
            summary.append(element("p", `${label}: ${score}`));
        }
        summary.append(element(
            "p",
            isSuitable ? "Suitable for the poster." : "Not suitable for the poster.",
            `evaluation-verdict ${isSuitable ? "suitable" : "not-suitable"}`,
        ));
        if (item.source.type === "tables" && item.source.table_html) {
            const table = element("div", "", "graphic-table");
            table.innerHTML = item.source.table_html;
            preview.append(table);
        } else {
            const image = document.createElement("img");
            image.src = `/content/${item.source.sid}/image`;
            image.alt = evaluation?.image_analysis.description || item.source.filename;
            preview.append(image);
        }
        if (item.complexity?.too_complex) {
            details.append(element(
                "p",
                `Complexity score (${(item.complexity.score * 100).toFixed(1)}%) exceeds the poster threshold (${(item.complexity.threshold * 100).toFixed(1)}%); this image is not suitable for the poster.`,
            ));
        }
        if (item.legibility?.below_threshold) {
            details.append(element(
                "p",
                `Legibility score (${(item.legibility.score * 100).toFixed(1)}%) is below the poster threshold (${(item.legibility.threshold * 100).toFixed(1)}%); this image is not suitable for the poster.`,
            ));
        }
        if (!evaluation) {
            details.append(element(
                "p",
                "Not evaluated because this image did not meet the complexity or legibility threshold.",
                "evaluation-skipped",
            ));
        } else {
            details.append(element("p", evaluation.image_analysis.description));
            const expressions = evaluation.image_analysis.math_expressions || [];
            if (expressions.length) {
                details.append(element("h4", "Math expressions"));
                for (const expression of expressions) {
                    const formula = element("p", `\\[${expression}\\]`, "graphic-math");
                    details.append(formula);
                    window.mathJaxSource.typeset(formula);
                }
            }
            renderSection(details, "Materials and Methods", evaluation.evaluations.materials_and_methods);
            renderSection(details, "Results", evaluation.evaluations.results);
        }
    }

    async function consumeEvent(event) {
        if (event.type === "start") {
            stopCountdown();
            total = event.total;
            completed = 0;
            processing = null;
            processingTime = 0;
            progress.hidden = total === 0;
            if (total > 0) {
                meter.max = total;
                updateProgress();
                estimateRemaining(total * 20);
                timer = setInterval(updateCountdown, 1000);
            }
            if (event.total > 0) {
                const count = numberToWords(event.total);
                status.textContent = `Preparing to evaluate ${event.total} graphic${event.total === 1 ? "" : "s"}.`;
                await window.wikkit.say(
                    `<p>I&rsquo;m reviewing ${count} graphic${event.total === 1 ? "" : "s"}.</p>`
                    + `<p>I&rsquo;m predicting ${event.estimate_word} minutes to evaluate `
                    + `${event.total === 1 ? "it" : "them"}.</p>`,
                );
            }
        } else if (event.type === "already-evaluated") {
            stopCountdown();
            terminalEventReceived = true;
            status.textContent = "Graphic review complete.";
            completionMessage = "<p>I&rsquo;ve finished reviewing your graphics.</p>";
        } else if (event.type === "empty") {
            stopCountdown();
            terminalEventReceived = true;
            await window.wikkit.say(
                "<p>Well, that was quick! I didn&rsquo;t find any graphics to evaluate. "
                    + "We&rsquo;re going to need some, but we&rsquo;ll get around to graphics shortly.</p>",
            );
            window.wikkit.execute({does: "redirect", payload: "/conferences/query"});
        } else if (event.type === "progress") {
            processing = {index: event.current, started: performance.now()};
            status.textContent = `I am reviewing image ${event.current} of ${event.total}.`;
            const response = [
                'I&rsquo;m reviewing graphic',
                'I&rsquo;m looking at graphic',
                'Now I&rsquo;m reviewing graphic',
                'I&rsquo;m considering graphic',
                'I am thinking about graphic',
                'I am making progress. I am on graphic',
                'It&rsquo;s worth the wait, I promise. I&rsquo;m on graphic',
            ][Math.floor(Math.random() * 7)];
            await window.wikkit.say(
                `<p>${response} ${event.current_word} of ${event.total_word}.</p>`
            );
        } else if (event.type === "result") {
            results.set(event.source.sid, event);
            render(event);
            if (processing !== null) {
                processingTime += performance.now() - processing.started;
                completed = processing.index;
                processing = null;
                updateProgress();
                estimateRemaining(processingTime / completed / 1000 * (total - completed));
            }
        } else if (event.type === "error") {
            throw new Error(event.detail);
        } else if (event.type === "complete") {
            stopCountdown();
            terminalEventReceived = true;
            const count = [...results.values()].filter(item =>
                suitable(item.evaluation, item.complexity, item.legibility)).length;
            const unsuitable = results.size - count;
            status.textContent = `Graphic review complete: ${numberToWords(count)} suitable and ${numberToWords(unsuitable)} not suitable.`;
            completionMessage = `<p>I&rsquo;ve finished reviewing your graphics. ${capitalizeFirstLetter(numberToWords(count))} may be useful for your poster; `
                + `${numberToWords(unsuitable)} are not suitable because of relevance, image complexity, or legibility.</p>`;
        }
    }

    async function promptForReview(message) {
        try {
            await window.wikkit.say(message + '<button data-sends="ok">Continue to Poster</button>');
            if (leaving) return;
            await window.wikkit.requestResponse();
            if (!leaving) await window.wikkit.execute({does: "redirect", payload: preserveTemplateOrigin("/content/review")});
        } catch (error) {
            if (!leaving) {
                console.error("Wikkit could not continue to poster review", error);
                status.textContent = "Graphic review complete. Use Continue to Poster below.";
            }
        }
    }

    async function evaluate(attempt = 0) {
        if (running) return;
        running = true;
        retry.hidden = true;
        next.hidden = true;
        terminalEventReceived = false;
        completionMessage = null;
        controller = new AbortController();
        status.textContent = "Checking graphics for legibility and complexity.";
        try {
            const response = await fetch(`/content/evaluate/${poster.pid}`, {
                method: "POST",
                credentials: "same-origin",
                headers: {"Content-Type": "application/json"},
                body: "{}",
                signal: controller.signal,
            }).catch(rethrowConnectionError);
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
                const {done, value} = await reader.read().catch(rethrowConnectionError);
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
                throw new EvaluationInterruptedError("Evaluation connection ended without a completion event.");
            }
            if (completionMessage !== null) {
                next.hidden = false;
                void promptForReview(completionMessage);
            }
        } catch (error) {
            stopCountdown();
            if (!progress.hidden) remaining.textContent = "(estimate paused)";
            if (!leaving && error.name !== "AbortError") {
                if (error instanceof EvaluationInterruptedError) {
                    if (attempt < 2) {
                        console.warn("Reconnecting interrupted graphic evaluation", error);
                        status.textContent = "Reconnecting to finish evaluating your graphics...";
                        await new Promise(resolve => setTimeout(resolve, 1000 * (attempt + 1)));
                        if (!leaving) {
                            running = false;
                            await evaluate(attempt + 1);
                        }
                        return;
                    }
                    console.error("Graphic evaluation could not reconnect", error);
                    error = new Error(
                        "I couldn't finish evaluating your graphics because the connection was interrupted. "
                        + "Please try again; completed evaluations have been saved.",
                    );
                }
                console.error("Graphic evaluation failed", error);
                status.textContent = error.message;
                retry.hidden = false;
                const escaped = document.createElement("span");
                escaped.textContent = error.message;
                window.wikkit.say(`<p>${escaped.innerHTML}</p>`);
            }
        } finally {
            running = false;
        }
    }

    retry.addEventListener("click", () => evaluate());
    next.addEventListener("click", () => window.location.assign(preserveTemplateOrigin("/content/review")));
    window.addEventListener("pagehide", () => {
        leaving = true;
        stopCountdown();
        controller?.abort();
    });
    evaluate();
})();
