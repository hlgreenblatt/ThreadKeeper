#!/usr/bin/env python3
"""Render docs/architecture.png — the ThreadKeeper four-node mesh.

Reproducible, dependency-light (Pillow only). Re-run after editing to
regenerate the diagram:

    python3 docs/make_architecture_diagram.py

The diagram is deliberately simple: four nodes, the escalation edge,
and the budget gate that sits on it. It mirrors the prose in
docs/architecture.md and the roles in threadkeeper.config.yaml.
"""

import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "architecture.png")

# Palette — calm, high-contrast, print-friendly.
BG = (250, 250, 252)
INK = (28, 32, 40)
MUTED = (110, 120, 135)
LOCAL = (38, 139, 110)      # cheap/local => green
LOCAL_FILL = (224, 244, 238)
CLOUD = (44, 96, 178)       # cloud => blue
CLOUD_FILL = (224, 235, 250)
ADJ = (150, 92, 176)        # adjudicator => purple
ADJ_FILL = (240, 230, 248)
GATE = (196, 110, 22)       # budget gate => amber
GATE_FILL = (252, 240, 220)

W, H = 1280, 760
SCALE = 2  # supersample then downscale for crisp text

FONT_DIR = "/usr/share/fonts/truetype/dejavu"


def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(os.path.join(FONT_DIR, name), size * SCALE)


def rounded_box(d, box, fill, outline, width=3):
    d.rounded_rectangle(box, radius=14 * SCALE, fill=fill, outline=outline,
                        width=width * SCALE)


def center_text(d, cx, y, text, fnt, fill):
    l, t, r, b = d.textbbox((0, 0), text, font=fnt)
    d.text((cx - (r - l) / 2, y), text, font=fnt, fill=fill)


def node(d, box, title, lines, edge, fill, tag):
    rounded_box(d, box, fill, edge, width=3)
    x0, y0, x1, y1 = box
    cx = (x0 + x1) / 2
    # tag chip
    chip = font(11, bold=True)
    center_text(d, cx, y0 + 12 * SCALE, tag, chip, edge)
    center_text(d, cx, y0 + 34 * SCALE, title, font(17, bold=True), INK)
    ly = y0 + 64 * SCALE
    body = font(11)
    for ln in lines:
        center_text(d, cx, ly, ln, body, MUTED)
        ly += 18 * SCALE


def arrow(d, p0, p1, color, width=3, dashed=False):
    x0, y0 = p0
    x1, y1 = p1
    if dashed:
        # simple dash
        import math
        dx, dy = x1 - x0, y1 - y0
        dist = math.hypot(dx, dy)
        steps = int(dist / (10 * SCALE))
        for i in range(steps):
            if i % 2 == 0:
                a = i / steps
                b = (i + 1) / steps
                d.line([(x0 + dx * a, y0 + dy * a), (x0 + dx * b, y0 + dy * b)],
                       fill=color, width=width * SCALE)
    else:
        d.line([p0, p1], fill=color, width=width * SCALE)
    # arrowhead
    import math
    ang = math.atan2(y1 - y0, x1 - x0)
    sz = 12 * SCALE
    d.polygon([
        (x1, y1),
        (x1 - sz * math.cos(ang - 0.4), y1 - sz * math.sin(ang - 0.4)),
        (x1 - sz * math.cos(ang + 0.4), y1 - sz * math.sin(ang + 0.4)),
    ], fill=color)


