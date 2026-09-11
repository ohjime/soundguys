import os
import re
import subprocess
from django.core.management.base import CommandError, LabelCommand


def extract_server_url_from_procfile(path: str) -> str | None:
    try:
        with open(path, "r") as f:
            content = f.read()
        m = re.search(r"runserver\s+([\w\.-]+):(\d+)", content)
        if m:
            host, port = m.group(1), m.group(2)
            return f"http://{host}:{port}"
        m = re.search(r"^\w+:.*\buvicorn\s+.*$", content, re.MULTILINE)
        if m:
            command = m.group(0)
            host_match = re.search(r"--host(?:=|\s+)([\w.-]+)", command)
            port_match = re.search(r"--port(?:=|\s+)(\d+)", command)
            host = host_match.group(1) if host_match else "127.0.0.1"
            port = port_match.group(1) if port_match else "8000"
            return f"http://{host}:{port}"
    except OSError:
        pass
    return None


class Command(LabelCommand):
    help = "Runs Vite related commands (build / runserver / dev)"
    missing_args_message = """
Command argument is missing, use one of:
  build       - build frontend (package manager script)
  runserver   - start Django + Vite via Honcho
  dev         - alias of runserver
Examples:
  uv run src/main.py proc build
  uv run src/main.py proc runserver
"""

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--procfile",
            default="procfile.dev",
            help="Path to Procfile for Honcho (default: procfile.dev)",
        )
        parser.add_argument(
            "--package-manager",
            default="npm",
            choices=["npm", "pnpm", "yarn", "bun"],
            help="JS package manager",
        )
        parser.add_argument(
            "--vite-script",
            default="dev",
            help="Package script name for dev (default: dev)",
        )
        parser.add_argument(
            "--build-script",
            default="build",
            help="Package script name for build (default: build)",
        )
        parser.add_argument(
            "--port", default="8000", help="ASGI dev server port (default: 8000)"
        )
        parser.add_argument(
            "--noreload",
            action="store_true",
            help="Disable the Uvicorn development autoreloader",
        )

    def handle(self, *labels, **options):
        if not labels:
            raise CommandError(self.missing_args_message)
        action = labels[0].replace("-", "_")
        method = getattr(self, f"handle_{action}_command", None)
        if not method:
            raise CommandError(f"Unknown vite action: {action}")
        return method(**options)

    # ---- Actions ----
    def handle_build_command(self, **options):
        pm = options["package_manager"]
        script = options["build_script"]
        cmd = self._pm_run_command(pm, script)
        self.stdout.write(f"Running build: {' '.join(cmd)}")
        self._run(cmd, "Vite build failed")

    def handle_runserver_command(self, **options):
        self._ensure_honcho()
        procfile = options["procfile"]
        if not os.path.exists(procfile):
            self._create_procfile(procfile, options)
        self._announce(procfile, "Starting Cosound Backend")
        self._run(
            ["honcho", "-f", procfile, "start"],
            "Failed to start Honcho",
            allow_interrupt=True,
        )

    def handle_dev_command(self, **options):
        return self.handle_runserver_command(**options)

    # ---- Helpers ----
    def _pm_run_command(self, pm: str, script: str) -> list[str]:
        if pm in ("npm", "pnpm"):
            return [pm, "run", script]
        if pm == "yarn":
            return ["yarn", script]
        if pm == "bun":
            return ["bun", "run", script]
        return [pm, "run", script]

    def _ensure_honcho(self):
        try:
            subprocess.run(["honcho", "--version"], check=True, capture_output=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            self.stdout.write("Honcho not found. Installing...")
            self._pip_install("honcho")
            self.stdout.write(self.style.SUCCESS("Honcho installed."))

    def _pip_install(self, package: str):
        try:
            subprocess.run(["uv", "pip", "install", package], check=True)
        except subprocess.CalledProcessError as e:
            raise CommandError(f"Failed to install {package}: {e}")

    def _create_procfile(self, path: str, options):
        self.stdout.write(f"Creating {path} ...")
        pm = options["package_manager"]
        vite_script = options["vite_script"]
        port = options["port"]
        noreload = options["noreload"]

        vite_cmd = {
            "npm": f"npm run {vite_script}",
            "pnpm": f"pnpm {vite_script}",
            "yarn": f"yarn {vite_script}",
            "bun": f"bun run {vite_script}",
        }[pm]

        base = (
            "PYTHONUNBUFFERED=1 uv run uvicorn config.asgi_dev:application "
            f"--app-dir src --host 0.0.0.0 --port {port}"
        )

        if not noreload:
            base += " --reload --reload-dir src"

        content = f"""vite: cd vite && {vite_cmd} -- --host
server: {base}
worker: PYTHONUNBUFFERED=1 uv run src/main.py db_worker --no-reload
tasks: PYTHONUNBUFFERED=1 uv run src/main.py refresh 0.0.0.0:9000
"""
        with open(path, "w") as f:
            f.write(content)
        self.stdout.write(self.style.SUCCESS(f"{path} created."))

    def _announce(self, procfile: str, message: str):
        url = extract_server_url_from_procfile(procfile)
        banner = "#" * (len(message) + 4)
        self.stdout.write(banner)
        self.stdout.write(f"# {message} #")
        self.stdout.write(banner)
        if url:
            self.stdout.write(self.style.SUCCESS(f"Access: {url}"))
        self.stdout.write("Ctrl+C to stop.\n")

    def _run(self, cmd, err_msg, allow_interrupt=False):
        try:
            subprocess.run(cmd, check=True)
        except KeyboardInterrupt:
            if allow_interrupt:
                self.stdout.write("\nInterrupted.")
            else:
                raise
        except subprocess.CalledProcessError as e:
            raise CommandError(f"{err_msg}: {e}")
