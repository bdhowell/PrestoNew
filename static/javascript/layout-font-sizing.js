(() => {
    "use strict";

    const controls = document.getElementById("font-size-controls");
    const canvas = document.getElementById("poster-canvas");
    if (!controls || !canvas) return;
    const target = document.getElementById("font-sizing");
    const slider = document.getElementById("font-size");
    const output = document.getElementById("font-size-value");
    const status = document.getElementById("content-save-status");
    const retry = document.getElementById("font-size-retry");
    const sizing = JSON.parse(controls.dataset.sizing);
    const dirty = new Map();
    let saving = false;
    let timer = null;
    let failed = false;

    function update() {
        const label = target.selectedOptions[0].textContent;
        slider.value = String(Math.round(sizing[target.value] * 100));
        slider.setAttribute("aria-label", `${label} font size`);
        output.textContent = `${slider.value}%`;
    }

    async function save() {
        if (saving || !dirty.size) return;
        saving = true;
        failed = false;
        retry.hidden = true;
        status.textContent = "Saving font sizing...";
        try {
            while (dirty.size) {
                const [name, value] = dirty.entries().next().value;
                const response = await fetch("/layout/font-sizing", {
                    method: "POST",
                    credentials: "same-origin",
                    keepalive: true,
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({pid: Number(controls.dataset.pid), target: name, value}),
                });
                let result;
                try {
                    result = await response.json();
                } catch {
                    throw new Error("The server returned an invalid response.");
                }
                if (!response.ok || result.ok !== true) {
                    throw new Error(result.detail || "Your font sizing could not be saved.");
                }
                if (dirty.get(name) === value) dirty.delete(name);
            }
            status.textContent = "Font sizing saved.";
        } catch (error) {
            failed = true;
            console.error("Could not save poster font sizing", error);
            status.textContent = `${error.message} Retry saving.`;
            retry.hidden = false;
        } finally {
            saving = false;
        }
    }

    target.addEventListener("change", update);
    slider.addEventListener("input", () => {
        const name = target.value;
        const value = Number(slider.value) / 100;
        sizing[name] = value;
        canvas.style.setProperty(`--${name}-font-scale`, value);
        if (name !== "sections") {
            document.getElementById(name === "title" ? "title" : "authors").style.width = "100%";
        }
        canvas.dispatchEvent(new Event("poster-font-size"));
        dirty.set(name, value);
        update();
        status.textContent = "Saving font sizing...";
        clearTimeout(timer);
        timer = setTimeout(save, 300);
    });
    slider.addEventListener("change", save);
    retry.addEventListener("click", save);
    window.addEventListener("beforeunload", event => {
        if (dirty.size || saving || failed) {
            event.preventDefault();
            event.returnValue = "";
        }
    });
    update();
})();