def main():
    img = Image.new("RGB", (W * SCALE, H * SCALE), BG)
    d = ImageDraw.Draw(img)

    # Title
    center_text(d, W * SCALE / 2, 26 * SCALE,
                "ThreadKeeper — configurable hybrid OmegaClaw mesh",
                font(22, bold=True), INK)
    center_text(d, W * SCALE / 2, 58 * SCALE,
                "Decouples reasoning quality from reasoning frequency. "
                "Models shown are examples; all roles are swappable.",
                font(12), MUTED)

    # Node geometry (in unscaled coords, then *SCALE applied via boxes)
    def B(x, y, w, h):
        return (x * SCALE, y * SCALE, (x + w) * SCALE, (y + h) * SCALE)

    # Control loop (top-left, persistent)
    control = B(80, 150, 360, 150)
    node(d, control, "Local control loop", [
        "Holds the thread: goal, memory,",
        "continuity, escalation decisions.",
        "Runs EVERY iteration — cheapest node.",
        "(e.g. local qwen3.5:9b)",
    ], LOCAL, LOCAL_FILL, "NODE 1 · LOCAL · PERSISTENT")

    # Worker loop (bottom-left)
    worker = B(80, 440, 360, 150)
    node(d, worker, "Local worker loop", [
        "Iterates cheaply on the sub-task:",
        "tools, files, search, drafting.",
        "Bounded turns per dispatch.",
        "(e.g. local qwen2.5-coder:14b)",
    ], LOCAL, LOCAL_FILL, "NODE 2 · LOCAL · CHEAP LOOP")

    # Cloud specialist (top-right)
    cloud = B(840, 150, 360, 150)
    node(d, cloud, "Cloud specialist(s)", [
        "Invoked ONLY for hard subproblems",
        "via the (delegate ...) skill.",
        "Quality bought at a price — rarely.",
        "(e.g. cloud reasoning model)",
    ], CLOUD, CLOUD_FILL, "NODE 3 · CLOUD · ON DEMAND")

    # Adjudicator (bottom-right, optional)
    adj = B(840, 440, 360, 150)
    node(d, adj, "Adjudicator (optional)", [
        "Tie-breaker when specialists",
        "disagree; high-stakes action gate.",
        "Omit for a 3-node mesh.",
        "(governance-sensitive deployments)",
    ], ADJ, ADJ_FILL, "NODE 4 · CLOUD · OPTIONAL")

    # Budget gate (center) — sits on the escalation edge
    gate = B(520, 300, 240, 120)
    rounded_box(d, gate, GATE_FILL, GATE, width=3)
    center_text(d, 640 * SCALE, 312 * SCALE, "BUDGET GATE", font(13, bold=True), GATE)
    center_text(d, 640 * SCALE, 338 * SCALE, "escalate?", font(15, bold=True), INK)
    center_text(d, 640 * SCALE, 362 * SCALE, "tokens-per-loop vs", font(10), MUTED)
    center_text(d, 640 * SCALE, 378 * SCALE, "thread budget threshold", font(10), MUTED)
    center_text(d, 640 * SCALE, 396 * SCALE, "(threadkeeper_budget.py)", font(9), MUTED)

    # Edges
    # control <-> worker (cheap inner loop, bidirectional)
    arrow(d, (260 * SCALE, 300 * SCALE), (260 * SCALE, 438 * SCALE), LOCAL, 3)
    arrow(d, (300 * SCALE, 440 * SCALE), (300 * SCALE, 302 * SCALE), LOCAL, 3)
    center_text(d, 340 * SCALE, 360 * SCALE, "cheap", font(10, bold=True), LOCAL)
    center_text(d, 340 * SCALE, 374 * SCALE, "inner loop", font(10), LOCAL)

    # control -> gate (escalation request)
    arrow(d, (440 * SCALE, 225 * SCALE), (520 * SCALE, 330 * SCALE), GATE, 3)
    # gate -> cloud (allowed)
    arrow(d, (760 * SCALE, 330 * SCALE), (840 * SCALE, 225 * SCALE), CLOUD, 3)
    center_text(d, 800 * SCALE, 285 * SCALE, "allow", font(10, bold=True), CLOUD)
    # gate -> back to control (denied / digest returns)
    arrow(d, (520 * SCALE, 388 * SCALE), (440 * SCALE, 300 * SCALE), MUTED, 2, dashed=True)
    center_text(d, 470 * SCALE, 350 * SCALE, "deny →", font(9), MUTED)
    center_text(d, 470 * SCALE, 363 * SCALE, "stay cheap", font(9), MUTED)

    # cloud -> adjudicator (on disagreement)
    arrow(d, (1020 * SCALE, 300 * SCALE), (1020 * SCALE, 438 * SCALE), ADJ, 2, dashed=True)
    center_text(d, 1100 * SCALE, 360 * SCALE, "on conflict", font(10), ADJ)

    # cloud -> control (digest returns to the thread)
    arrow(d, (840 * SCALE, 260 * SCALE), (640 * SCALE, 300 * SCALE), CLOUD, 2, dashed=True)
    center_text(d, 720 * SCALE, 250 * SCALE, "digest returns to thread", font(10), CLOUD)

    # Footer note
    center_text(d, W * SCALE / 2, 650 * SCALE,
                "Every LLM call is logged (memory/usage.jsonl); every escalation "
                "decision is recorded (memory/escalations.jsonl) — ISO/IEC 42001-friendly audit trail.",
                font(11), MUTED)

    img = img.resize((W, H), Image.LANCZOS)
    img.save(OUT)
    print(f"wrote {OUT} ({W}x{H})")


if __name__ == "__main__":
    main()
