"""
flowchart_builder.py  (v2 — auto-sizing, large text)

Generates professional flowchart & block diagram PNG using matplotlib.
All dimensions in "data inches" → fig size is set to match,
so 1 unit ≈ 1 inch at dpi=150 → crisp text at any embed size.

Public API
──────────
build_linear_flowchart(title, steps, output_path, ...)
build_branching_flowchart(title, decision_text, steps_left, label_left,
                           steps_right, label_right, merge_step, output_path)
build_block_diagram(output_path)
"""

import os
import math
import textwrap
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
from matplotlib.lines import Line2D
import numpy as np

# ── Palette ──────────────────────────────────────────────────────────────────
C_START    = "#1B2A3B"   # capsule start/end
C_PROCESS  = "#1F6FAE"   # process box
C_DECISION = "#D4691E"   # decision diamond
C_IO       = "#1A7A4A"   # parallelogram I/O
C_TEXT_LT  = "#FFFFFF"   # text on dark bg
C_ARROW    = "#2C3E50"   # arrow / connector colour
C_BG       = "#FFFFFF"

FONT      = "DejaVu Sans"
FONT_SZ   = 11           # body text inside nodes
TITLE_SZ  = 13           # chart title

# ── Layout constants ──────────────────────────────────────────────────────────
NODE_W      = 3.6        # default node width  (inches)
LINE_H      = 0.28       # height per text line (inches)
V_PAD       = 0.22       # top+bottom padding inside node
GAP         = 0.55       # vertical gap between nodes
ARROW_HEAD  = 16         # arrowhead scale
LBACK_OFF   = 0.7        # how far right the loop-back line goes


# ── Text helpers ──────────────────────────────────────────────────────────────
def _split_lines(text, max_chars=28):
    """Split text on \n, then wrap each part without breaking words."""
    out = []
    for raw in text.split("\n"):
        raw = raw.strip()
        if not raw:
            continue
        wrapped = textwrap.wrap(raw, width=max_chars, break_long_words=False,
                                break_on_hyphens=False)
        out.extend(wrapped if wrapped else [raw])
    return out


def _node_height(text, max_chars=28):
    """Auto-calculate node height based on line count."""
    lines = _split_lines(text, max_chars)
    return len(lines) * LINE_H + V_PAD * 2


# ── Shape drawers ─────────────────────────────────────────────────────────────
def _draw_capsule(ax, cx, cy, w, h, text, color=C_START):
    r = h / 2
    fancy = FancyBboxPatch((cx - w/2, cy - h/2), w, h,
                            boxstyle=f"round,pad=0,rounding_size={r}",
                            linewidth=1.8, edgecolor="white",
                            facecolor=color, zorder=3, clip_on=False)
    ax.add_patch(fancy)
    lines = _split_lines(text)
    ax.text(cx, cy, "\n".join(lines),
            ha="center", va="center", fontsize=FONT_SZ,
            fontfamily=FONT, color=C_TEXT_LT, fontweight="bold",
            zorder=4, linespacing=1.35, multialignment="center",
            clip_on=False)


def _draw_rect(ax, cx, cy, w, h, text, color=C_PROCESS):
    fancy = FancyBboxPatch((cx - w/2, cy - h/2), w, h,
                            boxstyle="round,pad=0,rounding_size=0.1",
                            linewidth=1.5, edgecolor="white",
                            facecolor=color, zorder=3, clip_on=False)
    ax.add_patch(fancy)
    lines = _split_lines(text)
    ax.text(cx, cy, "\n".join(lines),
            ha="center", va="center", fontsize=FONT_SZ,
            fontfamily=FONT, color=C_TEXT_LT,
            zorder=4, linespacing=1.35, multialignment="center",
            clip_on=False)


