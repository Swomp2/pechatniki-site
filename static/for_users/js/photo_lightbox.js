document.addEventListener("DOMContentLoaded", function () {
  setupPhotoLightbox();
});

// Lightbox работает делегированно от document, поэтому продолжает ловить
// изображения после page transitions, когда <main> заменяется новым HTML.
function setupPhotoLightbox() {
  const html = document.documentElement;
  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");

  const animationDuration = 420;
  const animationEasing = "cubic-bezier(0.16, 1, 0.3, 1)";

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

  window.addEventListener("resize", function () {
    if (!lightbox || !activeImage || isClosing) {
      return;
    }

    cancelAnimations(activeImage);

    const activeSourceImage = getActiveSourceImage();
    const finalRect = getFinalImageRect(activeSourceImage);

    applyRectToImage(activeImage, finalRect);

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
    // data-lightbox-gallery объединяет изображения секции; внутри карточки
    // проблемы fallback-группа строится по ближайшей .problem-card.
    const gallery = sourceImage.closest("[data-lightbox-gallery]");

    if (gallery) {
      return Array.from(
        gallery.querySelectorAll("[data-lightbox-image], .problem-card img"),
      );
    }

    const problemCard = sourceImage.closest(".problem-card");

    if (problemCard) {
      return Array.from(
        problemCard.querySelectorAll("[data-lightbox-image], img"),
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
  }

  function openPhotoLightbox(sourceImage) {
    isClosing = false;

    const generation = increaseAnimationGeneration();

    const sourceRect = sourceImage.getBoundingClientRect();
    const sourceBorderRadius = getComputedStyle(sourceImage).borderRadius;

    activeImage = createLightboxImage(sourceImage);

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

  function switchToPhoto(nextIndex, options) {
    if (!lightbox || isClosing) {
      return;
    }

    const nextSourceImage = galleryImages[nextIndex];

    if (!nextSourceImage) {
      return;
    }

    const generation = increaseAnimationGeneration();

    const previousImage = activeImage;
    const previousRect = previousImage
      ? previousImage.getBoundingClientRect()
      : nextSourceImage.getBoundingClientRect();

    removeInactiveLightboxImages(previousImage);

    if (previousImage) {
      cancelAnimations(previousImage);
      applyRectToImage(previousImage, {
        top: previousRect.top,
        left: previousRect.left,
        width: previousRect.width,
        height: previousRect.height,
        borderRadius: getComputedStyle(previousImage).borderRadius,
      });

      previousImage.style.objectFit = "contain";
      previousImage.style.pointerEvents = "none";
    }

    activeIndex = nextIndex;

    const nextImage = createLightboxImage(nextSourceImage);
    const finalRect = getFinalImageRect(nextSourceImage);

    activeImage = nextImage;

    updateControls();

    if (options.fromPageImage) {
      const sourceRect = nextSourceImage.getBoundingClientRect();
      const sourceBorderRadius = getComputedStyle(nextSourceImage).borderRadius;

      applyRectToImage(nextImage, {
        top: sourceRect.top,
        left: sourceRect.left,
        width: sourceRect.width,
        height: sourceRect.height,
        borderRadius: sourceBorderRadius,
      });

      nextImage.style.objectFit = "cover";
      lightbox.append(nextImage);

      animateImageMove(nextImage, sourceRect, finalRect, sourceBorderRadius)
        .then(function () {
          if (generation !== animationGeneration) {
            return;
          }

          applyRectToImage(nextImage, finalRect);
          nextImage.style.objectFit = "contain";
          previousImage?.remove();
        })
        .catch(function () {
          return null;
        });

      return;
    }

    applyRectToImage(nextImage, {
      top: previousRect.top,
      left: previousRect.left,
      width: previousRect.width,
      height: previousRect.height,
      borderRadius: getComputedStyle(previousImage || nextSourceImage)
        .borderRadius,
    });

    nextImage.style.opacity = "0";
    nextImage.style.objectFit = "contain";

    lightbox.append(nextImage);

    animatePhotoSwitch(
      previousImage,
      nextImage,
      previousRect,
      finalRect,
      options.direction,
    )
      .then(function () {
        if (generation !== animationGeneration) {
          return;
        }

        previousImage?.remove();
        applyRectToImage(nextImage, finalRect);
        nextImage.style.opacity = "";
        nextImage.style.transform = "";
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
    previousImage,
    nextImage,
    previousRect,
    finalRect,
    direction,
  ) {
    if (motionQuery.matches) {
      applyRectToImage(nextImage, finalRect);
      previousImage?.remove();
      return;
    }

    const safeDirection = direction || 1;
    const slideDistance = Math.min(36, window.innerWidth * 0.06);

    const animations = [];

    if (previousImage) {
      animations.push(
        previousImage.animate(
          [
            {
              opacity: 1,
              transform: "translate3d(0, 0, 0) scale(1)",
            },
            {
              opacity: 0,
              transform: `translate3d(${-safeDirection * slideDistance}px, 0, 0) scale(0.985)`,
            },
          ],
          {
            duration: 220,
            easing: animationEasing,
            fill: "forwards",
          },
        ).finished,
      );
    }

    animations.push(
      nextImage.animate(
        [
          {
            top: `${previousRect.top}px`,
            left: `${previousRect.left}px`,
            width: `${previousRect.width}px`,
            height: `${previousRect.height}px`,
            borderRadius: getComputedStyle(nextImage).borderRadius,
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
      ).finished,
    );

    await Promise.allSettled(animations);
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
