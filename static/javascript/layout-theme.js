(() => {
    "use strict";

    const button = document.getElementById("theme");
    const dialog = document.getElementById("theme-dialog");
    const form = document.getElementById("theme-form");
    const options = document.getElementById("theme-options");
    const cancel = document.getElementById("theme-cancel");
    const apply = document.getElementById("theme-apply");
    const status = document.getElementById("theme-status");
    const radios = [...form.querySelectorAll("input[name='theme']")];
    let saving = false;

    button.addEventListener("click", () => {
        for (const radio of radios) {
            radio.checked = document.body.classList.contains(radio.value);
        }
        status.textContent = "";
        dialog.showModal();
    });
    cancel.addEventListener("click", () => dialog.close());
    dialog.addEventListener("cancel", event => {
        if (saving) event.preventDefault();
    });
    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (saving || !form.reportValidity()) return;
        const theme = new FormData(form).get("theme");
        saving = true;
        options.disabled = cancel.disabled = apply.disabled = true;
        status.textContent = "Saving theme...";
        try {
            const response = await fetch("/layout/theme", {
                method: "POST",
                credentials: "same-origin",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({pid: Number(dialog.dataset.pid), theme}),
            });
            let result;
            try {
                result = await response.json();
            } catch {
                throw new Error("The server returned an invalid response.");
            }
            if (!response.ok || result.ok !== true) {
                throw new Error(result.detail || "Your theme could not be saved.");
            }
            document.body.classList.remove(...radios.map(radio => radio.value));
            document.body.classList.add(theme);
            dialog.close();
        } catch (error) {
            console.error("Could not save poster theme", error);
            status.textContent = `${error.message} Please try again.`;
        } finally {
            saving = false;
            options.disabled = cancel.disabled = apply.disabled = false;
        }
    });
})();