def _draw_diamond(ax, cx, cy, w, h, text, color=C_DECISION):
    dx, dy = w / 2, h / 2
    poly = plt.Polygon(
        [[cx, cy + dy], [cx + dx, cy], [cx, cy - dy], [cx - dx, cy]],
        closed=True, linewidth=1.5, edgecolor="white",
        facecolor=color, zorder=3, clip_on=False)
    ax.add_patch(poly)
    lines = _split_lines(text, max_chars=18)
    ax.text(cx, cy, "\n".join(lines),
            ha="center", va="center", fontsize=FONT_SZ,
            fontfamily=FONT, color=C_TEXT_LT, fontweight="bold",
            zorder=4, linespacing=1.35, multialignment="center",
            clip_on=False)


def _draw_para(ax, cx, cy, w, h, text, color=C_IO):
    skew = 0.15
    pts = [[cx - w/2 + skew*h, cy + h/2],
           [cx + w/2 + skew*h, cy + h/2],
           [cx + w/2 - skew*h, cy - h/2],
           [cx - w/2 - skew*h, cy - h/2]]
    poly = plt.Polygon(pts, closed=True, linewidth=1.5,
                       edgecolor="white", facecolor=color,
                       zorder=3, clip_on=False)
    ax.add_patch(poly)
    lines = _split_lines(text)
    ax.text(cx, cy, "\n".join(lines),
            ha="center", va="center", fontsize=FONT_SZ,
            fontfamily=FONT, color=C_TEXT_LT,
            zorder=4, linespacing=1.35, multialignment="center",
            clip_on=False)


_DRAW = {
    "start_end": _draw_capsule,
    "process":   _draw_rect,
    "decision":  _draw_diamond,
    "io":        _draw_para,
}

def _draw_node(ax, cx, cy, w, h, ntype, text):
    _DRAW.get(ntype, _draw_rect)(ax, cx, cy, w, h, text)


def _diamond_h(text):
    """Diamond height = node content height × 1.7 for visual clarity."""
    nh = _node_height(text, max_chars=18)
    return max(nh * 1.7, 0.9)


# ── Arrow helpers ─────────────────────────────────────────────────────────────
def _arrow_down(ax, x, y1, y2, label="", label_side="right"):
    ax.annotate("", xy=(x, y2), xytext=(x, y1),
                arrowprops=dict(arrowstyle="-|>", color=C_ARROW,
                                lw=1.5, mutation_scale=ARROW_HEAD),
                zorder=2, annotation_clip=False)
    if label:
        ox = 0.14 if label_side == "right" else -0.14
        ha = "left" if label_side == "right" else "right"
        ax.text(x + ox, (y1 + y2) / 2, label,
                fontsize=FONT_SZ - 1, color=C_ARROW,
                ha=ha, va="center", fontfamily=FONT)


def _loopback(ax, node_cx, node_w, from_y, to_y, label="Tidak"):
    """Right-side loop-back line."""
    x_right = node_cx + node_w / 2
    x_loop  = x_right + LBACK_OFF
    ax.plot([x_right, x_loop, x_loop, x_right],
            [from_y,  from_y, to_y,   to_y],
            color=C_ARROW, lw=1.5, zorder=2)
    ax.annotate("", xy=(x_right, to_y), xytext=(x_right + 0.01, to_y),
                arrowprops=dict(arrowstyle="-|>", color=C_ARROW,
                                lw=1.5, mutation_scale=ARROW_HEAD), zorder=2)
    mid_y = (from_y + to_y) / 2
    ax.text(x_loop + 0.08, mid_y, label,
            fontsize=FONT_SZ - 1, color=C_ARROW,
            ha="left", va="center", fontfamily=FONT)


