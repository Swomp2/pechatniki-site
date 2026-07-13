document.addEventListener("DOMContentLoaded", function () {
  setupPhotoLightbox();
});

// Lightbox работает делегированно от document, поэтому продолжает ловить
// изображения после page transitions, когда <main> заменяется новым HTML.
function setupPhotoLightbox() {
  const html = document.documentElement;
  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  const zoomQuery = window.matchMedia("(pointer: coarse)");

  const animationDuration = 420;
  const animationEasing = "cubic-bezier(0.16, 1, 0.3, 1)";
  const maxZoomScale = 4;
  const doubleTapZoomScale = 2.25;
  const doubleTapDelay = 280;

  let lightbox = null;
  let activeImage = null;
  let closeButton = null;
  let prevButton = null;
  let nextButton = null;
  let counter = null;

  let galleryImages = [];
  let activeIndex = 0;
  let isClosing = false;
  let animationGeneration = 0;
  // Стрелки привязываются к первой открытой фотографии, чтобы навигация
  // не прыгала при переключении изображений разного размера.
  let navigationAnchorSourceImage = null;
  const zoomState = {
    scale: 1,
    translateX: 0,
    translateY: 0,
    mode: null,
    moved: false,
    lastTapAt: 0,
    lastTapX: 0,
    lastTapY: 0,
    startX: 0,
    startY: 0,
    startTranslateX: 0,
    startTranslateY: 0,
    startScale: 1,
    pinchStartDistance: 0,
    pinchStartCenterX: 0,
    pinchStartCenterY: 0,
    pinchLocalX: 0,
    pinchLocalY: 0,
  };

  document.addEventListener("click", function (event) {
    const clickedImage = event.target.closest(
      "[data-lightbox-image], .page--problem-list .problem-card img",
    );

    if (!clickedImage) {
      return;
    }

    event.preventDefault();

    if (lightbox && !isClosing) {
      openFromPageImage(clickedImage, {
        reuseExistingLightbox: true,
      });

      return;
    }

    openFromPageImage(clickedImage, {
      reuseExistingLightbox: false,
    });
  });

  // Capture-обработчик закрывает фото по любому клику вне изображения и
  // контролов, включая верхнюю область, где под overlay визуально находится шапка.
  document.addEventListener(
    "click",
    function (event) {
      if (!lightbox || isClosing) {
        return;
      }

      const clickedInsideLightboxControls = event.target.closest(
        ".photo-lightbox__close, .photo-lightbox__nav, .photo-lightbox__counter",
      );

      if (clickedInsideLightboxControls) {
        return;
      }

      const clickedActiveImage = event.target.closest(".photo-lightbox__image");

      if (clickedActiveImage) {
        return;
      }

      event.preventDefault();
      event.stopPropagation();

      closePhotoLightbox();
    },
    true,
  );

  // Клавиатурная навигация делает просмотр фото юзабельным без мыши.
  document.addEventListener("keydown", function (event) {
    if (!lightbox || isClosing) {
      return;
    }

    if (event.key === "Escape") {
      event.preventDefault();
      closePhotoLightbox();
      return;
    }

    if (event.key === "ArrowLeft") {
      event.preventDefault();
      showPreviousPhoto();
      return;
    }

    if (event.key === "ArrowRight") {
      event.preventDefault();
      showNextPhoto();
    }
  });

  document.addEventListener("mousemove", handleZoomMouseMove);
  document.addEventListener("mouseup", handleZoomMouseUp);

  window.addEventListener("resize", function () {
    if (!lightbox || !activeImage || isClosing) {
      return;
    }

    cancelAnimations(activeImage);

    const activeSourceImage = getActiveSourceImage();
    const finalRect = getFinalImageRect(activeSourceImage);

    applyRectToImage(activeImage, finalRect);
    clampZoomToViewport();
    applyZoomTransform();

    if (navigationAnchorSourceImage) {
      updateNavPosition(getFinalImageRect(navigationAnchorSourceImage));
    }
  });

  function openFromPageImage(sourceImage, options) {
    galleryImages = getImageGroup(sourceImage);
    activeIndex = Math.max(0, galleryImages.indexOf(sourceImage));

    if (!options.reuseExistingLightbox) {
      navigationAnchorSourceImage = sourceImage;
    }

    if (options.reuseExistingLightbox && lightbox) {
      switchToPhoto(activeIndex, {
        direction: 0,
        fromPageImage: true,
      });

      return;
    }

    createLightbox();
    openPhotoLightbox(sourceImage);
  }

  function getImageGroup(sourceImage) {
    // В списках проблем галерея ограничена одной карточкой обращения.
    // На главной .problem-card нет, поэтому работает общая data-lightbox-gallery.
    const problemCard = sourceImage.closest(".problem-card");

    if (problemCard) {
      return Array.from(
        problemCard.querySelectorAll("[data-lightbox-image], img"),
      );
    }

    const gallery = sourceImage.closest("[data-lightbox-gallery]");

    if (gallery) {
      return Array.from(
        gallery.querySelectorAll("[data-lightbox-image], .problem-card img"),
      );
    }

    return [sourceImage];
  }

  function createLightbox() {
    // Создаём overlay лениво, только при первом открытии фото.
    lightbox = document.createElement("div");
    closeButton = document.createElement("button");
    prevButton = document.createElement("button");
    nextButton = document.createElement("button");
    counter = document.createElement("div");

    lightbox.className = "photo-lightbox";
    lightbox.setAttribute("role", "dialog");
    lightbox.setAttribute("aria-modal", "true");
    lightbox.setAttribute("aria-label", "Просмотр фотографии");

    closeButton.className = "photo-lightbox__close";
    closeButton.type = "button";
    closeButton.setAttribute("aria-label", "Закрыть фотографию");
    closeButton.textContent = "×";

    prevButton.className = "photo-lightbox__nav photo-lightbox__nav--prev";
    prevButton.type = "button";
    prevButton.setAttribute("aria-label", "Предыдущая фотография");
    prevButton.textContent = "‹";

    nextButton.className = "photo-lightbox__nav photo-lightbox__nav--next";
    nextButton.type = "button";
    nextButton.setAttribute("aria-label", "Следующая фотография");
    nextButton.textContent = "›";

    counter.className = "photo-lightbox__counter";

    lightbox.append(closeButton, prevButton, nextButton, counter);
    document.body.append(lightbox);

    html.classList.add("photo-lightbox-lock");

    lightbox.addEventListener("click", function (event) {
      if (event.target === lightbox) {
        closePhotoLightbox();
      }
    });

    closeButton.addEventListener("click", function (event) {
      event.stopPropagation();
      closePhotoLightbox();
    });

    prevButton.addEventListener("click", function (event) {
      event.stopPropagation();
      showPreviousPhoto();
    });

    nextButton.addEventListener("click", function (event) {
      event.stopPropagation();
      showNextPhoto();
    });

    lightbox.addEventListener("dblclick", handleZoomDoubleClick);
    lightbox.addEventListener("mousedown", handleZoomMouseDown);

    lightbox.addEventListener("touchstart", handleZoomTouchStart, {
      passive: false,
    });
    lightbox.addEventListener("touchmove", handleZoomTouchMove, {
      passive: false,
    });
    lightbox.addEventListener("touchend", handleZoomTouchEnd, {
      passive: false,
    });
    lightbox.addEventListener("touchcancel", handleZoomTouchCancel, {
      passive: false,
    });
  }

  function openPhotoLightbox(sourceImage) {
    isClosing = false;

    const generation = increaseAnimationGeneration();

    const sourceRect = sourceImage.getBoundingClientRect();
    const sourceBorderRadius = getComputedStyle(sourceImage).borderRadius;

    activeImage = createLightboxImage(sourceImage);
    resetZoom();

    applyRectToImage(activeImage, {
      top: sourceRect.top,
      left: sourceRect.left,
      width: sourceRect.width,
      height: sourceRect.height,
      borderRadius: sourceBorderRadius,
    });

    activeImage.style.objectFit = "cover";

    lightbox.append(activeImage);
    updateControls();

    const finalRect = getFinalImageRect(sourceImage);
    updateNavPosition(finalRect);

    requestAnimationFrame(function () {
      animateOpen(
        activeImage,
        lightbox,
        sourceRect,
        finalRect,
        sourceBorderRadius,
      )
        .then(function () {
          if (generation !== animationGeneration || !activeImage) {
            return;
          }

          applyRectToImage(activeImage, finalRect);
          activeImage.style.objectFit = "contain";
        })
        .catch(function () {
          return null;
        });
    });
  }

  function closePhotoLightbox() {
    if (!lightbox || !activeImage || isClosing) {
      return;
    }

    isClosing = true;

    const generation = increaseAnimationGeneration();

    const imageToClose = activeImage;
    const lightboxToClose = lightbox;
    const sourceImage = getActiveSourceImage();

    cancelAnimations(imageToClose);
    resetZoom();

    const currentRect = imageToClose.getBoundingClientRect();

    const sourceRect = sourceImage?.isConnected
      ? sourceImage.getBoundingClientRect()
      : null;

    const sourceBorderRadius = sourceImage?.isConnected
      ? getComputedStyle(sourceImage).borderRadius
      : "24px";

    const targetRect = sourceRect
      ? {
          top: sourceRect.top,
          left: sourceRect.left,
          width: sourceRect.width,
          height: sourceRect.height,
          borderRadius: sourceBorderRadius,
        }
      : {
          top: currentRect.top + currentRect.height / 2,
          left: currentRect.left + currentRect.width / 2,
          width: 0,
          height: 0,
          borderRadius: "24px",
        };

    imageToClose.style.objectFit = "cover";

    animateClose(imageToClose, lightboxToClose, currentRect, targetRect)
      .catch(function () {
        return null;
      })
      .finally(function () {
        if (generation !== animationGeneration) {
          return;
        }

        lightboxToClose.remove();
        html.classList.remove("photo-lightbox-lock");

        lightbox = null;
        activeImage = null;
        closeButton = null;
        prevButton = null;
        nextButton = null;
        counter = null;
        galleryImages = [];
        activeIndex = 0;
        navigationAnchorSourceImage = null;
        resetZoom();
        isClosing = false;
      });
  }

  function showPreviousPhoto() {
    if (!lightbox || galleryImages.length < 2 || isClosing) {
      return;
    }

    const nextIndex =
      activeIndex === 0 ? galleryImages.length - 1 : activeIndex - 1;

    switchToPhoto(nextIndex, {
      direction: -1,
      fromPageImage: false,
    });
  }

  function showNextPhoto() {
    if (!lightbox || galleryImages.length < 2 || isClosing) {
      return;
    }

    const nextIndex =
      activeIndex === galleryImages.length - 1 ? 0 : activeIndex + 1;

    switchToPhoto(nextIndex, {
      direction: 1,
      fromPageImage: false,
    });
  }

  // Реализация переключения восстановлена как проверенная рабочая схема:
  // один постоянный <img>, простой activeIndex и только смена src/alt.
  // Масштабирование не должно вмешиваться в эту логику без полного тестирования.
  function switchToPhoto(nextIndex, options) {
    if (!lightbox || !activeImage || isClosing) {
      return;
    }

    const nextSourceImage = galleryImages[nextIndex];

    if (!nextSourceImage) {
      return;
    }

    const generation = increaseAnimationGeneration();
    const previousRect = activeImage.getBoundingClientRect();
    const finalRect = getFinalImageRect(nextSourceImage);

    cancelAnimations(activeImage);
    removeInactiveLightboxImages(activeImage);
    resetZoom();
    activeIndex = nextIndex;
    updateActiveImageSource(nextSourceImage);
    updateControls();

    if (options.fromPageImage) {
      const sourceRect = nextSourceImage.getBoundingClientRect();
      const sourceBorderRadius = getComputedStyle(nextSourceImage).borderRadius;

      applyRectToImage(activeImage, {
        top: sourceRect.top,
        left: sourceRect.left,
        width: sourceRect.width,
        height: sourceRect.height,
        borderRadius: sourceBorderRadius,
      });

      activeImage.style.opacity = "1";
      activeImage.style.objectFit = "cover";

      animateImageMove(activeImage, sourceRect, finalRect, sourceBorderRadius)
        .then(function () {
          if (generation !== animationGeneration || !activeImage) {
            return;
          }

          applyRectToImage(activeImage, finalRect);
          activeImage.style.objectFit = "contain";
        })
        .catch(function () {
          return null;
        });

      return;
    }

    applyRectToImage(activeImage, {
      top: previousRect.top,
      left: previousRect.left,
      width: previousRect.width,
      height: previousRect.height,
      borderRadius: getComputedStyle(activeImage).borderRadius,
    });

    activeImage.style.opacity = "0";
    activeImage.style.objectFit = "contain";

    animatePhotoSwitch(activeImage, previousRect, finalRect, options.direction)
      .then(function () {
        if (generation !== animationGeneration || !activeImage) {
          return;
        }

        applyRectToImage(activeImage, finalRect);
        activeImage.style.opacity = "";
        activeImage.style.transform = "";
      })
      .catch(function () {
        return null;
      });
  }

  async function animateOpen(
    image,
    overlay,
    sourceRect,
    finalRect,
    sourceBorderRadius,
  ) {
    if (motionQuery.matches) {
      overlay.style.opacity = "1";
      applyRectToImage(image, finalRect);
      return;
    }

    const overlayAnimation = overlay.animate(
      [
        {
          opacity: 0,
        },
        {
          opacity: 1,
        },
      ],
      {
        duration: animationDuration,
        easing: animationEasing,
        fill: "forwards",
      },
    );

    const imageAnimation = image.animate(
      [
        {
          top: `${sourceRect.top}px`,
          left: `${sourceRect.left}px`,
          width: `${sourceRect.width}px`,
          height: `${sourceRect.height}px`,
          borderRadius: sourceBorderRadius,
          opacity: 1,
        },
        {
          top: `${finalRect.top}px`,
          left: `${finalRect.left}px`,
          width: `${finalRect.width}px`,
          height: `${finalRect.height}px`,
          borderRadius: finalRect.borderRadius,
          opacity: 1,
        },
      ],
      {
        duration: animationDuration,
        easing: animationEasing,
        fill: "forwards",
      },
    );

    await Promise.allSettled([
      overlayAnimation.finished,
      imageAnimation.finished,
    ]);
  }

  async function animateClose(image, overlay, currentRect, targetRect) {
    if (motionQuery.matches) {
      return;
    }

    const overlayAnimation = overlay.animate(
      [
        {
          opacity: 1,
        },
        {
          opacity: 0,
        },
      ],
      {
        duration: animationDuration,
        easing: animationEasing,
        fill: "forwards",
      },
    );

    const imageAnimation = image.animate(
      [
        {
          top: `${currentRect.top}px`,
          left: `${currentRect.left}px`,
          width: `${currentRect.width}px`,
          height: `${currentRect.height}px`,
          borderRadius: getComputedStyle(image).borderRadius,
          opacity: 1,
        },
        {
          top: `${targetRect.top}px`,
          left: `${targetRect.left}px`,
          width: `${targetRect.width}px`,
          height: `${targetRect.height}px`,
          borderRadius: targetRect.borderRadius,
          opacity: 1,
        },
      ],
      {
        duration: animationDuration,
        easing: animationEasing,
        fill: "forwards",
      },
    );

    await Promise.allSettled([
      overlayAnimation.finished,
      imageAnimation.finished,
    ]);
  }

  async function animateImageMove(
    image,
    sourceRect,
    finalRect,
    sourceBorderRadius,
  ) {
    if (motionQuery.matches) {
      applyRectToImage(image, finalRect);
      return;
    }

    const imageAnimation = image.animate(
      [
        {
          top: `${sourceRect.top}px`,
          left: `${sourceRect.left}px`,
          width: `${sourceRect.width}px`,
          height: `${sourceRect.height}px`,
          borderRadius: sourceBorderRadius,
          opacity: 1,
        },
        {
          top: `${finalRect.top}px`,
          left: `${finalRect.left}px`,
          width: `${finalRect.width}px`,
          height: `${finalRect.height}px`,
          borderRadius: finalRect.borderRadius,
          opacity: 1,
        },
      ],
      {
        duration: animationDuration,
        easing: animationEasing,
        fill: "forwards",
      },
    );

    await imageAnimation.finished;
  }

  async function animatePhotoSwitch(
    image,
    previousRect,
    finalRect,
    direction,
  ) {
    if (motionQuery.matches) {
      applyRectToImage(image, finalRect);
      image.style.opacity = "";
      return;
    }

    const safeDirection = direction || 1;
    const slideDistance = Math.min(36, window.innerWidth * 0.06);

    const imageAnimation = image.animate(
      [
        {
          top: `${previousRect.top}px`,
          left: `${previousRect.left}px`,
          width: `${previousRect.width}px`,
          height: `${previousRect.height}px`,
          borderRadius: getComputedStyle(image).borderRadius,
          opacity: 0,
          transform: `translate3d(${safeDirection * slideDistance}px, 0, 0) scale(0.985)`,
        },
        {
          top: `${finalRect.top}px`,
          left: `${finalRect.left}px`,
          width: `${finalRect.width}px`,
          height: `${finalRect.height}px`,
          borderRadius: finalRect.borderRadius,
          opacity: 1,
          transform: "translate3d(0, 0, 0) scale(1)",
        },
      ],
      {
        duration: 260,
        easing: animationEasing,
        fill: "forwards",
      },
    );

    await imageAnimation.finished;
  }

  function createLightboxImage(sourceImage) {
    const image = document.createElement("img");

    image.className = "photo-lightbox__image";
    image.src = sourceImage.currentSrc || sourceImage.src;
    image.alt = sourceImage.alt || "Фото проблемы";
    image.draggable = false;

    image.addEventListener("click", function (event) {
      event.stopPropagation();
    });

    return image;
  }

  function updateActiveImageSource(sourceImage) {
    activeImage.src = sourceImage.currentSrc || sourceImage.src;
    activeImage.alt = sourceImage.alt || "Фото проблемы";
  }

  function isMobileZoomEnabled() {
    return (
      activeImage &&
      (zoomQuery.matches || navigator.maxTouchPoints > 0)
    );
  }

  function isZoomImageEvent(event) {
    return (
      isMobileZoomEnabled() &&
      event.target.closest(".photo-lightbox__image") === activeImage
    );
  }

  function isActiveImageEvent(event) {
    return (
      activeImage &&
      event.target.closest(".photo-lightbox__image") === activeImage
    );
  }

  function handleZoomDoubleClick(event) {
    if (!isActiveImageEvent(event) || isClosing) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();

    if (zoomState.scale > 1) {
      resetZoom();
      return;
    }

    zoomAroundPoint(event.clientX, event.clientY, doubleTapZoomScale);
  }

  function handleZoomMouseDown(event) {
    if (
      !isActiveImageEvent(event) ||
      isClosing ||
      event.button !== 0 ||
      zoomState.scale <= 1
    ) {
      return;
    }

    zoomState.mode = "mouse-pan";
    zoomState.moved = false;
    zoomState.startX = event.clientX;
    zoomState.startY = event.clientY;
    zoomState.startTranslateX = zoomState.translateX;
    zoomState.startTranslateY = zoomState.translateY;
    activeImage.classList.add("is-dragging");

    event.preventDefault();
    event.stopPropagation();
  }

  function handleZoomMouseMove(event) {
    if (zoomState.mode !== "mouse-pan" || !activeImage || isClosing) {
      return;
    }

    const deltaX = event.clientX - zoomState.startX;
    const deltaY = event.clientY - zoomState.startY;

    if (Math.hypot(deltaX, deltaY) > 2) {
      zoomState.moved = true;
    }

    zoomState.translateX = zoomState.startTranslateX + deltaX;
    zoomState.translateY = zoomState.startTranslateY + deltaY;
    clampZoomToViewport();
    applyZoomTransform();

    event.preventDefault();
  }

  function handleZoomMouseUp(event) {
    if (zoomState.mode !== "mouse-pan") {
      return;
    }

    zoomState.mode = null;

    if (activeImage) {
      activeImage.classList.remove("is-dragging");
    }

    if (zoomState.moved) {
      event.preventDefault();
      event.stopPropagation();
    }
  }

  function handleZoomTouchStart(event) {
    if (!isZoomImageEvent(event) || isClosing) {
      return;
    }

    if (event.touches.length === 1) {
      const touch = event.touches[0];

      zoomState.mode = "tap";
      zoomState.moved = false;
      zoomState.startX = touch.clientX;
      zoomState.startY = touch.clientY;
      zoomState.startTranslateX = zoomState.translateX;
      zoomState.startTranslateY = zoomState.translateY;
      event.preventDefault();
      return;
    }

    if (event.touches.length === 2) {
      startPinchZoom(event.touches[0], event.touches[1]);
      event.preventDefault();
    }
  }

  function handleZoomTouchMove(event) {
    if (!isMobileZoomEnabled() || !zoomState.mode || isClosing) {
      return;
    }

    if (zoomState.mode === "pinch" && event.touches.length >= 2) {
      updatePinchZoom(event.touches[0], event.touches[1]);
      event.preventDefault();
      return;
    }

    if (
      (zoomState.mode === "pan" || zoomState.mode === "tap") &&
      event.touches.length === 1
    ) {
      const touch = event.touches[0];
      const deltaX = touch.clientX - zoomState.startX;
      const deltaY = touch.clientY - zoomState.startY;

      if (Math.hypot(deltaX, deltaY) > 6) {
        zoomState.moved = true;
      }

      if (zoomState.scale > 1) {
        zoomState.translateX = zoomState.startTranslateX + deltaX;
        zoomState.translateY = zoomState.startTranslateY + deltaY;
        clampZoomToViewport();
        applyZoomTransform();
      }

      event.preventDefault();
    }
  }

  function handleZoomTouchEnd(event) {
    if (!isMobileZoomEnabled() || !zoomState.mode) {
      return;
    }

    if (event.touches.length === 1 && zoomState.mode === "pinch") {
      const touch = event.touches[0];

      zoomState.mode = "pan";
      zoomState.startX = touch.clientX;
      zoomState.startY = touch.clientY;
      zoomState.startTranslateX = zoomState.translateX;
      zoomState.startTranslateY = zoomState.translateY;
      event.preventDefault();
      return;
    }

    if (event.touches.length > 0) {
      return;
    }

    if (
      zoomState.mode === "tap" &&
      !zoomState.moved &&
      event.changedTouches.length
    ) {
      handleZoomDoubleTap(event.changedTouches[0]);
    }

    if (zoomState.scale <= 1.02) {
      resetZoom({
        preserveTap:
          zoomState.mode === "tap" &&
          !zoomState.moved &&
          event.changedTouches.length > 0,
      });
    } else {
      clampZoomToViewport();
      applyZoomTransform();
    }

    zoomState.mode = null;
    event.preventDefault();
  }

  function handleZoomTouchCancel() {
    zoomState.mode = null;
    zoomState.moved = false;
  }

  function startPinchZoom(firstTouch, secondTouch) {
    const center = getTouchCenter(firstTouch, secondTouch);
    const baseRect = getImageBaseRect();
    const imageCenterX = baseRect.left + baseRect.width / 2;
    const imageCenterY = baseRect.top + baseRect.height / 2;

    zoomState.mode = "pinch";
    zoomState.moved = true;
    zoomState.startScale = zoomState.scale;
    zoomState.pinchStartDistance = getTouchDistance(firstTouch, secondTouch);
    zoomState.pinchStartCenterX = center.x;
    zoomState.pinchStartCenterY = center.y;
    zoomState.pinchLocalX =
      (center.x - imageCenterX - zoomState.translateX) / zoomState.scale;
    zoomState.pinchLocalY =
      (center.y - imageCenterY - zoomState.translateY) / zoomState.scale;
  }

  function updatePinchZoom(firstTouch, secondTouch) {
    const distance = getTouchDistance(firstTouch, secondTouch);
    const center = getTouchCenter(firstTouch, secondTouch);
    const baseRect = getImageBaseRect();
    const imageCenterX = baseRect.left + baseRect.width / 2;
    const imageCenterY = baseRect.top + baseRect.height / 2;
    const nextScale = clamp(
      zoomState.startScale * (distance / zoomState.pinchStartDistance),
      1,
      maxZoomScale,
    );

    zoomState.scale = nextScale;
    zoomState.translateX =
      center.x - imageCenterX - zoomState.pinchLocalX * nextScale;
    zoomState.translateY =
      center.y - imageCenterY - zoomState.pinchLocalY * nextScale;

    clampZoomToViewport();
    applyZoomTransform();
  }

  function handleZoomDoubleTap(touch) {
    const now = Date.now();
    const tapDistance = Math.hypot(
      touch.clientX - zoomState.lastTapX,
      touch.clientY - zoomState.lastTapY,
    );
    const isDoubleTap =
      now - zoomState.lastTapAt <= doubleTapDelay && tapDistance <= 34;

    zoomState.lastTapAt = now;
    zoomState.lastTapX = touch.clientX;
    zoomState.lastTapY = touch.clientY;

    if (!isDoubleTap) {
      return;
    }

    zoomState.lastTapAt = 0;

    if (zoomState.scale > 1) {
      resetZoom();
      return;
    }

    zoomAroundPoint(touch.clientX, touch.clientY, doubleTapZoomScale);
  }

  function zoomAroundPoint(clientX, clientY, nextScale) {
    const baseRect = getImageBaseRect();
    const imageCenterX = baseRect.left + baseRect.width / 2;
    const imageCenterY = baseRect.top + baseRect.height / 2;
    const localX =
      (clientX - imageCenterX - zoomState.translateX) / zoomState.scale;
    const localY =
      (clientY - imageCenterY - zoomState.translateY) / zoomState.scale;

    zoomState.scale = clamp(nextScale, 1, maxZoomScale);
    zoomState.translateX = clientX - imageCenterX - localX * zoomState.scale;
    zoomState.translateY = clientY - imageCenterY - localY * zoomState.scale;

    clampZoomToViewport();
    applyZoomTransform();
  }

  function resetZoom(options) {
    const preserveTap = options?.preserveTap || false;

    zoomState.scale = 1;
    zoomState.translateX = 0;
    zoomState.translateY = 0;
    zoomState.mode = null;
    zoomState.moved = false;

    if (!preserveTap) {
      zoomState.lastTapAt = 0;
      zoomState.lastTapX = 0;
      zoomState.lastTapY = 0;
    }

    zoomState.startX = 0;
    zoomState.startY = 0;
    zoomState.startTranslateX = 0;
    zoomState.startTranslateY = 0;
    zoomState.startScale = 1;
    zoomState.pinchStartDistance = 0;
    zoomState.pinchStartCenterX = 0;
    zoomState.pinchStartCenterY = 0;
    zoomState.pinchLocalX = 0;
    zoomState.pinchLocalY = 0;

    if (!activeImage) {
      return;
    }

    clearTransformAnimations(activeImage);
    activeImage.style.transform = "";
    activeImage.style.transformOrigin = "";
    activeImage.classList.remove("is-zoomed");
    activeImage.classList.remove("is-dragging");
  }

  function applyZoomTransform() {
    if (!activeImage) {
      return;
    }

    if (zoomState.scale <= 1) {
      activeImage.style.transform = "";
      activeImage.style.transformOrigin = "";
      activeImage.classList.remove("is-zoomed");
      activeImage.classList.remove("is-dragging");
      return;
    }

    clearTransformAnimations(activeImage);
    activeImage.style.transformOrigin = "center center";
    activeImage.style.transform = `translate3d(${zoomState.translateX}px, ${zoomState.translateY}px, 0) scale(${zoomState.scale})`;
    activeImage.classList.add("is-zoomed");
  }

  function clearTransformAnimations(image) {
    image.getAnimations().forEach(function (animation) {
      const keyframes = animation.effect?.getKeyframes?.() || [];
      const animatesTransform = keyframes.some(function (keyframe) {
        return Object.prototype.hasOwnProperty.call(keyframe, "transform");
      });

      if (animatesTransform) {
        animation.cancel();
      }
    });
  }

  function clampZoomToViewport() {
    if (!activeImage || zoomState.scale <= 1) {
      zoomState.translateX = 0;
      zoomState.translateY = 0;
      return;
    }

    const baseRect = getImageBaseRect();
    const viewport = getViewportBox();
    const imageCenterX = baseRect.left + baseRect.width / 2;
    const imageCenterY = baseRect.top + baseRect.height / 2;
    const scaledWidth = baseRect.width * zoomState.scale;
    const scaledHeight = baseRect.height * zoomState.scale;
    const minVisible = Math.min(64, viewport.width / 4, viewport.height / 4);
    const minTranslateX =
      viewport.left + minVisible - imageCenterX - scaledWidth / 2;
    const maxTranslateX =
      viewport.left + viewport.width - minVisible - imageCenterX + scaledWidth / 2;
    const minTranslateY =
      viewport.top + minVisible - imageCenterY - scaledHeight / 2;
    const maxTranslateY =
      viewport.top + viewport.height - minVisible - imageCenterY + scaledHeight / 2;

    zoomState.translateX = clamp(
      zoomState.translateX,
      minTranslateX,
      maxTranslateX,
    );
    zoomState.translateY = clamp(
      zoomState.translateY,
      minTranslateY,
      maxTranslateY,
    );
  }

  function getImageBaseRect() {
    const fallbackRect = activeImage.getBoundingClientRect();
    const top = Number.parseFloat(activeImage.style.top);
    const left = Number.parseFloat(activeImage.style.left);
    const width = Number.parseFloat(activeImage.style.width);
    const height = Number.parseFloat(activeImage.style.height);

    return {
      top: Number.isFinite(top) ? top : fallbackRect.top,
      left: Number.isFinite(left) ? left : fallbackRect.left,
      width: Number.isFinite(width) ? width : fallbackRect.width,
      height: Number.isFinite(height) ? height : fallbackRect.height,
    };
  }

  function getTouchDistance(firstTouch, secondTouch) {
    return Math.max(
      1,
      Math.hypot(
        firstTouch.clientX - secondTouch.clientX,
        firstTouch.clientY - secondTouch.clientY,
      ),
    );
  }

  function getTouchCenter(firstTouch, secondTouch) {
    return {
      x: (firstTouch.clientX + secondTouch.clientX) / 2,
      y: (firstTouch.clientY + secondTouch.clientY) / 2,
    };
  }

  function updateNavPosition(anchorRect) {
    if (!prevButton || !nextButton || !anchorRect) {
      return;
    }

    const viewport = getViewportBox();
    const buttonWidth = prevButton.offsetWidth || 54;
    const buttonHeight = prevButton.offsetHeight || 72;
    const gap = window.innerWidth <= 560 ? 10 : 14;
    const edgeMargin = window.innerWidth <= 560 ? 12 : 18;

    const centerY = anchorRect.top + anchorRect.height / 2;

    const minLeft = viewport.left + edgeMargin;
    const maxLeft = viewport.left + viewport.width - buttonWidth - edgeMargin;
    const minTop = viewport.top + edgeMargin + buttonHeight / 2;
    const maxTop = viewport.top + viewport.height - edgeMargin - buttonHeight / 2;

    const prevLeft = clamp(anchorRect.left - buttonWidth - gap, minLeft, maxLeft);

    const nextLeft = clamp(
      anchorRect.left + anchorRect.width + gap,
      minLeft,
      maxLeft,
    );

    const navTop = clamp(centerY, minTop, maxTop);

    prevButton.style.top = `${navTop}px`;
    nextButton.style.top = `${navTop}px`;

    prevButton.style.left = `${prevLeft}px`;
    nextButton.style.left = `${nextLeft}px`;

    prevButton.style.right = "auto";
    nextButton.style.right = "auto";
  }

  function updateControls() {
    const hasMultiplePhotos = galleryImages.length > 1;

    if (prevButton) {
      prevButton.hidden = !hasMultiplePhotos;
    }

    if (nextButton) {
      nextButton.hidden = !hasMultiplePhotos;
    }

    if (counter) {
      counter.hidden = !hasMultiplePhotos;
      counter.textContent = `${activeIndex + 1} / ${galleryImages.length}`;
    }
  }

  function getActiveSourceImage() {
    return galleryImages[activeIndex] || null;
  }

  function getFinalImageRect(sourceImage) {
    const viewport = getViewportBox();
    const isSmallViewport = viewport.width <= 560;
    const hasCounter = galleryImages.length > 1;

    const viewportPaddingX = isSmallViewport ? 14 : 34;
    const safeTop = isSmallViewport ? 16 : 22;
    const safeBottom = hasCounter ? (isSmallViewport ? 58 : 62) : 22;

    const maxWidth = Math.max(1, viewport.width - viewportPaddingX * 2);
    const maxHeight = Math.max(1, viewport.height - safeTop - safeBottom);

    const naturalWidth = sourceImage?.naturalWidth || 16;
    const naturalHeight = sourceImage?.naturalHeight || 9;
    const imageRatio = naturalWidth / naturalHeight;

    let width = maxWidth;
    let height = width / imageRatio;

    if (height > maxHeight) {
      height = maxHeight;
      width = height * imageRatio;
    }

    return {
      top: viewport.top + safeTop + (maxHeight - height) / 2,
      left: viewport.left + (viewport.width - width) / 2,
      width,
      height,
      borderRadius: isSmallViewport ? "18px" : "24px",
    };
  }

  function getViewportBox() {
    if (window.visualViewport) {
      return {
        top: window.visualViewport.offsetTop,
        left: window.visualViewport.offsetLeft,
        width: window.visualViewport.width,
        height: window.visualViewport.height,
      };
    }

    return {
      top: 0,
      left: 0,
      width: window.innerWidth,
      height: window.innerHeight,
    };
  }

  function clamp(value, min, max) {
    if (max < min) {
      return min;
    }

    return Math.min(Math.max(value, min), max);
  }

  function applyRectToImage(image, rect) {
    image.style.top = `${rect.top}px`;
    image.style.left = `${rect.left}px`;
    image.style.width = `${rect.width}px`;
    image.style.height = `${rect.height}px`;
    image.style.borderRadius = rect.borderRadius;
  }

  function cancelAnimations(element) {
    if (!element) {
      return;
    }

    element.getAnimations().forEach(function (animation) {
      animation.cancel();
    });
  }

  function removeInactiveLightboxImages(imageToKeep) {
    if (!lightbox) {
      return;
    }

    lightbox
      .querySelectorAll(".photo-lightbox__image")
      .forEach(function (image) {
        if (image !== imageToKeep) {
          image.remove();
        }
      });
  }

  function increaseAnimationGeneration() {
    animationGeneration += 1;
    return animationGeneration;
  }
}
