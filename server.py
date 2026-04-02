#!/usr/bin/env python3
"""
server.py — Mobile-friendly web UI for the carousel automation tool.

Run:
    python server.py              # localhost:5000
    python server.py --port 8080

Then expose to your phone via ngrok:
    ngrok http 5000
"""

import argparse
import logging
import os
import sys
import threading
import uuid
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template_string, request, send_from_directory

load_dotenv()

from utils import setup_logging, output_dir, rename_downloaded_images
from generator import generate_csv
from uploader import upload_and_download

setup_logging()
logger = logging.getLogger("carousel.server")

app = Flask(__name__)

# In-memory job store  {job_id: {"status", "log", "images", "topic"}}
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()

# ---------------------------------------------------------------------------
# HTML — single-file, mobile-first, no framework
# ---------------------------------------------------------------------------

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Carousel Generator</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #0f0f0f;
      color: #f0f0f0;
      min-height: 100vh;
      padding: 24px 16px 48px;
    }

    h1 {
      font-size: 1.5rem;
      font-weight: 700;
      margin-bottom: 6px;
      background: linear-gradient(135deg, #a78bfa, #38bdf8);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }

    .subtitle { font-size: 0.85rem; color: #888; margin-bottom: 28px; }

    .card {
      background: #1a1a1a;
      border: 1px solid #2a2a2a;
      border-radius: 14px;
      padding: 20px;
      margin-bottom: 20px;
    }

    label { display: block; font-size: 0.8rem; color: #aaa; margin-bottom: 8px; }

    input[type="text"], textarea {
      width: 100%;
      background: #0f0f0f;
      border: 1px solid #333;
      border-radius: 8px;
      color: #f0f0f0;
      font-size: 1rem;
      padding: 12px 14px;
      outline: none;
      transition: border-color 0.2s;
    }
    input[type="text"]:focus, textarea:focus { border-color: #a78bfa; }

    .row { display: flex; gap: 10px; margin-top: 14px; }

    .toggle {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 0.85rem;
      color: #aaa;
      cursor: pointer;
      margin-top: 14px;
    }
    .toggle input { width: 18px; height: 18px; cursor: pointer; }

    button {
      flex: 1;
      padding: 14px;
      border: none;
      border-radius: 10px;
      font-size: 1rem;
      font-weight: 600;
      cursor: pointer;
      transition: opacity 0.2s, transform 0.1s;
    }
    button:active { transform: scale(0.97); }
    button:disabled { opacity: 0.4; cursor: not-allowed; }

    .btn-primary { background: linear-gradient(135deg, #a78bfa, #38bdf8); color: #000; }
    .btn-secondary { background: #2a2a2a; color: #f0f0f0; border: 1px solid #333; }

    /* Job list */
    .job {
      border-bottom: 1px solid #222;
      padding: 14px 0;
    }
    .job:last-child { border-bottom: none; }

    .job-topic { font-weight: 600; font-size: 0.95rem; }
    .job-status {
      display: inline-block;
      font-size: 0.75rem;
      padding: 2px 8px;
      border-radius: 999px;
      margin-top: 4px;
    }
    .status-pending  { background: #333; color: #aaa; }
    .status-running  { background: #1e3a5f; color: #38bdf8; }
    .status-done     { background: #14532d; color: #4ade80; }
    .status-error    { background: #450a0a; color: #f87171; }

    .log-box {
      background: #0f0f0f;
      border-radius: 8px;
      font-size: 0.75rem;
      color: #6b7280;
      font-family: monospace;
      max-height: 160px;
      overflow-y: auto;
      padding: 10px;
      margin-top: 10px;
      white-space: pre-wrap;
      word-break: break-all;
    }

    .images {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
      gap: 8px;
      margin-top: 10px;
    }
    .images a { display: block; }
    .images img {
      width: 100%;
      border-radius: 8px;
      border: 1px solid #2a2a2a;
    }

    .empty { color: #555; font-size: 0.85rem; text-align: center; padding: 20px 0; }

    #toast {
      position: fixed;
      bottom: 24px;
      left: 50%;
      transform: translateX(-50%) translateY(80px);
      background: #a78bfa;
      color: #000;
      padding: 10px 20px;
      border-radius: 999px;
      font-size: 0.85rem;
      font-weight: 600;
      transition: transform 0.3s ease;
      z-index: 9999;
    }
    #toast.show { transform: translateX(-50%) translateY(0); }
  </style>
</head>
<body>
  <h1>Carousel Generator</h1>
  <p class="subtitle">Instagram carousel automation via Contentdrips</p>

  <!-- Input card -->
  <div class="card">
    <label for="topic">Topic</label>
    <input type="text" id="topic" placeholder="e.g. The benefits of daily journaling"
           autocomplete="off" autocorrect="off" spellcheck="false"/>

    <label class="toggle">
      <input type="checkbox" id="skipUpload"/>
      CSV only (skip Contentdrips upload)
    </label>

    <div class="row">
      <button class="btn-primary" id="generateBtn" onclick="startJob()">Generate</button>
      <button class="btn-secondary" onclick="refreshAll()">Refresh</button>
    </div>
  </div>

  <!-- Jobs card -->
  <div class="card" id="jobsCard">
    <div class="empty" id="emptyMsg">No jobs yet. Enter a topic above.</div>
    <div id="jobList"></div>
  </div>

  <div id="toast"></div>

  <script>
    const POLL_MS = 3000;
    let pollers = {};   // jobId → intervalId

    function toast(msg) {
      const el = document.getElementById('toast');
      el.textContent = msg;
      el.classList.add('show');
      setTimeout(() => el.classList.remove('show'), 2800);
    }

    async function startJob() {
      const topic = document.getElementById('topic').value.trim();
      if (!topic) { toast('Enter a topic first'); return; }

      const skipUpload = document.getElementById('skipUpload').checked;
      const btn = document.getElementById('generateBtn');
      btn.disabled = true;

      try {
        const res = await fetch('/api/generate', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({topic, skip_upload: skipUpload}),
        });
        const data = await res.json();
        if (!res.ok) { toast('Error: ' + (data.error || res.statusText)); return; }
        document.getElementById('topic').value = '';
        toast('Job started!');
        renderJob(data.job_id, {topic, status: 'pending', log: '', images: []});
        pollJob(data.job_id);
      } catch(e) {
        toast('Network error');
      } finally {
        btn.disabled = false;
      }
    }

    function renderJob(id, job) {
      document.getElementById('emptyMsg').style.display = 'none';

      let el = document.getElementById('job-' + id);
      if (!el) {
        el = document.createElement('div');
        el.id = 'job-' + id;
        el.className = 'job';
        document.getElementById('jobList').prepend(el);
      }

      const statusClass = 'status-' + job.status;
      const label = {pending:'Pending', running:'Running…', done:'Done ✓', error:'Error'}[job.status] || job.status;

      let imagesHtml = '';
      if (job.images && job.images.length) {
        imagesHtml = '<div class="images">' +
          job.images.map(src =>
            `<a href="${src}" target="_blank"><img src="${src}" loading="lazy"/></a>`
          ).join('') +
          '</div>';
      }

      el.innerHTML = `
        <div class="job-topic">${escHtml(job.topic)}</div>
        <span class="job-status ${statusClass}">${label}</span>
        <div class="log-box" id="log-${id}">${escHtml(job.log || '')}</div>
        ${imagesHtml}
      `;

      // Auto-scroll log
      const logEl = document.getElementById('log-' + id);
      if (logEl) logEl.scrollTop = logEl.scrollHeight;
    }

    function pollJob(id) {
      if (pollers[id]) return;
      pollers[id] = setInterval(async () => {
        try {
          const res = await fetch('/api/status/' + id);
          const job = await res.json();
          renderJob(id, job);
          if (job.status === 'done' || job.status === 'error') {
            clearInterval(pollers[id]);
            delete pollers[id];
            if (job.status === 'done') toast('Carousel ready!');
          }
        } catch(e) { /* network blip — keep polling */ }
      }, POLL_MS);
    }

    async function refreshAll() {
      const res = await fetch('/api/jobs');
      const data = await res.json();
      if (!data.jobs || !data.jobs.length) return;
      document.getElementById('emptyMsg').style.display = 'none';
      data.jobs.forEach(job => {
        renderJob(job.id, job);
        if (job.status === 'running' || job.status === 'pending') pollJob(job.id);
      });
    }

    function escHtml(s) {
      return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    // Restore existing jobs on page load
    refreshAll();

    // Submit on Enter
    document.getElementById('topic').addEventListener('keydown', e => {
      if (e.key === 'Enter') startJob();
    });
  </script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Background job runner
# ---------------------------------------------------------------------------

def _run_job(job_id: str, topic: str, skip_upload: bool) -> None:
    def log(msg: str) -> None:
        with jobs_lock:
            jobs[job_id]["log"] += msg + "\n"
        logger.info("[%s] %s", job_id[:8], msg)

    with jobs_lock:
        jobs[job_id]["status"] = "running"

    try:
        log(f"Generating CSV for: {topic!r}")
        csv = generate_csv(topic)
        log(f"CSV saved: {csv}")

        if skip_upload:
            log("Skip-upload mode — done.")
            with jobs_lock:
                jobs[job_id]["status"] = "done"
            return

        dest_dir = output_dir(topic)
        log(f"Uploading to Contentdrips → {dest_dir}")

        downloaded = upload_and_download(
            csv_path=csv,
            output_dir=dest_dir,
            headless=True,
        )

        rename_downloaded_images(dest_dir)

        # Build web-accessible image paths
        images = [f"/images/{dest_dir}/{p.name}" for p in sorted(dest_dir.iterdir()) if p.suffix == ".png"]

        with jobs_lock:
            jobs[job_id]["status"] = "done"
            jobs[job_id]["images"] = images

        log(f"Done! {len(images)} image(s) ready.")

    except Exception as exc:
        log(f"ERROR: {exc}")
        with jobs_lock:
            jobs[job_id]["status"] = "error"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/api/generate")
def api_generate():
    body = request.get_json(silent=True) or {}
    topic = (body.get("topic") or "").strip()
    if not topic:
        return jsonify({"error": "topic is required"}), 400

    skip_upload = bool(body.get("skip_upload", False))
    job_id = uuid.uuid4().hex

    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "topic": topic,
            "status": "pending",
            "log": "",
            "images": [],
        }

    t = threading.Thread(target=_run_job, args=(job_id, topic, skip_upload), daemon=True)
    t.start()

    return jsonify({"job_id": job_id})


@app.get("/api/status/<job_id>")
def api_status(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)


@app.get("/api/jobs")
def api_jobs():
    with jobs_lock:
        all_jobs = list(jobs.values())
    return jsonify({"jobs": all_jobs})


@app.get("/images/<path:filepath>")
def serve_image(filepath: str):
    """Serve generated carousel images."""
    full = Path(filepath)
    return send_from_directory(str(full.parent), full.name)


@app.get("/")
def index():
    return render_template_string(PAGE)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Carousel web server")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--host", default="0.0.0.0",
                        help="Bind address (default 0.0.0.0 — reachable on LAN)")
    args = parser.parse_args()

    print(f"\n  Carousel Generator running on http://{args.host}:{args.port}")
    print("  Open this URL on your phone (same WiFi) or expose via ngrok:\n")
    print(f"    ngrok http {args.port}\n")

    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
