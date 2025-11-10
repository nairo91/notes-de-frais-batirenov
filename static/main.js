// On va charger toutes les notes au chargement de la page
let allExpenses = [];
let currentSort = null;

async function fetchExpenses() {
    try {
        const res = await fetch("/api/expenses");
        if (!res.ok) {
            console.error("Erreur API /api/expenses");
            return;
        }
        allExpenses = await res.json();
        renderExpenses();
    } catch (err) {
        console.error("Erreur fetchExpenses:", err);
    }
}

// Applique les filtres et le tri, puis met à jour le tableau
function renderExpenses() {
    const tbody = document.querySelector("#expenses-table tbody");
    if (!tbody) return;

    // Récupération des filtres
    const dateFrom = document.getElementById("filter-date-from")?.value || "";
    const dateTo = document.getElementById("filter-date-to")?.value || "";
    const chantierFilter = (document.getElementById("filter-chantier")?.value || "").toLowerCase();

    let data = [...allExpenses];

    // Filtre par période
    if (dateFrom) {
        data = data.filter(e => e.date >= dateFrom);
    }
    if (dateTo) {
        data = data.filter(e => e.date <= dateTo);
    }

    // Filtre par chantier (contient)
    if (chantierFilter) {
        data = data.filter(e =>
            (e.chantier || "").toLowerCase().includes(chantierFilter)
        );
    }

    // Tri
    if (currentSort === "date") {
        data.sort((a, b) => a.date.localeCompare(b.date));
    } else if (currentSort === "amount") {
        data.sort((a, b) => a.amount - b.amount);
    }

    // Rendu dans le tableau
    tbody.innerHTML = "";
    for (const e of data) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>${e.date}</td>
            <td>${Number(e.amount).toFixed(2)} €</td>
            <td>${e.label}</td>
            <td>${e.chantier}</td>
            <td>${e.user_email}</td>
            <td>
                ${e.receipt_path
                    ? `<a href="/uploads/${e.receipt_path}" target="_blank" class="btn btn-link btn-sm">Voir</a>`
                    : '<span class="text-muted">-</span>'
                }
            </td>
        `;
        tbody.appendChild(tr);
    }
}

function sortExpenses(field) {
    currentSort = field;
    renderExpenses();
}

function resetFilters() {
    const df = document.getElementById("filter-date-from");
    const dt = document.getElementById("filter-date-to");
    const ch = document.getElementById("filter-chantier");
    if (df) df.value = "";
    if (dt) dt.value = "";
    if (ch) ch.value = "";
    currentSort = null;
    renderExpenses();
}

// On init quand la page est chargée
document.addEventListener("DOMContentLoaded", () => {
    const table = document.getElementById("expenses-table");
    if (table) {
        fetchExpenses();
    }
});
