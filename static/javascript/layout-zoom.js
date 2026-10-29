(() => {
    "use strict";

    const viewport = document.getElementById("poster-container");
    const stage = document.getElementById("poster-stage");
    const canvas = document.getElementById("poster-canvas");
    const slider = document.getElementById("poster-zoom");
    const output = document.getElementById("poster-zoom-value");
    let scale = 1;
    let fitting = false;
    let pending = false;

    function update() {
        pending = false;
        const width = Math.max(canvas.offsetWidth, canvas.scrollWidth);
        const height = Math.max(canvas.offsetHeight, canvas.scrollHeight);
        if (!width || !height || !viewport.clientWidth || !viewport.clientHeight) return;

        // Use the full viewport before scrollbars so fitting can remove them.
        const bounds = viewport.getBoundingClientRect();
        const minimum = Math.min(1, bounds.width / width, bounds.height / height);
        scale = fitting ? minimum : Math.max(minimum, Math.min(1, scale));
        slider.min = String(minimum * 100);
        slider.value = String(scale * 100);
        slider.disabled = minimum === 1;
        output.textContent = `${(scale * 100).toFixed(1)}%`;
        canvas.style.transform = `scale(${scale})`;
        stage.style.width = `${width * scale}px`;
        stage.style.height = `${height * scale}px`;
    }

    function schedule() {
        if (!pending) {
            pending = true;
            requestAnimationFrame(update);
        }
    }

    slider.addEventListener("input", () => {
        scale = Number(slider.value) / 100;
        fitting = scale <= Number(slider.min) / 100 + 1e-8;
        update();
    });
    const observer = new ResizeObserver(schedule);
    for (const element of [
        viewport, canvas, document.getElementById("poster-header"), document.getElementById("layout"),
    ]) {
        observer.observe(element);
    }
    canvas.addEventListener("load", schedule, true);
    document.fonts.ready.then(schedule);
    update();
})();
