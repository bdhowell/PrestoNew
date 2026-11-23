(() => {
    "use strict";
    const selection = document.getElementById("template-selection");
    const status = document.getElementById("template-selection-status");
    if (!selection || !status) return;

    const choices = [...selection.querySelectorAll("button[data-template]")];
    let saving = false;

    for (const choice of choices) {
        choice.addEventListener("click", async () => {
            if (saving || choice.disabled) return;
            saving = true;
            for (const button of choices) button.disabled = true;
            status.textContent = "Saving your template...";
            try {
                const response = await fetch("/layout/template", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        pid: Number(selection.dataset.pid),
                        template: choice.dataset.template,
                    }),
                });
                const result = await response.json();
                if (!response.ok) {
                    status.textContent = result.detail || "Your template could not be saved. Please try again.";
                    return;
                }
                window.location.assign(result.redirect);
            } catch {
                status.textContent = "Your template could not be saved. Please try again.";
            } finally {
                saving = false;
                for (const button of choices) {
                    button.disabled = button.dataset.available !== "true";
                }
            }
        });
    }
})();
