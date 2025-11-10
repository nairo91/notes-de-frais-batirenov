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

function renderExpenses() {
    const tbody = document.querySelector("#expenses-table tbody");
    if (!tbody) return;

    const dateFrom = document.getElementById("filter-date-from")?.value || "";
    const dateTo = document.getElementById("filter-date-to")?.value || "";
    const chantierFilter = (document.getElementById("filter-chantier")?.value || "").toLowerCase();

    let data = [...allExpenses];

    if (dateFrom) {
        data = data.filter(e => e.date >= dateFrom);
    }
    if (dateTo) {
        data = data.filter(e => e.date <= dateTo);
    }

    if (chantierFilter) {
        data = data.filter(e =>
            (e.chantier || "").toLowerCase().includes(chantierFilter)
        );
    }

    if (currentSort === "date") {
        data.sort((a, b) => a.date.localeCompare(b.date));
    } else if (currentSort === "amount") {
        data.sort((a, b) => a.amount - b.amount);
    }

    tbody.innerHTML = "";
    for (const e of data) {
        const isUrl = e.receipt_path && e.receipt_path.startsWith("http");
        const receiptCell = e.receipt_path
            ? (isUrl
                ? `<a href="${e.receipt_path}" target="_blank" class="btn btn-link btn-sm">Voir</a>`
                : `<a href="/uploads/${e.receipt_path}" target="_blank" class="btn btn-link btn-sm">Voir</a>`)
            : '<span class="text-muted">-</span>';

        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>${e.date}</td>
            <td>${Number(e.amount).toFixed(2)} €</td>
            <td>${e.label}</td>
            <td>${e.chantier}</td>
            <td>${e.user_email}</td>
            <td>${receiptCell}</td>
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

// --- SCAN TICKET (OCR) ---
async function scanTicket() {
    const input = document.getElementById("receipt-input");
    if (!input || !input.files || !input.files[0]) {
        alert("Choisissez d'abord un fichier ticket/facture.");
        return;
    }

    const file = input.files[0];
    const formData = new FormData();
    formData.append("receipt", file);

    try {
        const btns = document.querySelectorAll("button[onclick='scanTicket()']");
        btns.forEach(b => b.disabled = true);

        const res = await fetch("/api/scan_receipt", {
            method: "POST",
            body: formData,
        });

        const data = await res.json();

        // Si le backend a renvoyé une erreur ou un message explicite
        if (!res.ok || data.error) {
            console.error("Erreur OCR:", data);
            alert(data.error || "Erreur lors du scan du ticket.");
            return;
        }

        let changed = false;

        if (data.amount) {
            const amountInput = document.querySelector("input[name='amount']");
            if (amountInput) {
                amountInput.value = data.amount;
                changed = true;
            }
        }
        if (data.date) {
            const dateInput = document.querySelector("input[name='date']");
            if (dateInput) {
                dateInput.value = data.date;
                changed = true;
            }
        }
        if (data.label) {
            const labelInput = document.querySelector("input[name='label']");
            if (labelInput && !labelInput.value) {
                labelInput.value = data.label;
                changed = true;
            }
        }

        if (!changed) {
            alert("Le ticket a été lu, mais aucun montant ou date n'ont été détectés.");
            console.log("Texte OCR brut :", data.raw_text);
        }

    } catch (err) {
        console.error("Erreur scanTicket:", err);
        alert("Impossible de scanner le ticket pour le moment.");
    } finally {
        const btns = document.querySelectorAll("button[onclick='scanTicket()']");
        btns.forEach(b => b.disabled = false);
    }
}


document.addEventListener("DOMContentLoaded", () => {
    const table = document.getElementById("expenses-table");
    if (table) {
        fetchExpenses();
    }
});
