(() => {
    "use strict";

    const viewport = document.getElementById("poster-container");
    const stage = document.getElementById("poster-stage");
    const canvas = document.getElementById("poster-canvas");
    const content = document.getElementById("poster-content");
    const layout = document.getElementById("layout");
    const slider = document.getElementById("poster-zoom");
    const output = document.getElementById("poster-zoom-value");
    let scale = 1;
    let fitting = false;
    let pending = false;

    function fitContent() {
        const style = getComputedStyle(canvas);
        const width = canvas.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
        const height = canvas.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom);
        if (width <= 0 || height <= 0) {
            throw new Error("Poster margins leave no available content area.");
        }
        function overflowRatio(fit) {
            content.style.width = `${width / fit}px`;
            content.style.height = `${height / fit}px`;
            // scrollHeight can omit the trailing margin of an overflowing flex item.
            const layoutBottom = layout.offsetTop + layout.offsetHeight
                + parseFloat(getComputedStyle(layout).marginBottom);
            return Math.min(
                1, content.clientWidth / content.scrollWidth,
                content.clientHeight / Math.max(content.scrollHeight, Math.ceil(layoutBottom)),
            );
        }
        let fit = 1;
        let upper = 1;
        for (let attempt = 0; attempt < 30; attempt++) {
            const ratio = overflowRatio(fit);
            if (ratio === 1) {
                // Recover the largest fitting scale after bracketing the overflow.
                for (let step = 0; fit < upper && step < 12; step++) {
                    const middle = (fit + upper) / 2;
                    if (overflowRatio(middle) === 1) fit = middle;
                    else upper = middle;
                }
                overflowRatio(fit);
                content.style.transform = `scale(${fit})`;
                return;
            }
            upper = fit;
            fit *= ratio * 0.99;
        }
        throw new Error("Poster content could not be fitted within its margins.");
    }

    function update() {
        pending = false;
        fitContent();
        const width = canvas.offsetWidth;
        const height = canvas.offsetHeight;
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
        viewport, canvas, document.getElementById("poster-header"), layout,
    ]) {
        observer.observe(element);
    }
    const mutations = new MutationObserver(schedule);
    mutations.observe(content, {childList: true, characterData: true, subtree: true});
    canvas.addEventListener("load", schedule, true);
    document.fonts.ready.then(schedule);
    update();
})();
