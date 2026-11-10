(() => {
    "use strict";

    const layout = document.getElementById("layout");
    if (!layout) return;

    const pid = Number(layout.dataset.pid);
    const interval = Number(layout.dataset.profileValidationInterval);
    const status = document.getElementById("content-save-status");
    const attentionClass = "layout-profile-attention";
    const contextTargetClass = "layout-profile-context-target";
    const labels = {
        abstract: "Abstract",
        acknowledgements: "Acknowledgements",
        conclusion: "Conclusion",
        introduction: "Introduction",
        information: "Additional Information",
        methods: "Materials and Methods",
        results: "Results",
        plans: "Future Plans",
        citations: "Literature Cited",
        title: "Title",
    };
    let timer = null;
    let revision = 0;
    let activeSection = null;
    let contextMenu = null;
    let contentTimer = null;
    let latestProfile = null;
    const editedSections = new Set();

    if (!Number.isSafeInteger(pid) || pid <= 0
        || !Number.isSafeInteger(interval) || interval < 0) {
        console.error("Could not initialize poster layout profiling", {
            pid: layout.dataset.pid,
            interval: layout.dataset.profileValidationInterval,
        });
        if (status) status.textContent = "Your layout could not be checked. Please reload to try again.";
        return;
    }

    function showMessage(lines) {
        const message = document.createElement("div");
        const intro = document.createElement("p");
        intro.textContent = "A little wizardly note: a few sections may need your attention.";
        message.append(intro);

        const list = document.createElement("ul");
        for (const line of lines) {
            const item = document.createElement("li");
            item.textContent = line;
            list.append(item);
        }
        message.append(list);
        const hint = document.createElement("p");
        hint.textContent = "Right-click (or Command-click on a Mac) a highlighted section for section-specific actions.";
        message.append(hint);

//         says += """
// <p>
// I can shorten, lengthen, or adjust the fit of any section of your poster.
// I can change the tone of any section to better suit your message. 
// I can also help you add images to sections of your poster—although I recommend you limit them to <em>Results</em>, and maybe <em>Methods and Materials</em>.
// </p>
// """

        if (typeof window.wikkit?.say === "function") {
            window.wikkit.say(message.innerHTML);
        } else if (status) {
            status.textContent = message.textContent;
        }
    }

    function sectionElement(name) {
        return document.getElementById(name === "conclusion" ? "conclusions" : name);
    }

    function closeContextMenu() {
        contextMenu?.remove();
        contextMenu = null;
        activeSection?.classList.remove(contextTargetClass);
        activeSection = null;
        document.documentElement.classList.remove("layout-context-menu-open");
    }

    function positionContextMenu(menu, x, y) {
        menu.style.left = `${x}px`;
        menu.style.top = `${y}px`;
        const bounds = menu.getBoundingClientRect();
        menu.style.left = `${Math.max(8, Math.min(x, innerWidth - bounds.width - 8))}px`;
        menu.style.top = `${Math.max(8, Math.min(y, innerHeight - bounds.height - 8))}px`;
    }

    function openContextMenu(section, x, y) {
        closeContextMenu();
        const editor = section.querySelector(".content[contenteditable='true']");
        const menu = document.createElement("div");
        menu.id = "layout-context-menu";
        menu.setAttribute("role", "menu");
        menu.setAttribute("aria-label", `${labels[section.id] || section.id} actions`);

        for (const [action, label, available] of [
            ["auto-fit", "Auto-fit (not available yet)", false],
            ["resize-graphics", "Resize graphics (not available yet)", false],
            ["adjust-text", "Adjust text content", Boolean(editor)],
        ]) {
            const button = document.createElement("button");
            button.type = "button";
            button.dataset.contextAction = action;
            button.setAttribute("role", "menuitem");
            button.textContent = label;
            button.disabled = !available;
            menu.append(button);
        }

        menu.addEventListener("click", event => {
            const button = event.target.closest("button[data-context-action]");
            if (!button || button.disabled) return;
            closeContextMenu();
            if (button.dataset.contextAction === "adjust-text" && editor?.isConnected) {
                editor.scrollIntoView({block: "nearest"});
                editor.focus({preventScroll: true});
            }
        });
        document.body.append(menu);
        contextMenu = menu;
        activeSection = section;
        section.classList.add(contextTargetClass);
        document.documentElement.classList.add("layout-context-menu-open");
        positionContextMenu(menu, x, y);
        menu.querySelector("button:not(:disabled)")?.focus();
    }

    function applyProfile(profile) {
        if (!profile || !profile.sections || !Array.isArray(profile.overflowing_sections)
            || !Array.isArray(profile.underflowing_sections)
            || !Array.isArray(profile.oversized_graphics)) {
            throw new Error("The profiler returned invalid section measurements.");
        }

        latestProfile = profile;
        const liveOverflow = new Set(profile.overflowing_sections);
        const liveUnderflow = new Set(profile.underflowing_sections);
        for (const section of editedSections) {
            if (!layout.contains(section) || section.clientHeight <= 0) continue;
            const name = section.id === "conclusions" ? "conclusion" : section.id;
            liveOverflow.delete(name);
            liveUnderflow.delete(name);
            if (section.scrollHeight > section.clientHeight) liveOverflow.add(name);
        }
        const overflowing = liveOverflow;
        const underflowing = liveUnderflow;
        const oversized = new Map();
        for (const graphic of profile.oversized_graphics) {
            if (typeof graphic.section !== "string") {
                throw new Error("The profiler returned an invalid graphic measurement.");
            }
            oversized.set(graphic.section, (oversized.get(graphic.section) || 0) + 1);
        }

        const attention = new Set([...overflowing, ...underflowing, ...oversized.keys()]);
        const outlined = new Set();
        const feedback = [];
        for (const name of attention) {
            const section = sectionElement(name);
            if (!section?.classList.contains("section") || !layout.contains(section)) continue;
            outlined.add(section);
            const label = labels[name] || name;
            if (overflowing.has(name)) {
                feedback.push(`${label} is overflowing; try shortening its text or resizing a graphic.`);
            } else if (underflowing.has(name)) {
                feedback.push(`${label} has room for more content; consider adding a little text or a graphic.`);
            }
            if (oversized.has(name)) {
                feedback.push(`${label} has ${oversized.get(name) === 1 ? "a graphic" : "graphics"} that may be too large; consider resizing ${oversized.get(name) === 1 ? "it" : "them"}.`);
            }
        }

        for (const graphic of profile.oversized_graphics) {
            if (typeof graphic.id !== "string" || !graphic.id) continue;
            const section = sectionElement(graphic.section);
            const element = document.getElementById(graphic.id);
            if (outlined.has(section) && element && section.contains(element)
                && element.classList.contains("graphic")) {
                outlined.add(element);
            }
        }

        closeContextMenu();
        for (const element of layout.querySelectorAll(`.${attentionClass}, .${contextTargetClass}`)) {
            element.classList.toggle(attentionClass, outlined.has(element));
            element.classList.remove(contextTargetClass);
        }
        for (const element of outlined) element.classList.add(attentionClass);
        if (feedback.length) showMessage(feedback);
    }

    async function profile(currentRevision) {
        try {
            const response = await fetch(`/magic/${pid}/profile`, {
                credentials: "same-origin",
                cache: "no-store",
            });
            let result;
            try {
                result = await response.json();
            } catch {
                throw new Error("The profiler returned an invalid response.");
            }
            if (!response.ok) {
                throw new Error(result.detail || "Your layout could not be profiled.");
            }
            if (currentRevision === revision) applyProfile(result);
        } catch (error) {
            console.error("Could not profile the edited poster layout", error);
            if (currentRevision === revision) {
                if (typeof window.wikkit?.say === "function") {
                    window.wikkit.say("<p>I couldn&rsquo;t check the layout just now. Please try again after your next edit.</p>");
                } else if (status) {
                    status.textContent = "Your layout could not be checked. Please try again after your next edit.";
                }
            }
        }
    }

    function problemSection(target) {
        const section = target instanceof Element ? target.closest(".section") : null;
        return section?.classList.contains(attentionClass) ? section : null;
    }

    document.addEventListener("pointerdown", event => {
        if (contextMenu && !contextMenu.contains(event.target)) closeContextMenu();
    });

    document.addEventListener("pointerdown", event => {
        const section = problemSection(event.target);
        if (!section) return;
        const modifiedClick = event.button === 0 && (event.metaKey || event.ctrlKey);
        if (event.button !== 2 && !modifiedClick) return;
        section.classList.add(contextTargetClass);
        if (modifiedClick) {
            event.preventDefault();
            openContextMenu(section, event.clientX, event.clientY);
        }
    });

    document.addEventListener("contextmenu", event => {
        const section = problemSection(event.target);
        if (!section) return;
        event.preventDefault();
        openContextMenu(section, event.clientX, event.clientY);
    });

    document.addEventListener("keydown", event => {
        if (event.key !== "Escape" || !contextMenu) return;
        closeContextMenu();
        event.preventDefault();
    });
    window.addEventListener("resize", closeContextMenu);
    document.getElementById("poster-container")?.addEventListener("scroll", closeContextMenu);

    function scheduleProfile() {
        const currentRevision = ++revision;
        clearTimeout(timer);
        timer = setTimeout(() => profile(currentRevision), interval);
    }

    function profileOnLoad() {
        Promise.resolve(window.posterGeometryReady).then(() => {
            if (revision === 0) scheduleProfile();
        }).catch(error => {
            console.error("Could not profile the initial poster layout", error);
            if (status) {
                status.textContent = "Your layout could not be checked. Please reload to try again.";
            }
        });
    }

    document.addEventListener("poster-layout-updated", scheduleProfile);
    layout.addEventListener("input", event => {
        const editor = event.target instanceof Element
            ? event.target.closest("[contenteditable='true'], [contenteditable='plaintext-only']") : null;
        const section = editor?.closest(".section");
        if (!section) return;
        editedSections.add(section);
        ++revision;
        clearTimeout(timer);
        clearTimeout(contentTimer);
        contentTimer = setTimeout(() => {
            applyProfile(latestProfile || {
                sections: {},
                overflowing_sections: [],
                underflowing_sections: [],
                oversized_graphics: [],
            });
        }, interval);
    });
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", profileOnLoad, {once: true});
    } else {
        profileOnLoad();
    }
})();
