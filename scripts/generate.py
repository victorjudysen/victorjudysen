#!/usr/bin/env python3
"""Draw the profile cards in assets/ from the GitHub contribution calendar.

    assets/whoami.svg         ASCII portrait + streak and activity stats
    assets/contributions.svg  last year's contribution heatmap

Needs a token that can read the GraphQL API (GITHUB_TOKEN in Actions is
enough). Private contributions are counted only when "Include private
contributions on my profile" is turned on in GitHub's profile settings.

    GH_TOKEN=... python scripts/generate.py [--login victorjudysen]
"""
from __future__ import annotations

import argparse
import colorsys
import datetime as dt
import json
import os
import urllib.request
from collections import deque
from html import escape
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
AVATAR = ASSETS / "avatar-source.jpg"

FONT = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace"
BG, PANEL, TILE, BORDER = "#0d1117", "#161b22", "#0d1117", "#30363d"
TEXT, MUTED, GREEN, BAR = "#e6edf3", "#7d8590", "#3fb950", "#2ea043"
LEVELS = {
    "NONE": "#161b22",
    "FIRST_QUARTILE": "#0e4429",
    "SECOND_QUARTILE": "#006d32",
    "THIRD_QUARTILE": "#26a641",
    "FOURTH_QUARTILE": "#39d353",
}

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount contributionLevel } }
      }
    }
  }
}
"""


# ---- Data --------------------------------------------------------------------

def fetch_calendar(login: str, token: str) -> list[dict]:
    body = json.dumps({"query": QUERY, "variables": {"login": login}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    if payload.get("errors"):
        raise SystemExit(f"GitHub API error: {payload['errors']}")
    weeks = payload["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    days = [
        {"date": dt.date.fromisoformat(d["date"]), "count": d["contributionCount"], "level": d["contributionLevel"]}
        for w in weeks
        for d in w["contributionDays"]
    ]
    return sorted(days, key=lambda d: d["date"])


def streaks(days: list[dict]) -> tuple[tuple[int, dt.date | None, dt.date | None], tuple[int, dt.date | None, dt.date | None]]:
    """(current, longest) as (length, first day, last day). Today without a contribution yet doesn't break the streak."""
    longest = (0, None, None)
    run, start = 0, None
    for d in days:
        if d["count"] > 0:
            if run == 0:
                start = d["date"]
            run += 1
            if run > longest[0]:
                longest = (run, start, d["date"])
        else:
            run = 0

    tail = days[:-1] if days and days[-1]["count"] == 0 else days
    current, end = 0, None
    for d in reversed(tail):
        if d["count"] == 0:
            break
        current += 1
        end = end or d["date"]
    first = end - dt.timedelta(days=current - 1) if current else None
    return (current, first, end), longest


def monthly(days: list[dict], months: int = 12) -> list[tuple[dt.date, int]]:
    last = days[-1]["date"].replace(day=1)
    keys = []
    y, m = last.year, last.month
    for _ in range(months):
        keys.append(dt.date(y, m, 1))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    keys.reverse()
    totals = {k: 0 for k in keys}
    for d in days:
        k = d["date"].replace(day=1)
        if k in totals:
            totals[k] += d["count"]
    return [(k, totals[k]) for k in keys]


def short(d: dt.date | None) -> str:
    return f"{d:%b} {d.day}" if d else "—"


# ---- ASCII portrait ----------------------------------------------------------

RAMP = " .:-=+*#%@"


def background_mask(img: Image.Image, tolerance: float = 38.0) -> list[list[bool]]:
    """Flood-fill from the border across light, smoothly changing pixels: that's the backdrop."""
    w, h = img.size
    px = img.load()
    seen = [[False] * w for _ in range(h)]
    queue = deque((x, y) for x in range(w) for y in (0, h - 1))
    queue.extend((x, y) for y in range(h) for x in (0, w - 1))

    def lum(p):
        return 0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2]

    for x, y in list(queue):
        if lum(px[x, y]) > 150:
            seen[y][x] = True
    queue = deque((x, y) for x, y in queue if seen[y][x])
    while queue:
        x, y = queue.popleft()
        a = px[x, y]
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and not seen[ny][nx]:
                b = px[nx, ny]
                if lum(b) > 150 and sum((a[i] - b[i]) ** 2 for i in range(3)) ** 0.5 < tolerance:
                    seen[ny][nx] = True
                    queue.append((nx, ny))
    return seen


