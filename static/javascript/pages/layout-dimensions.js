(() => {
    "use strict";
    const region = document.querySelector("#dimension-correction");
    const preview = document.querySelector("#poster-preview");
    const size = document.querySelector("#poster-size");
    const form = document.querySelector("#dimension-form");
    const input = document.querySelector("#poster-dimensions");
    const status = document.querySelector("#dimension-status");
    const picker = document.querySelector("#dimension-picker");
    const accept = document.querySelector("#dimension-accept");
    const pickerStatus = document.querySelector("#dimension-picker-status");
    const sizeList = document.querySelector("#dimension-sizes");
    const empty = document.querySelector("#dimension-empty");
    const chatInput = document.querySelector("#chatinput");
    const pairs = JSON.parse(picker.dataset.sizes);
    let units = "in";
    let orientation = "landscape";
    let selected = null;
    let dimensionEntry = false;
    let active;
    let timer;

    function renderSizes() {
        if (selected) {
            if (dimensionEntry) window.wikkit.setInput("");
            pickerStatus.textContent = dimensionEntry
                ? "Choose a size or enter your own dimensions."
                : "Choose a size while waiting for Wikkit to ask for dimensions.";
        }
        sizeList.replaceChildren();
        selected = null;
        const sizes = pairs.flat().filter(([width, height, sourceUnits]) =>
            (units === "in" ? sourceUnits === "in" : sourceUnits === "cm" || sourceUnits === "mm")
            && (orientation === "landscape" ? width > height : height > width)
        ).map(([sourceWidth, sourceHeight, sourceUnits]) => {
            const factor = sourceUnits === units ? 1 : units === "mm" ? 10 : 0.1;
            return [Number((sourceWidth * factor).toFixed(4)), Number((sourceHeight * factor).toFixed(4))];
        });
        const scale = sizes.length ? Math.min(
            96 / Math.max(...sizes.map(([width]) => width)),
            80 / Math.max(...sizes.map(([, height]) => height)),
        ) : 1;
        for (const [width, height] of sizes) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "dimension-size";
            button.setAttribute("aria-pressed", "false");
            button.setAttribute("aria-label", `${width} by ${height} ${units}, width by height`);
            const rectangle = document.createElement("span");
            rectangle.className = "dimension-rectangle";
            rectangle.style.width = `${width * scale}px`;
            rectangle.style.height = `${height * scale}px`;
            const label = document.createElement("span");
            label.textContent = `${width} × ${height} ${units}`;
            button.append(rectangle, label);
            button.addEventListener("click", () => {
                selected?.button.setAttribute("aria-pressed", "false");
                const text = `${height} ${units} x ${width} ${units}`;
                if (dimensionEntry) window.wikkit.setInput(text);
                selected = { button, text };
                button.setAttribute("aria-pressed", "true");
                pickerStatus.textContent = dimensionEntry
                    ? "Size placed in Wikkit's input. Choose OK or press Enter to continue."
                    : "Size selected. Wait for Wikkit to ask for dimensions.";
            });
            sizeList.append(button);
        }
        empty.hidden = sizes.length > 0;
    }

    for (const [selector, attribute, update] of [
        ["#dimension-units", "unit", (value) => { units = value; }],
        ["#dimension-orientations", "orientation", (value) => { orientation = value; }],
    ]) {
        document.querySelector(selector).addEventListener("click", (event) => {
            const button = event.target.closest("button");
            if (!button) return;
            for (const option of event.currentTarget.querySelectorAll("button")) {
                option.setAttribute("aria-pressed", String(option === button));
            }
            update(button.dataset[attribute]);
            renderSizes();
        });
    }
    chatInput.addEventListener("input", () => {
        if (selected) {
            selected.button.setAttribute("aria-pressed", "false");
            selected = null;
        }
    });
    accept.addEventListener("click", () => {
        if (!window.wikkit.submitInput()) {
            pickerStatus.textContent = "Enter dimensions in Wikkit's input and wait for its prompt.";
        }
    });
    renderSizes();

    function show(payload) {
        region.hidden = false;
        if (payload && payload.width > 0 && payload.height > 0) {
            const ratio = payload.width / payload.height;
            preview.style.width = `min(100%, ${45 * ratio}vh)`;
            preview.style.aspectRatio = String(ratio);
            size.textContent = `${payload.height.toFixed(2)} x ${payload.width.toFixed(2)} ${payload.units} (height x width)`;
            input.value = `${payload.height} ${payload.units} x ${payload.width} ${payload.units}`;
        }
    }

    async function update(event) {
        if (event) event.preventDefault();
        clearTimeout(timer);
        if (active) active.abort();
        const controller = new AbortController();
        active = controller;
        status.textContent = "Checking dimensions...";
        try {
            const response = await fetch("/layout/dimensions", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ dimensions: input.value }),
                signal: controller.signal,
            });
            const result = await response.json();
            if (controller.signal.aborted) return;
            if (result.preview) show(result.preview);
            if (!response.ok) {
                status.textContent = result.errors ? result.errors.join(" ") : result.detail;
                return;
            }
            window.location.assign(result.redirect);
        } catch (error) {
            if (error.name !== "AbortError") {
                status.textContent = "Your dimensions could not be saved. Please try again.";
            }
        }
    }
    form.addEventListener("submit", update);
    input.addEventListener("input", () => {
        if (active) active.abort();
        clearTimeout(timer);
        timer = setTimeout(() => update(), 400);
    });
    window.registerWikkitCommands({ commands: {
        "correct-dimensions": { does: show },
        "dimension-entry": { does: (enabled) => {
            dimensionEntry = enabled;
            accept.disabled = !enabled;
            if (enabled && selected) {
                const choice = selected;
                window.wikkit.setInput(choice.text);
                selected = choice;
                choice.button.setAttribute("aria-pressed", "true");
            }
            if (!enabled && selected) {
                selected.button.setAttribute("aria-pressed", "false");
                selected = null;
            }
            pickerStatus.textContent = enabled
                ? (selected ? "Size placed in Wikkit's input. Choose OK or press Enter to continue."
                    : "Choose a size or enter your own dimensions.")
                : "Choose a size while waiting for Wikkit to ask for dimensions.";
        } },
    } });
})();
