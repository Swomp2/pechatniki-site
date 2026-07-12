document.addEventListener("DOMContentLoaded", function () {
  setupProblemEvidenceUpload();
});

window.addEventListener("pageshow", function () {
  // pageshow срабатывает при возврате из bfcache: синхронизируем disabled/hidden.
  setupProblemEvidenceUpload();
});

document.addEventListener("change", function (event) {
  if (!event.target.matches("#id_has_prior_attempts")) {
    return;
  }

  syncEvidenceFilesField();
});

document.addEventListener("site:page-loaded", function () {
  // Page transitions заменяют <main>, поэтому форму надо переинициализировать.
  setupProblemEvidenceUpload();
});

function setupProblemEvidenceUpload() {
  syncEvidenceFilesField();
}

function syncEvidenceFilesField() {
  const checkbox = document.getElementById("id_has_prior_attempts");
  const evidenceFilesField = document.querySelector(
    "[data-evidence-files-field]",
  );
  const evidenceFilesInput = document.getElementById("id_evidence_files");

  if (!checkbox || !evidenceFilesField || !evidenceFilesInput) {
    return;
  }

  const shouldShowEvidenceFiles = checkbox.checked;

  // Disabled нужен не только для UX: скрытое поле не должно случайно отправить
  // старый выбранный файл, если пользователь снял галочку.
  evidenceFilesField.hidden = !shouldShowEvidenceFiles;
  evidenceFilesInput.disabled = !shouldShowEvidenceFiles;

  if (!shouldShowEvidenceFiles) {
    evidenceFilesInput.value = "";
  }
}