# ══════════════════════════════════════════════════════════════════════════════
#  1. LINEAR FLOWCHART
# ══════════════════════════════════════════════════════════════════════════════
def build_linear_flowchart(title, steps, output_path, node_w=NODE_W):
    """
    steps: list of (type, text)
    type: 'start_end' | 'process' | 'decision' | 'io'
    """
    # Pre-compute node heights
    heights = []
    for stype, text in steps:
        if stype == "decision":
            h = _diamond_h(text)
        else:
            h = _node_height(text)
        heights.append(h)

    # Canvas size
    total_h = sum(heights) + GAP * (len(steps) - 1)
    fig_w   = node_w + LBACK_OFF + 1.4   # room for loop-back + margins
    fig_h   = total_h + 1.2              # room for title + bottom margin

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    fig.patch.set_facecolor(C_BG)
    ax.set_facecolor(C_BG)
    ax.set_xlim(0, fig_w)
    ax.set_ylim(0, fig_h)
    ax.axis("off")

    cx = fig_w / 2 - LBACK_OFF / 2   # slightly left to leave room for loop-back

    # Compute cy for each node (top-down)
    cys = []
    y = fig_h - 0.85
    for h in heights:
        cys.append(y - h / 2)
        y -= h + GAP

    # Draw nodes
    for i, (stype, text) in enumerate(steps):
        _draw_node(ax, cx, cys[i], node_w, heights[i], stype, text)

    # Draw arrows & loop-backs
    for i in range(len(steps) - 1):
        bot_i  = cys[i]   - heights[i]   / 2
        top_i1 = cys[i+1] + heights[i+1] / 2
        label  = "Ya" if steps[i][0] == "decision" else ""
        _arrow_down(ax, cx, bot_i, top_i1, label=label)

    # Loop-back for each decision node:
    # "Tidak" → go back to node 2 steps earlier (or previous process)
    for i, (stype, _) in enumerate(steps):
        if stype == "decision" and i > 0:
            from_y = cys[i]   - heights[i]   / 2   # bottom of decision
            # find nearest previous non-decision node
            target = max(i - 2, 0)
            to_y   = cys[target] + heights[target] / 2  # top of target
            _loopback(ax, cx, node_w, from_y, to_y)

    # Title
    ax.text(cx, fig_h - 0.3, title,
            ha="center", va="top", fontsize=TITLE_SZ, fontweight="bold",
            color=C_ARROW, fontfamily=FONT)

    plt.tight_layout(pad=0.2)
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=C_BG)
    plt.close(fig)
    return output_path