def ascii_portrait(path: Path, cols: int = 72, rows: int = 50) -> list[list[tuple[str, str]]]:
    """Rows of (char, colour). Brighter pixels get denser characters; the backdrop is blank."""
    src = Image.open(path).convert("RGB")

    # Crop to the person (plus a small margin) so the portrait fills its window.
    probe = src.resize((100, 100), Image.LANCZOS)
    probe_bg = background_mask(probe.filter(ImageFilter.SMOOTH))
    ys = [y for y in range(100) if not all(probe_bg[y])]
    xs = [x for x in range(100) if not all(probe_bg[y][x] for y in range(100))]
    if ys and xs:
        sx, sy = src.width / 100, src.height / 100
        top = max(0, (min(ys) - 3) * sy)
        left, right = max(0, (min(xs) - 2) * sx), min(src.width, (max(xs) + 3) * sx)
        src = src.crop((int(left), int(top), int(right), src.height))

    small = src.resize((cols, rows), Image.LANCZOS)
    mask = background_mask(small.filter(ImageFilter.SMOOTH))
    gray = ImageOps.grayscale(small)
    edges = ImageOps.autocontrast(gray.filter(ImageFilter.FIND_EDGES), cutoff=2)
    px, gp, ep = small.load(), gray.load(), edges.load()

    # Stretch brightness over the person only — the light backdrop would otherwise squash every tone into "dark".
    tones = sorted(gp[x, y] for y in range(rows) for x in range(cols) if not mask[y][x]) or [0, 255]
    lo, hi = tones[len(tones) // 50], tones[-len(tones) // 50 - 1]
    span = max(1, hi - lo)

    out = []
    for y in range(rows):
        line = []
        for x in range(cols):
            if mask[y][x]:
                line.append((" ", ""))
                continue
            # Brightness picks the density; edges add a little so dark clothing still has an outline.
            v = min(1.0, max(0.0, (gp[x, y] - lo) / span) * 0.9 + ep[x, y] / 255 * 0.3)
            ch = RAMP[max(1, min(len(RAMP) - 1, round(v * (len(RAMP) - 1))))]
            r, g, b = (c / 255 for c in px[x, y])
            hh, ll, ss = colorsys.rgb_to_hls(r, g, b)
            ll = 0.5 + v * 0.38  # lift so dark tones stay readable on the dark card
            ss = min(0.65, ss * 1.3)
            rr, gg, bb = colorsys.hls_to_rgb(hh, ll, ss)
            q = lambda c: min(255, int(round(c * 255 / 24) * 24))  # quantise: fewer colour runs, smaller SVG
            line.append((ch, f"#{q(rr):02x}{q(gg):02x}{q(bb):02x}"))
        out.append(line)
    return out


# ---- SVG pieces --------------------------------------------------------------

def window(x: float, y: float, w: float, h: float, title: str) -> str:
    return f"""
  <g transform="translate({x},{y})">
    <rect width="{w}" height="{h}" rx="10" fill="{PANEL}" stroke="{BORDER}"/>
    <path d="M0 10a10 10 0 0 1 10-10h{w - 20}a10 10 0 0 1 10 10v18h-{w}z" fill="#1c2128"/>
    <line x1="0" y1="28" x2="{w}" y2="28" stroke="{BORDER}"/>
    <circle cx="16" cy="14" r="5" fill="#ff5f57"/><circle cx="32" cy="14" r="5" fill="#febc2e"/><circle cx="48" cy="14" r="5" fill="#28c840"/>
    <text x="{w / 2}" y="18" text-anchor="middle" font-size="11" fill="{MUTED}">{escape(title)}</text>
  </g>"""


def tile(x: float, y: float, w: float, h: float, label: str, value: str, unit: str, note: str, accent: bool = False) -> str:
    return f"""
  <g transform="translate({x},{y})">
    <rect width="{w}" height="{h}" rx="6" fill="{TILE}" stroke="{BORDER}"/>
    <text x="14" y="20" font-size="11" fill="{MUTED}">$ {escape(label)}</text>
    <text x="14" y="49" font-size="26" font-weight="700" fill="{GREEN if accent else TEXT}">{escape(value)}<tspan font-size="12" font-weight="400" fill="{MUTED}" dx="6">{escape(unit)}</tspan></text>
    <text x="14" y="64" font-size="9.5" fill="{MUTED}">{escape(note)}</text>
  </g>"""


def whoami_svg(login: str, days: list[dict], portrait: list[list[tuple[str, str]]]) -> str:
    W, H = 1000, 600
    (cur, cur_a, cur_b), (best_run, best_a, best_b) = streaks(days)
    total = sum(d["count"] for d in days)
    active = sum(1 for d in days if d["count"] > 0)
    top = max(days, key=lambda d: d["count"])
    avg = total / active if active else 0
    months = monthly(days)

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{FONT}">',
             f'  <rect width="{W}" height="{H}" rx="14" fill="{BG}"/>',
             f'  <text x="{W / 2}" y="40" text-anchor="middle" font-size="17" font-weight="700" fill="{TEXT}">'
             f'<tspan fill="{GREEN}">{escape(login)}@github</tspan> ~ $ whoami</text>']

    # Left: portrait.
    lx, ly, lw, lh = 16, 64, 452, 520
    parts.append(window(lx, ly, lw, lh, f"{login}@github: ~/avatar.sh"))
    fs, lead = 7.2, 8.6
    art_w = lw - 40
    top_y = ly + 28 + 20
    for r, line in enumerate(portrait):
        if not any(ch.strip() for ch, _ in line):
            continue
        spans, run_c, run = [], None, ""
        for ch, c in line + [("", "END")]:
            if c != run_c and run:
                spans.append(f'<tspan fill="{run_c}">{escape(run)}</tspan>' if run_c else escape(run))
                run = ""
            run_c, run = c, run + ch
        parts.append(f'  <text x="{lx + 20}" y="{top_y + r * lead:.1f}" font-size="{fs}" xml:space="preserve" '
                     f'textLength="{art_w}" lengthAdjust="spacing">{"".join(spans)}</text>')
    parts.append(f'  <text x="{lx + 16}" y="{ly + lh - 16}" font-size="11" fill="{MUTED}">{escape(login)}@github:~$ '
                 f'<tspan fill="{TEXT}" font-weight="700">{escape(login)}</tspan></text>')

    # Right: stats.
    rx, ry, rw, rh = 484, 64, 500, 520
    parts.append(window(rx, ry, rw, rh, f"{login}@github: ~/stats.sh"))
    tw, th, gap = (rw - 24 - 10) / 2, 72, 10
    c1, c2 = rx + 12, rx + 12 + tw + gap
    y0 = ry + 28 + 12
    parts += [
        tile(c1, y0, tw, th, "current streak", str(cur), "days", f"{short(cur_a)} – {short(cur_b)}" if cur else "start one today", accent=True),
        tile(c2, y0, tw, th, "longest streak", str(best_run), "days", f"{short(best_a)} – {short(best_b)}"),
        tile(c1, y0 + th + gap, tw, th, "contributions", f"{total:,}", "", "in the last year"),
        tile(c2, y0 + th + gap, tw, th, "active days", str(active), f"/ {len(days)}", f"{round(100 * active / len(days))}% of the year"),
        tile(c1, y0 + 2 * (th + gap), tw, th, "best day", f"{top['count']:,}", "", short(top["date"])),
        tile(c2, y0 + 2 * (th + gap), tw, th, "avg / active day", f"{avg:.1f}", "", "contributions"),
    ]

    # Contributions per month.
    cy = y0 + 3 * (th + gap)
    ch_w, ch_h = rw - 24, ry + rh - 12 - cy
    parts.append(f'  <g transform="translate({c1},{cy})">'
                 f'<rect width="{ch_w}" height="{ch_h}" rx="6" fill="{TILE}" stroke="{BORDER}"/>'
                 f'<text x="14" y="20" font-size="11" fill="{MUTED}">$ contributions / month</text>')
    peak = max(v for _, v in months) or 1
    slot = (ch_w - 28) / len(months)
    base, max_h = ch_h - 26, ch_h - 76
    for i, (m, v) in enumerate(months):
        bx = 14 + i * slot + slot * 0.18
        bw = slot * 0.64
        bh = max(2, v / peak * max_h) if v else 2
        fill = "#56d364" if i == len(months) - 1 else BAR
        parts.append(f'<rect x="{bx:.1f}" y="{base - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="2" fill="{fill}"/>'
                     f'<text x="{bx + bw / 2:.1f}" y="{base - bh - 5:.1f}" text-anchor="middle" font-size="9.5" font-weight="700" fill="{TEXT}">{v:,}</text>'
                     f'<text x="{bx + bw / 2:.1f}" y="{base + 15:.1f}" text-anchor="middle" font-size="9.5" fill="{MUTED}">{m:%b}</text>')
    parts.append("  </g>")
    parts.append("</svg>")
    return "\n".join(parts)


