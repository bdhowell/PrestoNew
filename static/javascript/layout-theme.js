(() => {
    "use strict";

    for (const {id, setting, label} of [
        {id: "theme", setting: "theme", label: "theme"},
        {id: "font", setting: "fonts", label: "font set"},
    ]) {
        const button = document.getElementById(id);
        const dialog = document.getElementById(`${id}-dialog`);
        const form = document.getElementById(`${id}-form`);
        const options = document.getElementById(`${id}-options`);
        const cancel = document.getElementById(`${id}-cancel`);
        const apply = document.getElementById(`${id}-apply`);
        const status = document.getElementById(`${id}-status`);
        const radios = [...form.querySelectorAll(`input[name='${setting}']`)];
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
            const value = new FormData(form).get(setting);
            saving = true;
            options.disabled = cancel.disabled = apply.disabled = true;
            status.textContent = `Saving ${label}...`;
            try {
                const response = await fetch(`/layout/${setting}`, {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({pid: Number(dialog.dataset.pid), [setting]: value}),
                });
                let result;
                try {
                    result = await response.json();
                } catch {
                    throw new Error("The server returned an invalid response.");
                }
                if (!response.ok || result.ok !== true) {
                    throw new Error(result.detail || `Your ${label} could not be saved.`);
                }
                document.body.classList.remove(...radios.map(radio => radio.value));
                document.body.classList.add(value);
                dialog.close();
            } catch (error) {
                console.error(`Could not save poster ${label}`, error);
                status.textContent = `${error.message} Please try again.`;
            } finally {
                saving = false;
                options.disabled = cancel.disabled = apply.disabled = false;
            }
        });
    }
})();
