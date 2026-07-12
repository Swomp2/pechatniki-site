// При отказе в решении проблемы должно появляться поле с причиной отклонения.
// Этот скрипт его показывает

document.addEventListener("DOMContentLoaded", function () {
  const statusSelect = document.getElementById("id_status");
  const rejectionReasonRow = document.querySelector(".field-rejection_reason");

  if (!statusSelect || !rejectionReasonRow) {
    return;
  }

  function updateRejectionReasonVisibility() {
    const rejectedStatusValue = "rejected";

    if (statusSelect.value === rejectedStatusValue) {
      rejectionReasonRow.style.display = "";
    } else {
      rejectionReasonRow.style.display = "none";
    }
  }

  updateRejectionReasonVisibility();

  statusSelect.addEventListener("change", updateRejectionReasonVisibility);
});
