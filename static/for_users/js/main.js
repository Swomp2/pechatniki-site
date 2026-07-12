document.addEventListener("DOMContentLoaded", function () {
  setupThemeToggle();
  setupLogoContactMenu();
});

// Тема хранится на <html data-theme="...">: CSS мгновенно подхватывает
// цвета, фон и нужный вариант логотипа без перерисовки всего DOM.
function setupThemeToggle() {
  const themeToggle = document.querySelector("[data-theme-toggle]");
  const html = document.documentElement;
  const body = document.body;

  const themeTransitionDuration = 1100;
  const themeTransitionEasing = "cubic-bezier(0.16, 1, 0.3, 1)";

  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  const colorSchemeQuery = window.matchMedia("(prefers-color-scheme: dark)");

  let isThemeTransitionRunning = false;

  if (!themeToggle) {
    console.warn("Кнопка переключения темы не найдена");
    return;
  }

  // Ручной выбор темы важнее системной темы браузера и переживает перезагрузку.
  const savedTheme = localStorage.getItem("site-theme");

  if (savedTheme === "light" || savedTheme === "dark") {
    applyTheme(savedTheme);
  }

  updateThemeToggle();

  colorSchemeQuery.addEventListener("change", function () {
    const hasManualTheme =
      html.dataset.theme === "light" || html.dataset.theme === "dark";

    if (!hasManualTheme) {
      updateThemeToggle();
    }
  });

  themeToggle.addEventListener("click", function (event) {
    if (isThemeTransitionRunning) {
      return;
    }

    isThemeTransitionRunning = true;

    const currentTheme = getCurrentTheme();
    const nextTheme = currentTheme === "dark" ? "light" : "dark";

    switchThemeWithBackgroundWave(nextTheme, event);
  });

  // Волна поверх старого фона делает смену темы мягкой, но при reduced motion
  // мы сразу меняем data-theme и не запускаем анимацию.
  function switchThemeWithBackgroundWave(nextTheme, event) {
    if (motionQuery.matches) {
      applyTheme(nextTheme);
      localStorage.setItem("site-theme", nextTheme);
      updateThemeToggle();
      isThemeTransitionRunning = false;
      return;
    }

    const backgroundGeometry = lockBackgroundGeometry();
    const pointer = getThemePointer(event);
    const circleX = pointer.x;
    const circleY = pointer.y + backgroundGeometry.scrollY;

    const endRadius = Math.hypot(
      Math.max(circleX, window.innerWidth - circleX),
      Math.max(pointer.y, window.innerHeight - pointer.y),
    );

    const oldBodyStyles = window.getComputedStyle(body);
    const oldHtmlStyles = window.getComputedStyle(html);

    const oldBodyBackgroundImage = oldBodyStyles.backgroundImage;
    const oldBodyBackgroundColor = oldBodyStyles.backgroundColor;
    const oldHtmlBackgroundImage = oldHtmlStyles.backgroundImage;
    const oldHtmlBackgroundColor = oldHtmlStyles.backgroundColor;

    html.style.backgroundImage = oldHtmlBackgroundImage;
    html.style.backgroundColor = oldHtmlBackgroundColor;
    body.style.backgroundImage = oldBodyBackgroundImage;
    body.style.backgroundColor = oldBodyBackgroundColor;

    const wave = document.createElement("div");

    wave.className = `theme-background-wave theme-background-wave--${nextTheme}`;
    wave.style.setProperty("--theme-circle-x", `${circleX}px`);
    wave.style.setProperty("--theme-circle-y", `${circleY}px`);
    wave.style.setProperty("--theme-offset-y", `-${backgroundGeometry.scrollY}px`);
    wave.style.setProperty(
      "--theme-document-height",
      `${backgroundGeometry.height}px`,
    );

    html.classList.add("theme-transitioning");
    themeToggle.disabled = true;

    body.prepend(wave);

    wave.getBoundingClientRect();

    applyTheme(nextTheme);
    localStorage.setItem("site-theme", nextTheme);
    updateThemeToggle();
    body.getBoundingClientRect();
    backgroundGeometry.resize(getDocumentRenderHeight());
    wave.style.setProperty(
      "--theme-document-height",
      `${backgroundGeometry.height}px`,
    );

    const waveAnimation = wave.animate(
      {
        clipPath: [
          `circle(0px at ${circleX}px ${circleY}px)`,
          `circle(${endRadius}px at ${circleX}px ${circleY}px)`,
        ],
      },
      {
        duration: themeTransitionDuration,
        easing: themeTransitionEasing,
        fill: "forwards",
      },
    );

    waveAnimation.finished
      .catch(function () {
        applyTheme(nextTheme);
        localStorage.setItem("site-theme", nextTheme);
        updateThemeToggle();
      })
      .finally(function () {
        html.style.backgroundImage = "";
        html.style.backgroundColor = "";
        body.style.backgroundImage = "";
        body.style.backgroundColor = "";

        wave.remove();

        html.classList.remove("theme-transitioning");
        themeToggle.disabled = false;
        isThemeTransitionRunning = false;
        backgroundGeometry.unlock();
      });
  }

  function lockBackgroundGeometry() {
    const previousHtmlMinHeight = html.style.minHeight;
    const previousBodyMinHeight = body.style.minHeight;
    const stableHeight = getDocumentRenderHeight();
    const scrollY = Math.max(0, window.scrollY || window.pageYOffset || 0);

    html.style.minHeight = `${stableHeight}px`;
    body.style.minHeight = `${stableHeight}px`;

    body.getBoundingClientRect();

    return {
      height: stableHeight,
      scrollY,
      resize(nextHeight) {
        this.height = Math.max(this.height, nextHeight);
        html.style.minHeight = `${this.height}px`;
        body.style.minHeight = `${this.height}px`;
      },
      unlock() {
        html.style.minHeight = previousHtmlMinHeight;
        body.style.minHeight = previousBodyMinHeight;
      },
    };
  }

  function getThemePointer(event) {
    if (Number.isFinite(event.clientX) && Number.isFinite(event.clientY)) {
      return {
        x: event.clientX,
        y: event.clientY,
      };
    }

    const rect = themeToggle.getBoundingClientRect();

    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    };
  }

  function getDocumentRenderHeight() {
    return Math.max(
      document.documentElement.scrollHeight,
      document.documentElement.offsetHeight,
      body.scrollHeight,
      body.offsetHeight,
      window.innerHeight,
    );
  }

  function applyTheme(theme) {
    html.dataset.theme = theme;
  }

  // Если пользователь ещё не выбирал тему вручную, опираемся на системную.
  function getCurrentTheme() {
    if (html.dataset.theme === "light" || html.dataset.theme === "dark") {
      return html.dataset.theme;
    }

    return colorSchemeQuery.matches ? "dark" : "light";
  }

  // Подпись и aria-label описывают текущую тему, а не действие кнопки.
  function updateThemeToggle() {
    const currentTheme = getCurrentTheme();
    const isDark = currentTheme === "dark";

    const themeToggleIcon = themeToggle.querySelector(
      "[data-theme-toggle-icon]",
    );
    const themeToggleText = themeToggle.querySelector(
      "[data-theme-toggle-text]",
    );

    if (themeToggleIcon) {
      themeToggleIcon.textContent = isDark ? "☾" : "☼";
    }

    if (themeToggleText) {
      themeToggleText.textContent = isDark ? "Тёмная" : "Светлая";
    } else {
      themeToggle.textContent = isDark ? "☾ Тёмная" : "☼ Светлая";
    }

    themeToggle.setAttribute(
      "aria-label",
      `Текущая тема: ${isDark ? "тёмная" : "светлая"}`,
    );
  }
}

