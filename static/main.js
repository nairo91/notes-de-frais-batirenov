// Filtrage & tri côté client + scan OCR

function setupFiltersAndSorting() {
  const table = document.getElementById("expenses-table");
  if (!table) return;

  const tbody = table.querySelector("tbody");
  const rows = Array.from(tbody.querySelectorAll("tr"));

  const dateFromInput = document.getElementById("filter-date-from");
  const dateToInput = document.getElementById("filter-date-to");
  const chantierInput = document.getElementById("filter-chantier");
  const resetBtn = document.getElementById("reset-filters-btn");
  const sortDateBtn = document.getElementById("sort-date-btn");
  const sortAmountBtn = document.getElementById("sort-amount-btn");

  let sortDateAsc = true;
  let sortAmountAsc = true;

  function applyFilters() {
    const from = dateFromInput.value || null;
    const to = dateToInput.value || null;
    const chantier = (chantierInput.value || "").toLowerCase();

    rows.forEach((row) => {
      const rowDate = row.getAttribute("data-date") || "";
      const rowChantier = row.getAttribute("data-chantier") || "";

      let visible = true;

      if (from && rowDate < from) visible = false;
      if (to && rowDate > to) visible = false;

      if (chantier && !rowChantier.includes(chantier)) visible = false;

      row.style.display = visible ? "" : "none";
    });
  }

  function resetFilters() {
    if (dateFromInput) dateFromInput.value = "";
    if (dateToInput) dateToInput.value = "";
    if (chantierInput) chantierInput.value = "";
    applyFilters();
  }

  function sortBy(field, asc) {
    const factor = asc ? 1 : -1;

    const visibleRows = rows.slice().filter((r) => r.style.display !== "none");

    visibleRows.sort((a, b) => {
      let av, bv;
      if (field === "date") {
        av = a.getAttribute("data-date") || "";
        bv = b.getAttribute("data-date") || "";
      } else if (field === "amount") {
        av = parseFloat(a.getAttribute("data-amount") || "0");
        bv = parseFloat(b.getAttribute("data-amount") || "0");
      } else {
        av = "";
        bv = "";
      }

      if (av < bv) return -1 * factor;
      if (av > bv) return 1 * factor;
      return 0;
    });

    // Réinsère seulement les visibles dans l'ordre, sans toucher au HTML des boutons
    visibleRows.forEach((row) => tbody.appendChild(row));
  }

  if (dateFromInput) dateFromInput.addEventListener("change", applyFilters);
  if (dateToInput) dateToInput.addEventListener("change", applyFilters);
  if (chantierInput) chantierInput.addEventListener("input", applyFilters);
  if (resetBtn) resetBtn.addEventListener("click", resetFilters);

  if (sortDateBtn) {
    sortDateBtn.addEventListener("click", () => {
      sortBy("date", sortDateAsc);
      sortDateAsc = !sortDateAsc;
    });
  }

  if (sortAmountBtn) {
    sortAmountBtn.addEventListener("click", () => {
      sortBy("amount", sortAmountAsc);
      sortAmountAsc = !sortAmountAsc;
    });
  }
}

function setupScanButton() {
  const btn = document.getElementById("scan-ticket-btn");
  if (!btn) return;

  btn.addEventListener("click", () => {
    const fileInput = document.querySelector('input[name="receipt"]');
    if (!fileInput || !fileInput.files || !fileInput.files[0]) {
      alert("Choisis d'abord un fichier à scanner.");
      return;
    }

    const formData = new FormData();
    formData.append("receipt", fileInput.files[0]);

    btn.disabled = true;
    const originalText = btn.textContent;
    btn.textContent = "Scan en cours...";

    fetch("/api/scan_receipt", {
      method: "POST",
      body: formData,
    })
      .then((resp) => resp.json())
      .then((data) => {
        if (data.error) {
          console.error("OCR error:", data.error);
          alert("Erreur lors du scan : " + data.error);
          return;
        }

        // TTC
        if (data.amount) {
          const amountInput = document.querySelector('input[name="amount"]');
          if (amountInput) amountInput.value = data.amount.replace(".", ",");
        }

        // HT
        if (data.amount_ht) {
          const htInput = document.querySelector('input[name="amount_ht"]');
          if (htInput) htInput.value = data.amount_ht.replace(".", ",");
        }

        // TVA
        if (data.tva_amount) {
          const tvaInput = document.querySelector('input[name="tva_amount"]');
          if (tvaInput) tvaInput.value = data.tva_amount.replace(".", ",");
        }

        // Date
        if (data.date) {
          const dateInput = document.querySelector('input[name="date"]');
          if (dateInput) dateInput.value = data.date;
        }

        // Libellé
        if (data.label) {
          const labelInput = document.querySelector('input[name="label"]');
          if (labelInput && !labelInput.value) {
            labelInput.value = data.label;
          }
        }
      })
      .catch((err) => {
        console.error(err);
        alert("Erreur réseau pendant le scan du ticket.");
      })
      .finally(() => {
        btn.disabled = false;
        btn.textContent = originalText;
      });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  setupFiltersAndSorting();
  setupScanButton();
});
