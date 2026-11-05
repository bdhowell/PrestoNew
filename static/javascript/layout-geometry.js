(() => {
    "use strict";

    const layout = document.getElementById("layout");
    const content = document.getElementById("poster-content");
    const canvas = document.getElementById("poster-canvas");
    const header = document.getElementById("poster-header");
    const status = document.getElementById("content-save-status");
    let geometry = JSON.parse(layout.dataset.initialGeometry);
    let unsaved = false;

    const children = element => [...element.children]
        .filter(child => child.matches(".section, .section-parent"));
    const shares = values => {
        const total = values.reduce((sum, value) => sum + value, 0);
        if (!total || values.some(value => value <= 0)) {
            throw new Error("Initial layout areas have no available space.");
        }
        return values.map(value => value / total);
    };

    function measure() {
        const style = getComputedStyle(layout);
        const vertical = {};
        const horizontal = {};
        for (const element of layout.querySelectorAll(".pane, .section-parent")) {
            const items = children(element);
            if (!items.length) continue;
            const row = getComputedStyle(element).flexDirection === "row";
            (row ? horizontal : vertical)[element.id] = shares(items.map(item =>
                parseFloat(getComputedStyle(item)[row ? "width" : "height"])));
        }
        return {
            scale: new DOMMatrixReadOnly(getComputedStyle(content).transform).a,
            header: parseFloat(getComputedStyle(header).height) / parseFloat(getComputedStyle(content).height),
            layout: parseFloat(style.height) / parseFloat(getComputedStyle(content).height),
            columns: shares(style.gridTemplateColumns.split(" ").map(Number.parseFloat)),
            rows: shares(style.gridTemplateRows.split(" ").map(Number.parseFloat)),
            vertical, horizontal,
        };
    }

    function frame() {
        const style = getComputedStyle(canvas);
        const width = canvas.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
        const height = canvas.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
        if (width <= 0 || height <= 0) throw new Error("Poster margins leave no available content area.");
        content.style.width = `${width / geometry.scale}px`;
        content.style.height = `${height / geometry.scale}px`;
        content.style.transform = `scale(${geometry.scale})`;
        canvas.dispatchEvent(new Event("poster-font-size"));
    }

    function apply() {
        frame();
        content.dataset.fixedGeometry = "true";
        header.style.height = `${geometry.header * 100}%`;
        layout.style.height = `${geometry.layout * 100}%`;
        const style = getComputedStyle(layout);
        const columnsGap = parseFloat(style.columnGap) * (geometry.columns.length - 1);
        const rowsGap = parseFloat(style.rowGap) * (geometry.rows.length - 1);
        layout.style.gridTemplateColumns = geometry.columns.map(share =>
            `minmax(0, calc((100% - ${columnsGap}px) * ${share}))`).join(" ");
        layout.style.gridTemplateRows = geometry.rows.map(share =>
            `minmax(0, calc((100% - ${rowsGap}px) * ${share}))`).join(" ");
        for (const [direction, groups] of [
            ["column", geometry.vertical], ["row", geometry.horizontal],
        ]) {
            for (const [id, proportions] of Object.entries(groups)) {
                const element = document.getElementById(id);
                const items = children(element);
                const gap = parseFloat(getComputedStyle(element)[direction === "row" ? "columnGap" : "rowGap"]);
                items.forEach((item, index) => {
                    item.style.flex = `0 0 calc((100% - ${gap * (items.length - 1)}px) * ${proportions[index]})`;
                });
            }
        }
    }

    async function save() {
        const response = await fetch("/layout/geometry", {
            method: "POST",
            credentials: "same-origin",
            keepalive: true,
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                pid: Number(layout.dataset.pid), template: layout.dataset.template, geometry,
            }),
        });
        const result = await response.json();
        if (!response.ok || result.ok !== true) {
            throw new Error(result.detail || "Your initial layout dimensions could not be saved.");
        }
        // A concurrent first open may already have saved the authoritative baseline.
        geometry = result.geometry;
        unsaved = false;
        layout.dataset.initialGeometry = JSON.stringify(geometry);
        apply();
    }

    function failed(error) {
        console.error("Could not initialize poster layout dimensions", error);
        status.textContent = `${error.message} Reload to retry saving the initial dimensions.`;
    }

    window.addEventListener("beforeunload", event => {
        if (unsaved) {
            event.preventDefault();
            event.returnValue = "";
        }
    });
    window.posterGeometryReady = (async () => {
        await document.fonts.ready;
        if (geometry) {
            apply();
        } else {
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            geometry = measure();
            unsaved = true;
            apply();
            try {
                await save();
            } catch (error) {
                failed(error);
            }
        }
        const observer = new ResizeObserver(frame);
        observer.observe(canvas);
    })().catch(error => {
        failed(error);
        throw error;
    });
})();
