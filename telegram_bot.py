#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telegram bot front-end for a local SpiderFoot instance.

Runs the SpiderFoot web UI as a background subprocess (bound to
127.0.0.1) and exposes scan control through Telegram commands, talking
to SpiderFoot's own JSON API over HTTP.

Required environment variables:
    TELEGRAM_BOT_TOKEN   Token from @BotFather.
    ALLOWED_USER_IDS     Comma-separated Telegram user IDs allowed to
                          use the bot (leave empty to allow everyone --
                          not recommended for a recon tool).

Optional:
    SPIDERFOOT_HOST      Default: 127.0.0.1
    SPIDERFOOT_PORT      Default: 5001
    SPIDERFOOT_USECASE   Default module set for /scan (passive,
                          footprint, investigate, all). Default: footprint
    POLL_INTERVAL_SECONDS  How often to poll a running scan. Default: 20
"""

import asyncio
import csv
import io
import logging
import os
import subprocess
import sys
import time

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("sf-telegram-bot")

SF_HOST = os.environ.get("SPIDERFOOT_HOST", "127.0.0.1")
SF_PORT = os.environ.get("SPIDERFOOT_PORT", "5001")
SF_BASE_URL = f"http://{SF_HOST}:{SF_PORT}"
DEFAULT_USECASE = os.environ.get("SPIDERFOOT_USECASE", "footprint")
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "20"))

ALLOWED_USER_IDS = {
    int(uid) for uid in os.environ.get("ALLOWED_USER_IDS", "").split(",") if uid.strip()
}

# chat_id -> set of scan ids being tracked, so we don't spawn duplicate watchers
_tracked_scans: dict[int, set[str]] = {}


def _authorized(update: Update) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    user = update.effective_user
    return bool(user and user.id in ALLOWED_USER_IDS)


def _start_spiderfoot() -> subprocess.Popen:
    """Launch `sf.py` bound to localhost only, as a child process."""
    cmd = [sys.executable, "sf.py", "-l", f"{SF_HOST}:{SF_PORT}"]
    log.info("Starting SpiderFoot: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=os.path.dirname(os.path.abspath(__file__)),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    for _ in range(60):
        try:
            requests.get(f"{SF_BASE_URL}/", timeout=2)
            log.info("SpiderFoot web UI is up on %s", SF_BASE_URL)
            return proc
        except requests.RequestException:
            time.sleep(1)
    raise RuntimeError("SpiderFoot did not come up in time")


def _api_get(path: str, params: dict | None = None) -> dict | list:
    resp = requests.get(
        f"{SF_BASE_URL}{path}",
        params=params,
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _start_scan(target: str, usecase: str) -> str:
    resp = requests.get(
        f"{SF_BASE_URL}/startscan",
        params={
            "scanname": target,
            "scantarget": target,
            "modulelist": "",
            "typelist": "",
            "usecase": usecase,
        },
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list) and data and data[0] == "ERROR":
        raise ValueError(data[1] if len(data) > 1 else "unknown error")
    # SpiderFoot's JSON success response for startscan is ["SUCCESS", scan_id]
    if isinstance(data, list) and len(data) >= 2:
        return data[1]
    raise ValueError(f"Unexpected response from SpiderFoot: {data}")


def _scan_status(scan_id: str) -> dict:
    data = _api_get("/scanstatus", {"id": scan_id})
    # [name, target, created, started, ended, status]
    keys = ["name", "target", "created", "started", "ended", "status"]
    return dict(zip(keys, data))


def _scan_summary_csv(scan_id: str) -> str:
    resp = requests.get(
        f"{SF_BASE_URL}/scaneventresultexport",
        params={"id": scan_id, "type": "ALL", "filetype": "csv"},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.text


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        await update.message.reply_text("Access denied.")
        return
    await update.message.reply_text(
        "SpiderFoot OSINT bot.\n\n"
        "/scan <target> [usecase] - start a scan (default usecase: "
        f"{DEFAULT_USECASE}; options: passive, footprint, investigate, all)\n"
        "/status <scan_id> - check a scan's status\n"
        "/results <scan_id> - download results as CSV\n"
        "/list - list recent scans\n\n"
        "Only scan targets you are authorized to investigate."
    )


async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        await update.message.reply_text("Access denied.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /scan <target> [usecase]")
        return

    target = context.args[0]
    usecase = context.args[1] if len(context.args) > 1 else DEFAULT_USECASE

    try:
        scan_id = await asyncio.to_thread(_start_scan, target, usecase)
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(f"Failed to start scan: {exc}")
        return

    await update.message.reply_text(
        f"Scan started.\nTarget: {target}\nScan ID: `{scan_id}`\n"
        f"I will notify you here when it finishes.",
        parse_mode=ParseMode.MARKDOWN,
    )

    chat_id = update.effective_chat.id
    _tracked_scans.setdefault(chat_id, set()).add(scan_id)
    context.application.create_task(_watch_scan(context, chat_id, scan_id))


async def _watch_scan(context: ContextTypes.DEFAULT_TYPE, chat_id: int, scan_id: str) -> None:
    try:
        while True:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            try:
                status = await asyncio.to_thread(_scan_status, scan_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to poll scan %s: %s", scan_id, exc)
                continue

            if status["status"] in ("FINISHED", "ABORTED", "ERROR-FAILED"):
                await context.bot.send_message(
                    chat_id,
                    f"Scan `{scan_id}` finished with status: {status['status']}\n"
                    f"Use /results {scan_id} to download findings.",
                    parse_mode=ParseMode.MARKDOWN,
                )
                break
    finally:
        _tracked_scans.get(chat_id, set()).discard(scan_id)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        await update.message.reply_text("Access denied.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /status <scan_id>")
        return

    scan_id = context.args[0]
    try:
        status = await asyncio.to_thread(_scan_status, scan_id)
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(f"Failed to get status: {exc}")
        return

    await update.message.reply_text(
        f"Target: {status['target']}\nStatus: {status['status']}\n"
        f"Started: {status['started']}\nEnded: {status['ended']}"
    )


async def results_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        await update.message.reply_text("Access denied.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /results <scan_id>")
        return

    scan_id = context.args[0]
    try:
        csv_text = await asyncio.to_thread(_scan_summary_csv, scan_id)
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(f"Failed to fetch results: {exc}")
        return

    rows = list(csv.reader(io.StringIO(csv_text)))
    if len(rows) <= 1:
        await update.message.reply_text("No results yet (scan may still be running).")
        return

    buf = io.BytesIO(csv_text.encode("utf-8"))
    buf.name = f"spiderfoot_{scan_id}.csv"
    await update.message.reply_document(
        document=buf,
        filename=buf.name,
        caption=f"{len(rows) - 1} findings for scan {scan_id}",
    )


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        await update.message.reply_text("Access denied.")
        return
    try:
        scans = await asyncio.to_thread(_api_get, "/scanlist")
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(f"Failed to list scans: {exc}")
        return

    if not scans:
        await update.message.reply_text("No scans yet.")
        return

    lines = []
    for row in scans[:20]:
        # [id, name, target, created, started, ended, status, elements]
        scan_id, name, target, _created, _started, _ended, status = row[:7]
        lines.append(f"`{scan_id}` [{status}] {target}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN environment variable is required")

    sf_proc = _start_spiderfoot()

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", start_cmd))
    app.add_handler(CommandHandler("scan", scan_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("results", results_cmd))
    app.add_handler(CommandHandler("list", list_cmd))

    try:
        log.info("Starting Telegram bot polling loop")
        app.run_polling(close_loop=False)
    finally:
        sf_proc.terminate()


if __name__ == "__main__":
    main()
