document.addEventListener("DOMContentLoaded", function () {
  setupPageTransitions();
});

// Переходы заменяют только <main>, поэтому шапка, тема и dropdown остаются
// живыми между страницами. Скрипты страниц слушают site:page-loaded.
function setupPageTransitions() {
  const html = document.documentElement;
  const body = document.body;
  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  const pageCache = new Map();

  let isNavigating = false;

  document.addEventListener("site:content-mutated", function () {
    pageCache.clear();
  });

  if ("scrollRestoration" in history) {
    // Скроллом управляем вручную: новая страница начинается сверху или у hash.
    history.scrollRestoration = "manual";
  }

  window.addEventListener("pageshow", function () {
    html.classList.remove("page-transition-lock");
    isNavigating = false;
  });

  document.addEventListener("pointerover", handlePossiblePrefetch, true);
  document.addEventListener("focusin", handlePossiblePrefetch, true);
  document.addEventListener("click", handlePageTransitionClick);

  window.sitePageTransitions = {
    clearCache() {
      pageCache.clear();
    },
    navigate(url, options = {}) {
      if (isNavigating) {
        return Promise.resolve(false);
      }

      return navigateToPage(url, {
        shouldPushState: options.shouldPushState !== false,
        shouldAnimate:
          typeof options.shouldAnimate === "boolean"
            ? options.shouldAnimate
            : !motionQuery.matches,
      }).then(function () {
        return true;
      });
    },
    replaceWithHtml(htmlText, url, options = {}) {
      if (isNavigating) {
        return Promise.resolve(false);
      }

      return transitionToHtml(htmlText, url, {
        shouldPushState: options.shouldPushState !== false,
        shouldAnimate:
          typeof options.shouldAnimate === "boolean"
            ? options.shouldAnimate
            : !motionQuery.matches,
      }).then(function () {
        return true;
      });
    },
  };

  window.addEventListener("popstate", function () {
    navigateToPage(window.location.href, {
      shouldPushState: false,
      shouldAnimate: !motionQuery.matches,
    });
  });

  function handlePageTransitionClick(event) {
    const link = event.target.closest("a[href]");

    if (!link || !shouldInterceptLink(event, link)) {
      return;
    }

    event.preventDefault();

    if (isNavigating) {
      return;
    }

    navigateToPage(link.href, {
      shouldPushState: true,
      shouldAnimate: !motionQuery.matches,
    });
  }

  function handlePossiblePrefetch(event) {
    const link = event.target.closest?.("a[href]");

    if (!link || !isInternalHtmlPage(link.href)) {
      return;
    }

    const url = new URL(link.href, window.location.href);

    if (url.href === window.location.href || pageCache.has(url.href)) {
      return;
    }

    // Prefetch прогревает HTML при hover/focus, но ошибки не показываем пользователю.
    fetchPage(url.href).catch(function () {
      pageCache.delete(url.href);
    });
  }

  async function navigateToPage(url, options) {
    return transitionToPage(url, options, function () {
      return fetchPage(url);
    });
  }

  async function transitionToHtml(htmlText, url, options) {
    return transitionToPage(url, options, function () {
      return Promise.resolve(parseHtmlDocument(htmlText));
    });
  }

  async function transitionToPage(url, options, loadNextDocument) {
    const currentPage = document.querySelector("main.page");
    let backgroundTransition = null;

    if (!currentPage) {
      window.location.assign(url);
      return;
    }

    isNavigating = true;
    html.classList.add("page-transition-lock");

    const previousBodyMinHeight = body.style.minHeight;
    const lockedHeight = getDocumentRenderHeight();

    body.style.minHeight = `${lockedHeight}px`;

    try {
      const nextDocument = await loadNextDocument();
      const nextPage = nextDocument.querySelector("main.page");
      const nextTitle = nextDocument.querySelector("title");

      if (!nextPage) {
        window.location.assign(url);
        return;
      }

      if (options.shouldAnimate) {
        backgroundTransition = createBackgroundTransitionLayer();
        await animatePageOut(currentPage);
      }

      if (nextTitle) {
        document.title = nextTitle.textContent;
      }

      currentPage.replaceWith(nextPage);

      const nextLockedHeight = getDocumentRenderHeight();

      body.style.minHeight = `${nextLockedHeight}px`;
      body.getBoundingClientRect();

      // Сообщаем остальным скриптам, что DOM основной страницы заменён.
      document.dispatchEvent(
        new CustomEvent("site:page-loaded", {
          detail: {
            page: nextPage,
          },
        }),
      );

      if (options.shouldPushState) {
        history.pushState({}, "", url);
      }

      restoreScrollPosition(url);

      const backgroundFade = backgroundTransition
        ? backgroundTransition.fadeOut()
        : Promise.resolve();

      if (options.shouldAnimate) {
        await Promise.all([animatePageIn(nextPage), backgroundFade]);
      } else {
        await backgroundFade;
      }

      backgroundTransition = null;
      focusMainContent(nextPage);
    } catch (error) {
      console.error("Не удалось плавно открыть страницу:", error);
      window.location.assign(url);
    } finally {
      backgroundTransition?.remove();
      body.style.minHeight = previousBodyMinHeight;
      html.classList.remove("page-transition-lock");
      isNavigating = false;
    }
  }

  async function fetchPage(url) {
    if (pageCache.has(url)) {
      return pageCache.get(url).cloneNode(true);
    }

    const response = await fetch(url, {
      method: "GET",
      headers: {
        Accept: "text/html",
        "X-Requested-With": "fetch",
      },
      credentials: "same-origin",
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const htmlText = await response.text();
    const nextDocument = parseHtmlDocument(htmlText);

    pageCache.set(url, nextDocument);

    return nextDocument.cloneNode(true);
  }

  function parseHtmlDocument(htmlText) {
    return new DOMParser().parseFromString(htmlText, "text/html");
  }

  async function animatePageOut(page) {
    const settings = getPageTransitionSettings();
    const negativeDistance = makeNegativeLength(settings.distance);

    const animation = page.animate(
      [
        {
          opacity: 1,
          transform: "translate3d(0, 0, 0)",
        },
        {
          opacity: 0,
          transform: `translate3d(0, ${negativeDistance}, 0)`,
        },
      ],
      {
        duration: settings.duration,
        easing: settings.easing,
        fill: "forwards",
      },
    );

    await animation.finished;
  }

  async function animatePageIn(page) {
    const settings = getPageTransitionSettings();

    page.style.opacity = "0";
    page.style.transform = `translate3d(0, ${settings.distance}, 0)`;

    page.getBoundingClientRect();

    const animation = page.animate(
      [
        {
          opacity: 0,
          transform: `translate3d(0, ${settings.distance}, 0)`,
        },
        {
          opacity: 1,
          transform: "translate3d(0, 0, 0)",
        },
      ],
      {
        duration: settings.duration,
        easing: settings.easing,
        fill: "forwards",
      },
    );

    await animation.finished;

    page.style.opacity = "";
    page.style.transform = "";
  }

  function createBackgroundTransitionLayer() {
    const bodyStyles = window.getComputedStyle(body);
    const htmlStyles = window.getComputedStyle(html);
    const backgroundImage =
      bodyStyles.backgroundImage !== "none"
        ? bodyStyles.backgroundImage
        : htmlStyles.backgroundImage;
    const backgroundColor =
      bodyStyles.backgroundColor !== "rgba(0, 0, 0, 0)"
        ? bodyStyles.backgroundColor
        : htmlStyles.backgroundColor;
    const layerHeight = getDocumentRenderHeight();
    const scrollY = Math.max(0, window.scrollY || window.pageYOffset || 0);
    const layer = document.createElement("div");

    let isRemoved = false;

    layer.className = "page-background-transition-layer";
    layer.style.backgroundImage = backgroundImage;
    layer.style.backgroundColor = backgroundColor;
    layer.style.setProperty(
      "--page-background-transition-offset-y",
      `-${scrollY}px`,
    );
    layer.style.setProperty(
      "--page-background-transition-height",
      `${layerHeight}px`,
    );

    html.classList.add("page-background-transitioning");
    body.prepend(layer);
    layer.getBoundingClientRect();

    return {
      async fadeOut() {
        const settings = getPageTransitionSettings();
        const animation = layer.animate(
          [
            {
              opacity: 1,
            },
            {
              opacity: 0,
            },
          ],
          {
            duration: settings.duration + 120,
            easing: settings.easing,
            fill: "forwards",
          },
        );

        try {
          await animation.finished;
        } finally {
          this.remove();
        }
      },
      remove() {
        if (isRemoved) {
          return;
        }

        isRemoved = true;
        layer.remove();
        html.classList.remove("page-background-transitioning");
      },
    };
  }

  function getPageTransitionSettings() {
    const styles = getComputedStyle(html);

    const durationValue = styles
      .getPropertyValue("--page-transition-duration-ms")
      .trim();

    const distanceValue = styles
      .getPropertyValue("--page-transition-distance")
      .trim();

    const easingValue = styles
      .getPropertyValue("--page-transition-easing")
      .trim();

    const duration = Number.parseFloat(durationValue);

    return {
      duration: Number.isFinite(duration) ? duration : 620,
      distance: distanceValue || "10px",
      easing: easingValue || "cubic-bezier(0.25, 1, 0.5, 1)",
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

  function makeNegativeLength(length) {
    if (length.startsWith("-")) {
      return length;
    }

    return `-${length}`;
  }

  function shouldInterceptLink(event, link) {
    // Не трогаем новые вкладки, downloads и ссылки, которые явно отключили transition.
    if (event.defaultPrevented) {
      return false;
    }

    if (event.button !== 0) {
      return false;
    }

    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return false;
    }

    if (link.target && link.target !== "_self") {
      return false;
    }

    if (link.hasAttribute("download")) {
      return false;
    }

    if (link.dataset.noPageTransition !== undefined) {
      return false;
    }

    return isInternalHtmlPage(link.href);
  }

  function isInternalHtmlPage(href) {
    const nextUrl = new URL(href, window.location.href);
    const currentUrl = new URL(window.location.href);

    if (nextUrl.origin !== currentUrl.origin) {
      return false;
    }

    // Статику, media и файлы открывает браузер: их нельзя подменять через <main>.
    if (nextUrl.pathname.startsWith("/static/")) {
      return false;
    }

    if (nextUrl.pathname.startsWith("/media/")) {
      return false;
    }

    const looksLikeFile = /\.[a-zA-Z0-9]{2,8}$/.test(nextUrl.pathname);

    if (looksLikeFile && !nextUrl.pathname.endsWith(".html")) {
      return false;
    }

    const isSamePage =
      nextUrl.pathname === currentUrl.pathname &&
      nextUrl.search === currentUrl.search;

    if (isSamePage && nextUrl.hash) {
      return false;
    }

    if (isSamePage && !nextUrl.hash) {
      return false;
    }

    return true;
  }

  function restoreScrollPosition(url) {
    const nextUrl = new URL(url, window.location.href);

    if (nextUrl.hash) {
      const target = document.querySelector(nextUrl.hash);

      if (target) {
        target.scrollIntoView();
        return;
      }
    }

    window.scrollTo({
      top: 0,
      left: 0,
      behavior: "auto",
    });
  }

  function focusMainContent(page) {
    page.setAttribute("tabindex", "-1");
    page.focus({ preventScroll: true });
  }
}
