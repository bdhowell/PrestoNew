(() => {
    "use strict";

    const message = document.querySelector(".poster-message");
    let downloading = false;

    async function downloadPoster(link) {
        if (downloading) return;
        downloading = true;
        message.textContent = "Preparing your printable PDF...";
        try {
            const endpoint = new URL(link.dataset.downloadUrl || link.href, location.href);
            const response = await fetch(endpoint.href, {credentials: "same-origin"});
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || "Your poster could not be downloaded. Please try again.");
            }
            if (response.headers.get("Content-Type")?.split(";")[0] !== "application/pdf") {
                throw new Error("The server did not return a printable PDF. Please sign in and try again.");
            }
            const url = URL.createObjectURL(await response.blob());
            const download = document.createElement("a");
            download.href = url;
            download.download = `poster-${endpoint.searchParams.get("pid")}.pdf`;
            document.body.append(download);
            download.click();
            download.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            const name = document.createElement("span");
            name.textContent = link.dataset.posterName;
            await window.wikkit.say(`<p>I&rsquo;ve downloaded a copy of ${name.innerHTML}.</p>`);
            message.textContent = "";
        } catch (error) {
            console.error("Poster download failed", error);
            message.textContent = error.message;
        } finally {
            downloading = false;
        }
    }

    for (const link of document.querySelectorAll("[data-download-poster]")) {
        link.addEventListener("click", event => {
            event.preventDefault();
            downloadPoster(link);
        });
    }
    const automatic = document.querySelector("[data-auto-download]");
    if (automatic) {
        const url = new URL(location.href);
        url.searchParams.delete("download");
        history.replaceState(null, "", url);
        downloadPoster(automatic);
    }

    async function post(url, body) {
        const response = await fetch(url, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify(body),
        });
        const reply = await response.json();
        if (reply.say) {
            await window.wikkit.say(reply.say);
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
            let reply = await post("/posters/check", {});
            if (reply.ok && reply.redirect === "/posters/create") {
                reply = await post(reply.redirect, {});
            }
            if (reply.existing_poster) {
                const response = await window.wikkit.requestResponse();
                reply = await post("/posters/open", { response });
            }
            if (reply.ok && reply.redirect) {
                location.assign(reply.redirect);
            }
        } catch (error) {
            console.error("New poster request failed", error);
            message.textContent = error.message;
        } finally {
            button.disabled = false;
        }
    });

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

    for (const frame of document.querySelectorAll("iframe[data-thumbnail]")) {
        const fit = () => {
            const poster = frame.contentDocument?.querySelector(".poster-document");
            if (!poster) {
                console.error("Poster thumbnail could not be loaded", frame.src);
                frame.parentElement.textContent = "Preview unavailable";
                return;
            }
            const width = Number(poster.dataset.width);
            const height = Number(poster.dataset.height);
            const scale = Math.min(frame.parentElement.clientWidth / width, frame.parentElement.clientHeight / height);
            frame.style.width = `${width}px`;
            frame.style.height = `${height}px`;
            frame.style.transform = `scale(${scale})`;
            frame.style.left = `${(frame.parentElement.clientWidth - width * scale) / 2}px`;
            frame.style.top = `${(frame.parentElement.clientHeight - height * scale) / 2}px`;
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
