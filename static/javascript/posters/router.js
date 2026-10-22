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
            if (!window.confirm(`Delete "${button.dataset.posterName}"? This cannot be undone.`)) {
                return;
            }
            button.disabled = true;
            message.textContent = "";
            try {
                await post(`/posters/${button.dataset.deletePoster}/delete`, { confirmed: true });
                location.reload();
            } catch (error) {
                console.error("Poster deletion failed", error);
                message.textContent = error.message;
                button.disabled = false;
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
