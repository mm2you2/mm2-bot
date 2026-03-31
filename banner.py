"""Генерирует баннер для MM2 бота."""
import io
from PIL import Image, ImageDraw, ImageFont

def _font(size):
    for name in ["consolab.ttf", "arialbd.ttf", "consola.ttf", "arial.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def create_banner() -> io.BytesIO:
    W, H = 800, 300
    img = Image.new("RGB", (W, H), (5, 5, 5))
    draw = ImageDraw.Draw(img)

    # Сетка
    for x in range(0, W, 25):
        draw.line([(x, 0), (x, H)], fill=(12, 12, 12), width=1)
    for y in range(0, H, 25):
        draw.line([(0, y), (W, y)], fill=(12, 12, 12), width=1)

    # Свечение по центру
    for r in range(200, 0, -2):
        alpha = int(4 * (200 - r) / 200)
        draw.ellipse([W//2 - r, H//2 - r, W//2 + r, H//2 + r],
                     fill=(0, alpha, int(alpha * 0.4)))

    # Логотип
    neon = (0, 255, 102)
    f_logo = _font(72)
    f_sub = _font(16)

    # MM2
    text = "MM2"
    bbox = draw.textbbox((0, 0), text, font=f_logo)
    tw = bbox[2] - bbox[0]
    tx = (W - tw) // 2
    ty = 80

    # Тень
    draw.text((tx + 2, ty + 2), text, fill=(0, 50, 20), font=f_logo)
    draw.text((tx, ty), text, fill=neon, font=f_logo)

    # Подзаголовок
    sub = "INCOME TRACKER"
    bbox2 = draw.textbbox((0, 0), sub, font=f_sub)
    sw = bbox2[2] - bbox2[0]
    draw.text(((W - sw) // 2, ty + 85), sub, fill=(80, 80, 80), font=f_sub)

    # Линия
    lw = 120
    draw.line([(W//2 - lw, ty + 78), (W//2 + lw, ty + 78)], fill=(0, 100, 40), width=1)

    # Точки декор
    for i in range(5):
        x = W//2 - 40 + i * 20
        active = i < 3
        draw.ellipse([x, ty + 115, x + 6, ty + 121],
                     fill=neon if active else (30, 30, 30))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


if __name__ == "__main__":
    b = create_banner()
    with open("mm2_banner.png", "wb") as f:
        f.write(b.read())
    print("Saved mm2_banner.png")
