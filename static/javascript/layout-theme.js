(() => {
    "use strict";

    const themeButton = document.getElementById("theme");
    const themeDialog = document.getElementById("theme-dialog");
    const themeForm = document.getElementById("theme-form");
    const themeOptions = document.getElementById("theme-options");
    const themeCancel = document.getElementById("theme-cancel");
    const themeApply = document.getElementById("theme-apply");
    const themeStatus = document.getElementById("theme-status");
    const colors = [...themeForm.querySelectorAll("input[name='color']")];
    const corners = [...themeForm.querySelectorAll("input[name='corners']")];
    const posterHeader = themeForm.querySelector("input[name='colored_poster_header']");
    const sectionHeaders = themeForm.querySelector("input[name='colored_section_headers']");
    let savingTheme = false;

    const updateThemeExamples = () => {
        const color = themeForm.querySelector("input[name='color']:checked")?.value;
        const corner = themeForm.querySelector("input[name='corners']:checked")?.value;
        themeDialog.classList.remove(...colors.map(input => input.value));
        if (color) themeDialog.classList.add(color);
        themeDialog.classList.remove(...corners.map(input => `corners-${input.value}`));
        if (corner) themeDialog.classList.add(`corners-${corner}`);
    };

    themeButton.addEventListener("click", () => {
        for (const color of colors) {
            color.checked = document.body.classList.contains(color.value);
        }
        for (const corner of corners) {
            corner.checked = document.body.classList.contains(`corners-${corner.value}`);
        }
        posterHeader.checked = document.body.classList.contains("colored-poster-header");
        sectionHeaders.checked = document.body.classList.contains("colored-section-headers");
        updateThemeExamples();
        themeStatus.textContent = "";
        themeDialog.showModal();
    });
    themeForm.addEventListener("change", updateThemeExamples);
    themeCancel.addEventListener("click", () => themeDialog.close());
    themeDialog.addEventListener("cancel", event => {
        if (savingTheme) event.preventDefault();
    });
    themeForm.addEventListener("submit", async event => {
        event.preventDefault();
        if (savingTheme || !themeForm.reportValidity()) return;

        const color = themeForm.querySelector("input[name='color']:checked").value;
        const corner = themeForm.querySelector("input[name='corners']:checked").value;
        const settings = {
            pid: Number(themeDialog.dataset.pid),
            color,
            corners: corner,
            colored_poster_header: posterHeader.checked,
            colored_section_headers: sectionHeaders.checked,
        };
        savingTheme = true;
        themeOptions.disabled = themeCancel.disabled = themeApply.disabled = true;
        themeStatus.textContent = "Saving theme...";
        try {
            const response = await fetch("/layout/theme", {
                method: "POST",
                credentials: "same-origin",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(settings),
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
            document.body.classList.remove(
                ...colors.map(input => input.value),
                ...corners.map(input => `corners-${input.value}`),
                "colored-poster-header",
                "colored-section-headers",
            );
            document.body.classList.add(
                color,
                `corners-${corner}`,
                ...(posterHeader.checked ? ["colored-poster-header"] : []),
                ...(sectionHeaders.checked ? ["colored-section-headers"] : []),
            );
            themeDialog.close();
        } catch (error) {
            console.error("Could not save poster theme", error);
            themeStatus.textContent = `${error.message} Please try again.`;
        } finally {
            savingTheme = false;
            themeOptions.disabled = themeCancel.disabled = themeApply.disabled = false;
        }
    });

    for (const {id, setting, label} of [
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
