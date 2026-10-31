(() => {
    "use strict";

    const layout = document.querySelector(".layout.three-column");
    if (!layout) return;
    const panes = [0, 1, 2].map(index => document.getElementById(`pane${index}`));
    if (panes.some(pane => !pane || pane.parentElement !== layout)) return;

    const sizing = JSON.parse(layout.dataset.resizeSettings);
    const status = document.getElementById("content-save-status");
    const bottom = layout.querySelector(":scope > .pane.horizontal");
    const initialColumns = getComputedStyle(layout).gridTemplateColumns.split(" ").map(Number.parseFloat);
    const columnTotal = initialColumns.reduce((sum, width) => sum + width, 0);
    const columnShares = initialColumns.map(width => width / columnTotal);
    const initialRows = getComputedStyle(layout).gridTemplateRows.split(" ").map(Number.parseFloat);
    const topShare = bottom ? initialRows[0] / (initialRows[0] + initialRows[1]) : null;
    const overlay = document.createElement("div");
    overlay.className = "resize-overlay";
    layout.append(overlay);
    const handles = [];
    const rows = [];
    let dragging = null;
    let pending = false;
    let saving = false;
    let dirty = false;
    let timer = null;

    const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, value));
    const bounds = element => element.getBoundingClientRect();
    const scale = () => bounds(layout).width / layout.offsetWidth;
    const children = element => Array.from(element.children)
        .filter(child => child.matches(".section, .section-parent"));

    function notify(error) {
        console.error("Could not save poster sizing", error);
        status.textContent = `${error.message} Resize again to retry.`;
    }

    async function save() {
        if (saving || !dirty) return;
        saving = true;
        dirty = false;
        const value = JSON.stringify(sizing);
        status.textContent = "Saving layout sizing...";
        try {
            const response = await fetch("/layout/sizing", {
                method: "POST",
                credentials: "same-origin",
                keepalive: true,
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    pid: Number(layout.dataset.pid), template: layout.dataset.template,
                    sizing: JSON.parse(value),
                }),
            });
            const result = await response.json();
            if (!response.ok || result.ok !== true) {
                throw new Error(result.detail || "Your layout sizing could not be saved.");
            }
            if (!dirty) status.textContent = "Layout sizing saved.";
        } catch (error) {
            notify(error);
            // Retain the latest positions without an unbounded automatic retry loop.
            saving = false;
            if (dirty) save();
            return;
        }
        saving = false;
        if (dirty) save();
    }

    function changed() {
        apply();
        dirty = true;
        clearTimeout(timer);
        timer = setTimeout(save, 300);
    }

    function setShares(row, shares) {
        const style = getComputedStyle(row.element);
        const total = row.element.clientWidth - Number.parseFloat(style.paddingLeft)
            - Number.parseFloat(style.paddingRight)
            - (Number.parseFloat(style.columnGap) || 0) * (row.items.length - 1);
        row.items.forEach((item, index) => {
            item.style.flex = `0 0 ${total * shares[index]}px`;
            item.style.minWidth = `${layout.clientWidth * 0.2}px`;
        });
    }

    function apply() {
        const style = getComputedStyle(layout);
        const gap = Number.parseFloat(style.columnGap);
        const offsets = [sizing.left, -sizing.left - sizing.right, sizing.right];
        layout.style.gridTemplateColumns = columnShares.map((share, index) =>
            `calc((100% - ${2 * gap}px) * ${share} + ${offsets[index] * 100}%)`).join(" ");
        if (bottom) {
            const rowGap = Number.parseFloat(style.rowGap);
            layout.style.gridTemplateRows =
                `calc((100% - ${rowGap}px) * ${topShare} - ${sizing.bottom * 100}%) `
                + `calc((100% - ${rowGap}px) * ${1 - topShare} + ${sizing.bottom * 100}%)`;
        }
        if (sizing.introduction !== null && introductionHandle) {
            const parent = introductionHandle.first.parentElement;
            const style = getComputedStyle(parent);
            const siblings = children(parent);
            const others = siblings.filter(section =>
                section !== introductionHandle.first && section !== introductionHandle.second);
            const total = parent.clientHeight - Number.parseFloat(style.paddingTop)
                - Number.parseFloat(style.paddingBottom)
                - (Number.parseFloat(style.rowGap) || 0) * (siblings.length - 1)
                - others.reduce((sum, section) => sum + section.offsetHeight, 0);
            introductionHandle.first.style.flex = `0 0 ${total * sizing.introduction}px`;
            introductionHandle.second.style.flex = `0 0 ${total * (1 - sizing.introduction)}px`;
            for (const section of [introductionHandle.first, introductionHandle.second]) {
                section.style.minHeight = "0";
                section.style.overflow = "hidden";
            }
        }
        for (const row of rows) {
            const shares = sizing.sections[row.element.id];
            if (shares) {
                // Keep the 20%-of-layout minimum after a font or layout-size change.
                const total = row.items.reduce((sum, item) => sum + bounds(item).width, 0);
                const minimum = bounds(layout).width * 0.2 / total;
                const excess = shares.reduce((sum, share) => sum + Math.max(0, share - minimum), 0);
                const available = 1 - minimum * shares.length;
                const adjusted = shares.map(share =>
                    minimum + (excess > 0 ? Math.max(0, share - minimum) * available / excess : 0));
                setShares(row, adjusted);
            }
        }
        position();
    }

    function visible(handle, show) {
        handle.separator.hidden = !show;
        handle.toggle.setAttribute("aria-expanded", String(show));
        if (!show && document.activeElement === handle.separator) handle.toggle.focus();
    }

    function addHandle(id, axis, label, first, second, kind, row = null, index = null) {
        const gutter = document.createElement("div");
        gutter.className = `resize-gutter resize-${axis}`;
        gutter.dataset.resize = id;
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "resize-toggle";
        toggle.setAttribute("aria-label", `Toggle ${label.toLowerCase()} handle`);
        toggle.setAttribute("aria-expanded", "false");
        toggle.setAttribute("aria-controls", `resize-${id}`);
        toggle.title = "Click to show or hide the resize handle.";
        const separator = document.createElement("div");
        separator.id = `resize-${id}`;
        separator.className = "resize-handle";
        separator.hidden = true;
        separator.tabIndex = 0;
        separator.setAttribute("role", "separator");
        separator.setAttribute("aria-orientation", axis === "x" ? "vertical" : "horizontal");
        separator.setAttribute("aria-label", label);
        separator.setAttribute("aria-controls", kind === "bottom"
            ? `${panes.map(pane => pane.id).join(" ")} ${second.id}` : `${first.id} ${second.id}`);
        separator.title = "Drag or use arrow keys to resize. Home/End set limits; Escape hides.";
        gutter.append(toggle, separator);
        overlay.append(gutter);
        const handle = {id, axis, first, second, kind, row, index, gutter, toggle, separator};
        handles.push(handle);
        toggle.addEventListener("click", () => visible(handle, separator.hidden));
        separator.addEventListener("pointerdown", event => {
            if (event.button !== 0 || dragging) return;
            const firstBounds = bounds(first);
            const secondBounds = bounds(second);
            dragging = {
                handle, pointer: event.pointerId,
                start: event[axis === "x" ? "clientX" : "clientY"],
                layoutSize: bounds(layout)[axis === "x" ? "width" : "height"],
                value: current(handle),
                pair: axis === "x" ? firstBounds.width + secondBounds.width
                    : firstBounds.height + secondBounds.height,
                moved: false,
            };
            separator.setPointerCapture(event.pointerId);
            separator.focus();
            event.preventDefault();
        });
        separator.addEventListener("pointermove", event => {
            if (!dragging || dragging.handle !== handle || dragging.pointer !== event.pointerId) return;
            const delta = event[axis === "x" ? "clientX" : "clientY"] - dragging.start;
            if (!dragging.moved && Math.abs(delta) < 2) return;
            dragging.moved = true;
            move(handle, dragging.value, delta, dragging.layoutSize, dragging.pair);
        });
        function finish(event) {
            if (!dragging || dragging.handle !== handle || dragging.pointer !== event.pointerId) return;
            const moved = dragging.moved;
            dragging = null;
            if (separator.hasPointerCapture(event.pointerId)) separator.releasePointerCapture(event.pointerId);
            if (!moved && event.type === "pointerup") visible(handle, false);
            if (moved) {
                clearTimeout(timer);
                save();
            }
        }
        separator.addEventListener("pointerup", finish);
        separator.addEventListener("pointercancel", finish);
        separator.addEventListener("lostpointercapture", finish);
        separator.addEventListener("keydown", event => {
            if (event.key === "Escape") {
                visible(handle, false);
                event.preventDefault();
                return;
            }
            const negative = axis === "x" ? "ArrowLeft" : "ArrowUp";
            const positive = axis === "x" ? "ArrowRight" : "ArrowDown";
            if (![negative, positive, "Home", "End"].includes(event.key)) return;
            const direction = event.key === negative ? -1 : 1;
            const size = bounds(layout)[axis === "x" ? "width" : "height"];
            const pair = axis === "x" ? bounds(first).width + bounds(second).width
                : bounds(first).height + bounds(second).height;
            const reversed = handle.id === "right" || handle.kind === "bottom";
            const limit = (event.key === "Home" ? -Infinity : Infinity) * (reversed ? -1 : 1);
            move(handle, current(handle),
                ["Home", "End"].includes(event.key) ? limit
                    : direction * (event.shiftKey ? 10 : 1), size, pair);
            event.preventDefault();
        });
        return handle;
    }

    function current(handle) {
        if (handle.kind === "columns" || handle.kind === "bottom") return sizing[handle.id];
        if (handle.kind === "introduction") {
            return bounds(handle.first).height / (bounds(handle.first).height + bounds(handle.second).height);
        }
        return handle.row.items.map(item => bounds(item).width);
    }

    function move(handle, value, delta, size, pair) {
        if (handle.kind === "columns") {
            const other = handle.id === "left" ? "right" : "left";
            let offset = clamp(value + (handle.id === "left" ? delta : -delta) / size, 0, 0.15);
            if (Math.abs(offset - sizing[other]) * bounds(layout).width <= 6) offset = sizing[other];
            sizing[handle.id] = offset;
        } else if (handle.kind === "bottom") {
            sizing.bottom = clamp(value - delta / size, 0, 0.15);
        } else if (handle.kind === "introduction") {
            sizing.introduction = clamp(value + delta / pair, 0.25, 0.75);
        } else {
            const widths = value.slice();
            const minimum = bounds(layout).width * 0.2;
            const first = handle.index;
            const total = widths[first] + widths[first + 1];
            widths[first] = clamp(widths[first] + delta, minimum, total - minimum);
            widths[first + 1] = total - widths[first];
            const rowTotal = widths.reduce((sum, width) => sum + width, 0);
            sizing.sections[handle.row.element.id] = widths.map(width => width / rowTotal);
        }
        changed();
    }

    addHandle("left", "x", "Left column width", panes[0], panes[1], "columns");
    addHandle("right", "x", "Right column width", panes[1], panes[2], "columns");
    if (bottom) {
        addHandle("bottom", "y", "Bottom pane height", panes[0], bottom, "bottom");
        const containers = [bottom, ...bottom.querySelectorAll(".section-parent")];
        for (const element of containers) {
            if (getComputedStyle(element).flexDirection !== "row") continue;
            const items = children(element);
            if (items.length < 2) continue;
            const row = {element, items};
            rows.push(row);
            for (let index = 0; index < items.length - 1; index++) {
                addHandle(`${element.id}-${index}`, "x", `${items[index].id} section width`,
                    items[index], items[index + 1], "sections", row, index);
            }
        }
    }
    const introduction = layout.querySelector("#introduction");
    const methods = layout.querySelector("#methods");
    const introductionHandle = introduction && methods && introduction.nextElementSibling === methods
        && getComputedStyle(introduction.parentElement).flexDirection === "column"
        ? addHandle("introduction", "y", "Introduction and Materials and Methods height",
            introduction, methods, "introduction") : null;

    function position() {
        const layoutBounds = bounds(layout);
        const zoom = scale();
        overlay.style.setProperty("--resize-handle-size", `${Math.max(16, 8 / zoom)}px`);
        for (const handle of handles) {
            const first = bounds(handle.first);
            const second = bounds(handle.second);
            let left, top, width, height;
            if (handle.axis === "x") {
                left = first.right;
                top = Math.max(first.top, second.top);
                width = second.left - first.right;
                height = Math.min(first.bottom, second.bottom) - top;
                if (width < 4 * zoom) {
                    left -= 4 * zoom;
                    width = 8 * zoom;
                }
            } else {
                left = handle.kind === "bottom" ? layoutBounds.left : Math.max(first.left, second.left);
                top = first.bottom;
                width = handle.kind === "bottom" ? layoutBounds.width
                    : Math.min(first.right, second.right) - left;
                height = second.top - first.bottom;
            }
            Object.assign(handle.gutter.style, {
                left: `${(left - layoutBounds.left) / zoom}px`,
                top: `${(top - layoutBounds.top) / zoom}px`,
                width: `${width / zoom}px`, height: `${height / zoom}px`,
            });
            const value = current(handle);
            const column = handle.kind === "columns" || handle.kind === "bottom";
            handle.separator.setAttribute("aria-valuemin", String(column ? 0
                : handle.kind === "introduction" ? 25 : 20));
            handle.separator.setAttribute("aria-valuemax", String(column ? 15
                : handle.kind === "introduction" ? 75
                    : (value[handle.index] + value[handle.index + 1]) / layoutBounds.width * 100 - 20));
            handle.separator.setAttribute("aria-valuenow", (column ? value * 100
                : handle.kind === "introduction" ? value * 100
                    : value[handle.index] / layoutBounds.width * 100).toFixed(2));
            if (column) {
                handle.separator.setAttribute("aria-valuetext",
                    `${(value * 100).toFixed(2)}% ${handle.kind === "bottom" ? "upwards" : "inwards"} from the initial position`);
            }
        }
    }

    const observer = new ResizeObserver(() => {
        if (pending) return;
        pending = true;
        requestAnimationFrame(() => {
            pending = false;
            apply();
        });
    });
    observer.observe(layout);
    for (const handle of handles) {
        observer.observe(handle.first);
        observer.observe(handle.second);
    }
    apply();
})();
