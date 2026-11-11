/* Read-only session polling shared by regular pages and the layout editor. */
(() => {
    "use strict";

    let expiresAt = null;
    let deadline = null;
    let expiryTimer = null;
    let checking = false;
    let activityPending = false;
    let stopped = false;
    let name = "Friend";
    const warnings = new Map();
    function logout() {
        stopped = true;
        clearInterval(poll);
        clearTimers();
        window.location.assign("/account/signout");
    }

    function clearTimers() {
        clearTimeout(expiryTimer);
        for (const warning of warnings.values()) clearTimeout(warning);
        warnings.clear();
    }

    function warn(minutes) {
        const escaped = document.createElement("span");
        escaped.textContent = name;
        window.wikkit.say(
            `<p>${escaped.innerHTML}, your session will expire in ${numberToWords(minutes)} minutes.</p>`
        );
    }

    async function check() {
        if (checking || stopped) return;
        checking = true;
        const started = Date.now();
        const activity = activityPending;
        activityPending = false;
        try {
            const response = await fetch("/system/timecheck", {
                credentials: "same-origin", cache: "no-store",
                headers: activity ? { "X-Session-Activity": "1" } : {},
                signal: AbortSignal.timeout(10000),
            });
            if (stopped) return;
            if (response.redirected && new URL(response.url).pathname.replace(/\/$/, "") === "/account/signout") {
                logout();
                return;
            }
            if (!response.ok) throw new Error(`Session timecheck failed (${response.status}).`);
            const data = await response.json();
            if (stopped) return;
            if (!Number.isFinite(data.remaining_seconds) || !Number.isFinite(data.expires_at)
                || typeof data.name !== "string") {
                throw new Error("Session timecheck returned invalid data.");
            }
            name = data.name;
            const remaining = data.remaining_seconds * 1000 - (Date.now() - started);
            if (remaining <= 0) {
                logout();
                return;
            }
            if (expiresAt !== data.expires_at) {
                clearTimers();
                expiresAt = data.expires_at;
                deadline = Date.now() + remaining;
                // Recheck at the deadline in case another tab renewed the session.
                expiryTimer = setTimeout(check, remaining);
            }
            for (const [minutes, upper] of [[5, 7], [2, 4]]) {
                if (remaining < upper * 60000 && remaining > minutes * 60000
                    && !warnings.has(minutes)) {
                    warnings.set(minutes, setTimeout(() => {
                        if (!stopped) warn(minutes);
                    }, Math.max(0, deadline - Date.now() - minutes * 60000)));
                }
            }
        } catch (error) {
            if (activity) activityPending = true;
            console.error("Could not check the session expiry.", error);
            if (deadline !== null && Date.now() >= deadline) logout();
        } finally {
            checking = false;
        }
    }

    const poll = setInterval(check, 60000);
    check();
    for (const event of ["mousemove", "click", "touchstart", "keydown"]) {
        document.addEventListener(event, () => {
            activityPending = true;
        }, { passive: true });
    }
    window.addEventListener("pagehide", () => {
        stopped = true;
        clearInterval(poll);
        clearTimers();
    }, { once: true });
    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") check();
    });
})();
