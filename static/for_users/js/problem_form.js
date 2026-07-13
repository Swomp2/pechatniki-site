document.addEventListener("DOMContentLoaded", function () {
  setupProblemForm();
});

window.addEventListener("pageshow", function () {
  // pageshow срабатывает при возврате из bfcache: синхронизируем disabled/hidden.
  setupProblemForm();
});

document.addEventListener("change", function (event) {
  if (!event.target.matches("#id_has_prior_attempts")) {
    return;
  }

  syncEvidenceFilesField();
});

document.addEventListener("site:page-loaded", function () {
  // Page transitions заменяют <main>, поэтому форму надо переинициализировать.
  setupProblemForm();
});

function setupProblemForm() {
  setupProblemEvidenceUpload();
  setupProblemAjaxSubmit();
}

function setupProblemEvidenceUpload() {
  syncEvidenceFilesField();
}

function setupProblemAjaxSubmit() {
  const forms = document.querySelectorAll(".problem-form");

  if (!window.fetch || !window.FormData) {
    return;
  }

  forms.forEach(function (form) {
    if (form.dataset.ajaxSubmitBound === "true") {
      return;
    }

    form.dataset.ajaxSubmitBound = "true";
    form.addEventListener("submit", handleProblemFormSubmit);
  });
}

async function handleProblemFormSubmit(event) {
  const form = event.currentTarget;

  event.preventDefault();

  if (form.dataset.isSubmitting === "true") {
    return;
  }

  clearProblemFormSubmitMessage(form);
  setProblemFormSubmitting(form, true);

  try {
    const response = await fetch(form.action || window.location.href, {
      method: form.method || "POST",
      body: new FormData(form),
      headers: {
        Accept: "text/html",
        "X-Requested-With": "fetch",
      },
      credentials: "same-origin",
    });

    if (isProblemFormHtmlResponse(response)) {
      const htmlText = await response.text();
      const destination =
        response.headers.get("X-Redirect-URL") ||
        response.url ||
        window.location.href;

      await replaceAfterProblemSubmit(htmlText, destination, {
        shouldPushState: response.ok,
      });
      return;
    }

    const payload = await readProblemFormJson(response);

    if (!response.ok) {
      showProblemFormSubmitMessage(
        form,
        payload?.message || "Проверьте поля формы и попробуйте ещё раз.",
      );
      setProblemFormSubmitting(form, false);
      return;
    }

    if (!payload?.redirect_url) {
      throw new Error("В ответе нет адреса страницы успеха");
    }

    await navigateAfterProblemSubmit(payload.redirect_url);
  } catch (error) {
    console.error("Не удалось отправить обращение без перезагрузки:", error);
    showProblemFormSubmitMessage(
      form,
      "Не удалось отправить обращение. Проверьте соединение и попробуйте ещё раз.",
    );
    setProblemFormSubmitting(form, false);
  }
}

function isProblemFormHtmlResponse(response) {
  const contentType = response.headers.get("content-type") || "";

  return contentType.includes("text/html");
}

async function readProblemFormJson(response) {
  const contentType = response.headers.get("content-type") || "";

  if (!contentType.includes("application/json")) {
    return {};
  }

  return response.json();
}

async function replaceAfterProblemSubmit(htmlText, url, options) {
  const destination = new URL(url, window.location.href).href;
  const transitions = window.sitePageTransitions;

  transitions?.clearCache?.();

  if (transitions?.replaceWithHtml) {
    const didReplace = await transitions.replaceWithHtml(htmlText, destination, {
      shouldPushState: options.shouldPushState,
    });

    if (didReplace !== false) {
      return;
    }
  }

  window.location.assign(destination);
}

async function navigateAfterProblemSubmit(url) {
  const destination = new URL(url, window.location.href).href;
  const transitions = window.sitePageTransitions;

  transitions?.clearCache?.();

  if (transitions?.navigate) {
    const didNavigate = await transitions.navigate(destination, {
      shouldPushState: true,
    });

    if (didNavigate !== false) {
      return;
    }
  }

  window.location.assign(destination);
}

function setProblemFormSubmitting(form, isSubmitting) {
  const submitControls = form.querySelectorAll(
    'button[type="submit"], input[type="submit"]',
  );

  form.dataset.isSubmitting = isSubmitting ? "true" : "false";
  form.setAttribute("aria-busy", isSubmitting ? "true" : "false");

  submitControls.forEach(function (control) {
    control.disabled = isSubmitting;
  });
}

function getProblemFormSubmitMessage(form) {
  let message = form.querySelector("[data-problem-submit-message]");

  if (message) {
    return message;
  }

  message = document.createElement("p");
  message.className = "form-warning";
  message.dataset.problemSubmitMessage = "";
  message.setAttribute("role", "alert");
  message.hidden = true;

  form.prepend(message);

  return message;
}

function showProblemFormSubmitMessage(form, text) {
  const message = getProblemFormSubmitMessage(form);

  message.textContent = text;
  message.hidden = false;
}

function clearProblemFormSubmitMessage(form) {
  const message = form.querySelector("[data-problem-submit-message]");

  if (!message) {
    return;
  }

  message.textContent = "";
  message.hidden = true;
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