# ══════════════════════════════════════════════════════════════════════════════
#  2. BRANCHING FLOWCHART
# ══════════════════════════════════════════════════════════════════════════════
def build_branching_flowchart(title, decision_text,
                               steps_left, label_left,
                               steps_right, label_right,
                               merge_step, output_path, node_w=NODE_W - 0.4):
    """Diamond → two branches → merge node."""

    # Pre-compute heights
    dec_h = _diamond_h(decision_text)

    def _hs(steps):
        return [_node_height(t) if tp != "decision" else _diamond_h(t)
                for tp, t in steps]

    hl = _hs(steps_left)
    hr = _hs(steps_right)
    hm = _node_height(merge_step[1])

    max_branch_h = max(sum(hl) + GAP * (len(hl) - 1) if hl else 0,
                       sum(hr) + GAP * (len(hr) - 1) if hr else 0)

    col_gap  = 0.7                          # gap between centre and each column
    cx_left  = node_w / 2 + col_gap * 0.4
    cx_right_rel = node_w + col_gap * 1.4  # from left edge
    fig_w    = cx_right_rel + node_w / 2 + 1.0
    cx       = fig_w / 2

    total_h  = (dec_h + GAP + max_branch_h + GAP + hm)
    fig_h    = total_h + 1.4

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    fig.patch.set_facecolor(C_BG)
    ax.set_facecolor(C_BG)
    ax.set_xlim(0, fig_w)
    ax.set_ylim(0, fig_h)
    ax.axis("off")

    # Column centres
    cx_l = cx - node_w / 2 - col_gap / 2
    cx_r = cx + node_w / 2 + col_gap / 2

    # ── Decision ──────────────────────────────────────────────────────────────
    dec_cy = fig_h - 0.85 - dec_h / 2
    _draw_diamond(ax, cx, dec_cy, node_w + 0.6, dec_h, decision_text)

    # ── Left branch ───────────────────────────────────────────────────────────
    y = dec_cy - dec_h / 2 - GAP
    lcys = []
    for i, (stp, txt) in enumerate(steps_left):
        h  = hl[i]
        cy = y - h / 2
        _draw_node(ax, cx_l, cy, node_w, h, stp, txt)
        lcys.append(cy)
        y -= h + GAP

    # ── Right branch ──────────────────────────────────────────────────────────
    y = dec_cy - dec_h / 2 - GAP
    rcys = []
    for i, (stp, txt) in enumerate(steps_right):
        h  = hr[i]
        cy = y - h / 2
        _draw_node(ax, cx_r, cy, node_w, h, stp, txt)
        rcys.append(cy)
        y -= h + GAP

    # ── Merge node ────────────────────────────────────────────────────────────
    last_l = lcys[-1] if lcys else dec_cy
    last_r = rcys[-1] if rcys else dec_cy
    hl_last = hl[-1] if hl else dec_h
    hr_last = hr[-1] if hr else dec_h
    merge_top = min(last_l - hl_last / 2, last_r - hr_last / 2) - GAP
    merge_cy  = merge_top - hm / 2
    _draw_node(ax, cx, merge_cy, node_w + 0.3, hm, merge_step[0], merge_step[1])

    # ── Arrows: decision → branches ───────────────────────────────────────────
    def _diag_arrow(x1, y1, x2, y2, lbl, lbl_side):
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle="-|>", color=C_ARROW,
                                    lw=1.5, mutation_scale=ARROW_HEAD),
                    zorder=2, annotation_clip=False)
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        ox = -0.12 if lbl_side == "left" else 0.12
        ha = "right" if lbl_side == "left" else "left"
        ax.text(mx + ox, my, lbl, fontsize=FONT_SZ - 1, color=C_ARROW,
                ha=ha, va="center", fontfamily=FONT)

    if lcys:
        _diag_arrow(cx - (node_w + 0.6) / 2, dec_cy,
                    cx_l, lcys[0] + hl[0] / 2,
                    label_left, "left")
        for i in range(len(lcys) - 1):
            _arrow_down(ax, cx_l,
                        lcys[i] - hl[i] / 2,
                        lcys[i+1] + hl[i+1] / 2)

    if rcys:
        _diag_arrow(cx + (node_w + 0.6) / 2, dec_cy,
                    cx_r, rcys[0] + hr[0] / 2,
                    label_right, "right")
        for i in range(len(rcys) - 1):
            _arrow_down(ax, cx_r,
                        rcys[i] - hr[i] / 2,
                        rcys[i+1] + hr[i+1] / 2)

    # ── Arrows: branches → merge (L-shaped) ──────────────────────────────────
    mw = node_w + 0.3
    # left last → merge
    ly_bot = (lcys[-1] if lcys else dec_cy) - (hl[-1] if hl else dec_h) / 2
    ax.plot([cx_l, cx_l, cx - mw / 2],
            [ly_bot, merge_cy, merge_cy],
            color=C_ARROW, lw=1.5, zorder=2)
    ax.annotate("", xy=(cx - mw / 2, merge_cy),
                xytext=(cx - mw / 2 - 0.01, merge_cy),
                arrowprops=dict(arrowstyle="-|>", color=C_ARROW,
                                lw=1.5, mutation_scale=ARROW_HEAD), zorder=2)

    # right last → merge
    ry_bot = (rcys[-1] if rcys else dec_cy) - (hr[-1] if hr else dec_h) / 2
    ax.plot([cx_r, cx_r, cx + mw / 2],
            [ry_bot, merge_cy, merge_cy],
            color=C_ARROW, lw=1.5, zorder=2)
    ax.annotate("", xy=(cx + mw / 2, merge_cy),
                xytext=(cx + mw / 2 + 0.01, merge_cy),
                arrowprops=dict(arrowstyle="-|>", color=C_ARROW,
                                lw=1.5, mutation_scale=ARROW_HEAD), zorder=2)

    # Title
    ax.text(cx, fig_h - 0.3, title,
            ha="center", va="top", fontsize=TITLE_SZ, fontweight="bold",
            color=C_ARROW, fontfamily=FONT)

    plt.tight_layout(pad=0.2)
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=C_BG)
    plt.close(fig)
    return output_path


