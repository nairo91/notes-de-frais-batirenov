async function loadExpenses(sortField) {
    try {
        const res = await fetch("/api/expenses");
        if (!res.ok) {
            console.error("Erreur API /api/expenses");
            return;
        }
        const data = await res.json();

        if (sortField === "date") {
            data.sort((a, b) => a.date.localeCompare(b.date));
        }

        const tbody = document.querySelector("#expenses-table tbody");
        tbody.innerHTML = "";

        for (const e of data) {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td>${e.date}</td>
                <td>${Number(e.amount).toFixed(2)}</td>
                <td>${e.label}</td>
                <td>${e.chantier}</td>
                <td>${e.user_email}</td>
                <td>${e.receipt_path ? '<a href="/uploads/' + e.receipt_path + '" target="_blank">Voir</a>' : '-'}</td>
            `;
            tbody.appendChild(tr);
        }
    } catch (err) {
        console.error(err);
    }
}
