(() => {
    "use strict";
    const form = document.getElementById("template-selection");
    const status = document.getElementById("template-selection-status");
    const apply = document.getElementById("template-selection-apply");
    if (!form || !status || !apply) return;

    form.addEventListener("submit", async event => {
        event.preventDefault();
        const selected = form.querySelector("input[name=template]:checked");
        if (!selected) {
            status.textContent = "Choose a template.";
            return;
        }
        apply.disabled = true;
        status.textContent = "Saving your template...";
        try {
            const response = await fetch("/layout/template", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    pid: Number(form.dataset.pid),
                    template: selected.value,
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
            apply.disabled = false;
        }
    });
})();