# ══════════════════════════════════════════════════════════════════════════════
#  3. BLOCK DIAGRAM SISTEM
# ══════════════════════════════════════════════════════════════════════════════
def build_block_diagram(output_path):
    """
    Simple 3-tier block diagram:
    Tier 1: Pengguna (Browser)
    Tier 2: Aplikasi Dashboard (Web)
    Tier 3: Kamera CCTV | Server Storage | Basis Data
    """
    FIG_W, FIG_H = 11.0, 7.5

    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
    fig.patch.set_facecolor("#F8F9FA")
    ax.set_facecolor("#F8F9FA")
    ax.set_xlim(0, FIG_W)
    ax.set_ylim(0, FIG_H)
    ax.axis("off")

    C = {
        "user":    "#2C3E50",
        "app":     "#1F6FAE",
        "cam":     "#922B21",
        "storage": "#1B4F72",
        "db":      "#6C3483",
        "arrow":   "#2C3E50",
        "grp_cam": "#FDEDEC",
        "grp_sto": "#EBF5FB",
        "grp_db":  "#F5EEF8",
    }

    def box(cx, cy, w, h, text, fc, fontsize=11, bold=False, tc="white"):
        p = FancyBboxPatch((cx - w/2, cy - h/2), w, h,
                           boxstyle="round,pad=0,rounding_size=0.2",
                           linewidth=1.8, edgecolor="white",
                           facecolor=fc, zorder=3, clip_on=False)
        ax.add_patch(p)
        lines = _split_lines(text, max_chars=24)
        ax.text(cx, cy, "\n".join(lines),
                ha="center", va="center", fontsize=fontsize,
                fontfamily=FONT, color=tc,
                fontweight="bold" if bold else "normal",
                zorder=4, linespacing=1.4, multialignment="center",
                clip_on=False)

    def grp(x, y, w, h, label, fc, bc):
        p = FancyBboxPatch((x, y), w, h,
                           boxstyle="round,pad=0,rounding_size=0.3",
                           linewidth=2, edgecolor=bc,
                           facecolor=fc, zorder=1, clip_on=False)
        ax.add_patch(p)
        ax.text(x + w/2, y + h - 0.15, label,
                ha="center", va="top", fontsize=10,
                fontfamily=FONT, color=bc, fontweight="bold", zorder=2)

    def arr(x1, y1, x2, y2, label="", two_way=False):
        style = "<|-|>" if two_way else "-|>"
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                    arrowprops=dict(arrowstyle=style, color=C["arrow"],
                                    lw=1.8, mutation_scale=15), zorder=5)
        if label:
            mx, my = (x1+x2)/2, (y1+y2)/2
            ax.text(mx + 0.18, my, label, fontsize=9.5, color=C["arrow"],
                    ha="left", va="center", fontfamily=FONT,
                    bbox=dict(facecolor="white", edgecolor="none",
                              alpha=0.85, pad=2))

    # ── Tier 1: Pengguna ─────────────────────────────────────────────────────
    box(FIG_W/2, 6.7, 4.2, 0.9,
        "PENGGUNA\n(Antarmuka Web Browser)",
        C["user"], fontsize=11, bold=True)

    # ── Tier 2: Aplikasi Dashboard ────────────────────────────────────────────
    grp(0.5, 4.05, FIG_W - 1.0, 1.9, "APLIKASI DASHBOARD MONITORING CCTV",
        "#EAF4FB", C["app"])

    box(2.2, 4.85, 2.8, 0.85,
        "Manajemen\nKamera", C["app"], fontsize=10.5)
    box(5.5, 4.85, 2.8, 0.85,
        "Manajemen\nServer Storage", C["app"], fontsize=10.5)
    box(8.8, 4.85, 1.8, 0.85,
        "Penjadwalan\nOtomatis", "#6C3483", fontsize=10.5)

    ax.text(2.2,  4.25, "Pantau · Filter · Peta",
            ha="center", fontsize=8.5, color="#1A5276",
            fontstyle="italic", fontfamily=FONT)
    ax.text(5.5,  4.25, "Suhu · HDD · Daya",
            ha="center", fontsize=8.5, color="#1A5276",
            fontstyle="italic", fontfamily=FONT)
    ax.text(8.8,  4.25, "Berkala",
            ha="center", fontsize=8.5, color="#6C3483",
            fontstyle="italic", fontfamily=FONT)

    # ── Tier 3: Perangkat & DB ────────────────────────────────────────────────
    # Kamera CCTV
    grp(0.3, 0.6, 3.3, 2.8, "Kamera CCTV Pelco", C["grp_cam"], C["cam"])
    box(1.1, 2.2, 1.3, 0.75, "Kamera\nDome", C["cam"], fontsize=9.5)
    box(2.5, 2.2, 1.3, 0.75, "Kamera\nPTZ", C["cam"], fontsize=9.5)
    box(1.8, 1.3, 1.8, 0.75, "Kamera\nPanoramik", C["cam"], fontsize=9.5)

    # Server Storage
    grp(3.9, 0.6, 3.9, 2.8, "Server Storage Pelco", C["grp_sto"], C["storage"])
    box(5.0, 2.2, 1.6, 0.75, "VX Storage\n(NVR)", C["storage"], fontsize=9.5)
    box(6.7, 2.2, 1.6, 0.75, "Endura\nNSM5200", C["storage"], fontsize=9.5)
    ax.text(5.85, 1.25, "Penyimpanan rekaman video",
            ha="center", fontsize=8.5, color="#1B4F72",
            fontstyle="italic", fontfamily=FONT)

    # Basis Data
    grp(8.1, 0.6, 2.6, 2.8, "Basis Data", C["grp_db"], C["db"])
    box(9.4, 2.2, 1.8, 0.75, "Data\nKamera", C["db"], fontsize=9.5)
    box(9.4, 1.3, 1.8, 0.75, "Data\nServer & HDD", C["db"], fontsize=9.5)

    # ── Arrows ────────────────────────────────────────────────────────────────
    # User ↔ App
    arr(FIG_W/2, 6.25, FIG_W/2, 5.95, "Permintaan & Tampilan", two_way=True)

    # App → Kamera
    arr(2.2, 4.05, 1.95, 3.4, "Pantau\nStatus")
    # App → Server Storage
    arr(5.5, 4.05, 5.85, 3.4, "Pantau\nKesehatan")
    # App ↔ DB
    arr(8.8, 4.05, 9.4, 3.4, "Simpan &\nBaca Data", two_way=True)

    # Title
    ax.text(FIG_W/2, FIG_H - 0.12,
            "Diagram Blok Sistem Dashboard Monitoring CCTV",
            ha="center", va="top", fontsize=13.5, fontweight="bold",
            color=C["arrow"], fontfamily=FONT)

    plt.tight_layout(pad=0.15)
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="#F8F9FA")
    plt.close(fig)
    return output_path


# ── Quick self-test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import tempfile, os
    d = tempfile.mkdtemp()

    p1 = build_linear_flowchart(
        "Test Linear",
        [("start_end", "MULAI"),
         ("process",   "Ambil semua kamera\ndari database"),
         ("io",        "ONVIF probe:\nGetDeviceInformation()"),
         ("decision",  "Probe berhasil?"),
         ("process",   "Update: is_active=True\nstream_uri · last_seen"),
         ("process",   "Update: is_active=False"),
         ("start_end", "SELESAI")],
        os.path.join(d, "linear.png"))
    print("Linear →", p1)

    p2 = build_branching_flowchart(
        "Test Branching",
        decision_text="server_type\n== ?",
        steps_left=[("process","Redfish GET /Systems"),
                    ("process","Redfish GET /Storage")],
        label_left="vxstorage",
        steps_right=[("process","SNMP GET sysDescr"),
                     ("process","SNMP WALK hrStorage")],
        label_right="endura",
        merge_step=("process", "UPSERT ke DB:\nserver · hdd · psu"),
        output_path=os.path.join(d, "branching.png"))
    print("Branching →", p2)

    p3 = build_block_diagram(os.path.join(d, "block.png"))
    print("Block diagram →", p3)
