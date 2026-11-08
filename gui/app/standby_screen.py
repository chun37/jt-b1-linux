"""Shared boot and app-closed portrait screen. No hardware or network writes."""
import argparse
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import system_status
from portrait_geometry import image_to_panel

WIDTH, HEIGHT, BAR = 600, 1024, 44
FONT = Path('/opt/jtb1-gui/share/NotoSansCJK-Regular.ttc')
ICON = Path('/opt/jtb1-gui/share/spotify/Spotify_Primary_Logo_RGB_Green.png')
# App-area coordinates (status bar excluded); rendering and hit-testing share bounds.
SPOTIFY_TILE = (24, 132, 288, 396)


def launch_target(x, y):
    left, top, right, bottom = SPOTIFY_TILE
    return 'spotify' if left <= x < right and top <= y < bottom else None


def load_fonts(path=FONT):
    return {size: ImageFont.truetype(str(path), size, index=0) for size in (18, 22, 28)}


def render_body(fonts):
    image = Image.new('RGBA', (WIDTH, HEIGHT - BAR), '#10151c')
    draw = ImageDraw.Draw(image)
    draw.text((24, 30), 'ホーム', font=fonts[28], fill='#f4f6f8')
    draw.text((24, 78), 'アプリを選んでください', font=fonts[18], fill='#8796a6')
    left, top, right, bottom = SPOTIFY_TILE
    draw.rounded_rectangle((left, top, right - 1, bottom - 1), radius=24,
                           fill='#18372c', outline='#2e7656', width=2)
    # Official Spotify Newsroom asset, installed locally; no boot-time download.
    try:
        with Image.open(ICON) as original:
            icon = original.convert('RGBA')
        icon.thumbnail((76, 76), Image.Resampling.LANCZOS)
        image.alpha_composite(icon, (left + 30, top + 34))
    except (OSError, ValueError):
        # A missing image must not prevent the labeled app tile from working.
        pass
    draw.text((left + 24, top + 158), 'Spotify', font=fonts[28], fill='#f4f6f8')
    draw.text((left + 24, top + 210), '音楽を開く', font=fonts[18], fill='#a5c9b8')
    return image


def render_screen(fonts, state=None, now=None):
    image = Image.new('RGBA', (WIDTH, HEIGHT))
    image.paste(render_body(fonts), (0, BAR))
    image.paste(system_status.render_bar(fonts, state or {}, now=now), (0, 0))
    return image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # Before network services are ready, show unknown indicators rather than stale data.
    raw = image_to_panel(render_screen(load_fonts()), 'ccw').tobytes('raw', 'BGRA')
    if len(raw) != 2457600:
        raise ValueError('invalid framebuffer size')
    args.output.write_bytes(raw)


if __name__ == '__main__':
    main()
