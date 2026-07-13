(function () {
  const protectedPhotoSelector = "[data-protected-photo]";
  const objectUrls = new Map();

  document.addEventListener("DOMContentLoaded", function () {
    loadProtectedPhotos(document);
  });

  window.addEventListener("pageshow", function () {
    loadProtectedPhotos(document);
  });

  window.addEventListener("pagehide", function () {
    revokeAllObjectUrls();
  });

  document.addEventListener("site:page-loaded", function (event) {
    revokeDetachedObjectUrls();
    loadProtectedPhotos(event.detail?.page || document);
  });

  function loadProtectedPhotos(root) {
    root.querySelectorAll(protectedPhotoSelector).forEach(function (image) {
      if (image.dataset.protectedPhotoState === "loaded") {
        return;
      }

      if (image.dataset.protectedPhotoState === "loading") {
        return;
      }

      loadProtectedPhoto(image);
    });
  }

  async function loadProtectedPhoto(image) {
    const endpoint = image.dataset.photoEndpoint;
    const problemId = image.dataset.problemId;
    const position = image.dataset.photoPosition;

    if (!endpoint || !problemId || position === undefined) {
      return;
    }

    image.dataset.protectedPhotoState = "loading";

    try {
      const formData = new FormData();

      formData.set("problem_id", problemId);
      formData.set("position", position);

      const response = await fetch(endpoint, {
        method: "POST",
        body: formData,
        headers: {
          Accept: "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
          "X-Requested-With": "fetch",
          "X-CSRFToken": getCookie("csrftoken"),
        },
        credentials: "same-origin",
        cache: "no-store",
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const previousUrl = objectUrls.get(image);

      if (previousUrl) {
        URL.revokeObjectURL(previousUrl);
      }

      objectUrls.set(image, objectUrl);
      image.src = objectUrl;
      image.dataset.protectedPhotoState = "loaded";
    } catch (error) {
      console.error("Не удалось загрузить фото обращения:", error);
      image.dataset.protectedPhotoState = "failed";
    }
  }

  function getCookie(name) {
    const cookies = document.cookie ? document.cookie.split(";") : [];

    for (const cookie of cookies) {
      const trimmedCookie = cookie.trim();

      if (trimmedCookie.startsWith(`${name}=`)) {
        return decodeURIComponent(trimmedCookie.slice(name.length + 1));
      }
    }

    return "";
  }

  function revokeDetachedObjectUrls() {
    objectUrls.forEach(function (objectUrl, image) {
      if (image.isConnected) {
        return;
      }

      URL.revokeObjectURL(objectUrl);
      objectUrls.delete(image);
    });
  }

  function revokeAllObjectUrls() {
    objectUrls.forEach(function (objectUrl) {
      URL.revokeObjectURL(objectUrl);
    });
    objectUrls.clear();
  }
})();
