(() => {
    "use strict";
    const selection = document.getElementById("template-selection");
    const status = document.getElementById("template-selection-status");
    const next = document.getElementById("template-selection-continue");
    if (!selection || !status || !next) return;

    const choices = [...selection.querySelectorAll("button[data-template]")];
    let selected = choices.find(choice => choice.getAttribute("aria-pressed") === "true" && !choice.disabled);
    let saving = false;

    for (const choice of choices) {
        choice.addEventListener("click", () => {
            if (saving || choice.disabled) return;
            selected = choice;
            for (const button of choices) {
                button.setAttribute("aria-pressed", String(button === choice));
            }
            next.disabled = false;
            status.textContent = "";
        });
    }

    next.addEventListener("click", async () => {
        if (saving || !selected || selected.disabled) return;
        saving = true;
        next.disabled = true;
        for (const button of choices) button.disabled = true;
        status.textContent = "Saving your template...";
        try {
            const response = await fetch("/layout/template", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    pid: Number(selection.dataset.pid),
                    template: selected.dataset.template,
                    from_dimensions: selection.dataset.fromDimensions === "true",
                }),
            });
            const result = await response.json();
            if (!response.ok) {
                status.textContent = result.detail || "Your template could not be saved. Please try again.";
                return;
            }
            window.location.assign(result.redirect);
        } catch (error) {
            console.error("Could not save template selection", error);
            status.textContent = "Your template could not be saved. Please try again.";
        } finally {
            saving = false;
            for (const button of choices) {
                button.disabled = button.dataset.available !== "true";
            }
            next.disabled = !selected || selected.disabled;
        }
    });
})();
