(() => {
    "use strict";
    const region = document.querySelector("#dimension-correction");
    const preview = document.querySelector("#poster-preview");
    const size = document.querySelector("#poster-size");
    const form = document.querySelector("#dimension-form");
    const input = document.querySelector("#poster-dimensions");
    const status = document.querySelector("#dimension-status");
    let active;
    let timer;

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
    window.registerWikkitCommands({ commands: { "correct-dimensions": { does: show } } });
})();
