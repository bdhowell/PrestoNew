(() => {
    "use strict";

    window.balancePosterAuthors = (element, previousHeight) => {
        const authors = [...element.querySelectorAll(".poster-author")];
        const header = element.closest(".poster-header");
        if (!header) throw new Error("Cannot balance authors without a poster header.");
        const originalHeight = previousHeight ?? header.getBoundingClientRect().height;
        element.style.width = "100%";
        const maximum = parseFloat(getComputedStyle(element).width);
        if (!(maximum > 0)) throw new Error("Cannot balance authors with no available width.");
        if (authors.length < 2) return 100;

        const heightLimit = Math.max(originalHeight, header.getBoundingClientRect().height);
        const scale = element.getBoundingClientRect().width / maximum;
        const ranges = authors.map(author => {
            const range = document.createRange();
            range.selectNodeContents(author);
            return range;
        });

        function distribution() {
            const lines = [];
            let splits = 0;
            for (const range of ranges) {
                const rectangles = [...range.getClientRects()].filter(rect => rect.width && rect.height);
                if (!rectangles.length) continue;
                const authorLines = [];
                for (const rect of rectangles) {
                    if (authorLines.some(top => Math.abs(top - rect.top) <= scale)) continue;
                    authorLines.push(rect.top);
                    let line = lines.find(item => Math.abs(item.top - rect.top) <= scale);
                    if (!line) {
                        line = {top: rect.top, count: 0};
                        lines.push(line);
                    }
                    line.count++;
                }
                splits += authorLines.length - 1;
            }
            return {counts: lines.map(line => line.count), splits};
        }

        const initial = distribution();
        let bestWidth = maximum;
        let bestLines = initial.counts.length;
        let bestScore = initial.counts.reduce((sum, count) => sum + count ** 2, 0);
        const lineHeight = parseFloat(getComputedStyle(element).lineHeight);
        const availableHeight = (element.getBoundingClientRect().height + heightLimit
            - header.getBoundingClientRect().height) / scale;
        const maxLines = Math.min(authors.length, Math.floor((availableHeight + 0.1) / lineHeight));
        const quotient = Math.floor(authors.length / maxLines);
        const remainder = authors.length % maxLines;
        const idealScore = (maxLines - remainder) * quotient ** 2 + remainder * (quotient + 1) ** 2;
        // Prefer more fitting lines, then the most even author counts.
        for (let width = maximum - 1; width >= 1; width -= 1) {
            if (!initial.splits && bestLines === maxLines && bestScore === idealScore) break;
            element.style.width = `${width}px`;
            if (header.getBoundingClientRect().height > heightLimit + scale * 0.1) continue;
            const candidate = distribution();
            if (candidate.splits > initial.splits) continue;
            const lines = candidate.counts.length;
            const score = candidate.counts.reduce((sum, count) => sum + count ** 2, 0);
            if (lines > bestLines || (lines === bestLines && score < bestScore)) {
                bestWidth = width;
                bestLines = lines;
                bestScore = score;
            }
        }
        const percentage = bestWidth / maximum * 100;
        element.style.width = `${percentage}%`;
        return percentage;
    };
})();
