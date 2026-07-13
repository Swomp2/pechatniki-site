import hashlib
import hmac
import secrets

from django.conf import settings


VOTER_TOKEN_MIN_LENGTH = 32


def is_valid_voter_token(value):
    # Cookie содержит только случайный токен. Он не должен включать телефон,
    # IP, User-Agent или любые персональные данные.
    if not value or not isinstance(value, str):
        return False

    if len(value) < VOTER_TOKEN_MIN_LENGTH or len(value) > 128:
        return False

    return all(character.isalnum() or character in {"-", "_"} for character in value)


def make_voter_token():
    # token_urlsafe(32) даёт около 256 бит случайности в URL-safe алфавите.
    return secrets.token_urlsafe(32)


def make_voter_hash(token):
    # В БД храним HMAC, а не сам cookie-токен. Если база утечёт отдельно от
    # секрета приложения, браузерный идентификатор нельзя будет просто забрать.
    key = settings.PROBLEM_VOTER_HMAC_KEY.encode("utf-8")

    return hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()


def get_or_create_voter_identity(request):
    token = request.COOKIES.get(settings.PROBLEM_VOTER_COOKIE_NAME)
    should_set_cookie = False

    if not is_valid_voter_token(token):
        # Мост для старой логики: если у браузера сохранилась Django session,
        # используем её ключ как исходный токен и дальше закрепляем отдельной
        # cookie. После удаления cookie пользователь получит новый идентификатор.
        session_key = getattr(request.session, "session_key", "")

        if is_valid_voter_token(session_key):
            token = session_key
        else:
            token = make_voter_token()

        should_set_cookie = True

    return {
        "token": token,
        "hash": make_voter_hash(token),
        "should_set_cookie": should_set_cookie,
    }


def set_voter_cookie(response, token):
    response.set_cookie(
        settings.PROBLEM_VOTER_COOKIE_NAME,
        token,
        max_age=settings.PROBLEM_VOTER_COOKIE_AGE,
        secure=settings.PROBLEM_VOTER_COOKIE_SECURE,
        httponly=True,
        samesite=settings.PROBLEM_VOTER_COOKIE_SAMESITE,
    )

    return response
