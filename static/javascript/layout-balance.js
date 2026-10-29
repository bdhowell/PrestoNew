(element) => {
    const maximum = element.getBoundingClientRect().width;
    if (maximum <= 0) {
        throw new Error("Cannot balance a container with no available width.");
    }

    const ranges = [];
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
        const node = walker.currentNode;
        const start = node.textContent.search(/\S/);
        if (start < 0) continue;
        const range = document.createRange();
        range.setStart(node, start);
        range.setEnd(node, node.textContent.trimEnd().length);
        ranges.push(range);
    }

    function lineWidths() {
        const rectangles = ranges.flatMap(range => Array.from(range.getClientRects()))
            .filter(rect => rect.width > 0 && rect.height > 0)
            .sort((a, b) => a.top - b.top || a.left - b.left);
        const lines = [];
        for (const rect of rectangles) {
            const line = lines.find(item =>
                rect.top < item.bottom - 1 && rect.bottom > item.top + 1);
            if (line) {
                line.left = Math.min(line.left, rect.left);
                line.right = Math.max(line.right, rect.right);
            } else {
                lines.push({
                    top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right,
                });
            }
        }
        return lines.map(line => line.right - line.left);
    }

    function imbalance(widths) {
        const mean = widths.reduce((sum, width) => sum + width, 0) / widths.length;
        return widths.reduce((sum, width) => sum + (width - mean) ** 2, 0)
            / (widths.length * mean ** 2);
    }

    const original = lineWidths();
    if (original.length < 2) return 100;
    let bestWidth = maximum;
    let bestScore = imbalance(original);

    // Locate the narrowest width that preserves the original number of lines.
    let lower = 0;
    let upper = maximum;
    while (upper - lower > 1) {
        const middle = (lower + upper) / 2;
        element.style.width = `${middle}px`;
        if (lineWidths().length > original.length) lower = middle;
        else upper = middle;
    }

    // Test every CSS-pixel width in the admissible interval; prefer wider ties.
    for (let width = maximum; width >= upper; width -= 1) {
        element.style.width = `${width}px`;
        const widths = lineWidths();
        if (widths.length !== original.length) continue;
        const score = imbalance(widths);
        if (score < bestScore - 1e-10) {
            bestScore = score;
            bestWidth = width;
        }
    }
    const percentage = bestWidth / maximum * 100;
    element.style.width = `${percentage}%`;
    return percentage;
}
