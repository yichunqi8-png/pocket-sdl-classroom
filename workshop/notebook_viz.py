"""Workshop Jupyter Notebook 零依赖内联 HTML/SVG 可视化助手。

不依赖 matplotlib / plotly，仅用 Python 标准库生成清晰可交互的 8 通道彩虹光谱与收敛曲线。
"""

import math

CHANNELS = (
    (415, "#7c3aed", "F1"),
    (445, "#3b82f6", "F2"),
    (480, "#06b6d4", "F3"),
    (515, "#10b981", "F4"),
    (555, "#84cc16", "F5"),
    (590, "#facc15", "F6"),
    (630, "#f97316", "F7"),
    (680, "#ef4444", "F8"),
)


def _display_html(html):
    try:
        from IPython.display import HTML, display

        display(HTML(html))
        return None
    except ImportError:
        return html


def _css_rgb(x):
    if x is None:
        return "#1e293b"
    return f"rgb({round(x[0] * 255)},{round(x[1] * 255)},{round(x[2] * 255)})"


def spectrum_svg(values, overlay=None, title="AS7341 8 通道光谱", x=None, is_norm=False, width=680, height=250):
    """生成单幅 8 通道彩虹光谱柱状图 HTML（可选叠加目标虚线轮廓）。"""
    vals = [float(v) for v in values]
    ov = [float(v) for v in overlay] if overlay is not None else None
    max_v = max([0.6 if is_norm else 1000.0] + vals + (ov or [])) * 1.18

    pad_l, pad_r, pad_t, pad_b = 54, 20, 26, 44
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    step = plot_w / 8.0
    bar_w = step * 0.56

    def y_of(v):
        ratio = max(0.0, min(1.0, v / max_v))
        return pad_t + plot_h - ratio * plot_h

    parts = []
    for i in range(5):
        gv = max_v * i / 4.0
        gy = y_of(gv)
        lbl = f"{gv:.2f}" if is_norm else f"{int(round(gv))}"
        parts.append(f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}" stroke="rgba(148,163,184,0.2)"/>')
        parts.append(f'<text x="{pad_l - 8}" y="{gy + 4:.1f}" text-anchor="end" fill="#94a3b8" font-size="11" font-family="monospace">{lbl}</text>')

    for idx, (nm, color, fn) in enumerate(CHANNELS):
        cx = pad_l + step * idx + step / 2.0
        bx = cx - bar_w / 2.0
        by = y_of(vals[idx])
        bh = max(2.0, pad_t + plot_h - by)
        parts.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" rx="5" fill="{color}" fill-opacity="0.9"/>')
        v_txt = f"{vals[idx]:.2f}" if is_norm else f"{int(round(vals[idx]))}"
        parts.append(f'<text x="{cx:.1f}" y="{max(14.0, by - 5):.1f}" text-anchor="middle" fill="#e2e8f0" font-size="10.5" font-family="monospace">{v_txt}</text>')
        parts.append(f'<text x="{cx:.1f}" y="{height - 20}" text-anchor="middle" fill="#cbd5e1" font-size="11.5" font-weight="700">{nm}nm</text>')
        parts.append(f'<text x="{cx:.1f}" y="{height - 6}" text-anchor="middle" fill="#64748b" font-size="10">{fn}</text>')

    if ov is not None:
        pts = " ".join(f"{pad_l + step * i + step / 2.0:.1f},{y_of(ov[i]):.1f}" for i in range(8))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="#ffffff" stroke-width="2.4" stroke-dasharray="6 4"/>')
        for i in range(8):
            cx = pad_l + step * i + step / 2.0
            parts.append(f'<circle cx="{cx:.1f}" cy="{y_of(ov[i]):.1f}" r="4" fill="#0f172a" stroke="#ffffff" stroke-width="2"/>')

    swatch = ""
    if x is not None:
        swatch = (
            f'<span style="display:inline-block;width:16px;height:16px;border-radius:4px;'
            f'background:{_css_rgb(x)};border:1px solid rgba(255,255,255,0.4);vertical-align:middle;margin-right:8px;"></span>'
            f'<span style="font-family:monospace;font-size:12px;color:#94a3b8;margin-left:8px;">x={[round(float(c),1) for c in x]}</span>'
        )

    return (
        '<div style="background:#0f172a;color:#f1f5f9;border:1px solid #334155;border-radius:12px;padding:12px 14px;margin:8px 0;max-width:720px;font-family:system-ui,sans-serif;">'
        f'<div style="font-size:14px;font-weight:700;margin-bottom:8px;">{swatch}{title}</div>'
        f'<svg viewBox="0 0 {width} {height}" style="width:100%;height:auto;display:block;">{"".join(parts)}</svg>'
        "</div>"
    )


def show_spectrum(values, overlay=None, title="AS7341 8 通道光谱", x=None, is_norm=False):
    return _display_html(spectrum_svg(values, overlay=overlay, title=title, x=x, is_norm=is_norm))


