"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.contrib import admin
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import staticfiles_storage
from django.http import Http404, HttpResponse
from django.urls import include, path


def health_check(request):
    return HttpResponse("ok\n", content_type="text/plain")


def public_static_file(logical_name, content_type, cache_control):
    source_path = finders.find(logical_name)

    if source_path:
        with open(source_path, "rb") as source:
            content = source.read()
    else:
        try:
            with staticfiles_storage.open(logical_name, "rb") as source:
                content = source.read()
        except FileNotFoundError as exc:
            raise Http404 from exc

    response = HttpResponse(content, content_type=content_type)
    response["Cache-Control"] = cache_control
    response["Content-Length"] = str(len(content))

    return response


def favicon(request):
    return public_static_file(
        "favicon/heart.ico",
        "image/x-icon",
        "public, max-age=86400, must-revalidate",
    )


def site_webmanifest(request):
    return public_static_file(
        "favicon/site.webmanifest",
        "application/manifest+json",
        "public, max-age=3600, must-revalidate",
    )


urlpatterns = [
    path("health/", health_check, name="health_check"),
    path("favicon.ico", favicon, name="favicon"),
    path("site.webmanifest", site_webmanifest, name="site_webmanifest"),
    path(f"{settings.ADMIN_URL}/", admin.site.urls),
    path("", include("problems.urls")),
]