// На мобильных первый тап по логотипу открывает меню контактов,
// а второй тап уже даёт перейти на главную страницу.
function setupLogoContactMenu() {
  const logoMenu = document.querySelector("[data-logo-menu]");
  const logoTrigger = document.querySelector("[data-logo-menu-trigger]");

  if (!logoMenu || !logoTrigger) {
    return;
  }

  const touchOrMobileQuery = window.matchMedia(
    "(max-width: 760px), (hover: none), (pointer: coarse)",
  );

  updateLogoMenuMode();
  touchOrMobileQuery.addEventListener("change", updateLogoMenuMode);

  logoTrigger.addEventListener("click", function (event) {
    if (!touchOrMobileQuery.matches) {
      return;
    }

    const isOpen = logoMenu.classList.contains("is-open");

    if (!isOpen) {
      event.preventDefault();

      logoMenu.classList.add("is-open");
      logoTrigger.setAttribute("aria-expanded", "true");

      return;
    }

    closeLogoMenu();
    logoTrigger.blur();
  });

  document.addEventListener("click", function (event) {
    if (!logoMenu.classList.contains("is-open")) {
      return;
    }

    if (logoMenu.contains(event.target)) {
      return;
    }

    closeLogoMenu();
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") {
      return;
    }

    closeLogoMenu();
  });

  document.addEventListener("site:page-loaded", function () {
    // После ajax-like перехода закрываем меню, чтобы оно не зависало поверх новой страницы.
    closeLogoMenu();
  });

  function closeLogoMenu() {
    logoMenu.classList.remove("is-open");
    logoTrigger.setAttribute("aria-expanded", "false");
  }

  function updateLogoMenuMode() {
    const isTouchMode = touchOrMobileQuery.matches;

    logoMenu.classList.toggle("logo-menu--touch", isTouchMode);

    if (!isTouchMode) {
      closeLogoMenu();
    }
  }
}
