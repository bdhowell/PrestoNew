/*
 * Account router: submits .account-form forms as same-origin JSON (AJAX).
 *
 * Replies are {ok, say?, command?, signed_in?, redirect?, errors?}.
 * `signed_in` updates the shell menu before navigating. Wikkit says `say`; `errors` maps
 * field names to plain-text messages; `redirect` navigates this iframe page.
 * Forms with data-done="hide" are hidden after a successful submission.
 */
(() => {
    "use strict";

    function wikkitSay(html) {
        if (html && window.wikkit) {
            window.wikkit.say(html);
        }
    }

    function showErrors(form, errors) {
        for (const slot of form.querySelectorAll(".field-error")) {
            slot.textContent = errors?.[slot.dataset.for] ?? "";
        }
        for (const input of form.elements) {
            if (input.name) {
                input.toggleAttribute("aria-invalid", Boolean(errors?.[input.name]));
            }
        }
    }

    async function submit(form) {
        const message = form.querySelector(".form-message");
        const button = form.querySelector('[type="submit"]');
        const body = Object.fromEntries(new FormData(form));
        message.textContent = "";
        showErrors(form, null);
        button.disabled = true;
        try {
            const response = await fetch(form.action, {
                method: "POST",
                headers: { "Content-Type": "application/json", Accept: "application/json" },
                credentials: "same-origin",
                body: JSON.stringify(body),
            });
            let reply = null;
            try {
                reply = await response.json();
            } catch {
                reply = null;
            }
            if (!reply || typeof reply.ok !== "boolean") {
                message.textContent = reply?.detail ?? "Something went wrong. Please try again.";
                return;
            }
            if (typeof reply.signed_in === "boolean") {
                window.wikkit.setSignedIn(reply.signed_in);
            }
            if (reply.command) {
                await window.wikkit.execute(reply.command);
            }
            wikkitSay(reply.say);
            showErrors(form, reply.errors);
            if (reply.ok && reply.redirect) {
                location.assign(reply.redirect);
            } else if (reply.ok && form.dataset.done === "hide") {
                form.hidden = true;
            }
        } catch (error) {
            console.error("Account request failed", error);
            message.textContent = "I couldn't reach the server. Please try again.";
        } finally {
            button.disabled = false;
        }
    }

    function init() {
        for (const form of document.querySelectorAll("form.account-form")) {
            form.addEventListener("submit", (event) => {
                event.preventDefault();
                submit(form);
            });
            const recover = form.querySelector("[data-recover]");
            recover?.addEventListener("click", () => {
                const email = form.elements.email?.value.trim();
                location.assign(
                    "/account/recover" + (email ? `?${new URLSearchParams({ email })}` : "")
                );
            });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init, { once: true });
    } else {
        init();
    }
})();
