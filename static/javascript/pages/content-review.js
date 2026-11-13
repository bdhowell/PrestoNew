(() => {
    "use strict";

    const root = document.getElementById("review");
    const pid = Number(root.dataset.pid);
    const status = document.getElementById("content-save-status");
    const next = document.getElementById("review-continue");
    const dialog = document.getElementById("revision-dialog");
    const accept = document.getElementById("revision-accept");
    const reject = document.getElementById("revision-reject");
    const revisionStatus = document.getElementById("revision-status");
    const sectionNavigation = root.querySelector("nav[aria-label='Poster sections']");
    const panes = document.getElementById("review-panes");
    const divider = document.getElementById("review-divider");
    const editors = [...root.querySelectorAll(".content[contenteditable='true'][data-section]")];
    const username = root.dataset.username || "Friend";
    document.execCommand("defaultParagraphSeparator", false, "p");
    const states = new Map(editors.map(element => [element, {
        dirty: false, saving: null, timer: null, failed: false, expected: undefined,
    }]));
    let continuing = false;
    let revising = false;
    let revisionTimer = null;
    let proposal = null;
    let accepting = false;
    let resizePointer = null;

    function resizePanes(value) {
        const share = Math.max(20, Math.min(80, value));
        panes.style.setProperty("--review-sections-width", `${share}fr`);
        panes.style.setProperty("--review-graphics-width", `${100 - share}fr`);
        divider.setAttribute("aria-valuenow", String(Math.round(share)));
        divider.setAttribute("aria-valuetext",
            `Sections ${Math.round(share)}%, source graphics ${100 - Math.round(share)}%`);
    }

    divider.addEventListener("pointerdown", event => {
        if (event.button !== 0 || resizePointer !== null) return;
        resizePointer = event.pointerId;
        divider.setPointerCapture(event.pointerId);
        divider.focus();
        event.preventDefault();
    });
    divider.addEventListener("pointermove", event => {
        if (resizePointer !== event.pointerId) return;
        const width = panes.clientWidth - divider.offsetWidth;
        const offset = event.clientX - panes.getBoundingClientRect().left - panes.clientLeft
            - divider.offsetWidth / 2;
        resizePanes(offset / width * 100);
    });
    function finishResize(event) {
        if (resizePointer !== event.pointerId) return;
        resizePointer = null;
        if (divider.hasPointerCapture(event.pointerId)) divider.releasePointerCapture(event.pointerId);
    }
    divider.addEventListener("pointerup", finishResize);
    divider.addEventListener("pointercancel", finishResize);
    divider.addEventListener("lostpointercapture", finishResize);
    divider.addEventListener("keydown", event => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        const current = Number(divider.getAttribute("aria-valuenow"));
        const step = event.shiftKey ? 10 : 1;
        const value = event.key === "Home" ? 20 : event.key === "End" ? 80
            : current + (event.key === "ArrowLeft" ? -step : step);
        resizePanes(value);
        event.preventDefault();
    });

    function announceSaveFailure() {
        const name = document.createElement("span");
        name.textContent = username;
        window.wikkit.say(`<p>${name.innerHTML}, I&rsquo;m very sorry but I&rsquo;m having trouble saving `
            + "your changes right now. Please reload the page and try making your changes again "
            + "in a few minutes. I apology for inconveniencing you.</p>");
    }

    const sections = document.getElementById("review-sections");
    const graphicPanes = document.getElementById("review-graphic-panes");
    const graphicsPanel = document.getElementById("review-graphics");
    const assessmentPanel = document.getElementById("review-assessment");
    const assessmentContent = document.getElementById("review-assessment-content");
    const graphicsData = JSON.parse(document.getElementById("review-graphics-data").textContent);
    const figureBySid = new Map(
        [...graphicsPanel.querySelectorAll(".review-graphic")]
            .map(button => [Number(button.dataset.sid), button.closest("figure")]),
    );
    const originalFigureOrder = new Map([...figureBySid.values()].map((figure, index) => [figure, index]));
    const imageTypes = ["svg", "png", "jpg", "jpeg", "gif", "tables", "math"];
    const visibleTypes = {
        introduction: new Set(["math"]),
        methods: new Set(imageTypes),
        results: new Set(imageTypes),
        conclusion: new Set(["math"]),
    };
    const ratingOrder = ["none", "weakly", "somewhat", "significantly", "highly", "definitively"];
    let selectedCategory = null;

    function appendText(parent, tag, value, className = "") {
        const element = document.createElement(tag);
        if (className) element.className = className;
        element.textContent = value;
        parent.append(element);
        return element;
    }

    function appendAssessmentList(parent, title, values) {
        if (!Array.isArray(values) || !values.length) return;
        appendText(parent, "h4", title);
        const list = document.createElement("ul");
        for (const value of values) appendText(list, "li", value);
        parent.append(list);
    }

    function showAssessment(sid) {
        for (const button of graphicsPanel.querySelectorAll(".review-graphic")) {
            const selected = Number(button.dataset.sid) === sid;
            button.classList.toggle("is-selected", selected);
            button.setAttribute("aria-pressed", String(selected));
        }
        assessmentContent.replaceChildren();
        const graphic = graphicsData.find(item => item.sid === sid);
        if (!graphic) return;

        appendText(assessmentContent, "h3", "Description");
        appendText(assessmentContent, "p", graphic.description || "No description is available.");
        const section = selectedCategory === "methods" ? "materials and methods" : "results";
        const label = selectedCategory === "methods" ? "Materials and Methods" : "Results";
        const assessment = graphic.assessments[section];
        appendText(assessmentContent, "h3", `${label} Assessment`);
        if (!assessment) {
            appendText(assessmentContent, "p", `No current ${label} assessment is available.`);
            return;
        }
        appendText(assessmentContent, "p", `Rating: ${assessment.rating || "not rated"}`, "graphic-assessment-rating");
        appendText(assessmentContent, "p", assessment.explanation || "");
        const criteria = assessment.criteria || {};
        const criterionEntries = Object.entries(criteria);
        if (criterionEntries.length) {
            appendText(assessmentContent, "h4", "Criteria");
            const list = document.createElement("dl");
            for (const [criterion, explanation] of criterionEntries) {
                appendText(list, "dt", criterion.replaceAll("_", " "));
                appendText(list, "dd", explanation);
            }
            assessmentContent.append(list);
        }
        appendAssessmentList(assessmentContent, "Supported elements", assessment.supported_elements);
        appendAssessmentList(assessmentContent, "Limitations", assessment.limitations);
        appendAssessmentList(assessmentContent, "Suggested improvements", assessment.suggested_improvements);
    }

    function updateGraphicPanels(category) {
        selectedCategory = category;
        const types = visibleTypes[category];
        const assessmentVisible = category === "methods" || category === "results";
        graphicsPanel.hidden = !types;
        assessmentPanel.hidden = !assessmentVisible;
        graphicPanes.classList.toggle("has-assessment", assessmentVisible);
        if (!types) {
            assessmentContent.replaceChildren();
            return;
        }

        const figures = [...figureBySid.entries()].map(([sid, figure]) => ({
            sid, figure, button: figure.querySelector(".review-graphic"),
            graphic: graphicsData.find(item => item.sid === sid),
        }));
        const visible = figures.filter(item => types.has(item.button.dataset.type));
        if (assessmentVisible) {
            const typeOrder = type => imageTypes.indexOf(type);
            const rating = graphic => ratingOrder.indexOf(
                graphic?.assessments[category === "methods" ? "materials and methods" : "results"]?.rating,
            );
            visible.sort((first, second) =>
                typeOrder(first.button.dataset.type) - typeOrder(second.button.dataset.type)
                || rating(second.graphic) - rating(first.graphic)
                || originalFigureOrder.get(first.figure) - originalFigureOrder.get(second.figure));
        } else {
            visible.sort((first, second) =>
                originalFigureOrder.get(first.figure) - originalFigureOrder.get(second.figure));
        }
        const visibleSet = new Set(visible.map(item => item.figure));
        for (const {figure} of figures) figure.hidden = !visibleSet.has(figure);
        const empty = graphicsPanel.querySelector("[data-graphics-empty]");
        empty.hidden = visible.length > 0;
        empty.textContent = graphicsData.length
            ? "No graphics of the supported types are available for this section."
            : "No source graphics are available for this poster.";
        graphicsPanel.append(...visible.map(item => item.figure));
        graphicsPanel.append(...figures.filter(item => !visibleSet.has(item.figure))
            .sort((first, second) => originalFigureOrder.get(first.figure) - originalFigureOrder.get(second.figure))
            .map(item => item.figure));
        showAssessment(visible[0]?.sid ?? null);
        if (assessmentVisible && !visible.length) {
            appendText(assessmentContent, "p", "No matching graphics are available for assessment.");
        }
    }

    graphicsPanel.addEventListener("click", event => {
        const button = event.target.closest(".review-graphic");
        if (!button || !graphicsPanel.contains(button)) return;
        showAssessment(Number(button.dataset.sid));
    });

    function selectSectionLink(link) {
        sectionNavigation.querySelector("[aria-current='location']")
            ?.removeAttribute("aria-current");
        link.setAttribute("aria-current", "location");
        sections.querySelector("section.is-selected")?.classList.remove("is-selected");
        const section = document.getElementById(link.getAttribute("href").slice(1));
        if (section && section.parentElement === sections) {
            section.classList.add("is-selected");
            updateGraphicPanels(section.querySelector("[data-category]")?.dataset.category);
        }
    }

    sectionNavigation.addEventListener("click", event => {
        const link = event.target.closest("a[href^='#review-']");
        if (!link || !sectionNavigation.contains(link)) return;
        selectSectionLink(link);
    });

    function selectSection(event) {
        const section = event.target.closest("#review-sections > section[id]");
        if (!section) return;
        const link = [...sectionNavigation.querySelectorAll("a[href^='#review-']")]
            .find(candidate => candidate.getAttribute("href") === `#${section.id}`);
        if (link) selectSectionLink(link);
    }
    sections.addEventListener("click", selectSection);
    sections.addEventListener("focusin", selectSection);
    const initialLink = [...sectionNavigation.querySelectorAll("a[href^='#review-']")]
        .find(link => link.getAttribute("href") === location.hash)
        || sectionNavigation.querySelector("a[href^='#review-']");
    if (initialLink) selectSectionLink(initialLink);

    function updateContinue() {
        next.disabled = continuing || revising || proposal !== null;
    }

    function clearProposal() {
        proposal = null;
        dialog.close();
        updateContinue();
    }

    function unchanged(element, original) {
        const state = states.get(element);
        const expected = document.createElement("div");
        expected.innerHTML = original;
        const serialize = value => window.mathJaxSource?.serialize(value) ?? value.innerHTML;
        return !state.dirty && !state.saving
            && serialize(element) === serialize(expected);
    }

    function updateStatus() {
        const values = [...states.values()];
        const failed = values.some(state => state.failed);
        status.textContent = failed ? "Changes could not be saved."
            : values.some(state => state.dirty || state.saving) ? "Saving changes..."
                : "Changes saved.";
    }

    async function save(element) {
        const state = states.get(element);
        if (state.saving) return state.saving;
        if (!state.dirty) return true;
        const value = window.mathJaxSource?.serialize(element) ?? element.innerHTML;
        state.dirty = false;
        state.failed = false;
        state.saving = (async () => {
            try {
                const data = {pid, section: element.dataset.section, content: value};
                if (state.expected !== undefined) data.expected = state.expected;
                const response = await fetch("/layout/content", {
                    method: "POST", credentials: "same-origin",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(data),
                });
                const result = await response.json();
                if (!response.ok || result.ok !== true) {
                    throw new Error(result.detail || "The section could not be saved.");
                }
                state.expected = undefined;
                return true;
            } catch (error) {
                console.error("Could not save poster section", element.dataset.section, error);
                state.dirty = true;
                state.failed = true;
                announceSaveFailure();
                return false;
            }
        })();
        updateStatus();
        const saved = await state.saving;
        state.saving = null;
        updateStatus();
        if (saved && state.dirty) return save(element);
        return saved;
    }

    for (const element of editors) {
        const state = states.get(element);
        element.addEventListener("focusin", () => window.mathJaxSource?.restore(element));
        element.addEventListener("focusout", () => window.mathJaxSource?.typeset(element));
        element.addEventListener("input", () => {
            state.dirty = true;
            state.failed = false;
            clearTimeout(state.timer);
            state.timer = setTimeout(() => {
                state.timer = null;
                save(element);
            }, 300);
            updateStatus();
        });
    }

    async function saveAll() {
        for (const state of states.values()) {
            clearTimeout(state.timer);
            state.timer = null;
        }
        const saved = await Promise.all(editors.map(save));
        return saved.every(Boolean) && [...states.values()].every(state => !state.dirty && !state.saving);
    }

    next.addEventListener("click", async () => {
        if (continuing || revising || proposal) return;
        continuing = true;
        next.disabled = true;
        if (await saveAll()) {
            location.assign(root.dataset.nextPage);
        } else {
            continuing = false;
            updateContinue();
        }
    });

    window.addEventListener("beforeunload", event => {
        if (revising || proposal || [...states.values()].some(state => state.dirty || state.saving)) {
            event.preventDefault();
            event.returnValue = "";
        }
    });

    reject.addEventListener("click", () => {
        if (accepting || !proposal) return;
        clearProposal();
        window.wikkit.say("<p>Okay, I&rsquo;ll keep the current section.</p>");
    });
    dialog.addEventListener("cancel", event => {
        event.preventDefault();
        if (!accepting) reject.click();
    });
    accept.addEventListener("click", async () => {
        if (!proposal || accepting) return;
        const {element, payload} = proposal;
        if (!unchanged(element, payload.original)) {
            revisionStatus.textContent = "The section changed. Reject this revision and request a new one.";
            accept.disabled = true;
            return;
        }
        accepting = true;
        accept.disabled = true;
        reject.disabled = true;
        revisionStatus.textContent = "Saving revision...";
        try {
            const response = await fetch("/layout/content", {
                method: "POST", credentials: "same-origin",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    pid, section: payload.section, content: payload.content, expected: payload.expected,
                }),
            });
            const result = await response.json();
            if (!response.ok || result.ok !== true) {
                throw new Error(result.detail || "The revision could not be saved.");
            }
            if (unchanged(element, payload.original)) {
                element.innerHTML = payload.content;
                window.mathJaxSource?.typeset(element);
                updateStatus();
                clearProposal();
                window.wikkit.say("<p>I&rsquo;ve saved the accepted revision.</p>");
            } else {
                clearProposal();
                window.wikkit.say("<p>The revision was saved, but this section changed locally while "
                    + "saving. Your local edits remain visible; please review them.</p>");
            }
        } catch (error) {
            console.error("Could not save accepted revision", error);
            announceSaveFailure();
            revisionStatus.textContent = `${error.message || "The revision could not be saved."} `
                + "Try again or reject the revision.";
        } finally {
            accepting = false;
            accept.disabled = false;
            reject.disabled = false;
        }
    });

    window.registerWikkitCommands({commands: {
        "review-start": {does: payload => {
            if (payload?.pid !== pid) return;
            revising = true;
            updateContinue();
            clearTimeout(revisionTimer);
            revisionTimer = setTimeout(() => {
                revising = false;
                updateContinue();
                window.wikkit.say("<p>The revision is taking too long. "
                    + "Please check your connection and try again.</p>");
            }, 150000);
        }},
        "review-failed": {does: payload => {
            if (payload?.pid !== pid) return;
            clearTimeout(revisionTimer);
            revising = false;
            updateContinue();
        }},
        "review-rewrite": {does: payload => {
            const element = editors.find(editor => editor.dataset.section === payload?.section);
            if (!element || payload.pid !== pid || typeof payload.original !== "string"
                || typeof payload.content !== "string"
                || !(payload.expected === null || typeof payload.expected === "string")) {
                throw new Error("Invalid section revision.");
            }
            try {
                if (proposal) {
                    window.wikkit.say("<p>Please accept or reject the current revision "
                        + "before requesting another.</p>");
                    return;
                }
                if (!unchanged(element, payload.original)) {
                    window.wikkit.say("<p>The section changed while I was working. "
                        + "Your edits are safe; please request the revision again.</p>");
                    return;
                }
                proposal = {element, payload};
                document.getElementById("revision-section").textContent = `Section: ${payload.section}`;
                document.getElementById("revision-original").innerHTML = payload.original;
                document.getElementById("revision-proposed").innerHTML = payload.content;
                revisionStatus.textContent = "";
                accept.disabled = false;
                dialog.showModal();
                window.mathJaxSource?.typeset(dialog);
                window.wikkit.say("<p>Please review my proposed revision and accept or reject it.</p>");
            } finally {
                clearTimeout(revisionTimer);
                revising = false;
                updateContinue();
            }
        }},
    }});
})();
