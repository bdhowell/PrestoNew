(() => {
    "use strict";

    const viewport = document.querySelector(".poster-viewport");
    const canvas = document.querySelector(".poster-canvas");
    const frame = canvas.querySelector("iframe");
    const controls = document.querySelector(".viewer-controls");
    const slider = controls.querySelector("input");
    const output = controls.querySelector("output");
    const close = controls.querySelector("[data-close-viewer]");
    let width = 0;
    let height = 0;
    let fitting = true;

    function zoom(scale) {
        if (!width || !height) {
            return;
        }
        frame.style.width = `${width}px`;
        frame.style.height = `${height}px`;
        frame.style.transform = `scale(${scale})`;
        canvas.style.width = `${width * scale}px`;
        canvas.style.height = `${height * scale}px`;
        slider.value = scale * 100;
        output.textContent = `${Math.round(scale * 100)}%`;
    }

    function fit() {
        if (width && height) {
            const scale = Math.min(1, (viewport.clientWidth - 24) / width, (viewport.clientHeight - 24) / height);
            slider.min = Math.min(1, scale * 100);
            zoom(scale);
        }
    }

    function loaded() {
        const poster = frame.contentDocument?.querySelector(".poster-document");
        if (!poster) {
            console.error("Poster viewer could not load", frame.src);
            document.querySelector(".viewer-message").textContent = "The poster could not be loaded. Close this viewer and try again.";
            return;
        }
        width = Number(poster.dataset.width);
        height = Number(poster.dataset.height);
        frame.contentDocument.addEventListener("keydown", closeOnEscape);
        fit();
    }

    new ResizeObserver(() => {
        document.documentElement.style.setProperty("--viewer-toolbar-height", `${controls.offsetHeight}px`);
        if (fitting) {
            fit();
        }
    }).observe(controls);
    new ResizeObserver(() => { if (fitting) { fit(); } }).observe(viewport);
    frame.addEventListener("load", loaded);
    if (frame.contentDocument?.querySelector(".poster-document")) {
        loaded();
    }
    slider.step = "any";
    slider.addEventListener("input", () => {
        fitting = false;
        zoom(Number(slider.value) / 100);
    });
    controls.querySelector("[data-fit]").addEventListener("click", () => {
        fitting = true;
        fit();
    });
    controls.querySelector("[data-full-size]").addEventListener("click", () => {
        fitting = false;
        zoom(1);
    });
    if (document.referrer) {
        const previous = new URL(document.referrer);
        if (previous.origin === location.origin && previous.pathname === "/posters/list") {
            close.href = previous.pathname + previous.search;
        }
    }
    function closeOnEscape(event) {
        if (event.key === "Escape") {
            location.assign(close.href);
        }
    }
    document.addEventListener("keydown", closeOnEscape);
})();
