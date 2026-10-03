"""
Thumbnail extraction and visual scene selector generation.
Extracts a representative frame from each video and builds
an interactive HTML page for scene ordering.
"""

import base64
import subprocess
from pathlib import Path

import config
from project import Project, SceneMatch


def extract_thumbnail(
    video_path: str,
    output_path: str,
    time_sec: float = None,
    width: int = None,
) -> Path:
    """
    Extract a single frame from a video as a thumbnail image.

    Args:
        video_path: Path to the video file.
        output_path: Where to save the thumbnail.
        time_sec: Time offset in seconds to grab the frame.
        width: Output width (height scales proportionally).

    Returns:
        Path to the thumbnail image.
    """
    time_sec = time_sec if time_sec is not None else config.THUMBNAIL_TIME_SEC
    width = width or config.THUMBNAIL_WIDTH

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(time_sec),
        "-i", str(video_path),
        "-vframes", "1",
        "-vf", f"scale={width}:-1",
        "-q:v", str(max(1, min(31, 32 - int(config.THUMBNAIL_QUALITY / 3.2)))),
        str(out),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Thumbnail extraction failed for {video_path}:\n{result.stderr[-500:]}")

    return out


def extract_all_thumbnails(
    project: Project,
    output_dir: str,
) -> Project:
    """
    Extract thumbnails for all matched scenes.

    Args:
        project: Project with matched scenes.
        output_dir: Directory to save thumbnails.

    Returns:
        Updated project with thumbnail paths.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Extracting thumbnails...")
    for i, match in enumerate(project.matches):
        m = match if isinstance(match, SceneMatch) else SceneMatch(**match)

        thumb_path = out_dir / f"thumb_{m.index:03d}.{config.THUMBNAIL_FORMAT}"
        try:
            extract_thumbnail(m.video_path, str(thumb_path))
            m.thumbnail_path = str(thumb_path)
            project.matches[i] = m
            print(f"  [{m.index}] {Path(m.video_path).name} -> {thumb_path.name}")
        except RuntimeError as e:
            print(f"  [{m.index}] Failed: {e}")

    return project


def _image_to_base64(image_path: str) -> str:
    """Read an image file and return as base64 data URI."""
    p = Path(image_path)
    if not p.exists():
        return ""
    with open(p, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    ext = p.suffix.lstrip(".")
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png"}.get(ext, "jpeg")
    return f"data:image/{mime};base64,{data}"


def generate_selector_html(
    project: Project,
    output_path: str = None,
) -> Path:
    """
    Generate an interactive HTML page for visual scene ordering.

    The page shows a thumbnail for each scene with its metadata.
    Users can drag scenes to reorder, set trim points, and export
    their selection as a JSON that feeds back into the pipeline.

    Args:
        project: Project with matched scenes and thumbnails.
        output_path: Where to save the HTML file.

    Returns:
        Path to the generated HTML file.
    """
    output_path = output_path or config.SELECTOR_HTML
    out = Path(output_path)

    # Build scene data for the HTML
    scenes_js = []
    for match in project.matches:
        m = match if isinstance(match, SceneMatch) else SceneMatch(**match)
        thumb_b64 = _image_to_base64(m.thumbnail_path) if m.thumbnail_path else ""

        scenes_js.append({
            "index": m.index,
            "videoFile": Path(m.video_path).name,
            "audioFile": Path(m.audio_path).name,
            "duration": round(m.video_duration, 2),
            "audioDuration": round(m.audio_duration, 2),
            "offset": round(m.offset, 3),
            "confidence": round(m.confidence, 3),
            "thumbnail": thumb_b64,
            "label": m.label,
            "trimStart": m.trim_start,
            "trimEnd": m.trim_end,
        })

    import json
    scenes_json = json.dumps(scenes_js, indent=2)

    html = _build_selector_html(scenes_json)

    with open(out, "w") as f:
        f.write(html)

    print(f"Scene selector saved to: {out}")
    print(f"Open it in your browser to arrange scenes visually.")
    return out


def _build_selector_html(scenes_json: str) -> str:
    """Build the complete HTML for the scene selector."""
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Scene Selector — Ambient Video Automation</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #1a1a2e;
    color: #e0e0e0;
    padding: 20px;
  }}
  h1 {{
    text-align: center;
    margin-bottom: 8px;
    color: #e94560;
    font-size: 1.6em;
  }}
  .subtitle {{
    text-align: center;
    color: #888;
    margin-bottom: 24px;
    font-size: 0.9em;
  }}
  .sections {{
    display: flex;
    gap: 24px;
    max-width: 1400px;
    margin: 0 auto;
  }}
  .panel {{
    flex: 1;
    min-width: 0;
  }}
  .panel-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
  }}
  .panel-header h2 {{
    font-size: 1.1em;
    color: #ccc;
  }}
  .panel-header .count {{
    background: #e94560;
    color: white;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 0.85em;
  }}
  .scene-list {{
    min-height: 200px;
    background: #16213e;
    border-radius: 8px;
    padding: 8px;
  }}
  .scene-card {{
    display: flex;
    gap: 12px;
    background: #0f3460;
    border-radius: 6px;
    padding: 10px;
    margin-bottom: 8px;
    cursor: grab;
    transition: transform 0.15s, box-shadow 0.15s;
    border: 2px solid transparent;
  }}
  .scene-card:hover {{
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
  }}
  .scene-card.dragging {{
    opacity: 0.5;
    border-color: #e94560;
  }}
  .scene-card.drag-over {{
    border-color: #e94560;
    background: #1a1a4e;
  }}
  .scene-card .thumb {{
    width: 160px;
    height: 90px;
    border-radius: 4px;
    object-fit: cover;
    flex-shrink: 0;
    background: #0a0a2e;
  }}
  .scene-card .info {{
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 4px;
  }}
  .scene-card .scene-title {{
    font-weight: 600;
    font-size: 0.95em;
    color: #e94560;
  }}
  .scene-card .meta {{
    font-size: 0.8em;
    color: #999;
  }}
  .scene-card .files {{
    font-size: 0.75em;
    color: #666;
    word-break: break-all;
  }}
  .scene-card .actions {{
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 6px;
    flex-shrink: 0;
  }}
  .btn {{
    padding: 6px 14px;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.8em;
    font-weight: 500;
  }}
  .btn-add {{
    background: #e94560;
    color: white;
  }}
  .btn-add:hover {{ background: #c73e54; }}
  .btn-remove {{
    background: #444;
    color: #ccc;
  }}
  .btn-remove:hover {{ background: #666; }}
  .btn-export {{
    background: #e94560;
    color: white;
    padding: 10px 24px;
    font-size: 1em;
    border: none;
    border-radius: 6px;
    cursor: pointer;
    font-weight: 600;
  }}
  .btn-export:hover {{ background: #c73e54; }}
  .trim-inputs {{
    display: flex;
    gap: 6px;
    align-items: center;
    font-size: 0.75em;
    margin-top: 4px;
  }}
  .trim-inputs label {{ color: #888; }}
  .trim-inputs input {{
    width: 50px;
    padding: 2px 4px;
    background: #1a1a2e;
    border: 1px solid #444;
    color: #e0e0e0;
    border-radius: 3px;
    font-size: 0.9em;
    text-align: center;
  }}
  .order-num {{
    display: flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    background: #e94560;
    color: white;
    border-radius: 50%;
    font-weight: 700;
    font-size: 0.85em;
    flex-shrink: 0;
    align-self: center;
  }}
  .export-bar {{
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 16px;
    margin-top: 20px;
    padding: 16px;
    background: #16213e;
    border-radius: 8px;
  }}
  .total-dur {{
    color: #999;
    font-size: 0.9em;
  }}
  .export-output {{
    margin-top: 12px;
    padding: 12px;
    background: #0a0a2e;
    border-radius: 6px;
    font-family: monospace;
    font-size: 0.85em;
    color: #e94560;
    word-break: break-all;
    display: none;
  }}
  .match-conf {{
    display: inline-block;
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 0.7em;
    font-weight: 600;
  }}
  .conf-high {{ background: #1b4332; color: #95d5b2; }}
  .conf-med {{ background: #3d3200; color: #ffd166; }}
  .conf-low {{ background: #4a1111; color: #ff6b6b; }}
</style>
</head>
<body>

<h1>Scene Selector</h1>
<p class="subtitle">Drag scenes from Available to Timeline, or click the + button. Drag to reorder.</p>

<div class="sections">
  <div class="panel">
    <div class="panel-header">
      <h2>Available Scenes</h2>
      <span class="count" id="available-count">0</span>
    </div>
    <div class="scene-list" id="available-list"></div>
  </div>

  <div class="panel">
    <div class="panel-header">
      <h2>Timeline (ordered)</h2>
      <span class="count" id="timeline-count">0</span>
    </div>
    <div class="scene-list" id="timeline-list"></div>
  </div>
</div>

<div class="export-bar">
  <span class="total-dur" id="total-duration">Total: 00:00</span>
  <button class="btn-export" onclick="exportSelection()">Export Selection</button>
</div>
<div class="export-output" id="export-output"></div>

<script>
const ALL_SCENES = {scenes_json};

let available = ALL_SCENES.map(s => ({{ ...s }}));
let timeline = [];

function fmtDur(sec) {{
  const m = Math.floor(sec / 60);
  const s = (sec % 60).toFixed(1);
  return m > 0 ? m + ":" + s.padStart(4, "0") : s + "s";
}}

function confClass(c) {{
  if (c >= 0.3) return "conf-high";
  if (c >= 0.15) return "conf-med";
  return "conf-low";
}}

function makeCard(scene, isTimeline, orderNum) {{
  const card = document.createElement("div");
  card.className = "scene-card";
  card.draggable = true;
  card.dataset.index = scene.index;

  const thumbHtml = scene.thumbnail
    ? '<img class="thumb" src="' + scene.thumbnail + '" alt="Scene ' + scene.index + '">'
    : '<div class="thumb" style="display:flex;align-items:center;justify-content:center;color:#444;">No thumbnail</div>';

  const confCls = confClass(scene.confidence);

  let trimHtml = "";
  if (isTimeline) {{
    trimHtml = '<div class="trim-inputs">' +
      '<label>Trim start:</label>' +
      '<input type="number" step="0.5" min="0" value="' + (scene.trimStart || 0) + '" ' +
        'onchange="updateTrim(' + scene.index + ', \\'start\\', this.value)">' +
      '<label>end:</label>' +
      '<input type="number" step="0.5" min="0" value="' + (scene.trimEnd || 0) + '" ' +
        'onchange="updateTrim(' + scene.index + ', \\'end\\', this.value)">' +
      '</div>';
  }}

  card.innerHTML =
    (isTimeline ? '<div class="order-num">' + orderNum + '</div>' : '') +
    thumbHtml +
    '<div class="info">' +
      '<div class="scene-title">Scene ' + scene.index + (scene.label ? " — " + scene.label : "") + '</div>' +
      '<div class="meta">' + fmtDur(scene.duration) +
        ' &nbsp; <span class="match-conf ' + confCls + '">match: ' + (scene.confidence * 100).toFixed(0) + '%</span>' +
        ' &nbsp; offset: ' + scene.offset.toFixed(2) + 's</div>' +
      '<div class="files">' + scene.videoFile + ' + ' + scene.audioFile + '</div>' +
      trimHtml +
    '</div>' +
    '<div class="actions">' +
      (isTimeline
        ? '<button class="btn btn-remove" onclick="removeFromTimeline(' + scene.index + ')">Remove</button>'
        : '<button class="btn btn-add" onclick="addToTimeline(' + scene.index + ')">+ Add</button>') +
    '</div>';

  // Drag events
  card.addEventListener("dragstart", e => {{
    card.classList.add("dragging");
    e.dataTransfer.setData("text/plain", JSON.stringify({{
      index: scene.index,
      from: isTimeline ? "timeline" : "available"
    }}));
  }});
  card.addEventListener("dragend", () => card.classList.remove("dragging"));
  card.addEventListener("dragover", e => {{ e.preventDefault(); card.classList.add("drag-over"); }});
  card.addEventListener("dragleave", () => card.classList.remove("drag-over"));
  card.addEventListener("drop", e => {{
    e.preventDefault();
    card.classList.remove("drag-over");
    const data = JSON.parse(e.dataTransfer.getData("text/plain"));
    if (isTimeline && data.from === "available") {{
      addToTimeline(data.index, scene.index);
    }} else if (isTimeline && data.from === "timeline") {{
      reorderTimeline(data.index, scene.index);
    }}
  }});

  return card;
}}

function render() {{
  const avail = document.getElementById("available-list");
  const tl = document.getElementById("timeline-list");
  avail.innerHTML = "";
  tl.innerHTML = "";

  available.forEach(s => avail.appendChild(makeCard(s, false)));
  timeline.forEach((s, i) => tl.appendChild(makeCard(s, true, i + 1)));

  document.getElementById("available-count").textContent = available.length;
  document.getElementById("timeline-count").textContent = timeline.length;

  const totalSec = timeline.reduce((sum, s) => {{
    return sum + s.duration - (s.trimStart || 0) - (s.trimEnd || 0);
  }}, 0);
  document.getElementById("total-duration").textContent = "Total: " + fmtDur(totalSec);
}}

function addToTimeline(sceneIndex, beforeIndex) {{
  const idx = available.findIndex(s => s.index === sceneIndex);
  if (idx === -1) return;
  const scene = available.splice(idx, 1)[0];
  if (beforeIndex !== undefined) {{
    const pos = timeline.findIndex(s => s.index === beforeIndex);
    timeline.splice(pos, 0, scene);
  }} else {{
    timeline.push(scene);
  }}
  render();
}}

function removeFromTimeline(sceneIndex) {{
  const idx = timeline.findIndex(s => s.index === sceneIndex);
  if (idx === -1) return;
  const scene = timeline.splice(idx, 1)[0];
  scene.trimStart = 0;
  scene.trimEnd = 0;
  available.push(scene);
  available.sort((a, b) => a.index - b.index);
  render();
}}

function reorderTimeline(fromIndex, toIndex) {{
  const fromPos = timeline.findIndex(s => s.index === fromIndex);
  const toPos = timeline.findIndex(s => s.index === toIndex);
  if (fromPos === -1 || toPos === -1) return;
  const [moved] = timeline.splice(fromPos, 1);
  timeline.splice(toPos, 0, moved);
  render();
}}

function updateTrim(sceneIndex, which, value) {{
  const scene = timeline.find(s => s.index === sceneIndex);
  if (!scene) return;
  const v = parseFloat(value) || 0;
  if (which === "start") scene.trimStart = v;
  else scene.trimEnd = v;
  // Update total duration display
  const totalSec = timeline.reduce((sum, s) =>
    sum + s.duration - (s.trimStart || 0) - (s.trimEnd || 0), 0);
  document.getElementById("total-duration").textContent = "Total: " + fmtDur(totalSec);
}}

// Allow dropping on the timeline list itself (for empty timeline)
document.getElementById("timeline-list").addEventListener("dragover", e => e.preventDefault());
document.getElementById("timeline-list").addEventListener("drop", e => {{
  e.preventDefault();
  const data = JSON.parse(e.dataTransfer.getData("text/plain"));
  if (data.from === "available") {{
    addToTimeline(data.index);
  }}
}});

function exportSelection() {{
  const selection = timeline.map(s => s.index);
  const trims = {{}};
  timeline.forEach(s => {{
    if (s.trimStart > 0 || s.trimEnd > 0) {{
      trims[s.index] = {{ start: s.trimStart || 0, end: s.trimEnd || 0 }};
    }}
  }});

  const output = {{
    selection: selection,
    selection_trims: trims,
  }};

  const json = JSON.stringify(output, null, 2);
  const el = document.getElementById("export-output");
  el.style.display = "block";
  el.innerHTML = "<strong>Copy this into your terminal when prompted, or save as selection.json:</strong><br><br>"
    + '<textarea style="width:100%;height:120px;background:#1a1a2e;color:#e94560;border:1px solid #444;'
    + 'border-radius:4px;padding:8px;font-family:monospace;font-size:0.9em;" readonly>'
    + json + '</textarea>'
    + '<br><br><strong>CLI command:</strong><br>'
    + '<code>python main.py sync --selection \\'' + json.replace(/\\n/g, "") + '\\'</code>';

  // Also copy to clipboard
  navigator.clipboard.writeText(json).then(() => {{
    el.innerHTML += '<br><br><span style="color:#95d5b2;">Copied to clipboard!</span>';
  }}).catch(() => {{}});
}}

render();
</script>
</body>
</html>'''
