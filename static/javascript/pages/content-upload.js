(() => {
    "use strict";

    const input = document.querySelector("#source-files");
    const status = document.querySelector("#upload-status");
    const rows = document.querySelector("#items-list tbody");
    const finish = document.querySelector("#finish-upload");
    let uploading = false;

    function showItems(items) {
        rows.replaceChildren();
        for (const item of items) {
            const row = rows.insertRow();
            row.dataset.sourceId = item.sid;
            const preview = row.insertCell();
            if (item.thumbnail) {
                const image = document.createElement("img");
                image.src = `/content/${item.sid}/thumbnail`;
                image.alt = `Preview of ${item.filename}`;
                image.width = 80;
                image.loading = "lazy";
                preview.appendChild(image);
            } else {
                preview.textContent = "DOC";
                preview.setAttribute("aria-label", "Document");
            }
            const name = row.insertCell();
            name.style.paddingLeft = `${item.depth * 1.5}rem`;
            const label = document.createElement("span");
            label.textContent = item.filename;
            label.classList.toggle("ill-sized", item.ill_sized);
            name.appendChild(label);
            if (item.description) {
                const description = document.createElement("p");
                description.className = "source-description";
                description.textContent = item.description.split(/\r?\n/, 1)[0];
                name.appendChild(description);
            }
            row.insertCell().textContent = item.mimetype;
        }
    }

    async function finished() {
        if (uploading || finish.disabled) {
            status.textContent = "Please wait for the current uploads to finish.";
            return;
        }
        finish.disabled = true;
        try {
            const response = await fetch("/content/finished", {
                method: "POST", credentials: "same-origin",
                headers: {"Content-Type": "application/json"},
                body: "{}",
            });
            const reply = await response.json();
            if (reply.say) {
                window.wikkit.say(reply.say);
            }
            if (!response.ok && !reply.say) {
                throw new Error(reply.detail ?? "Your content could not be checked.");
            }
            if (reply.ok && reply.redirect) {
                location.assign(reply.redirect);
            }
        } catch (error) {
            console.error("Upload completion failed", error);
            status.textContent = error.message;
        } finally {
            finish.disabled = false;
        }
    }

    finish.addEventListener("click", finished);
    window.registerWikkitCommands({
        commands: {"finished-uploading": {does: finished}},
    });

    async function upload(files) {
        if (uploading) {
            status.textContent = "Please wait for the current uploads to finish.";
            return;
        }
        uploading = true;
        input.disabled = true;
        finish.disabled = true;
        let succeeded = 0;
        let failed = 0;
        try {
            for (const file of files) {
                status.textContent = `Uploading ${file.name}...`;
                try {
                    const response = await fetch("/content/upload", {
                        method: "POST",
                        credentials: "same-origin",
                        headers: {
                            "Content-Type": "application/octet-stream",
                            "X-Filename": encodeURIComponent(file.name),
                            Accept: "application/json",
                        },
                        body: file,
                    });
                    const reply = await response.json();
                    if (!response.ok) {
                        throw new Error(reply.detail ?? "The file could not be uploaded.");
                    }
                    showItems(reply.items);
                    if (reply.say) {
                        window.wikkit.say(reply.say);
                    }
                    succeeded += 1;
                } catch (error) {
                    failed += 1;
                    console.error("Source upload failed", error);
                    const failure = document.createElement("p");
                    failure.setAttribute("role", "alert");
                    failure.textContent = `${file.name}: ${error.message}`;
                    status.before(failure);
                }
            }
            status.textContent = `${succeeded} uploaded; ${failed} failed.`;
        } finally {
            uploading = false;
            input.disabled = false;
            finish.disabled = false;
            input.value = "";
        }
    }

    input.addEventListener("change", () => upload([...input.files]));
    document.addEventListener("dragover", (event) => {
        if (event.dataTransfer.types.includes("Files")) {
            event.preventDefault();
        }
    });
    document.addEventListener("drop", (event) => {
        if (event.dataTransfer.types.includes("Files")) {
            event.preventDefault();
            upload([...event.dataTransfer.files]);
        }
    });
})();
