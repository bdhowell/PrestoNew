(() => {
    "use strict";

    let leaving = false;
    window.addEventListener("pagehide", () => { leaving = true; }, { once: true });

    async function waitForLogin() {
        try {
            const response = await window.wikkit.requestResponse();
            if (response === "login") window.location.assign("/account/signin");
        } catch (error) {
            if (!leaving) console.error("Wikkit could not wait for the login response.", error);
        }
    }

    waitForLogin();
})();
