(() => {
    "use strict";

    function replaceMath(root, replace) {
        for (const wrapper of root.querySelectorAll("[data-mathjax-tex]")) {
            replace(wrapper, wrapper.dataset.mathjaxTex);
        }
    }

    function prepareMath(element) {
        if (element.matches("script, style, pre, code, textarea, [data-mathjax-tex]")) return;
        const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, {
            acceptNode(node) {
                if (!node.parentElement
                    || node.parentElement.closest("script, style, pre, code, textarea, [data-mathjax-tex]")) {
                    return NodeFilter.FILTER_REJECT;
                }
                return /\\\[.+?\\\]|\\\(.+?\\\)/s.test(node.textContent)
                    ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
            },
        });
        const nodes = [];
        while (walker.nextNode()) nodes.push(walker.currentNode);

        for (const node of nodes) {
            const expression = /\\\[.+?\\\]|\\\(.+?\\\)/gs;
            const fragment = document.createDocumentFragment();
            let offset = 0;
            for (const match of node.textContent.matchAll(expression)) {
                fragment.append(document.createTextNode(node.textContent.slice(offset, match.index)));
                const wrapper = document.createElement("span");
                wrapper.dataset.mathjaxTex = match[0];
                wrapper.style.display = "contents";
                wrapper.textContent = match[0];
                fragment.append(wrapper);
                offset = match.index + match[0].length;
            }
            fragment.append(document.createTextNode(node.textContent.slice(offset)));
            node.replaceWith(fragment);
        }
    }

    let typesetting = Promise.resolve();

    window.mathJaxSource = {
        prepare: prepareMath,
        serialize(element) {
            const copy = element.cloneNode(true);
            replaceMath(copy, (wrapper, tex) => {
                wrapper.replaceWith(copy.ownerDocument.createTextNode(tex));
            });
            return copy.innerHTML;
        },
        restore(element) {
            replaceMath(element, (wrapper, tex) => {
                wrapper.replaceWith(element.ownerDocument.createTextNode(tex));
            });
        },
        typeset(element) {
            const render = () => {
                if (!window.MathJax?.typesetPromise) return;
                typesetting = typesetting.then(() => window.MathJax.startup?.promise).then(() => {
                    window.MathJax.typesetClear([element]);
                    window.mathJaxSource.restore(element);
                    prepareMath(element);
                    return window.MathJax.typesetPromise([element]);
                }).catch(error => {
                    console.error("Could not render LaTeX with MathJax", error);
                });
            };
            if (window.MathJax?.typesetPromise) {
                render();
            } else {
                document.addEventListener("mathjax-ready", render, {once: true});
            }
        },
    };

    window.MathJax = {
        tex: {
            inlineMath: [["\\(", "\\)"]],
            displayMath: [["\\[", "\\]"]],
        },
        startup: {
            typeset: false,
            ready() {
                window.MathJax.startup.defaultReady();
                window.MathJax.startup.promise.then(() => {
                    const typesetPage = () => {
                        typesetting = typesetting.then(() => {
                            prepareMath(document.body);
                            return window.MathJax.typesetPromise([document.body]);
                        }).then(() => {
                            document.dispatchEvent(new Event("mathjax-ready"));
                        }).catch(error => {
                            console.error("Could not render page mathematics with MathJax", error);
                        });
                    };
                    if (document.readyState === "loading") {
                        document.addEventListener("DOMContentLoaded", typesetPage, {once: true});
                    } else {
                        typesetPage();
                    }
                }).catch(error => {
                    console.error("Could not initialize MathJax", error);
                });
            },
        },
    };
})();
