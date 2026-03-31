"""
Рендер визуальной статистики MM2 в стиле HTA-приложения.
Генерирует PNG-картинку с тёмным фоном и неоновым зелёным.
"""

import io
from PIL import Image, ImageDraw, ImageFont


# ── Цвета ─────────────────────────────────────────────────────
BG = (5, 5, 5)
CARD_BG = (12, 12, 12)
BORDER = (30, 30, 30)
NEON = (0, 255, 102)
NEON_DIM = (0, 150, 60)
RED = (255, 77, 77)
WHITE = (255, 255, 255)
GRAY = (100, 100, 100)
DARK_GRAY = (55, 55, 55)
TEXT_DIM = (70, 70, 70)


def _font(size):
    """Пытаемся загрузить шрифт, если нет — fallback."""
    for name in ["consola.ttf", "consolab.ttf", "cour.ttf", "arial.ttf", "DejaVuSansMono.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _font_bold(size):
    for name in ["consolab.ttf", "arialbd.ttf", "DejaVuSansMono-Bold.ttf", "consola.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return _font(size)


def render_stats(data: dict, settings: dict, rate: float, today: int) -> io.BytesIO:
    """
    Рендерит статистику в PNG.

    Args:
        data: {day: amount} — данные за месяц
        settings: {"target": float, "currency": str}
        rate: курс USD/RUB
        today: текущий день месяца

    Returns:
        BytesIO с PNG-картинкой
    """
    currency = settings.get("currency", "USD")
    target = settings.get("target", 7000)
    mult = rate if currency == "RUB" else 1
    sym = "$" if currency == "USD" else "₽"

    # Подсчёты
    sum1, sum2, d1, d2 = 0, 0, 0, 0
    for day, amount in data.items():
        if day <= 15:
            sum1 += amount
            if amount > 0: d1 += 1
        else:
            sum2 += amount
            if amount > 0: d2 += 1
    total = sum1 + sum2
    remaining = max(target - total, 0)
    progress = min(total / target, 1.0) if target > 0 else 0

    def f(v):
        val = v * mult
        if abs(val) >= 1000:
            return f"{val:,.0f}".replace(",", " ")
        return f"{val:,.2f}".replace(",", " ")

    # ── Размеры ───────────────────────────────────────────
    W, H = 900, 750
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Шрифты
    f_sm = _font(12)
    f_md = _font(14)
    f_lg = _font_bold(18)
    f_xl = _font_bold(32)
    f_xxl = _font_bold(42)
    f_label = _font_bold(10)

    # ── Сетка фона ────────────────────────────────────────
    for x in range(0, W, 30):
        draw.line([(x, 0), (x, H)], fill=(10, 10, 10), width=1)
    for y in range(0, H, 30):
        draw.line([(0, y), (W, y)], fill=(10, 10, 10), width=1)

    # ── Status bar ────────────────────────────────────────
    draw.text((W - 100, 12), "ONLINE", fill=DARK_GRAY, font=f_label)
    draw.ellipse([W - 25, 12, W - 17, 20], fill=NEON)

    # ── Валюта ────────────────────────────────────────────
    cx = W // 2
    btn_w, btn_h = 80, 32
    # USD
    usd_x = cx - btn_w - 5
    usd_active = currency == "USD"
    if usd_active:
        draw.rounded_rectangle([usd_x, 30, usd_x + btn_w, 30 + btn_h], radius=8, fill=NEON)
        draw.text((usd_x + 15, 36), "USD $", fill=(0, 0, 0), font=f_lg)
    else:
        draw.rounded_rectangle([usd_x, 30, usd_x + btn_w, 30 + btn_h], radius=8, outline=BORDER)
        draw.text((usd_x + 15, 36), "USD $", fill=DARK_GRAY, font=f_lg)
    # RUB
    rub_x = cx + 5
    rub_active = currency == "RUB"
    if rub_active:
        draw.rounded_rectangle([rub_x, 30, rub_x + btn_w, 30 + btn_h], radius=8, fill=NEON)
        draw.text((rub_x + 15, 36), "RUB ₽", fill=(0, 0, 0), font=f_lg)
    else:
        draw.rounded_rectangle([rub_x, 30, rub_x + btn_w, 30 + btn_h], radius=8, outline=BORDER)
        draw.text((rub_x + 15, 36), "RUB ₽", fill=DARK_GRAY, font=f_lg)

    # ── Основной блок ─────────────────────────────────────
    main_y = 80
    draw.rounded_rectangle([20, main_y, W - 20, H - 20], radius=20, fill=(3, 3, 3), outline=BORDER)

    # ── Период I (1-15) ──────────────────────────────────
    col1_x = 40
    col_w = 200
    draw.rounded_rectangle([col1_x, main_y + 15, col1_x + col_w, main_y + 530], radius=10, fill=CARD_BG, outline=BORDER)
    draw.text((col1_x + 60, main_y + 22), "ПЕРИОД I", fill=DARK_GRAY, font=f_label)
    draw.line([(col1_x, main_y + 42), (col1_x + col_w, main_y + 42)], fill=BORDER)

    for i in range(1, 16):
        y = main_y + 48 + (i - 1) * 32
        is_today = i == today
        val = data.get(i, 0)

        if is_today:
            draw.rectangle([col1_x + 1, y, col1_x + col_w - 1, y + 28], fill=(0, 255, 102, 15))
            draw.line([(col1_x + 1, y), (col1_x + 1, y + 28)], fill=NEON, width=2)

        draw.text((col1_x + 12, y + 6), f"{i}", fill=DARK_GRAY if not is_today else NEON, font=f_md)
        if val != 0:
            txt = f"{f(val)}"
            tw = draw.textlength(txt, font=f_lg)
            color = WHITE if val > 0 else RED
            draw.text((col1_x + col_w - 15 - tw, y + 4), txt, fill=color, font=f_lg)
        else:
            draw.text((col1_x + col_w - 30, y + 6), "—", fill=(30, 30, 30), font=f_md)

    # ── Период II (16-31) ─────────────────────────────────
    col2_x = 260
    draw.rounded_rectangle([col2_x, main_y + 15, col2_x + col_w, main_y + 530], radius=10, fill=CARD_BG, outline=BORDER)
    draw.text((col2_x + 55, main_y + 22), "ПЕРИОД II", fill=DARK_GRAY, font=f_label)
    draw.line([(col2_x, main_y + 42), (col2_x + col_w, main_y + 42)], fill=BORDER)

    for idx, i in enumerate(range(16, 32)):
        y = main_y + 48 + idx * 32
        is_today = i == today
        val = data.get(i, 0)

        if is_today:
            draw.rectangle([col2_x + 1, y, col2_x + col_w - 1, y + 28], fill=(0, 255, 102, 15))
            draw.line([(col2_x + 1, y), (col2_x + 1, y + 28)], fill=NEON, width=2)

        draw.text((col2_x + 12, y + 6), f"{i}", fill=DARK_GRAY if not is_today else NEON, font=f_md)
        if val != 0:
            txt = f"{f(val)}"
            tw = draw.textlength(txt, font=f_lg)
            color = WHITE if val > 0 else RED
            draw.text((col2_x + col_w - 15 - tw, y + 4), txt, fill=color, font=f_lg)
        else:
            draw.text((col2_x + col_w - 30, y + 6), "—", fill=(30, 30, 30), font=f_md)

    # ── Правая панель ─────────────────────────────────────
    rx = 485
    rw = 385

    # Цель
    gy = main_y + 20
    draw.rounded_rectangle([rx, gy, rx + rw, gy + 105], radius=10, fill=(8, 8, 8), outline=BORDER)
    draw.text((rx + 15, gy + 10), f"ЦЕЛЬ ({sym})", fill=DARK_GRAY, font=f_label)
    draw.text((rx + 15, gy + 28), f"{f(target)}", fill=WHITE, font=f_xl)
    # Progress bar
    bar_y = gy + 72
    bar_w = rw - 30
    draw.rounded_rectangle([rx + 15, bar_y, rx + 15 + bar_w, bar_y + 6], radius=3, fill=(20, 20, 20))
    fill_w = int(bar_w * progress)
    if fill_w > 0:
        draw.rounded_rectangle([rx + 15, bar_y, rx + 15 + fill_w, bar_y + 6], radius=3, fill=NEON)
    # Milestones
    dots_y = bar_y + 14
    dot_count = max(int(target / 500), 1)
    for m in range(dot_count):
        dx = rx + 15 + m * 8
        if dx > rx + rw - 15:
            break
        active = total >= (m + 1) * 500
        draw.ellipse([dx, dots_y, dx + 5, dots_y + 5], fill=NEON if active else (25, 25, 25))
    draw.text((rx + 15, dots_y + 10), f"ОСТАЛОСЬ: {sym}{f(remaining)}", fill=DARK_GRAY, font=f_label)

    # Курс ЦБ
    ky = gy + 115
    draw.rounded_rectangle([rx, ky, rx + rw, ky + 35], radius=8, fill=(8, 8, 8), outline=BORDER)
    draw.text((rx + 15, ky + 10), "КУРС ЦБ:", fill=DARK_GRAY, font=f_label)
    draw.text((rx + rw - 140, ky + 8), f"1$ = {rate:.2f}₽", fill=NEON, font=f_md)

    # ── Стат-блоки ────────────────────────────────────────
    def draw_stat_block(y, label, sumv, profit, pace, is_main=False):
        h = 100
        bg = (5, 15, 8) if is_main else CARD_BG
        brd = NEON if is_main else BORDER
        draw.rounded_rectangle([rx, y, rx + rw, y + h], radius=10, fill=bg, outline=brd if is_main else None)
        draw.line([(rx, y), (rx, y + h)], fill=NEON if is_main else BORDER, width=3)

        draw.text((rx + 15, y + 8), label, fill=DARK_GRAY, font=f_label)
        draw.text((rx + 15, y + 25), f"{sym}{f(sumv)}", fill=NEON, font=f_xxl)
        draw.text((rx + 15, y + 72), f"20%: ", fill=DARK_GRAY, font=f_sm)
        draw.text((rx + 50, y + 72), f"{f(profit)}", fill=NEON, font=f_sm)
        draw.text((rx + 150, y + 72), f"Темп: {sym}{f(pace)}/день", fill=GRAY, font=f_sm)

    pace1 = sum1 / d1 if d1 > 0 else 0
    pace2 = sum2 / d2 if d2 > 0 else 0
    pace_total = total / (d1 + d2) if (d1 + d2) > 0 else 0

    sy = ky + 50
    draw_stat_block(sy, "1 - 15 ЧИСЛО", sum1, sum1 * 0.2, pace1)
    draw_stat_block(sy + 115, "ИТОГО ЗА МЕСЯЦ", total, total * 0.2, pace_total, is_main=True)
    draw_stat_block(sy + 230, "16 - 31 ЧИСЛО", sum2, sum2 * 0.2, pace2)

    # ── Сохраняем ─────────────────────────────────────────
    buf = io.BytesIO()
    img.save(buf, format="PNG", quality=95)
    buf.seek(0)
    return buf
