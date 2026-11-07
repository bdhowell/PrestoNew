(() => {
    "use strict";

    const button = document.getElementById("template");
    const dialog = document.getElementById("template-dialog");
    const form = document.getElementById("template-form");
    if (!button || !dialog || !form) return;

    const categoryOptions = [...form.querySelectorAll("input[name='category']")];
    const templateOptions = [...form.querySelectorAll("input[name='template']")];
    const sizeOptions = form.querySelector(".template-size-options");
    const customDimensions = form.querySelector(".template-custom-dimensions");
    const widthInput = form.elements.width;
    const heightInput = form.elements.height;
    const unitsInput = form.elements.units;
    const status = document.getElementById("template-status");
    const cancel = document.getElementById("template-cancel");
    const apply = document.getElementById("template-apply");
    const dragHandle = document.getElementById("template-dialog-title");
    const scales = {in: 2.54, cm: 1, mm: 0.1};
    const tilePixelsPerInch = 2;
    const presets = JSON.parse(dialog.dataset.presets);
    const presetErrors = JSON.parse(dialog.dataset.presetErrors);
    let activeUnits = dialog.dataset.units;
    let selectedSize = null;
    let saving = false;
    let dragging = null;

    function moveDialog(left, top) {
        const bounds = dialog.getBoundingClientRect();
        const clamp = (value, max) => Math.min(Math.max(value, 0), Math.max(max, 0));
        dialog.style.margin = "0";
        dialog.style.left = `${clamp(left, window.innerWidth - bounds.width)}px`;
        dialog.style.top = `${clamp(top, window.innerHeight - bounds.height)}px`;
    }

    dragHandle.addEventListener("pointerdown", event => {
        if (event.button !== 0 || dragging) return;
        const bounds = dialog.getBoundingClientRect();
        dragging = {
            pointer: event.pointerId,
            offsetX: event.clientX - bounds.left,
            offsetY: event.clientY - bounds.top,
        };
        moveDialog(bounds.left, bounds.top);
        dragHandle.setPointerCapture(event.pointerId);
        dialog.classList.add("dragging");
        event.preventDefault();
    });
    dragHandle.addEventListener("pointermove", event => {
        if (!dragging || dragging.pointer !== event.pointerId) return;
        moveDialog(event.clientX - dragging.offsetX, event.clientY - dragging.offsetY);
    });
    function finishDrag(event) {
        if (!dragging || dragging.pointer !== event.pointerId) return;
        dragging = null;
        dialog.classList.remove("dragging");
        if (dragHandle.hasPointerCapture(event.pointerId)) {
            dragHandle.releasePointerCapture(event.pointerId);
        }
    }
    dragHandle.addEventListener("pointerup", finishDrag);
    dragHandle.addEventListener("pointercancel", finishDrag);
    dragHandle.addEventListener("lostpointercapture", finishDrag);
    window.addEventListener("resize", () => {
        if (!dialog.open || !dialog.style.left) return;
        const bounds = dialog.getBoundingClientRect();
        moveDialog(bounds.left, bounds.top);
    });

    function sizesFor(category) {
        if (category === "custom") return [];
        return presets[unitsInput.value]
            .map(([name, width, height], index) => ({
                name,
                width: category === "landscape" ? Math.max(width, height) : Math.min(width, height),
                height: category === "landscape" ? Math.min(width, height) : Math.max(width, height),
                errors: presetErrors[unitsInput.value]?.[category]?.[index] || [],
            }))
            .filter(({width, height}) => (width === height) === (category === "square"));
    }

    function matches(size, width, height) {
        return Math.abs(size.width - width) < 0.000001 && Math.abs(size.height - height) < 0.000001;
    }

    function categoryValue() {
        return form.querySelector("input[name='category']:checked").value;
    }

    function setDimensions(width, height) {
        widthInput.value = String(Number(width.toFixed(9)));
        heightInput.value = String(Number(height.toFixed(9)));
    }

    function revealSelectedSize() {
        if (!dialog.open || sizeOptions.hidden) return;
        const selected = sizeOptions.querySelector("input:checked");
        if (!selected) return;
        const tile = selected.nextElementSibling.getBoundingClientRect();
        const viewport = sizeOptions.getBoundingClientRect();
        const offset = (start, end, size) => {
            if (start < 0 && end > size) return 0;
            return start < 0 ? start : Math.max(0, end - size);
        };
        sizeOptions.scrollBy({
            left: offset(tile.left - viewport.left, tile.right - viewport.left, sizeOptions.clientWidth),
            top: offset(tile.top - viewport.top, tile.bottom - viewport.top, sizeOptions.clientHeight),
            behavior: "instant",
        });
    }

    function renderSizes(category) {
        dialog.dataset.category = category;
        sizeOptions.replaceChildren();
        const options = sizesFor(category);
        const isCustom = category === "custom";
        const availableOptions = options.filter(size => size.errors.length === 0);
        selectedSize = availableOptions.find(size =>
            matches(size, Number(widthInput.value), Number(heightInput.value))) || availableOptions[0] || null;
        if (!isCustom && selectedSize) setDimensions(selectedSize.width, selectedSize.height);
        const dimensionGroups = new Map();
        const groupDimension = category === "portrait" ? "width" : "height";
        if (category === "portrait" || category === "landscape") {
            const dimensions = [...new Set(options.map(size => size[groupDimension]))].sort((a, b) => a - b);
            for (const dimension of dimensions) {
                const group = document.createElement("div");
                group.className = `template-${groupDimension}-group`;
                const caption = document.createElement("span");
                caption.className = `template-${groupDimension}-label`;
                caption.textContent = `${dimension}${unitsInput.value} ${category === "portrait" ? "wide" : "Hgh"}`;
                group.setAttribute("role", "group");
                group.setAttribute("aria-label", caption.textContent);
                const tiles = document.createElement("div");
                tiles.className = `template-${groupDimension}-options`;
                if (category === "portrait") group.append(tiles, caption);
                else group.append(caption, tiles);
                sizeOptions.append(group);
                dimensionGroups.set(dimension, tiles);
            }
        }
        for (const [index, {name, width, height, errors}] of options.entries()) {
            const label = document.createElement("label");
            label.className = "template-size-option";
            label.style.setProperty("--tile-ratio", String(width / height));
            label.style.setProperty(
                "--tile-width", `${width * scales[unitsInput.value] / scales.in * tilePixelsPerInch}px`,
            );
            const radio = document.createElement("input");
            radio.type = "radio";
            radio.name = "size";
            radio.value = String(index);
            radio.disabled = errors.length > 0;
            radio.checked = selectedSize !== null && matches(selectedSize, width, height);
            const shape = document.createElement("span");
            shape.className = "template-size-shape";
            shape.textContent = name || (unitsInput.value === "in"
                ? `${width}" x ${height}"` : `${width} x ${height} ${unitsInput.value}`);
            label.title = `${width} x ${height} ${unitsInput.value}`;
            label.append(radio, shape);
            if (errors.length) {
                label.title += `: ${errors.join(" ")}`;
            }
            (dimensionGroups.get(groupDimension === "width" ? width : height) || sizeOptions).append(label);
            radio.addEventListener("change", () => {
                selectedSize = options[index];
                setDimensions(width, height);
                revealSelectedSize();
            });
        }
        sizeOptions.hidden = isCustom;
        customDimensions.hidden = !isCustom;
        widthInput.disabled = heightInput.disabled = !isCustom;
        widthInput.required = heightInput.required = isCustom;
        apply.disabled = !isCustom && !selectedSize;
        status.textContent = !isCustom && !selectedSize
            ? "No presets are available for this category and units. Choose another category or Custom." : "";
        const disabledCount = options.length - availableOptions.length;
        if (disabledCount) {
            const quantity = ["one", "a couple of", "some"][Math.min(disabledCount - 1, 2)];
            window.wikkit.say(
                `<p>I&rsquo;ve disabled ${quantity} ${category} option${disabledCount > 1 ? "s" : ""} because they&rsquo;re too large for your conference requirements.</p>`,
            );
        }
        requestAnimationFrame(revealSelectedSize);
    }

    function initialize() {
        const width = Number(dialog.dataset.width);
        const height = Number(dialog.dataset.height);
        activeUnits = dialog.dataset.units;
        unitsInput.value = activeUnits;
        const orientation = width === height ? "square" : width > height ? "landscape" : "portrait";
        const category = sizesFor(orientation).some(size => matches(size, width, height))
            ? orientation : "custom";
        for (const option of categoryOptions) option.checked = option.value === category;
        for (const option of templateOptions) {
            option.checked = option.value === dialog.dataset.template;
        }
        setDimensions(width, height);
        renderSizes(category);
    }

    button.addEventListener("click", () => {
        initialize();
        dialog.style.removeProperty("margin");
        dialog.style.removeProperty("left");
        dialog.style.removeProperty("top");
        dialog.showModal();
        revealSelectedSize();
        button.setAttribute("aria-expanded", "true");
    });
    dialog.addEventListener("close", () => button.setAttribute("aria-expanded", "false"));
    dialog.addEventListener("cancel", event => {
        if (saving) event.preventDefault();
    });
    form.addEventListener("change", event => {
        if (event.target.name === "category") renderSizes(event.target.value);
        if (event.target === unitsInput) {
            const conversion = scales[activeUnits] / scales[unitsInput.value];
            setDimensions(Number(widthInput.value) * conversion, Number(heightInput.value) * conversion);
            activeUnits = unitsInput.value;
            renderSizes(categoryValue());
        }
    });
    cancel.addEventListener("click", () => dialog.close());
    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (saving || !form.reportValidity()) return;

        const width = Number(widthInput.value);
        const height = Number(heightInput.value);
        const units = unitsInput.value;
        const template = form.querySelector("input[name='template']:checked")?.value;
        if (!(width > 0 && height > 0 && scales[units] && template)
            || (categoryValue() !== "custom" && !selectedSize)) {
            status.textContent = "Choose a valid template and positive dimensions.";
            return;
        }

        saving = true;
        form.querySelectorAll("fieldset").forEach(fieldset => { fieldset.disabled = true; });
        cancel.disabled = apply.disabled = true;
        status.textContent = "Saving dimensions and template...";
        try {
            const response = await fetch("/layout/dimensions", {
                method: "POST",
                credentials: "same-origin",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    dimensions: `${height} ${units} x ${width} ${units}`,
                    template,
                }),
            });
            let result;
            try {
                result = await response.json();
            } catch {
                throw new Error("The server returned an invalid response.");
            }
            if (!response.ok || result.ok !== true) {
                const message = result.errors?.join(" ") || result.detail;
                throw new Error(message || "Your dimensions and template could not be saved.");
            }
            window.location.assign(result.redirect);
        } catch (error) {
            console.error("Could not save poster dimensions and template", error);
            status.textContent = `${error.message} Please try again.`;
        } finally {
            saving = false;
            form.querySelectorAll("fieldset").forEach(fieldset => { fieldset.disabled = false; });
            cancel.disabled = false;
            apply.disabled = categoryValue() !== "custom" && !selectedSize;
        }
    });
})();
