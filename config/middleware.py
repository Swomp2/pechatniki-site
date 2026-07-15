from django.templatetags.static import static


class AdminCssMiddleware:
    # Подключаем общий CSS админки ко всем HTML-ответам Django admin.
    # Это покрывает не только index.html, но и changelist/change form.
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        resolver_match = getattr(request, "resolver_match", None)

        if not resolver_match or resolver_match.app_name != "admin":
            return response

        content_type = response.get("Content-Type", "")

        if "text/html" not in content_type:
            return response

        if getattr(response, "streaming", False):
            return response

        charset = response.charset or "utf-8"
        content = response.content.decode(charset)

        css_href = static("for_admin/css/main.css")

        if css_href in content:
            return response

        if "</head>" not in content:
            return response

        css_link = f'<link rel="stylesheet" href="{css_href}" />'

        content = content.replace(
            "</head>",
            f"  {css_link}\n</head>",
            1,
        )

        response.content = content.encode(charset)
        response["Content-Length"] = str(len(response.content))

        return response


class DynamicResponseCacheMiddleware:
    """Require browsers to revalidate HTML while preserving file policies."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if "text/html" not in response.get("Content-Type", ""):
            return response

        if not response.has_header("Cache-Control"):
            response["Cache-Control"] = "private, no-cache"

        return response
