(() => {
    "use strict";

    const status = document.querySelector("#elucidation-status");
    const retry = document.querySelector("#retry-elucidation");
    const messages = JSON.parse(document.querySelector("#elucidation-messages").textContent);
    let running = false;
    let timer = null;
    let controller = null;
    let leaving = false;

    function scheduleProgress() {
        timer = setTimeout(() => {
            window.wikkit.say(`<p>${messages[Math.floor(Math.random() * messages.length)]}</p>`);
            scheduleProgress();
        }, 15000 + Math.floor(Math.random() * 15001));
    }

    async function analyze() {
        if (running) {
            return;
        }
        running = true;
        retry.hidden = true;
        status.textContent = "Wikkit is preparing your poster content.";
        controller = new AbortController();
        scheduleProgress();
        try {
            const response = await fetch("/content/elucidate", {
                method: "POST", credentials: "same-origin",
                headers: {"Content-Type": "application/json"},
                body: "{}", signal: controller.signal,
            });
            const reply = await response.json();
            if (!response.ok) {
                throw new Error(reply.detail ?? "Your poster content could not be prepared.");
            }
            status.textContent = "Your poster content is ready.";
            location.assign(reply.redirect);
        } catch (error) {
            if (!leaving && error.name !== "AbortError") {
                console.error("Poster elucidation failed", error);
                status.textContent = error.message;
                // Error details are text, not trusted HTML.
                const escaped = document.createElement("span");
                escaped.textContent = error.message;
                window.wikkit.say(`<p>${escaped.innerHTML}</p>`);
                retry.hidden = false;
            }
        } finally {
            running = false;
            clearTimeout(timer);
        }
    }

    retry.addEventListener("click", analyze);
    window.addEventListener("pagehide", () => {
        leaving = true;
        clearTimeout(timer);
        controller?.abort();
    });
    analyze();
})();
