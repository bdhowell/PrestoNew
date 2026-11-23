(() => {
    "use strict";
    const region = document.querySelector("#dimension-correction");
    const preview = document.querySelector("#poster-preview");
    const size = document.querySelector("#poster-size");
    const form = document.querySelector("#dimension-form");
    const input = document.querySelector("#poster-dimensions");
    const status = document.querySelector("#dimension-status");
    const picker = document.querySelector("#dimension-picker");
    const controls = document.querySelector("#dimension-picker-controls");
    const pickerStatus = document.querySelector("#dimension-picker-status");
    const sizeList = document.querySelector("#dimension-sizes");
    const empty = document.querySelector("#dimension-empty");
    const pairs = JSON.parse(picker.dataset.sizes);
    const fromEditor = picker.dataset.fromEditor === "true";
    const current = picker.dataset.current ? JSON.parse(picker.dataset.current) : null;
    let units = current?.units || "in";
    let orientation = current ? (current.width > current.height ? "landscape"
        : current.height > current.width ? "portrait" : "square") : "landscape";
    const currentOrientation = orientation;
    let active;
    let timer;

    document.querySelector("#return-to-poster")?.addEventListener("click", () => {
        window.location.assign("/layout/edit");
    });

    if (current) {
        for (const [selector, value] of [
            ["#dimension-units", units], ["#dimension-orientations", orientation],
        ]) {
            for (const button of document.querySelectorAll(`${selector} button`)) {
                const matches = button.dataset.unit === value || button.dataset.orientation === value;
                button.setAttribute("aria-pressed", String(matches));
                button.classList.toggle("current-dimension", matches);
            }
        }
    }

    function renderSizes() {
        sizeList.replaceChildren();
        const sizes = pairs.flat().filter(([width, height, sourceUnits]) =>
            (units === "in" ? sourceUnits === "in" : sourceUnits === "cm" || sourceUnits === "mm")
            && (orientation === "landscape" ? width > height
                : orientation === "portrait" ? height > width : width === height)
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
            if (current && units === current.units && orientation === currentOrientation
                && Math.abs(width - current.width) < 0.0001 && Math.abs(height - current.height) < 0.0001) {
                button.classList.add("current-dimension");
                button.setAttribute("aria-current", "true");
            }
            button.setAttribute("aria-label", `${width} by ${height} ${units}, width by height`);
            const rectangle = document.createElement("span");
            rectangle.className = "dimension-rectangle";
            rectangle.style.width = `${width * scale}px`;
            rectangle.style.height = `${height * scale}px`;
            const label = document.createElement("span");
            label.textContent = `${width} × ${height} ${units}`;
            button.append(rectangle, label);
            button.addEventListener("click", () => {
                saveDimensions(`${height} ${units} x ${width} ${units}`, true);
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

    async function saveDimensions(dimensions, fromPicker = false) {
        clearTimeout(timer);
        if (active) active.abort();
        const controller = new AbortController();
        active = controller;
        controls.disabled = true;
        if (fromPicker) form.inert = true;
        const message = fromPicker ? pickerStatus : status;
        message.textContent = "Checking dimensions...";
        try {
            const response = await fetch("/layout/dimensions", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ dimensions, ...(fromEditor ? { from_editor: true } : {}) }),
                signal: controller.signal,
            });
            const result = await response.json();
            if (controller.signal.aborted) return;
            if (result.preview) show(result.preview);
            if (!response.ok) {
                if (fromPicker) {
                    region.hidden = false;
                    if (!result.preview) input.value = dimensions;
                }
                message.textContent = result.errors ? result.errors.join(" ")
                    : result.detail || "Your dimensions could not be saved. Please try again.";
                return;
            }
            window.location.assign(result.redirect);
        } catch (error) {
            if (error.name !== "AbortError") {
                message.textContent = "Your dimensions could not be saved. Please try again.";
            }
        } finally {
            if (active === controller) {
                active = null;
                controls.disabled = false;
            }
            if (fromPicker) {
                form.inert = false;
            }
        }
    }
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        saveDimensions(input.value);
    });
    input.addEventListener("input", () => {
        if (active) active.abort();
        clearTimeout(timer);
        timer = setTimeout(() => saveDimensions(input.value), 400);
    });
    window.registerWikkitCommands({ commands: {
        "correct-dimensions": { does: show },
    } });
})();
