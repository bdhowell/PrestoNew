(() => {
    "use strict";

    const status = document.querySelector("#extraction-status");
    const retry = document.querySelector("#retry-extraction");
    const pending = JSON.parse(document.querySelector("#extraction-files").textContent);
    let running = false;
    let leaving = false;
    let controller = null;

    async function post(url) {
        const response = await fetch(url, {
            method: "POST", credentials: "same-origin",
            headers: {"Content-Type": "application/json"},
            body: "{}", signal: controller.signal,
        });
        const reply = await response.json();
        if (reply.say) {
            window.wikkit.say(reply.say);
        }
        if (!response.ok && !reply.say) {
            throw new Error(reply.detail ?? "Your content could not be read.");
        }
        return reply;
    }

    async function extract() {
        if (running) {
            return;
        }
        running = true;
        retry.hidden = true;
        controller = new AbortController();
        const timer = setInterval(() => {
            window.wikkit.say("<p>I&rsquo;m still reading through your content. Thanks for your patience.</p>");
        }, 20000);
        try {
            while (pending.length) {
                const file = pending[0];
                status.textContent = `Reading ${file.filename} (${pending.length} remaining)...`;
                await post(`/content/extract/${file.sid}`);
                pending.shift();
            }
            status.textContent = "Checking your extracted content...";
            const reply = await post("/content/extracted");
            if (reply.ok && reply.redirect) {
                status.textContent = "Your content has been read.";
                location.assign(reply.redirect);
            } else {
                status.textContent = "More source content is needed. Return to uploads to add files.";
            }
        } catch (error) {
            if (!leaving && error.name !== "AbortError") {
                console.error("Source extraction failed", error);
                status.textContent = error.message;
                const escaped = document.createElement("span");
                escaped.textContent = error.message;
                window.wikkit.say(`<p>${escaped.innerHTML}</p>`);
                retry.hidden = false;
            }
        } finally {
            running = false;
            clearInterval(timer);
        }
    }

    retry.addEventListener("click", extract);
    window.addEventListener("pagehide", () => {
        leaving = true;
        controller?.abort();
    });
    extract();
})();
