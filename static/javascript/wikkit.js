/*
 * Wikkit chatbot client.
 *
 * Loaded by /wikkit, where it runs the #chatbot overlay and its websocket, and
 * by every page shown in the /wikkit iframe, where connect(listen) binds the
 * page to that overlay and returns the apiobj singleton.
 */
(() => {
    "use strict";

    const pageCommands = new Map();
    const pageReady = document.readyState === "loading"
        ? new Promise((resolve) => document.addEventListener("DOMContentLoaded", resolve, { once: true }))
        : Promise.resolve();

    window.registerWikkitCommands = function registerWikkitCommands({ commands }) {
        if (!commands || typeof commands !== "object" || Array.isArray(commands)) {
            throw new TypeError("commands must be an object.");
        }
        const entries = Object.entries(commands);
        for (const [name, command] of entries) {
            if (!name.trim() || !command || typeof command.does !== "function") {
                throw new TypeError(`Command ${name} must contain a does function.`);
            }
        }
        for (const [name, command] of entries) {
            pageCommands.set(name, { does: command.does, data: command.data });
        }
    };

    async function dispatchPageCommand(command) {
        await pageReady;
        const handler = pageCommands.get(command.does);
        if (!handler) {
            return false;
        }
        const data = command.payload === undefined ? handler.data : command.payload;
        await handler.does(data ?? null);
        return true;
    }

    function createHost(chatbot, frame) {
        const chatlog = chatbot.querySelector("#chatlog");
        const chatinput = chatbot.querySelector("#chatinput");
        const handle = chatbot.querySelector(".chatbot-handle");
        const pending = [];
        const attachmentWaiters = [];
        let client = null;
        let closed = false;
        let reminderTimer = null;
        let requestingResponse = false;
        const reminderInterval = 120000;

        function append(div) {
            chatlog.appendChild(div);
            chatlog.scrollTop = chatlog.scrollHeight;
        }

        function say(html) {
            const div = document.createElement("div");
            div.className = "chat";
            div.innerHTML = html;
            append(div);
        }

        function sayText(text, kind) {
            const div = document.createElement("div");
            div.className = `chat ${kind}`;
            div.textContent = text;
            append(div);
        }

        function stopRequest() {
            requestingResponse = false;
            clearTimeout(reminderTimer);
            reminderTimer = null;
        }

        function resetReminder() {
            clearTimeout(reminderTimer);
            if (requestingResponse) {
                reminderTimer = setTimeout(() => {
                    sayText("Are you still there? Let me know if you need help.", "notice");
                    resetReminder();
                }, reminderInterval);
            }
        }

        function requestResponse() {
            requestingResponse = true;
            resetReminder();
        }

        const intrinsicCommands = new Map([
            ["redirect", (payload) => {
                if (typeof payload !== "string") {
                    throw new TypeError("redirect requires a URL string.");
                }
                const url = new URL(payload, location.href);
                if (url.origin !== location.origin || !["http:", "https:"].includes(url.protocol)) {
                    throw new Error("Wikkit can only redirect to same-origin pages.");
                }
                client = null;
                frame.src = url.href;
            }],
            ["request", requestResponse],
            ["request-response", requestResponse],
        ]);

        const scheme = location.protocol === "https:" ? "wss:" : "ws:";
        const socket = new WebSocket(`${scheme}//${location.host}${chatbot.dataset.socket}`);

        function send(text) {
            if (socket.readyState === WebSocket.OPEN) {
                if (text.trim()) {
                    stopRequest();
                }
                socket.send(text);
            } else if (socket.readyState === WebSocket.CONNECTING) {
                if (text.trim()) {
                    stopRequest();
                }
                pending.push(text);
            } else {
                sayText("Wikkit is not connected.", "notice");
            }
        }

        async function executeCommand(command) {
            if (closed) {
                return;
            }
            if (!command || typeof command !== "object") {
                throw new TypeError("Wikkit command must be an object.");
            }
            const normalized = {
                does: command.does ?? command.action,
                payload: command.payload,
            };
            if (typeof normalized.does !== "string" || !normalized.does.trim()) {
                throw new TypeError("Wikkit command requires a does key.");
            }
            const entry = client;
            if (!entry) {
                await new Promise((resolve) => attachmentWaiters.push(resolve));
                return executeCommand(normalized);
            }
            await entry.ready;
            if (closed) {
                return;
            }
            if (client !== entry) {
                return executeCommand(normalized);
            }
            if (await entry.dispatch(normalized)) {
                return;
            }
            const intrinsic = intrinsicCommands.get(normalized.does);
            if (!intrinsic) {
                throw new Error(`Unknown Wikkit client command: ${normalized.does}`);
            }
            await intrinsic(normalized.payload);
        }

        // Lets the server cancel the previous page's workflow and start this page's.
        function announcePage(page) {
            if (!page) {
                return;
            }
            stopRequest();
            const message = JSON.stringify({ page });
            if (socket.readyState === WebSocket.OPEN) {
                socket.send(message);
            } else if (socket.readyState === WebSocket.CONNECTING) {
                pending.push(message);
            }
        }

        // The shell is rendered once, so iframe pages report sign-in changes.
        function updateMenu(signedIn) {
            const link = document.querySelector('.basic-menu a[href="/account/signin"], .basic-menu a[href="/account/signout"]');
            if (link && (signedIn === "true" || signedIn === "false")) {
                const inside = signedIn === "true";
                link.href = inside ? "/account/signout" : "/account/signin";
                link.textContent = inside ? "Sign out" : "Sign in";
            }
        }

        // Menu links navigate the iframe rather than replacing the shell.
        document.querySelector(".basic-menu")?.addEventListener("click", (event) => {
            const link = event.target.closest("a[href]");
            if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
                return;
            }
            const url = new URL(link.href, location.href);
            if (url.origin === location.origin) {
                event.preventDefault();
                frame.src = url.href;
            }
        });

        function commandFailed(error) {
            console.error("Wikkit command failed", error);
            sayText("Wikkit could not complete a page action. Please try again.", "notice");
        }

        let delivery = Promise.resolve();

        function deliver(message) {
            const html = message.say ?? message.user;
            if (typeof html === "string" && html) {
                say(html);
            }
            const entry = client;
            if (entry) {
                try {
                    entry.listen(message);
                } catch (error) {
                    console.error("Wikkit listener failed", error);
                }
            }
            const command = message.command ?? message.client;
            if (!command) {
                return;
            }
            delivery = delivery.then(() => executeCommand(command)).catch(commandFailed);
        }

        socket.addEventListener("open", () => {
            while (pending.length) {
                socket.send(pending.shift());
            }
        });
        socket.addEventListener("message", (event) => {
            let message;
            try {
                message = JSON.parse(event.data);
            } catch (error) {
                console.error("Wikkit received invalid JSON", error);
                return;
            }
            if (message && typeof message === "object") {
                deliver(message);
            }
        });
        socket.addEventListener("close", (event) => {
            closed = true;
            stopRequest();
            pending.length = 0;
            for (const resolve of attachmentWaiters.splice(0)) {
                resolve();
            }
            chatbot.classList.add("disconnected");
            chatinput.contentEditable = "false";
            sayText(event.reason || "The connection to Wikkit has closed.", "notice");
        });

        chatinput.addEventListener("input", resetReminder);
        window.addEventListener("pagehide", stopRequest, { once: true });

        chatinput.addEventListener("keydown", (event) => {
            if (event.key !== "Enter" || event.shiftKey || event.isComposing) {
                return;
            }
            event.preventDefault();
            const text = chatinput.innerText.trim();
            chatinput.replaceChildren();
            if (text) {
                sayText(text, "user");
                send(text);
            }
        });

        handle.addEventListener("pointerdown", (event) => {
            if (event.button !== 0) {
                return;
            }
            const rect = chatbot.getBoundingClientRect();
            const offsetX = event.clientX - rect.left;
            const offsetY = event.clientY - rect.top;
            const clamp = (value, max) => Math.min(Math.max(value, 0), Math.max(max, 0));
            const move = (moveEvent) => {
                chatbot.style.left = `${clamp(moveEvent.clientX - offsetX, innerWidth - chatbot.offsetWidth)}px`;
                chatbot.style.top = `${clamp(moveEvent.clientY - offsetY, innerHeight - chatbot.offsetHeight)}px`;
                chatbot.style.right = "auto";
                chatbot.style.bottom = "auto";
            };
            handle.setPointerCapture(event.pointerId);
            chatbot.classList.add("dragging");
            handle.addEventListener("pointermove", move);
            handle.addEventListener("lostpointercapture", () => {
                handle.removeEventListener("pointermove", move);
                chatbot.classList.remove("dragging");
            }, { once: true });
            event.preventDefault();
        });

        // Loading the iframe only after the host exists guarantees connect() finds it.
        frame.src = frame.dataset.src;

        return Object.freeze({
            attach(win, listen, dispatch, ready) {
                if (win.frameElement !== frame) {
                    throw new Error("connect() must be called from the Wikkit iframe.");
                }
                if (client && client.win === win) {
                    client.listen = listen;
                    return client.apiobj;
                }
                const apiobj = Object.freeze({
                    // Appends text or an HTML fragment to #chatlog as <div class="chat">.
                    say: (html) => say(String(html)),
                    // Sends text to the server; with echo, also shows it via say().
                    command: (html, echo = false) => {
                        if (echo) {
                            say(String(html));
                        }
                        send(String(html));
                    },
                });
                const entry = { win, listen, apiobj, dispatch, ready };
                client = entry;
                const dataset = win.document.body?.dataset ?? {};
                updateMenu(dataset.signedIn);
                announcePage(dataset.page);
                for (const resolve of attachmentWaiters.splice(0)) {
                    resolve();
                }
                win.addEventListener("pagehide", () => {
                    if (client === entry) {
                        client = null;
                    }
                }, { once: true });
                return apiobj;
            },
        });
    }

    function initHost() {
        const chatbot = document.getElementById("chatbot");
        const frame = document.getElementById("wikkit-frame");
        if (chatbot && frame) {
            window.Wikkit = createHost(chatbot, frame);
        }
    }

    function findHost() {
        try {
            return window.parent !== window ? window.parent.Wikkit ?? null : null;
        } catch {
            return null;  // cross-origin parent
        }
    }

    // Reopens a page opened outside the shell (e.g. from an email link) inside it.
    function openInShell() {
        const form = document.createElement("form");
        form.method = "post";
        form.action = "/wikkit";
        const url = document.createElement("input");
        url.type = "hidden";
        url.name = "url";
        url.value = location.pathname + location.search + location.hash;
        form.appendChild(url);
        document.documentElement.appendChild(form);
        form.submit();
    }

    const inert = Object.freeze({ say: () => {}, command: () => {} });

    window.connect = function connect(listen) {
        if (typeof listen !== "function") {
            throw new TypeError("connect(listen) requires a callback function.");
        }
        const host = findHost();
        if (!host && window.top === window && !document.getElementById("chatbot")) {
            openInShell();
            return inert;
        }
        if (!host) {
            throw new Error("connect() must be called from a page loaded in the /wikkit iframe.");
        }
        return host.attach(window, listen, dispatchPageCommand, pageReady);
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initHost, { once: true });
    } else {
        initHost();
    }
})();
