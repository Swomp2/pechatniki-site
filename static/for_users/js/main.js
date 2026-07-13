document.addEventListener("DOMContentLoaded", function () {
  runSetup(setupProblemNavAutoscroll);
  runSetup(setupThemeToggle);
  runSetup(setupLogoContactMenu);
});

function runSetup(setup) {
  try {
    setup();
  } catch (error) {
    console.error("Не удалось запустить модуль страницы:", error);
  }
}

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

function setupProblemNavAutoscroll() {
  const nav = document.querySelector("[data-problem-nav]");

  if (!nav || nav.dataset.problemNavAutoscrollBound === "true") {
    return;
  }

  nav.dataset.problemNavAutoscrollBound = "true";

  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  const initialIdleMs = 450;
  const edgePauseMs = 900;
  const interactionPauseMs = 6000;
  const scrollSpeed = 48;

  let maxScrollLeft = 0;
  let animatedScrollLeft = 0;
  let direction = 1;
  let animationFrame = 0;
  let resumeTimer = 0;
  let edgePauseTimer = 0;
  let resizeFrame = 0;
  let programmaticScrollTimer = 0;
  let lastFrameTime = 0;
  let isOverflowing = false;
  let isPointerDown = false;
  let isPointerInside = false;
  let isFocusInside = false;
  let isInViewport = true;
  let isProgrammaticScroll = false;

  // Предыдущие варианты двигали внутренние обёртки и ломали ручную прокрутку.
  // Здесь единственная движущаяся величина - scrollLeft самого .site-nav.
  function measureOverflow() {
    maxScrollLeft = Math.max(0, nav.scrollWidth - nav.clientWidth);
    isOverflowing = maxScrollLeft > 1.5;
    animatedScrollLeft = clampAutoscrollValue(nav.scrollLeft);
    nav.scrollLeft = animatedScrollLeft;
    nav.dataset.autoscroll =
      isOverflowing && !motionQuery.matches ? "active" : "idle";

    if (!isOverflowing) {
      setNavScrollLeft(0);
      stopAutoscroll();
      return;
    }

    updateDirectionFromPosition();

    if (!canScheduleAutoscroll()) {
      stopAutoscroll();
      return;
    }

    scheduleAutoscroll(initialIdleMs);
  }

  function canScheduleAutoscroll() {
    return (
      isOverflowing &&
      !document.hidden &&
      !motionQuery.matches &&
      isInViewport &&
      !hasHeldInteraction()
    );
  }

  function startAutoscroll() {
    window.clearTimeout(resumeTimer);
    resumeTimer = 0;

    if (animationFrame || !canScheduleAutoscroll() || edgePauseTimer) {
      return;
    }

    lastFrameTime = 0;
    animationFrame = window.requestAnimationFrame(step);
  }

  function stopAutoscroll() {
    if (!animationFrame) {
      return;
    }

    window.cancelAnimationFrame(animationFrame);
    animationFrame = 0;
    lastFrameTime = 0;
  }

  function step(timestamp) {
    animationFrame = 0;

    if (!canScheduleAutoscroll()) {
      return;
    }

    if (!lastFrameTime) {
      lastFrameTime = timestamp;
      animationFrame = window.requestAnimationFrame(step);
      return;
    }

    const deltaSeconds = Math.min((timestamp - lastFrameTime) / 1000, 0.08);
    const nextScrollLeft =
      animatedScrollLeft + direction * scrollSpeed * deltaSeconds;

    lastFrameTime = timestamp;

    if (nextScrollLeft >= maxScrollLeft) {
      setNavScrollLeft(maxScrollLeft);
      direction = -1;
      pauseAtEdge();
      return;
    }

    if (nextScrollLeft <= 0) {
      setNavScrollLeft(0);
      direction = 1;
      pauseAtEdge();
      return;
    }

    setNavScrollLeft(nextScrollLeft);
    animationFrame = window.requestAnimationFrame(step);
  }

  function setNavScrollLeft(value) {
    animatedScrollLeft = clampAutoscrollValue(value);
    isProgrammaticScroll = true;
    nav.scrollLeft = animatedScrollLeft;
    window.clearTimeout(programmaticScrollTimer);
    programmaticScrollTimer = window.setTimeout(function () {
      isProgrammaticScroll = false;
    }, 120);
  }

  function clampAutoscrollValue(value) {
    return Math.min(maxScrollLeft, Math.max(0, value));
  }

  function pauseAtEdge() {
    stopAutoscroll();
    window.clearTimeout(edgePauseTimer);

    // У границ делаем спокойную паузу и продолжаем движение в обратную сторону,
    // без резкого перескока как у бегущей строки.
    edgePauseTimer = window.setTimeout(function () {
      edgePauseTimer = 0;
      startAutoscroll();
    }, edgePauseMs);
  }

  function holdAutoscroll() {
    stopAutoscroll();
    window.clearTimeout(edgePauseTimer);
    window.clearTimeout(resumeTimer);
    edgePauseTimer = 0;
    resumeTimer = 0;
    animatedScrollLeft = clampAutoscrollValue(nav.scrollLeft);
    updateDirectionFromPosition();
  }

  function pauseAfterInteraction() {
    holdAutoscroll();

    if (!hasHeldInteraction()) {
      scheduleAutoscroll(interactionPauseMs, { restart: true });
    }
  }

  function scheduleAutoscroll(delay, options = {}) {
    if (!canScheduleAutoscroll()) {
      return;
    }

    if (resumeTimer && !options.restart) {
      return;
    }

    stopAutoscroll();
    window.clearTimeout(edgePauseTimer);
    window.clearTimeout(resumeTimer);
    edgePauseTimer = 0;

    // Один resumeTimer и один requestAnimationFrame-цикл: повторные клики только
    // перезапускают паузу, но не ускоряют меню несколькими параллельными циклами.
    resumeTimer = window.setTimeout(function () {
      resumeTimer = 0;
      updateDirectionFromPosition();
      startAutoscroll();
    }, delay);
  }

  function hasHeldInteraction() {
    return isPointerDown || isPointerInside || isFocusInside;
  }

  function updateDirectionFromPosition() {
    if (animatedScrollLeft <= 1) {
      direction = 1;
    } else if (animatedScrollLeft >= maxScrollLeft - 1) {
      direction = -1;
    }
  }

  function revealFocusedLink(event) {
    const link = event.target.closest(".site-nav a");

    if (!link) {
      return;
    }

    link.scrollIntoView({
      block: "nearest",
      inline: "nearest",
      behavior: motionQuery.matches ? "auto" : "smooth",
    });
  }

  function queueMeasure() {
    if (resizeFrame) {
      return;
    }

    // ResizeObserver, смена темы и загрузка шрифтов могут менять ширины пунктов.
    // Пересчёт откладываем в кадр, чтобы не читать layout несколько раз подряд.
    resizeFrame = window.requestAnimationFrame(function () {
      resizeFrame = 0;
      measureOverflow();
    });
  }

  nav.addEventListener("mouseenter", function () {
    isPointerInside = true;
    holdAutoscroll();
  });

  nav.addEventListener("mouseleave", function () {
    isPointerInside = false;
    pauseAfterInteraction();
  });

  if ("PointerEvent" in window) {
    nav.addEventListener("pointerdown", function () {
      isPointerDown = true;
      holdAutoscroll();
    });

    nav.addEventListener("pointerup", function () {
      isPointerDown = false;
      pauseAfterInteraction();
    });

    nav.addEventListener("pointercancel", function () {
      isPointerDown = false;
      pauseAfterInteraction();
    });
  } else {
    nav.addEventListener("mousedown", function () {
      isPointerDown = true;
      holdAutoscroll();
    });

    nav.addEventListener("mouseup", function () {
      isPointerDown = false;
      pauseAfterInteraction();
    });

    nav.addEventListener(
      "touchstart",
      function () {
        isPointerDown = true;
        holdAutoscroll();
      },
      { passive: true },
    );

    nav.addEventListener(
      "touchend",
      function () {
        isPointerDown = false;
        pauseAfterInteraction();
      },
      { passive: true },
    );

    nav.addEventListener(
      "touchcancel",
      function () {
        isPointerDown = false;
        pauseAfterInteraction();
      },
      { passive: true },
    );
  }

  nav.addEventListener("click", pauseAfterInteraction);
  nav.addEventListener("wheel", pauseAfterInteraction, { passive: true });

  nav.addEventListener("focusin", function (event) {
    isFocusInside = true;
    holdAutoscroll();
    revealFocusedLink(event);
  });

  nav.addEventListener("focusout", function () {
    window.setTimeout(function () {
      isFocusInside = nav.contains(document.activeElement);

      if (!isFocusInside) {
        pauseAfterInteraction();
      }
    }, 0);
  });

  nav.addEventListener("scroll", function () {
    if (isProgrammaticScroll) {
      return;
    }

    animatedScrollLeft = clampAutoscrollValue(nav.scrollLeft);
    updateDirectionFromPosition();
    pauseAfterInteraction();
  });

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      stopAutoscroll();
      return;
    }

    queueMeasure();
  });

  document.addEventListener("site:page-loaded", queueMeasure);
  window.addEventListener("resize", queueMeasure);
  window.addEventListener("orientationchange", queueMeasure);
  if (motionQuery.addEventListener) {
    motionQuery.addEventListener("change", queueMeasure);
  } else if (motionQuery.addListener) {
    motionQuery.addListener(queueMeasure);
  }

  if (document.fonts?.ready) {
    document.fonts.ready.then(queueMeasure).catch(function () {
      queueMeasure();
    });
  }

  if ("ResizeObserver" in window) {
    const resizeObserver = new ResizeObserver(queueMeasure);

    resizeObserver.observe(nav);
    resizeObserver.observe(nav.parentElement || nav);
  }

  if ("IntersectionObserver" in window) {
    const intersectionObserver = new IntersectionObserver(function (entries) {
      isInViewport = entries.some(function (entry) {
        return entry.isIntersecting;
      });

      if (!isInViewport) {
        stopAutoscroll();
        return;
      }

      queueMeasure();
    });

    intersectionObserver.observe(nav);
  }

  new MutationObserver(queueMeasure).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme", "class"],
  });

  measureOverflow();
}