def show_palette_spectra(items):
    """items: list of (label, x, raw_vector)。多色光谱小卡片并排展示。"""
    cards = []
    for label, x, raw in items:
        cards.append(spectrum_svg(raw, title=label, x=x, width=340, height=180))
    html = (
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:10px;">'
        + "".join(cards)
        + "</div>"
    )
    return _display_html(html)


def show_convergence(series_dict, title="优化收敛对比（谱差 L(x) 越低越好）", width=700, height=260):
    """series_dict: {name: {"color": "#38bdf8", "best_so_far": [..]}}"""
    max_iter = max((len(v["best_so_far"]) for v in series_dict.values()), default=10)
    max_loss = max(
        [0.4] + [loss for v in series_dict.values() for loss in v["best_so_far"] if math.isfinite(loss)]
    ) * 1.15
    pad_l, pad_r, pad_t, pad_b = 54, 24, 24, 40
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    def x_of(step):
        return pad_l + ((step - 1) / max(1, max_iter - 1)) * plot_w

    def y_of(loss):
        return pad_t + plot_h - min(1.0, max(0.0, loss / max_loss)) * plot_h

    parts = []
    for i in range(5):
        gv = max_loss * i / 4.0
        gy = y_of(gv)
        parts.append(f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}" stroke="rgba(148,163,184,0.2)"/>')
        parts.append(f'<text x="{pad_l - 8}" y="{gy + 4:.1f}" text-anchor="end" fill="#94a3b8" font-size="11" font-family="monospace">{gv:.2f}</text>')

    if max_iter >= 4:
        x4 = x_of(4)
        parts.append(f'<rect x="{pad_l}" y="{pad_t}" width="{x4 - pad_l:.1f}" height="{plot_h}" fill="rgba(255,255,255,0.04)"/>')
        parts.append(f'<line x1="{x4:.1f}" y1="{pad_t}" x2="{x4:.1f}" y2="{pad_t + plot_h}" stroke="rgba(255,255,255,0.22)" stroke-dasharray="4 4"/>')
        parts.append(f'<text x="{(pad_l + x4)/2:.1f}" y="{pad_t + 14}" text-anchor="middle" fill="#94a3b8" font-size="10.5">前4次共同初始点</text>')

    step_tick = 1 if max_iter <= 12 else 2
    for step in range(1, max_iter + 1, step_tick):
        parts.append(f'<text x="{x_of(step):.1f}" y="{height - 16}" text-anchor="middle" fill="#94a3b8" font-size="11" font-family="monospace">{step}</text>')
    parts.append(f'<text x="{width / 2:.1f}" y="{height - 2}" text-anchor="middle" fill="#64748b" font-size="11">实验次数 (Iteration)</text>')

    legend = []
    for name, cfg in series_dict.items():
        vals = cfg["best_so_far"]
        color = cfg.get("color", "#38bdf8")
        if not vals:
            continue
        pts = " ".join(f"{x_of(i + 1):.1f},{y_of(v):.1f}" for i, v in enumerate(vals))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.6"/>')
        for i, v in enumerate(vals):
            parts.append(f'<circle cx="{x_of(i + 1):.1f}" cy="{y_of(v):.1f}" r="3.4" fill="{color}"/>')
        legend.append(f'<span style="color:{color};font-weight:700;margin-right:14px;">● {name} (最终 L={vals[-1]:.4f})</span>')

    html = (
        '<div style="background:#0f172a;color:#f1f5f9;border:1px solid #334155;border-radius:12px;padding:12px 14px;margin:8px 0;max-width:740px;font-family:system-ui,sans-serif;">'
        f'<div style="font-size:14px;font-weight:700;margin-bottom:6px;">{title}</div>'
        f'<div style="font-size:12px;margin-bottom:8px;">{"".join(legend)}</div>'
        f'<svg viewBox="0 0 {width} {height}" style="width:100%;height:auto;display:block;">{"".join(parts)}</svg>'
        "</div>"
    )
    return _display_html(html)


def show_color_comparison(entries, title="目标色 vs 最佳寻优结果"):
    """entries: list of (label, x, subtitle)"""
    cards = []
    for label, x, subtitle in entries:
        cards.append(
            '<div style="background:#1e293b;border:1px solid #334155;border-radius:10px;padding:10px;text-align:center;">'
            f'<div style="height:56px;border-radius:8px;background:{_css_rgb(x)};border:1px solid rgba(255,255,255,0.25);margin-bottom:8px;"></div>'
            f'<div style="font-size:13px;font-weight:700;">{label}</div>'
            f'<div style="font-size:11.5px;color:#94a3b8;font-family:monospace;margin-top:2px;">{subtitle}</div>'
            "</div>"
        )
    html = (
        '<div style="background:#0f172a;color:#f1f5f9;border:1px solid #334155;border-radius:12px;padding:12px 14px;margin:8px 0;max-width:740px;font-family:system-ui,sans-serif;">'
        f'<div style="font-size:14px;font-weight:700;margin-bottom:10px;">{title}</div>'
        f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px;">{"".join(cards)}</div>'
        "</div>"
    )
    return _display_html(html)
