"""Run cf-gui, choosing the first available port from 8000 upwards."""

import errno
import os
from wsgiref.simple_server import make_server

from .web import create_app


def main() -> None:
    app = create_app()
    first_port = int(os.environ.get("CF_GUI_PORT", "8000"))
    for port in range(first_port, 65536):
        try:
            server = make_server("0.0.0.0", port, app)
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                continue
            raise
        print(f"cf-gui listening on 0.0.0.0:{port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return
    raise RuntimeError(f"Brak wolnego portu od {first_port} do 65535.")


if __name__ == "__main__":
    main()
