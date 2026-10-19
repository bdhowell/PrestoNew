/*
 * Wikkit chatbot client.
 *
 * Loaded by /wikkit, where it runs the #chatbot overlay and its websocket, and
 * by every page shown in the /wikkit iframe, where connect(listen) binds the
 * page to that overlay and returns the apiobj singleton.
 */
(() => {
    "use strict";

    function createHost(chatbot, frame) {
        const chatlog = chatbot.querySelector("#chatlog");
        const chatinput = chatbot.querySelector("#chatinput");
        const handle = chatbot.querySelector(".chatbot-handle");
        const pending = [];
        let client = null;

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

        const scheme = location.protocol === "https:" ? "wss:" : "ws:";
        const socket = new WebSocket(`${scheme}//${location.host}${chatbot.dataset.socket}`);

        function send(text) {
            if (socket.readyState === WebSocket.OPEN) {
                socket.send(text);
            } else if (socket.readyState === WebSocket.CONNECTING) {
                pending.push(text);
            } else {
                sayText("Wikkit is not connected.", "notice");
            }
        }

        function deliver(message) {
            if (typeof message.user === "string" && message.user) {
                say(message.user);
            }
            if (client) {
                try {
                    client.listen(message);
                } catch (error) {
                    console.error("Wikkit listener failed", error);
                }
            }
            const command = message.client;
            if (!command) {
                return;
            }
            if (command.action === "redirect" && typeof command.payload === "string") {
                frame.src = command.payload;
            } else {
                console.warn("Unknown Wikkit client command", command);
            }
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
            pending.length = 0;
            chatbot.classList.add("disconnected");
            chatinput.contentEditable = "false";
            sayText(event.reason || "The connection to Wikkit has closed.", "notice");
        });

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
            attach(win, listen) {
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
                const entry = { win, listen, apiobj };
                client = entry;
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

    window.connect = function connect(listen) {
        if (typeof listen !== "function") {
            throw new TypeError("connect(listen) requires a callback function.");
        }
        const host = findHost();
        if (!host) {
            throw new Error("connect() must be called from a page loaded in the /wikkit iframe.");
        }
        return host.attach(window, listen);
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initHost, { once: true });
    } else {
        initHost();
    }
})();
