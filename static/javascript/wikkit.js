/*
 * Wikkit chatbot client.
 *
 * Loaded on every page. Owns the in-page overlay and websocket, restoring
 * the overlay's content and geometry from this tab's sessionStorage.
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

    const storageKey = "wikkit.chatbot";
    let host = null;

    function createChatbot() {
        const chatbot = document.createElement("section");
        chatbot.id = "chatbot";
        chatbot.setAttribute("aria-label", "Wikkit chatbot");
        chatbot.dataset.socket = document.body.dataset.socket || "/wikkit/ws";
        chatbot.style.setProperty("--chat-window-transition", `${document.body.dataset.chatWindowTransition || 440}ms`);
        chatbot.style.setProperty("--chat-input-transition", `${document.body.dataset.chatInputTransition || 500}ms`);
        chatbot.innerHTML = '<header class="chatbot-handle">Wikkit</header>'
            + '<div id="chatlog" role="log" aria-live="polite"></div>'
            + '<div id="chatinput" contenteditable="plaintext-only" role="textbox"'
            + ' aria-label="Message Wikkit" aria-multiline="true"></div>';
        try {
            const saved = JSON.parse(sessionStorage.getItem(storageKey) || "null");
            if (saved) {
                if (typeof saved.content !== "string" || !saved.geometry
                    || !["left", "top", "width", "height"].every((key) => Number.isFinite(saved.geometry[key]))
                    || saved.geometry.width <= 0 || saved.geometry.height <= 0) {
                    throw new Error("Invalid saved chatbot state.");
                }
                const content = document.createElement("template");
                content.innerHTML = saved.content;
                if (!content.content.querySelector(".chatbot-handle")
                    || !content.content.querySelector("#chatlog")
                    || !content.content.querySelector("#chatinput")) {
                    throw new Error("Saved chatbot content is missing required elements.");
                }
                chatbot.replaceChildren(content.content.cloneNode(true));
                for (const key of ["left", "top", "width", "height"]) {
                    chatbot.style[key] = `${saved.geometry[key]}px`;
                }
                chatbot.style.right = "auto";
                chatbot.style.bottom = "auto";
                for (const name of ["chat-hidden", "input-closed"]) {
                    chatbot.classList.toggle(name, saved[name] === true);
                }
                chatbot.inert = chatbot.classList.contains("chat-hidden");
                if (chatbot.inert) {
                    chatbot.setAttribute("aria-hidden", "true");
                }
            }
        } catch (error) {
            console.error("Wikkit could not restore its saved state", error);
        }
        if (document.body.dataset.clearChat === "true") {
            chatbot.querySelector("#chatlog").replaceChildren();
        }
        document.body.appendChild(chatbot);
        function keepInView() {
            const rect = chatbot.getBoundingClientRect();
            if (chatbot.style.left || rect.left < 0 || rect.top < 0) {
                chatbot.style.left = `${Math.max(0, Math.min(rect.left, innerWidth - rect.width))}px`;
                chatbot.style.top = `${Math.max(0, Math.min(rect.top, innerHeight - rect.height))}px`;
                chatbot.style.right = "auto";
                chatbot.style.bottom = "auto";
            }
        }
        keepInView();
        window.addEventListener("resize", keepInView);
        return chatbot;
    }

    function persistChatbot(chatbot) {
        function save() {
            const clone = chatbot.cloneNode(true);
            // Live form values are properties, not necessarily serialized attributes.
            const controls = chatbot.querySelectorAll("input, textarea, select");
            clone.querySelectorAll("input, textarea, select").forEach((copy, index) => {
                const control = controls[index];
                if (control.tagName === "TEXTAREA") {
                    copy.textContent = control.value;
                } else if (control.tagName === "SELECT") {
                    [...copy.options].forEach((option, i) => {
                        option.toggleAttribute("selected", control.options[i].selected);
                    });
                } else {
                    if (control.type !== "file") {
                        copy.setAttribute("value", control.value);
                    }
                    copy.toggleAttribute("checked", control.checked);
                }
            });
            const rect = chatbot.getBoundingClientRect();
            try {
                sessionStorage.setItem(storageKey, JSON.stringify({
                    content: clone.innerHTML,
                    geometry: { left: rect.left, top: rect.top, width: rect.width, height: rect.height },
                    "chat-hidden": chatbot.classList.contains("chat-hidden"),
                    "input-closed": chatbot.classList.contains("input-closed"),
                }));
            } catch (error) {
                console.error("Wikkit could not save its state", error);
            }
        }
        const mutations = new MutationObserver(save);
        mutations.observe(chatbot, { subtree: true, childList: true, characterData: true, attributes: true });
        const resize = new ResizeObserver(save);
        resize.observe(chatbot);
        chatbot.addEventListener("input", save);
        chatbot.addEventListener("change", save);
        window.addEventListener("resize", save);
        save();
        return save;
    }

    function createHost(chatbot) {
        const chatlog = chatbot.querySelector("#chatlog");
        const chatinput = chatbot.querySelector("#chatinput");
        chatinput.contentEditable = "plaintext-only";
        const handle = chatbot.querySelector(".chatbot-handle");
        const pending = [];
        const attachmentWaiters = [];
        let client = null;
        let closed = false;
        let reminderTimer = null;
        let requestingResponse = false;
        let pageResponse = null;
        let memoryRequestId = 0;
        let leaving = false;
        const memoryRequests = new Map();
        const reminderInterval = 120000;

        function append(div) {
            chatlog.appendChild(div);
            chatlog.scrollTop = chatlog.scrollHeight;
        }

        function say(html) {
            const div = document.createElement("div");
            div.className = "chat";
            div.innerHTML = html;
            prepareControls(div);
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

        function clearChat() {
            chatlog.innerHTML = "";
        }

        function hideChat() {
            chatbot.classList.add("chat-hidden");
            chatbot.inert = true;
            chatbot.setAttribute("aria-hidden", "true");
        }

        function showChat() {
            chatbot.inert = false;
            chatbot.removeAttribute("aria-hidden");
            chatbot.classList.remove("chat-hidden");
        }

        document.getElementById("wikkit")?.addEventListener("click", () => {
            if (chatbot.classList.contains("chat-hidden")) {
                showChat();
            } else {
                hideChat();
            }
        });

        function closeInput() {
            if (document.activeElement === chatinput) {
                chatinput.blur();
            }
            chatinput.inert = true;
            chatinput.setAttribute("aria-hidden", "true");
            chatbot.classList.add("input-closed");
        }

        function openInput() {
            chatinput.inert = false;
            chatinput.removeAttribute("aria-hidden");
            chatbot.classList.remove("input-closed");
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
                save();
                location.assign(url.href);
            }],
            ["request", requestResponse],
            ["request-response", requestResponse],
            ["clear-chat", clearChat],
            ["hide-chat", hideChat],
            ["show-chat", showChat],
            ["close-input", closeInput],
            ["open-input", openInput],
            ["hide-close", () => {
                hideChat();
                closeInput();
            }],
            ["show-open", () => {
                showChat();
                openInput();
            }],
        ]);

        const scheme = location.protocol === "https:" ? "wss:" : "ws:";
        const socket = new WebSocket(`${scheme}//${location.host}${chatbot.dataset.socket}`);

        function send(text, response = true) {
            if (socket.readyState === WebSocket.OPEN) {
                if (response && text.trim()) {
                    stopRequest();
                }
                socket.send(text);
            } else if (socket.readyState === WebSocket.CONNECTING) {
                if (response && text.trim()) {
                    stopRequest();
                }
                pending.push(text);
            } else {
                sayText("Wikkit is not connected.", "notice");
                return false;
            }
            return true;
        }

        function submitResponse(text, echo = true) {
            if (!text.trim() || text.length > 2000) {
                sayText(text.trim() ? "That message is too long. Please keep it under 2000 characters."
                    : "Please enter a response.", "notice");
                return false;
            }
            if (pageResponse) {
                const response = pageResponse;
                pageResponse = null;
                stopRequest();
                response.resolve(text);
            } else if (!send(text)) {
                return false;
            }
            if (echo) {
                sayText(text, "user");
            }
            return true;
        }

        function remember(control) {
            if (!control.hasAttribute("data-memory")) {
                return Promise.resolve();
            }
            const id = ++memoryRequestId;
            return new Promise((resolve, reject) => {
                memoryRequests.set(id, { resolve, reject });
                if (!send(JSON.stringify({ memory: {
                    id, scope: control.dataset.memory, name: control.dataset.name,
                    value: control.value,
                } }), false)) {
                    memoryRequests.delete(id);
                    reject(new Error("Wikkit is not connected."));
                }
            });
        }

        function prepareControls(div) {
            // Controls in chatbot output must not submit surrounding HTML forms.
            div.addEventListener("submit", (event) => event.preventDefault());
            for (const button of div.querySelectorAll("button:not(.embedded-ok)")) {
                button.type = "button";
                button.addEventListener("click", async () => {
                    if (button.disabled) {
                        return;
                    }
                    const group = button.dataset.group;
                    const buttons = group === undefined ? [button]
                        : [...chatlog.querySelectorAll("button[data-group]")]
                            .filter((other) => other.dataset.group === group);
                    const enabled = buttons.filter((other) => !other.disabled);
                    enabled.forEach((other) => { other.disabled = true; });
                    try {
                        if (button.hasAttribute("data-sends")
                            && !submitResponse(button.dataset.sends, false)) {
                            enabled.forEach((other) => { other.disabled = closed; });
                            return;
                        }
                        if (button.hasAttribute("data-does")) {
                            await executeCommand({ does: button.dataset.does });
                        }
                        buttons.forEach((other) => {
                            other.classList.remove("selected");
                            other.setAttribute("aria-pressed", "false");
                        });
                        button.classList.add("selected");
                        button.setAttribute("aria-pressed", "true");
                    } catch (error) {
                        // A sent decision cannot be retracted even if its command fails.
                        if (!button.hasAttribute("data-sends")) {
                            enabled.forEach((other) => { other.disabled = closed; });
                        }
                        commandFailed(error);
                    }
                });
            }
            for (const control of div.querySelectorAll("input, textarea, select")) {
                const hasMemory = control.hasAttribute("data-memory");
                const hasName = control.hasAttribute("data-name");
                if (hasMemory !== hasName || (hasMemory && (
                    !["user", "session", "poster"].includes(control.dataset.memory)
                    || !control.dataset.name.trim()
                ))) {
                    control.disabled = true;
                    const error = new Error("Embedded controls require both a valid data-memory and a nonempty data-name, or neither.");
                    console.error("Wikkit embedded control failed", error);
                    sayText(error.message, "notice");
                    continue;
                }
                const select = control.tagName === "SELECT";
                let ok = null;
                if (select) {
                    control.multiple = false;
                    control.size = 1;
                } else {
                    ok = control.nextElementSibling?.matches("button.embedded-ok")
                        ? control.nextElementSibling : document.createElement("button");
                    ok.type = "button";
                    ok.className = "embedded-ok";
                    ok.textContent = "OK";
                    ok.disabled = control.disabled;
                    control.after(ok);
                }
                const submit = async () => {
                    if (control.disabled || !control.reportValidity()) {
                        return;
                    }
                    const value = control.value;
                    if (!value.trim() || value.length > 2000) {
                        sayText(value.trim() ? "That message is too long. Please keep it under 2000 characters."
                            : "Please enter a response.", "notice");
                        return;
                    }
                    control.disabled = true;
                    if (ok) {
                        ok.disabled = true;
                    }
                    try {
                        await remember(control);
                        if (submitResponse(value, false)) {
                            return;
                        }
                    } catch (error) {
                        console.error("Wikkit embedded control failed", error);
                        sayText(error.message, "notice");
                    }
                    control.disabled = closed;
                    if (ok) {
                        ok.disabled = closed;
                    }
                };
                control.addEventListener("input", resetReminder);
                if (select) {
                    control.addEventListener("change", submit);
                } else {
                    ok.addEventListener("click", submit);
                    if (control.tagName === "INPUT") {
                        control.addEventListener("keydown", (event) => {
                            if (event.key === "Enter" && !event.isComposing) {
                                event.preventDefault();
                                submit();
                            }
                        });
                    }
                }
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

        function updateMenu(signedIn) {
            if (typeof signedIn !== "boolean") {
                throw new TypeError("Sign-in state must be a boolean.");
            }
            const link = document.querySelector('.basic-menu a[href="/account/signin"], .basic-menu a[href="/account/signout"]');
            if (link) {
                link.href = signedIn ? "/account/signout" : "/account/signin";
                link.textContent = signedIn ? "Sign out" : "Sign in";
            }
            const postersMenu = document.querySelector("[data-posters-menu]");
            if (postersMenu) {
                postersMenu.hidden = !signedIn;
            }
        }

        function commandFailed(error) {
            console.error("Wikkit command failed", error);
            sayText("Wikkit could not complete a page action. Please try again.", "notice");
        }

        let delivery = Promise.resolve();

        function deliver(message) {
            if (message.memory) {
                const result = message.memory;
                const request = memoryRequests.get(result.id);
                if (request) {
                    memoryRequests.delete(result.id);
                    if (result.ok === true) {
                        request.resolve();
                    } else {
                        request.reject(new Error(result.error || "Wikkit could not store that value."));
                    }
                }
                return;
            }
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
            if (leaving) {
                return;
            }
            closed = true;
            for (const request of memoryRequests.values()) {
                request.reject(new Error("The connection to Wikkit has closed."));
            }
            memoryRequests.clear();
            chatlog.querySelectorAll("button, input, textarea, select").forEach((control) => {
                control.disabled = true;
            });
            stopRequest();
            if (pageResponse) {
                pageResponse.reject(new Error("The connection to Wikkit has closed."));
                pageResponse = null;
            }
            pending.length = 0;
            for (const resolve of attachmentWaiters.splice(0)) {
                resolve();
            }
            chatbot.classList.add("disconnected");
            chatinput.contentEditable = "false";
            sayText(event.reason || "The connection to Wikkit has closed.", "notice");
        });

        chatinput.addEventListener("input", resetReminder);
        window.addEventListener("pagehide", () => {
            leaving = true;
            stopRequest();
            save();
            socket.close();
        }, { once: true });
        window.addEventListener("pageshow", (event) => {
            if (event.persisted) {
                location.reload();
            }
        });

        chatinput.addEventListener("keydown", (event) => {
            if (event.key !== "Enter" || event.shiftKey || event.isComposing) {
                return;
            }
            event.preventDefault();
            const text = chatinput.innerText.trim();
            if (text) {
                if (submitResponse(text)) {
                    chatinput.replaceChildren();
                }
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

        for (const message of [...chatlog.children]) {
            prepareControls(message);
        }
        chatlog.scrollTop = chatlog.scrollHeight;
        const save = persistChatbot(chatbot);

        return Object.freeze({
            attach(win, listen, dispatch, ready) {
                if (client && client.win === win) {
                    client.listen = listen;
                    return client.apiobj;
                }
                const apiobj = Object.freeze({
                    execute: (command) => executeCommand(command),
                    setSignedIn: updateMenu,
                    // Appends text or an HTML fragment to #chatlog as <div class="chat">.
                    say: (html) => say(String(html)),
                    requestResponse: () => {
                        if (closed || pageResponse || requestingResponse) {
                            return Promise.reject(new Error("Wikkit is unavailable or already waiting for a response."));
                        }
                        showChat();
                        openInput();
                        requestResponse();
                        chatinput.focus();
                        return new Promise((resolve, reject) => {
                            pageResponse = { resolve, reject, win };
                        });
                    },
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
                if (dataset.signedIn === "true" || dataset.signedIn === "false") {
                    updateMenu(dataset.signedIn === "true");
                }
                announcePage(dataset.page);
                for (const resolve of attachmentWaiters.splice(0)) {
                    resolve();
                }
                win.addEventListener("pagehide", () => {
                    if (client === entry) {
                        client = null;
                    }
                    if (pageResponse?.win === win) {
                        pageResponse.reject(new Error("The page was closed before Wikkit received a response."));
                        pageResponse = null;
                        stopRequest();
                    }
                }, { once: true });
                return apiobj;
            },
        });
    }

    function initHost() {
        if (!host) {
            host = createHost(createChatbot());
            window.Wikkit = host;
        }
        return host;
    }

    window.connect = function connect(listen) {
        if (typeof listen !== "function") {
            throw new TypeError("connect(listen) requires a callback function.");
        }
        const host = initHost();
        return host.attach(window, listen, dispatchPageCommand, pageReady);
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initHost, { once: true });
    } else {
        initHost();
    }
})();