def contributions_svg(login: str, days: list[dict]) -> str:
    cell, gap = 13, 3
    step = cell + gap
    # Columns are weeks starting on Sunday, like GitHub's own graph.
    first = days[0]["date"] - dt.timedelta(days=(days[0]["date"].weekday() + 1) % 7)
    weeks = (days[-1]["date"] - first).days // 7 + 1
    gx, gy = 52, 96
    W = gx + weeks * step + 30
    H = gy + 7 * step + 50
    total = sum(d["count"] for d in days)

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{FONT}">',
             f'  <rect width="{W}" height="{H}" rx="14" fill="{BG}"/>',
             window(16, 12, W - 32, H - 24, f"{login}@github: ~/contributions.sh"),
             f'  <text x="36" y="66" font-size="12" fill="{MUTED}">$ gh contributions --last-year  '
             f'<tspan fill="{TEXT}" font-weight="700">{total:,}</tspan> contributions</text>']
    seen_month = None
    for d in days:
        col = (d["date"] - first).days // 7
        row = (d["date"].weekday() + 1) % 7
        x, y = gx + col * step, gy + row * step
        parts.append(f'  <rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2.5" fill="{LEVELS.get(d["level"], LEVELS["NONE"])}">'
                     f'<title>{d["count"]} on {d["date"]:%b} {d["date"].day}</title></rect>')
        if row == 0 and d["date"].month != seen_month and d["date"].day <= 7 and col < weeks - 1:
            seen_month = d["date"].month
            parts.append(f'  <text x="{x}" y="{gy - 8}" font-size="10" fill="{MUTED}">{d["date"]:%b}</text>')
    for row, name in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        parts.append(f'  <text x="{gx - 30}" y="{gy + row * step + 10}" font-size="10" fill="{MUTED}">{name}</text>')
    lx = W - 46 - 5 * step - 34
    ly = gy + 7 * step + 12
    parts.append(f'  <text x="{lx - 8}" y="{ly + 10}" text-anchor="end" font-size="10" fill="{MUTED}">Less</text>')
    for i, c in enumerate(LEVELS.values()):
        parts.append(f'  <rect x="{lx + i * step}" y="{ly}" width="{cell}" height="{cell}" rx="2.5" fill="{c}"/>')
    parts.append(f'  <text x="{lx + 5 * step + 4}" y="{ly + 10}" font-size="10" fill="{MUTED}">More</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", default=os.environ.get("PROFILE_LOGIN") or os.environ.get("GITHUB_REPOSITORY_OWNER") or "victorjudysen")
    args = ap.parse_args()
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("Set GH_TOKEN (or GITHUB_TOKEN) to read the contribution calendar.")
    days = fetch_calendar(args.login, token)
    ASSETS.mkdir(exist_ok=True)
    (ASSETS / "whoami.svg").write_text(whoami_svg(args.login, days, ascii_portrait(AVATAR)), encoding="utf-8")
    (ASSETS / "contributions.svg").write_text(contributions_svg(args.login, days), encoding="utf-8")
    print(f"Wrote assets/whoami.svg and assets/contributions.svg for {args.login} ({len(days)} days).")


if __name__ == "__main__":
    main()
