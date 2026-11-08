"""Pure coordinate transforms; no framebuffer or input-device writes.

'cw' means holding the physical tablet 90 degrees clockwise from its current
landscape orientation. Text remains horizontal on a 600 x 1024 logical canvas.
"""
PANEL_WIDTH = 1024
PANEL_HEIGHT = 600
WIDTH = PANEL_HEIGHT
HEIGHT = PANEL_WIDTH


def _check(x, y, width, height):
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError("coordinate outside canvas")


def panel_to_portrait(x, y, orientation="cw"):
    _check(x, y, PANEL_WIDTH, PANEL_HEIGHT)
    if orientation == "cw":
        return PANEL_HEIGHT - 1 - y, x
    if orientation == "ccw":
        return y, PANEL_WIDTH - 1 - x
    raise ValueError("unknown orientation")


def portrait_to_panel(x, y, orientation="cw"):
    _check(x, y, WIDTH, HEIGHT)
    if orientation == "cw":
        return y, PANEL_HEIGHT - 1 - x
    if orientation == "ccw":
        return PANEL_WIDTH - 1 - y, x
    raise ValueError("unknown orientation")


def image_to_panel(image, orientation="cw"):
    from PIL import Image
    if image.size != (WIDTH, HEIGHT):
        raise ValueError("portrait image must be 600 x 1024")
    if orientation not in ("cw", "ccw"):
        raise ValueError("unknown orientation")
    method = Image.Transpose.ROTATE_90 if orientation == "cw" else Image.Transpose.ROTATE_270
    return image.transpose(method)
