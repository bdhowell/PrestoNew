(() => {
    "use strict";

    const message = document.querySelector(".poster-message");
    const existing = document.querySelector("#existing-poster");

    async function post(url, body) {
        const response = await fetch(url, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify(body),
        });
        const reply = await response.json();
        if (reply.say) {
            window.wikkit.say(reply.say);
        }
        if (!response.ok && !reply.existing_poster) {
            throw new Error(reply.detail ?? "The request failed. Please try again.");
        }
        return reply;
    }

    document.querySelector("[data-new-poster]").addEventListener("click", async (event) => {
        const button = event.currentTarget;
        button.disabled = true;
        message.textContent = "";
        try {
            const reply = await post("/posters/new", {});
            if (reply.existing_poster) {
                existing.querySelector("[data-open-existing]").href = `/posters/${reply.existing_poster}/open`;
                existing.showModal();
            } else if (reply.ok && reply.redirect) {
                location.assign(reply.redirect);
            }
        } catch (error) {
            console.error("New poster request failed", error);
            message.textContent = error.message;
        } finally {
            button.disabled = false;
        }
    });

    existing.querySelector("[data-close-dialog]").addEventListener("click", () => existing.close());

    for (const button of document.querySelectorAll("[data-delete-poster]")) {
        button.addEventListener("click", async () => {
            const buttons = document.querySelectorAll("[data-delete-poster]");
            buttons.forEach((item) => { item.disabled = true; });
            message.textContent = "";
            try {
                const pid = Number(button.dataset.deletePoster);
                const prompt = await post("/posters/delete", { pid });
                if (prompt.confirmation_required) {
                    const response = await window.wikkit.requestResponse();
                    const reply = await post("/posters/delete", { pid, response });
                    if (reply.deleted) {
                        button.closest("[data-poster-id]").remove();
                        if (!document.querySelector("[data-poster-id]")) {
                            const empty = document.createElement("p");
                            empty.textContent = "No posters to display.";
                            document.querySelector(".poster-items").appendChild(empty);
                        }
                    }
                }
            } catch (error) {
                console.error("Poster deletion failed", error);
                message.textContent = error.message;
            } finally {
                buttons.forEach((item) => { item.disabled = false; });
            }
        });
    }

    for (const frame of document.querySelectorAll("[data-thumbnail]")) {
        const fit = () => {
            const poster = frame.contentDocument?.querySelector(".poster-document");
            if (!poster) {
                console.error("Poster thumbnail could not be loaded", frame.src);
                frame.parentElement.textContent = "Preview unavailable";
                return;
            }
            const width = Number(poster.dataset.width);
            const height = Number(poster.dataset.height);
            frame.style.width = `${width}px`;
            frame.style.height = `${height}px`;
            frame.style.transform = `scale(${Math.min(frame.parentElement.clientWidth / width, frame.parentElement.clientHeight / height)})`;
            frame.style.visibility = "visible";
        };
        frame.addEventListener("load", fit);
        new ResizeObserver(() => {
            if (frame.style.visibility === "visible") {
                fit();
            }
        }).observe(frame.parentElement);
        if (frame.contentDocument?.readyState === "complete" && frame.contentDocument.querySelector(".poster-document")) {
            fit();
        }
    }
})();
