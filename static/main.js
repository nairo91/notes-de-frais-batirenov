// static/main.js

let allExpenses = [];
let isAdmin = false;

function loadExpenses() {
  const table = document.getElementById("expenses-table");
  if (!table) return;

  isAdmin = table.dataset.isAdmin === "true";

  fetch("/api/expenses")
    .then((res) => res.json())
    .then((data) => {
      allExpenses = data;
      renderExpenses(allExpenses);
    })
    .catch((err) => {
      console.error("Erreur lors du chargement des notes de frais :", err);
    });
}

function renderExpenses(expenses) {
  const tbody = document.querySelector("#expenses-table tbody");
  if (!tbody) return;
  tbody.innerHTML = "";

  expenses.forEach((e) => {
    const tr = document.createElement("tr");

    // Date
    const tdDate = document.createElement("td");
    tdDate.dataset.date = e.date;
    tdDate.textContent = e.date;
    tr.appendChild(tdDate);

    // Montant
    const tdAmount = document.createElement("td");
    tdAmount.dataset.amount = e.amount;
    tdAmount.textContent = Number(e.amount).toFixed(2) + " €";
    tr.appendChild(tdAmount);

    // Libellé
    const tdLabel = document.createElement("td");
    tdLabel.textContent = e.label;
    tr.appendChild(tdLabel);

    // Chantier
    const tdChantier = document.createElement("td");
    tdChantier.classList.add("chantier-cell");
    tdChantier.textContent = e.chantier;
    tr.appendChild(tdChantier);

    // Utilisateur
    const tdUser = document.createElement("td");
    tdUser.textContent = e.user_email;
    tr.appendChild(tdUser);

    // Statut
    const tdStatus = document.createElement("td");
    let badgeSpan = document.createElement("span");
    badgeSpan.classList.add("badge");

    if (e.status === "approved") {
      badgeSpan.classList.add("bg-success");
      badgeSpan.textContent = "Validée";
    } else if (e.status === "rejected") {
      badgeSpan.classList.add("bg-danger");
      badgeSpan.textContent = "Refusée";
    } else {
      badgeSpan.classList.add("bg-secondary");
      badgeSpan.textContent = "En attente";
    }
    tdStatus.appendChild(badgeSpan);

    if (e.validated_by) {
      const info = document.createElement("small");
      info.classList.add("text-muted");
      let txt = " par " + e.validated_by;
      if (e.validated_at) {
        txt += " le " + e.validated_at.substring(0, 10);
      }
      info.textContent = " " + txt;
      tdStatus.appendChild(document.createElement("br"));
      tdStatus.appendChild(info);
    }

    tr.appendChild(tdStatus);

    // Justificatif
    const tdJustif = document.createElement("td");
    if (e.receipt_path) {
      const a = document.createElement("a");
      a.textContent = "Voir";
      a.classList.add("btn", "btn-link", "btn-sm");
      a.target = "_blank";

      if (e.receipt_path.startsWith("http")) {
        a.href = e.receipt_path;
      } else {
        a.href = "/uploads/" + e.receipt_path;
      }
      tdJustif.appendChild(a);
    } else {
      const span = document.createElement("span");
      span.classList.add("text-muted");
      span.textContent = "-";
      tdJustif.appendChild(span);
    }
    tr.appendChild(tdJustif);

    // Actions (admin uniquement)
    if (isAdmin) {
      const tdActions = document.createElement("td");
      const actionsDiv = document.createElement("div");
      actionsDiv.classList.add("d-flex", "flex-wrap", "gap-1");

      if (e.status !== "approved") {
        const formApprove = document.createElement("form");
        formApprove.method = "post";
        formApprove.action = `/admin/expenses/${e.id}/approve`;

        const btnApprove = document.createElement("button");
        btnApprove.type = "submit";
        btnApprove.classList.add("btn", "btn-success", "btn-sm");
        btnApprove.textContent = "Valider";

        formApprove.appendChild(btnApprove);
        actionsDiv.appendChild(formApprove);
      }

      if (e.status !== "rejected") {
        const formReject = document.createElement("form");
        formReject.method = "post";
        formReject.action = `/admin/expenses/${e.id}/reject`;

        const btnReject = document.createElement("button");
        btnReject.type = "submit";
        btnReject.classList.add("btn", "btn-outline-danger", "btn-sm");
        btnReject.textContent = "Refuser";

        formReject.appendChild(btnReject);
        actionsDiv.appendChild(formReject);
      }

      tdActions.appendChild(actionsDiv);
      tr.appendChild(tdActions);
    }

    tbody.appendChild(tr);
  });
}

/* -------- Filtres / Tri -------- */

function getFilteredExpenses() {
  const from = document.getElementById("filter-date-from")?.value;
  const to = document.getElementById("filter-date-to")?.value;
  const chantierFilter = document.getElementById("filter-chantier")?.value.toLowerCase() || "";

  return allExpenses.filter((e) => {
    if (from && e.date < from) return false;
    if (to && e.date > to) return false;
    if (chantierFilter && !e.chantier.toLowerCase().includes(chantierFilter)) return false;
    return true;
  });
}

function resetFilters() {
  const from = document.getElementById("filter-date-from");
  const to = document.getElementById("filter-date-to");
  const chantier = document.getElementById("filter-chantier");
  if (from) from.value = "";
  if (to) to.value = "";
  if (chantier) chantier.value = "";
  renderExpenses(allExpenses);
}

function sortExpenses(by) {
  const filtered = getFilteredExpenses().slice(); // copie
  if (by === "date") {
    filtered.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
  } else if (by === "amount") {
    filtered.sort((a, b) => Number(b.amount) - Number(a.amount));
  }
  renderExpenses(filtered);
}

/* -------- Scan ticket -------- */

function setupScanButton() {
  const btn = document.getElementById("scan-receipt-btn");
  if (!btn) return;

  btn.addEventListener("click", () => {
    const fileInput = document.querySelector('input[name="receipt"]');
    if (!fileInput || !fileInput.files.length) {
      alert("Choisis d'abord une image de ticket.");
      return;
    }

    const formData = new FormData();
    formData.append("receipt", fileInput.files[0]);

    fetch("/api/scan_receipt", {
      method: "POST",
      body: formData,
    })
      .then((res) => res.json())
      .then((data) => {
        if (data.error) {
          alert(data.error);
          return;
        }
        if (data.amount) {
          const amountInput = document.querySelector('input[name="amount"]');
          if (amountInput) amountInput.value = data.amount;
        }
        if (data.date) {
          const dateInput = document.querySelector('input[name="date"]');
          if (dateInput) dateInput.value = data.date;
        }
        if (data.label) {
          const labelInput = document.querySelector('input[name="label"]');
          if (labelInput) labelInput.value = data.label;
        }
      })
      .catch((err) => {
        console.error("Erreur pendant le scan :", err);
        alert("Erreur lors du scan du ticket.");
      });
  });
}

/* -------- Init -------- */

document.addEventListener("DOMContentLoaded", () => {
  loadExpenses();
  setupScanButton();
});
