{
  description = "Проект сайта первичного отделения района Печатники";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-26.05";

  outputs =
    { nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };

      site = pkgs.writeShellApplication {
        name = "site";

        runtimeInputs = with pkgs; [
          coreutils
          curl
          fuse-overlayfs
          gawk
          gnugrep
          gnused
          iproute2
          openssl
          passt
          podman
          python313
          slirp4netns
          uv
        ];

        text = ''
          set -euo pipefail

          export PATH="/run/wrappers/bin:$PATH"

          PROJECT_NAME="np-site-local"
          APP_IMAGE="localhost/np-site:local-production"
          NGINX_IMAGE="docker.io/library/nginx:1.31.2-alpine"
          WEB_CONTAINER="$PROJECT_NAME-web"
          NGINX_CONTAINER="$PROJECT_NAME-nginx"
          BACKEND_NETWORK="$PROJECT_NAME-backend"
          FRONTEND_NETWORK="$PROJECT_NAME-frontend"
          DATA_VOLUME="$PROJECT_NAME-data"
          MEDIA_VOLUME="$PROJECT_NAME-media"
          STATIC_VOLUME="$PROJECT_NAME-staticfiles"
          PROJECT_LABEL="io.swomp.project=$PROJECT_NAME"

          require_project_root() {
            if [[ ! -f manage.py || ! -f pyproject.toml || ! -f Dockerfile ]]; then
              echo "Ошибка: запусти команду из корня проекта" >&2
              exit 1
            fi
          }

          detect_lan_ip() {
            ip -o -4 addr show scope global \
              | awk '$4 !~ /^127\./ { split($4, address, "/"); print address[1]; exit }'
          }

          container_exists() {
            podman container exists "$1"
          }

          network_exists() {
            podman network exists "$1"
          }

          volume_exists() {
            podman volume exists "$1"
          }

          require_rootless_podman() {
            if ! command -v podman >/dev/null 2>&1; then
              echo "Ошибка: Podman отсутствует в devShell" >&2
              exit 1
            fi

            if [[ "$(id -u)" == "0" ]]; then
              echo "Ошибка: локальную схему надо запускать обычным пользователем" >&2
              exit 1
            fi

            if ! podman info >/dev/null 2>&1; then
              echo "Ошибка: rootless Podman не запускается" >&2
              echo >&2
              echo "Проверь:" >&2
              echo "  command -v newuidmap" >&2
              echo "  command -v newgidmap" >&2
              echo "  grep '^$(id -un):' /etc/subuid /etc/subgid" >&2
              echo >&2
              echo "Для NixOS достаточно настроить subordinate UID/GID для пользователя" >&2
              exit 1
            fi

            if [[ "$(podman info --format '{{.Host.Security.Rootless}}')" != "true" ]]; then
              echo "Ошибка: Podman запущен не в rootless-режиме" >&2
              exit 1
            fi
          }

          prepare_local_production() {
            require_project_root

            PROJECT_ROOT="$(pwd -P)"
            LOCAL_ROOT="$PROJECT_ROOT/.git/np-site-local"
            LOCAL_ENV_FILE="$LOCAL_ROOT/app.env"
            LOCAL_NGINX_TEMPLATE="$LOCAL_ROOT/production.conf.template"
            LOCAL_HOST="''${SITE_LAN_HOST:-$(detect_lan_ip)}"
            LOCAL_HOST="''${LOCAL_HOST:-127.0.0.1}"
            LOCAL_HOSTNAME="''${SITE_LAN_HOSTNAME:-$(uname -n)}"
            LOCAL_HTTP_PORT="''${SITE_HTTP_PORT:-8080}"
            LOCAL_HTTPS_PORT="''${SITE_HTTPS_PORT:-8443}"

            export PROJECT_ROOT LOCAL_ROOT LOCAL_ENV_FILE LOCAL_NGINX_TEMPLATE
            export LOCAL_HOST LOCAL_HOSTNAME LOCAL_HTTP_PORT LOCAL_HTTPS_PORT

            mkdir -p \
              "$LOCAL_ROOT/certbot/www" \
              "$LOCAL_ROOT/letsencrypt/live/local"
            chmod 700 "$LOCAL_ROOT"

            secrets="$LOCAL_ROOT/secrets.env"

            if [[ ! -s "$secrets" ]]; then
              field_key="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')"
              umask 077
              cat > "$secrets" <<EOF_SECRETS
          ADMIN_URL=local-admin-$(openssl rand -hex 12)
          DJANGO_SECRET_KEY=$(openssl rand -hex 64)
          FIELD_ENCRYPTION_KEYS=v1:$field_key
          PROBLEM_VOTER_HMAC_KEY=$(openssl rand -hex 64)
          EOF_SECRETS
            fi

            # shellcheck disable=SC1090
            source "$secrets"

            if [[ "$LOCAL_HTTPS_PORT" == "443" ]]; then
              https_suffix=""
            else
              https_suffix=":$LOCAL_HTTPS_PORT"
            fi

            umask 077
            cat > "$LOCAL_ENV_FILE" <<EOF_ENV
          ADMIN_URL=$ADMIN_URL
          DJANGO_DEBUG=false
          DJANGO_SECRET_KEY=$DJANGO_SECRET_KEY
          DJANGO_ALLOWED_HOSTS=$LOCAL_HOST,$LOCAL_HOSTNAME,localhost,127.0.0.1
          DJANGO_CSRF_TRUSTED_ORIGINS=https://$LOCAL_HOST$https_suffix,https://$LOCAL_HOSTNAME$https_suffix,https://localhost$https_suffix,https://127.0.0.1$https_suffix
          DJANGO_HEALTHCHECK_HOST=$LOCAL_HOST
          DJANGO_SQLITE_PATH=/app/data/db.sqlite3
          DJANGO_SECURE_SSL_REDIRECT=true
          DJANGO_SESSION_COOKIE_SECURE=true
          DJANGO_CSRF_COOKIE_SECURE=true
          DJANGO_SECURE_HSTS_SECONDS=0
          DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=false
          DJANGO_SECURE_HSTS_PRELOAD=false
          FIELD_ENCRYPTION_KEYS=$FIELD_ENCRYPTION_KEYS
          PROBLEM_VOTER_HMAC_KEY=$PROBLEM_VOTER_HMAC_KEY
          PROBLEM_VOTER_COOKIE_SECURE=true
          PROTECTED_MEDIA_USE_X_ACCEL=true
          DJANGO_COLLECTSTATIC=1
          DJANGO_MIGRATE=1
          EOF_ENV

            cp deploy/nginx/templates/production.conf.template "$LOCAL_NGINX_TEMPLATE"
            sed -i 's/max-age=2592000/max-age=0/g' "$LOCAL_NGINX_TEMPLATE"

            if [[ "$LOCAL_HTTPS_PORT" != "443" ]]; then
              sed -i \
                "s|https://\\\$host\\\$request_uri|https://\\\$host:$LOCAL_HTTPS_PORT\\\$request_uri|g" \
                "$LOCAL_NGINX_TEMPLATE"
            fi

            cert_dir="$LOCAL_ROOT/letsencrypt/live/local"
            cert_host_file="$cert_dir/host"

            if [[ ! -s "$cert_dir/fullchain.pem" \
               || ! -s "$cert_dir/privkey.pem" \
               || "$(cat "$cert_host_file" 2>/dev/null || true)" != "$LOCAL_HOST" ]]; then
              san="DNS:localhost,DNS:$LOCAL_HOSTNAME,IP:127.0.0.1"

              if [[ "$LOCAL_HOST" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
                san="$san,IP:$LOCAL_HOST"
              else
                san="$san,DNS:$LOCAL_HOST"
              fi

              openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 30 \
                -keyout "$cert_dir/privkey.pem" \
                -out "$cert_dir/fullchain.pem" \
                -subj "/CN=$LOCAL_HOST" \
                -addext "subjectAltName=$san" \
                >/dev/null 2>&1

              printf '%s\n' "$LOCAL_HOST" > "$cert_host_file"
              chmod 600 "$cert_dir/privkey.pem"
            fi
          }

          ensure_resources() {
            network_exists "$BACKEND_NETWORK" \
              || podman network create --internal --label "$PROJECT_LABEL" "$BACKEND_NETWORK" >/dev/null

            network_exists "$FRONTEND_NETWORK" \
              || podman network create --label "$PROJECT_LABEL" "$FRONTEND_NETWORK" >/dev/null

            for volume in "$DATA_VOLUME" "$MEDIA_VOLUME" "$STATIC_VOLUME"; do
              volume_exists "$volume" \
                || podman volume create --label "$PROJECT_LABEL" "$volume" >/dev/null
            done
          }

          remove_containers() {
            podman rm -f -t 10 "$NGINX_CONTAINER" >/dev/null 2>&1 || true
            podman rm -f -t 30 "$WEB_CONTAINER" >/dev/null 2>&1 || true
          }

          build_app_image() {
            extra_args=("$@")
            podman build \
              --tag "$APP_IMAGE" \
              --label "$PROJECT_LABEL" \
              "''${extra_args[@]}" \
              .
          }

          initialize_volumes() {
            local volume mountpoint

            for volume in "$DATA_VOLUME" "$MEDIA_VOLUME" "$STATIC_VOLUME"; do
              mountpoint="$(podman volume inspect \
                --format '{{.Mountpoint}}' \
                "$volume")"

              podman unshare chown -R 10001:10001 "$mountpoint"
            done
          }

          start_web() {
            podman run -d \
              --name "$WEB_CONTAINER" \
              --label "$PROJECT_LABEL" \
              --env-file "$LOCAL_ENV_FILE" \
              --user 10001:10001 \
              --read-only \
              --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
              --security-opt no-new-privileges \
              --cap-drop all \
              --pids-limit 256 \
              --memory "''${WEB_MEMORY_LIMIT:-512m}" \
              --cpus "''${WEB_CPU_LIMIT:-1.0}" \
              --volume "$DATA_VOLUME:/app/data:rw,U" \
              --volume "$MEDIA_VOLUME:/app/media:rw,U" \
              --volume "$STATIC_VOLUME:/app/staticfiles:rw,U" \
              --network "$BACKEND_NETWORK" \
              --network-alias web \
              "$APP_IMAGE" \
              gunicorn config.wsgi:application \
                --bind 0.0.0.0:8000 \
                --worker-class gthread \
                --workers "''${GUNICORN_WORKERS:-2}" \
                --threads "''${GUNICORN_THREADS:-2}" \
                --timeout 60 \
                --graceful-timeout 30 \
                --keep-alive 5 \
                --max-requests 1000 \
                --max-requests-jitter 100 \
                --worker-tmp-dir /tmp \
                --no-control-socket \
                --error-logfile - \
              >/dev/null
          }

          wait_for_web() {
            for _ in $(seq 1 60); do
              state="$(podman inspect --format '{{.State.Status}}' "$WEB_CONTAINER" 2>/dev/null || true)"

              if podman exec "$WEB_CONTAINER" python -c       "import os, urllib.request; host=os.environ.get('DJANGO_HEALTHCHECK_HOST', 'localhost'); request=urllib.request.Request('http://127.0.0.1:8000/health/', headers={'Host': host, 'X-Forwarded-Proto': 'https'}); urllib.request.urlopen(request, timeout=3).read()"       >/dev/null 2>&1; then
                return 0
              fi

              if [[ "$state" == "exited" || "$state" == "stopped" ]]; then
                podman logs --tail 200 "$WEB_CONTAINER" >&2 || true
                exit 1
              fi

              sleep 2
            done

            podman logs --tail 200 "$WEB_CONTAINER" >&2 || true
            echo "Ошибка: Gunicorn не отвечает на healthcheck" >&2
            exit 1
          }

          start_nginx() {
            podman run -d \
              --name "$NGINX_CONTAINER" \
              --label "$PROJECT_LABEL" \
              --env "NGINX_SERVER_NAME=$LOCAL_HOST $LOCAL_HOSTNAME localhost 127.0.0.1" \
              --env NGINX_SSL_CERTIFICATE=/etc/letsencrypt/live/local/fullchain.pem \
              --env NGINX_SSL_CERTIFICATE_KEY=/etc/letsencrypt/live/local/privkey.pem \
              --env "NGINX_CLIENT_MAX_BODY_SIZE=''${NGINX_CLIENT_MAX_BODY_SIZE:-300m}" \
              --read-only \
              --tmpfs /etc/nginx/conf.d:rw,noexec,nosuid,nodev,size=2m \
              --tmpfs /var/cache/nginx:rw,noexec,nosuid,nodev,size=32m \
              --tmpfs /var/run:rw,noexec,nosuid,nodev,size=2m \
              --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m \
              --security-opt no-new-privileges \
              --cap-drop all \
              --cap-add net_bind_service \
              --cap-add setuid \
              --cap-add setgid \
              --cap-add chown \
              --pids-limit 128 \
              --memory "''${NGINX_MEMORY_LIMIT:-256m}" \
              --cpus "''${NGINX_CPU_LIMIT:-0.5}" \
              --volume "$LOCAL_NGINX_TEMPLATE:/etc/nginx/templates/default.conf.template:ro" \
              --volume "$STATIC_VOLUME:/srv/compiled-static:ro" \
              --volume "$MEDIA_VOLUME:/srv/protected-media:ro" \
              --volume "$LOCAL_ROOT/certbot/www:/var/www/certbot:ro" \
              --volume "$LOCAL_ROOT/letsencrypt:/etc/letsencrypt:ro" \
              --network "$BACKEND_NETWORK" \
              --network "$FRONTEND_NETWORK" \
              --publish "0.0.0.0:$LOCAL_HTTP_PORT:80" \
              --publish "0.0.0.0:$LOCAL_HTTPS_PORT:443" \
              --pull missing \
              "$NGINX_IMAGE" \
              >/dev/null
          }

          wait_for_nginx() {
            for _ in $(seq 1 60); do
              if curl -kfsS \
                -H "Host: $LOCAL_HOST" \
                "https://127.0.0.1:$LOCAL_HTTPS_PORT/nginx-health" \
                >/dev/null 2>&1; then
                return 0
              fi

              if container_exists "$NGINX_CONTAINER"; then
                state="$(podman inspect --format '{{.State.Status}}' "$NGINX_CONTAINER" 2>/dev/null || true)"
                if [[ "$state" == "exited" || "$state" == "stopped" ]]; then
                  podman logs --tail 200 "$NGINX_CONTAINER" >&2 || true
                  exit 1
                fi
              fi

              sleep 2
            done

            podman logs --tail 200 "$NGINX_CONTAINER" >&2 || true
            echo "Ошибка: nginx не запустился" >&2
            exit 1
          }

          show_local_urls() {
            echo "HTTPS: https://$LOCAL_HOST:$LOCAL_HTTPS_PORT"
            echo "HTTP:  http://$LOCAL_HOST:$LOCAL_HTTP_PORT"
            echo "Admin: https://$LOCAL_HOST:$LOCAL_HTTPS_PORT/$ADMIN_URL/"
          }

          start_stack() {
            no_cache="''${1:-false}"

            require_rootless_podman
            remove_containers
            ensure_resources

            if [[ "$no_cache" == "true" ]]; then
              build_app_image --no-cache
            else
              build_app_image
            fi

            initialize_volumes
            start_web
            wait_for_web
            start_nginx
            wait_for_nginx
            show_local_urls
          }

          stop_stack() {
            require_rootless_podman
            remove_containers
            podman network rm "$BACKEND_NETWORK" >/dev/null 2>&1 || true
            podman network rm "$FRONTEND_NETWORK" >/dev/null 2>&1 || true
          }

          exec_web() {
            if ! container_exists "$WEB_CONTAINER"; then
              echo "Ошибка: контейнер web не запущен" >&2
              exit 1
            fi

            tty_args=()
            if [[ -t 0 && -t 1 ]]; then
              tty_args=(-it)
            fi

            podman exec "''${tty_args[@]}" "$WEB_CONTAINER" "$@"
          }

          show_logs() {
            service="''${1:-web}"
            shift || true

            case "$service" in
              web)
                podman logs --tail 200 -f "$WEB_CONTAINER" "$@"
                ;;
              nginx)
                podman logs --tail 200 -f "$NGINX_CONTAINER" "$@"
                ;;
              all)
                echo "===== web ====="
                podman logs --tail 200 "$WEB_CONTAINER" || true
                echo "===== nginx ====="
                podman logs --tail 200 "$NGINX_CONTAINER" || true
                ;;
              *)
                echo "Неизвестный сервис: $service" >&2
                exit 1
                ;;
            esac
          }

          podman_doctor() {
            require_project_root

            echo "Podman: $(podman --version 2>/dev/null || echo 'не найден')"
            echo "Пользователь: $(id -un) ($(id -u):$(id -g))"
            echo "newuidmap: $(command -v newuidmap 2>/dev/null || echo 'не найден')"
            echo "newgidmap: $(command -v newgidmap 2>/dev/null || echo 'не найден')"
            echo "subuid: $(grep "^$(id -un):" /etc/subuid 2>/dev/null || echo 'не настроен')"
            echo "subgid: $(grep "^$(id -un):" /etc/subgid 2>/dev/null || echo 'не настроен')"

            if podman info >/dev/null 2>&1; then
              echo "Rootless: $(podman info --format '{{.Host.Security.Rootless}}')"
              echo "Хранилище: $(podman info --format '{{.Store.GraphRoot}}')"
              echo "Сеть: $(podman info --format '{{.Host.NetworkBackend}}')"
            else
              echo "Podman info: ошибка"
              podman info || true
              exit 1
            fi
          }

          prod_local() {
            action="''${1:-help}"
            shift || true

            prepare_local_production

            case "$action" in
              up|start)
                start_stack false
                ;;
              rebuild)
                start_stack true
                ;;
              down|stop)
                stop_stack
                ;;
              restart)
                stop_stack
                start_stack false
                ;;
              logs)
                require_rootless_podman
                show_logs "$@"
                ;;
              ps|status)
                require_rootless_podman
                podman ps -a --filter "label=$PROJECT_LABEL"
                ;;
              check)
                require_rootless_podman
                exec_web python manage.py check --deploy --fail-level ERROR
                curl -kfsS -H "Host: $LOCAL_HOST" \
                  "https://127.0.0.1:$LOCAL_HTTPS_PORT/nginx-health"
                ;;
              superuser)
                require_rootless_podman
                exec_web python manage.py createsuperuser
                ;;
              manage)
                require_rootless_podman
                exec_web python manage.py "$@"
                ;;
              shell)
                require_rootless_podman
                exec_web python manage.py shell
                ;;
              inspect)
                require_rootless_podman
                podman inspect "$WEB_CONTAINER" "$NGINX_CONTAINER"
                ;;
              url)
                show_local_urls
                ;;
              certificate)
                echo "$LOCAL_ROOT/letsencrypt/live/local/fullchain.pem"
                ;;
              doctor)
                podman_doctor
                ;;
              reset)
                require_rootless_podman
                stop_stack
                podman volume rm -f "$DATA_VOLUME" "$MEDIA_VOLUME" "$STATIC_VOLUME" >/dev/null 2>&1 || true
                podman image rm -f "$APP_IMAGE" >/dev/null 2>&1 || true
                rm -rf "$LOCAL_ROOT"
                ;;
              help|-h|--help)
                cat <<'EOF_HELP'
          site prod-local up              Собрать и запустить production-схему через rootless Podman
          site prod-local rebuild         Пересобрать образ без кеша и запустить
          site prod-local down            Остановить контейнеры, сохранив данные
          site prod-local restart         Пересоздать контейнеры
          site prod-local logs [web|nginx|all]
          site prod-local ps
          site prod-local check
          site prod-local superuser
          site prod-local shell
          site prod-local manage <команда>
          site prod-local inspect
          site prod-local doctor          Проверить rootless Podman
          site prod-local certificate
          site prod-local reset           Удалить контейнеры, volumes, образ и локальные секреты
          EOF_HELP
                ;;
              *)
                echo "Неизвестная команда: $action" >&2
                exit 1
                ;;
            esac
          }

          command="''${1:-help}"
          shift || true

          case "$command" in
            setup)
              uv sync --frozen
              uv run python manage.py migrate
              uv run python manage.py check
              ;;
            run)
              exec uv run python manage.py runserver "''${1:-127.0.0.1:8000}"
              ;;
            migrations)
              exec uv run python manage.py makemigrations "$@"
              ;;
            migrate)
              exec uv run python manage.py migrate "$@"
              ;;
            check)
              uv run python manage.py check
              uv run python manage.py makemigrations --check --dry-run
              ;;
            test)
              exec uv run python manage.py test "$@"
              ;;
            superuser)
              exec uv run python manage.py createsuperuser
              ;;
            manage)
              exec uv run python manage.py "$@"
              ;;
            prod-local)
              prod_local "$@"
              ;;
            help|-h|--help)
              cat <<'EOF_HELP'
          site setup
          site run [адрес:порт]
          site migrations
          site migrate
          site check
          site test
          site superuser
          site manage <команда>
          site prod-local help
          EOF_HELP
              ;;
            *)
              echo "Неизвестная команда: $command" >&2
              exit 1
              ;;
          esac
        '';
      };
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        packages = with pkgs; [
          fish
          fuse-overlayfs
          passt
          podman
          python313
          slirp4netns
          uv
          site
        ];

        env = {
          UV_PYTHON = "${pkgs.python313}/bin/python";
          PYTHONDONTWRITEBYTECODE = "1";
          PYTHONUNBUFFERED = "1";
          DJANGO_DEBUG = "true";
        };

        shellHook = ''
          export PATH="/run/wrappers/bin:$PATH"

          echo "Python: $(${pkgs.python313}/bin/python --version)"
          echo "uv: $(${pkgs.uv}/bin/uv --version)"
          echo "Podman: $(${pkgs.podman}/bin/podman --version)"
          echo "Команды: site help"
          echo "Локальный production без daemon: site prod-local up"

          if [[ -z "''${FISH_VERSION:-}" && -t 1 ]]; then
            exec ${pkgs.fish}/bin/fish
          fi
        '';
      };
    };
}
