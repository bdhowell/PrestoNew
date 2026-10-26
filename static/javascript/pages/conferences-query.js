(() => {
    "use strict";

    const table = document.querySelector("#conferences-list");
    const rows = table.querySelector("tbody");
    const status = document.querySelector("#conferences-status");

    function dates(item) {
        return item.starts === item.ends ? item.starts : `${item.starts} – ${item.ends}`;
    }

    function choose(number, row) {
        if (table.getAttribute("aria-busy") === "true") {
            return;
        }
        for (const other of rows.rows) {
            other.classList.toggle("selected", other === row);
        }
        window.wikkit.command(String(number));
    }

    function show(items) {
        rows.replaceChildren();
        items.forEach((item, index) => {
            const number = index + 1;
            const row = rows.insertRow();
            row.insertCell().textContent = number;
            const name = document.createElement("button");
            name.type = "button";
            name.className = "conference-choice";
            name.textContent = item.name;
            row.insertCell().appendChild(name);
            row.insertCell().textContent = item.location;
            row.insertCell().textContent = item.purpose;
            row.insertCell().textContent = dates(item);
            row.addEventListener("click", () => choose(number, row));
        });
        table.hidden = items.length === 0;
        table.removeAttribute("aria-busy");
        status.textContent = "";
    }

    function searching() {
        table.setAttribute("aria-busy", "true");
        status.textContent = "Searching for conferences and fairs...";
    }

    window.registerWikkitCommands({
        commands: {
            "show-conferences": { does: show },
            "conferences-searching": { does: searching },
        },
    });
})();
