"""Command-line lifecycle and direct document submission."""

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

import httpx2 as httpx

from .assets import download_asset, installed_cwebp, provision_cwebp, register_assets, verify_assets
from .config import load_settings


def client(base_url, api_key):
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    return httpx.Client(
        base_url=base_url.rstrip("/") + "/", headers=headers, timeout=60, trust_env=False
    )


def checked(response):
    response.raise_for_status()
    return response.json()


def submit(args):
    with client(args.url, args.api_key) as http:
        path = args.input.resolve(strict=True)
        upload = checked(
            http.post(
                "v1/uploads",
                json={"filename": path.name, "bytes": path.stat().st_size, "purpose": "parse"},
            )
        )
        with path.open("rb") as stream:
            http.put(upload["upload_url"], content=stream).raise_for_status()
        uploaded = checked(http.post(f"v1/uploads/{upload['id']}/complete", json={}))
        job = checked(
            http.post(
                "v1/parse/jobs",
                json={
                    "files": [
                        {
                            "source": {"type": "file_id", "file_id": uploaded["file"]["id"]},
                            "page_range": "all",
                        }
                    ],
                    "tier": args.tier,
                    "ocr_mode": "ocr" if args.ocr else "auto",
                    "output_formats": ["zip"],
                },
            )
        )
        print(json.dumps(job), flush=True)
        if not args.wait:
            return
        try:
            deadline = time.monotonic() + args.timeout
            while job["status"] in {"queued", "running"}:
                if time.monotonic() > deadline:
                    raise TimeoutError("Job wait timed out")
                time.sleep(1)
                job = checked(http.get(f"v1/parse/jobs/{job['job_id']}"))
            if job["status"] != "completed":
                raise RuntimeError(json.dumps(job))
            file_id = job["files"][0]["output_files"]["zip"]["file_id"]
            download(http, file_id, args.output)
            print(str(args.output))
        except BaseException:
            if job["status"] in {"queued", "running"}:
                try:
                    http.delete(f"v1/parse/jobs/{job['job_id']}", timeout=10)
                except httpx.HTTPError:
                    logging.exception("Could not cancel submitted job")
            raise


def download(http, file_id, output):
    if output.exists():
        raise ValueError(f"Output already exists: {output}")
    with http.stream("GET", f"v1/files/{file_id}/content") as response:
        response.raise_for_status()
        try:
            with output.open("xb") as stream:
                for chunk in response.iter_bytes():
                    stream.write(chunk)
        except BaseException:
            output.unlink(missing_ok=True)
            raise


def main():
    parser = argparse.ArgumentParser(description="Local OvisOCR3 parsing server")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("serve", "setup"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--config", type=Path, required=True)
        if name == "serve":
            cmd.add_argument("--device", choices=["auto", "cpu", "cuda"])
            cmd.add_argument("--host")
            cmd.add_argument("--port", type=int)
        else:
            cmd.add_argument(
                "--skip-cwebp",
                action="store_true",
                help="Use an existing encoder or retain lossless-only optimization",
            )
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--config", type=Path, required=True)
    doctor.add_argument(
        "--smoke",
        type=Path,
        help="Explicitly convert this Office file to test the configured renderer",
    )
    fetch = commands.add_parser("download-asset")
    fetch.add_argument("--url", required=True)
    fetch.add_argument("--sha256", required=True)
    fetch.add_argument("--output", type=Path, required=True)
    for name in ("submit", "status", "cancel", "download"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--url", default="http://127.0.0.1:8000")
        cmd.add_argument("--api-key")
        if name == "submit":
            cmd.add_argument("input", type=Path)
            cmd.add_argument("--ocr", action="store_true")
            cmd.add_argument("--tier", default="standard", choices=["standard", "advanced"])
            cmd.add_argument("--wait", action="store_true")
            cmd.add_argument("--timeout", type=float, default=7200)
            cmd.add_argument("--output", type=Path, default=Path("result.zip"))
        else:
            cmd.add_argument("id")
            if name == "download":
                cmd.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    try:
        if args.command == "download-asset":
            download_asset(args.url, args.sha256, args.output)
        elif args.command == "doctor":
            from .libreoffice import LibreOfficeRenderer

            settings = load_settings(args.config)

            async def diagnose():
                renderer = LibreOfficeRenderer(settings.libreoffice)
                report = {
                    "libreoffice": await renderer.probe(),
                    "cwebp": str(
                        settings.compression.cwebp_binary
                        or installed_cwebp(settings.data_dir)
                        or "unavailable"
                    ),
                }
                if args.smoke:
                    report["smoke_pdf"] = str(
                        await renderer.convert(
                            args.smoke, args.smoke.suffix, settings.data_dir / "doctor"
                        )
                    )
                return report

            print(json.dumps(asyncio.run(diagnose()), indent=2))
        elif args.command in {"serve", "setup"}:
            settings = load_settings(args.config)
            if args.command == "setup":
                if settings.runtime.mode == "external":
                    print("External inference configured; its operator provisions assets.")
                else:
                    print(register_assets(settings))
                if settings.compression.enabled and not args.skip_cwebp:
                    print(settings.compression.cwebp_binary or provision_cwebp(settings.data_dir))
                return
            if args.device:
                settings.runtime.device = args.device
            if args.host:
                settings.host = args.host
            if args.port:
                settings.port = args.port
            verify_assets(settings)
            import uvicorn

            from .api import create_app
            from .processor import DocumentProcessor
            from .runtime import InferenceRuntime

            runtime = InferenceRuntime(settings.runtime)
            pipeline = DocumentProcessor(settings, runtime)
            uvicorn.run(
                create_app(settings, parser=pipeline.parse, engine=runtime),
                host=settings.host,
                port=settings.port,
            )
        elif args.command == "submit":
            submit(args)
        else:
            with client(args.url, args.api_key) as http:
                if args.command == "download":
                    download(http, args.id, args.output)
                elif args.command == "cancel":
                    print(json.dumps(checked(http.delete(f"v1/parse/jobs/{args.id}"))))
                else:
                    print(json.dumps(checked(http.get(f"v1/parse/jobs/{args.id}")), indent=2))
    except (ValueError, OSError, httpx.HTTPError, RuntimeError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
