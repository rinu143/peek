"""Generate 32-bit ARGB PNG tile icon frames and fallback BMP for Peek Credential Provider."""
import math
import os
from PIL import Image, ImageDraw

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "credential_provider", "res")
TARGET_SIZE = 96
SCALE = 4
CANVAS_SIZE = TARGET_SIZE * SCALE  # 384x384
CENTER = CANVAS_SIZE // 2

# Palette (calm, premium minimalist palette)
COLOR_BG_CIRCLE = (255, 255, 255, 18)     # Soft subtle translucent tile disc
COLOR_BORDER = (255, 255, 255, 55)        # Subtle border ring
COLOR_FG_WHITE = (255, 255, 255, 235)     # Primary foreground stroke
COLOR_ACCENT_TEAL = (52, 199, 120, 245)   # Success smile & accent
COLOR_MUTED_SLATE = (210, 215, 225, 220)  # Calm failure/retry stroke
COLOR_AMBER = (255, 190, 60, 235)         # Liveness/prompt accent

def create_base_canvas():
    img = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Draw soft circular background disc
    r = int(CANVAS_SIZE * 0.44)
    draw.ellipse([CENTER - r, CENTER - r, CENTER + r, CENTER + r],
                 fill=COLOR_BG_CIRCLE, outline=COLOR_BORDER, width=int(2.5 * SCALE))
    return img, draw

def draw_face_oval(draw, color=COLOR_FG_WHITE, width=3):
    # Minimalist head silhouette / face oval
    hw = int(48 * SCALE)
    hh = int(60 * SCALE)
    top = CENTER - int(28 * SCALE)
    draw.ellipse([CENTER - hw // 2, top, CENTER + hw // 2, top + hh],
                 outline=color, width=int(width * SCALE))

def draw_eyes(draw, color=COLOR_FG_WHITE, radius=3.2, y_offset=-4, x_offset=12):
    ey = CENTER + int(y_offset * SCALE)
    ex1 = CENTER - int(x_offset * SCALE)
    ex2 = CENTER + int(x_offset * SCALE)
    r = int(radius * SCALE)
    draw.ellipse([ex1 - r, ey - r, ex1 + r, ey + r], fill=color)
    draw.ellipse([ex2 - r, ey - r, ex2 + r, ey + r], fill=color)

def draw_corner_brackets(draw, color=COLOR_FG_WHITE, radius=34, arm=10, width=3):
    r = int(radius * SCALE)
    a = int(arm * SCALE)
    w = int(width * SCALE)
    left, right = CENTER - r, CENTER + r
    top, bottom = CENTER - r, CENTER + r
    # Top-left
    draw.line([(left, top + a), (left, top), (left + a, top)], fill=color, width=w)
    # Top-right
    draw.line([(right - a, top), (right, top), (right, top + a)], fill=color, width=w)
    # Bottom-left
    draw.line([(left, bottom - a), (left, bottom), (left + a, bottom)], fill=color, width=w)
    # Bottom-right
    draw.line([(right - a, bottom), (right, bottom), (right, bottom - a)], fill=color, width=w)

def save_scaled(img, filename):
    resized = img.resize((TARGET_SIZE, TARGET_SIZE), Image.Resampling.LANCZOS)
    out_path = os.path.join(OUTPUT_DIR, filename)
    resized.save(out_path, format="PNG")
    print(f"Saved {out_path}")
    return resized

def generate_frames():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 1. IDLE: Calm neutral face silhouette with eyes and subtle neutral mouth line
    img, draw = create_base_canvas()
    draw_face_oval(draw, COLOR_FG_WHITE, width=2.5)
    draw_eyes(draw, COLOR_FG_WHITE, radius=3.0)
    # subtle neutral mouth
    my = CENTER + int(14 * SCALE)
    draw.line([(CENTER - int(7 * SCALE), my), (CENTER + int(7 * SCALE), my)],
              fill=COLOR_FG_WHITE, width=int(2.2 * SCALE))
    idle_img = save_scaled(img, "frame_idle.png")

    # Save peek_logo.bmp as 32-bit BMP from idle frame
    bmp_path = os.path.join(OUTPUT_DIR, "peek_logo.bmp")
    idle_img.save(bmp_path, format="BMP")
    print(f"Saved {bmp_path}")

    # 2. SEARCHING: 3 pulse frames cycling radial radar scan rings
    search_radii = [24, 33, 42]
    search_alphas = [200, 160, 110]
    for idx, (rad, alpha) in enumerate(zip(search_radii, search_alphas), 1):
        img, draw = create_base_canvas()
        draw_face_oval(draw, (255, 255, 255, 180), width=2.2)
        draw_eyes(draw, (255, 255, 255, 180), radius=2.8)
        # Pulse ring
        pulse_color = (255, 255, 255, alpha)
        r = int(rad * SCALE)
        draw.ellipse([CENTER - r, CENTER - r, CENTER + r, CENTER + r],
                     outline=pulse_color, width=int(2.5 * SCALE))
        save_scaled(img, f"frame_search_{idx}.png")

    # 3. FACE FOUND: Solid focus corner brackets + locked face
    img, draw = create_base_canvas()
    draw_corner_brackets(draw, COLOR_FG_WHITE, radius=35, arm=11, width=3.0)
    draw_face_oval(draw, COLOR_FG_WHITE, width=2.8)
    draw_eyes(draw, COLOR_FG_WHITE, radius=3.2)
    save_scaled(img, "frame_face_found.png")

    # 4. VERIFYING: Biometric landmark points across face
    img, draw = create_base_canvas()
    draw_face_oval(draw, (255, 255, 255, 170), width=2.2)
    draw_eyes(draw, COLOR_FG_WHITE, radius=3.2)
    # 5 biometric verification nodes (eyes, nose bridge, mouth corners)
    landmarks = [
        (CENTER, CENTER + int(4 * SCALE)),                     # Nose
        (CENTER - int(9 * SCALE), CENTER + int(14 * SCALE)),   # Mouth left
        (CENTER + int(9 * SCALE), CENTER + int(14 * SCALE)),   # Mouth right
        (CENTER, CENTER - int(18 * SCALE)),                    # Forehead
    ]
    for lx, ly in landmarks:
        lr = int(2.5 * SCALE)
        draw.ellipse([lx - lr, ly - lr, lx + lr, ly + lr], fill=COLOR_FG_WHITE)
    # Subtle dashed connect lines
    draw.line([(CENTER - int(12 * SCALE), CENTER - int(4 * SCALE)),
               (CENTER, CENTER + int(4 * SCALE)),
               (CENTER + int(12 * SCALE), CENTER - int(4 * SCALE))],
              fill=(255, 255, 255, 120), width=int(1.5 * SCALE))
    save_scaled(img, "frame_verifying.png")

    # 5. LIVENESS: Face with dynamic motion/glance indicator arcs
    img, draw = create_base_canvas()
    draw_face_oval(draw, COLOR_FG_WHITE, width=2.5)
    draw_eyes(draw, COLOR_AMBER, radius=3.4)  # Attentive amber glance
    # Glance guidance arc (left and right motion arcs)
    arc_r = int(38 * SCALE)
    draw.arc([CENTER - arc_r, CENTER - arc_r, CENTER + arc_r, CENTER + arc_r],
             start=-40, end=40, fill=COLOR_AMBER, width=int(2.5 * SCALE))
    draw.arc([CENTER - arc_r, CENTER - arc_r, CENTER + arc_r, CENTER + arc_r],
             start=140, end=220, fill=COLOR_AMBER, width=int(2.5 * SCALE))
    save_scaled(img, "frame_liveness.png")

    # 6. SUCCESS: Smile curve + teal checkmark badge
    img, draw = create_base_canvas()
    draw_face_oval(draw, COLOR_ACCENT_TEAL, width=3.0)
    # Happy eyes (upward curved arcs or sparkling dots)
    ey = CENTER - int(6 * SCALE)
    ex1 = CENTER - int(12 * SCALE)
    ex2 = CENTER + int(12 * SCALE)
    er = int(5 * SCALE)
    draw.arc([ex1 - er, ey - er, ex1 + er, ey + er], start=180, end=360,
             fill=COLOR_ACCENT_TEAL, width=int(3 * SCALE))
    draw.arc([ex2 - er, ey - er, ex2 + er, ey + er], start=180, end=360,
             fill=COLOR_ACCENT_TEAL, width=int(3 * SCALE))
    # Elegant wide smile arc
    my = CENTER + int(4 * SCALE)
    mr = int(12 * SCALE)
    draw.arc([CENTER - mr, my, CENTER + mr, my + mr], start=10, end=170,
             fill=COLOR_ACCENT_TEAL, width=int(3.2 * SCALE))
    save_scaled(img, "frame_success.png")

    # 7. FAILURE: Calm, understated sad curve (no alarming red, calm slate monochrome)
    img, draw = create_base_canvas()
    draw_face_oval(draw, COLOR_MUTED_SLATE, width=2.5)
    draw_eyes(draw, COLOR_MUTED_SLATE, radius=3.0)
    # Inverted mouth arc (calm, gentle downward curve)
    my = CENTER + int(15 * SCALE)
    mr = int(9 * SCALE)
    draw.arc([CENTER - mr, my - mr, CENTER + mr, my], start=200, end=340,
             fill=COLOR_MUTED_SLATE, width=int(2.5 * SCALE))
    save_scaled(img, "frame_failure.png")

    # 8. RETRY: Face outline with circular reload arrow
    img, draw = create_base_canvas()
    draw_face_oval(draw, (255, 255, 255, 170), width=2.0)
    draw_eyes(draw, (255, 255, 255, 170), radius=2.5)
    # Circular retry arrow around the face
    arr_r = int(36 * SCALE)
    draw.arc([CENTER - arr_r, CENTER - arr_r, CENTER + arr_r, CENTER + arr_r],
             start=30, end=300, fill=COLOR_FG_WHITE, width=int(3.0 * SCALE))
    # Arrow head at angle 30 deg
    tip_angle = math.radians(30)
    tx = CENTER + arr_r * math.cos(tip_angle)
    ty = CENTER + arr_r * math.sin(tip_angle)
    # Arrowhead points
    draw.polygon([
        (tx, ty),
        (tx + int(8 * SCALE), ty - int(4 * SCALE)),
        (tx + int(4 * SCALE), ty + int(7 * SCALE)),
    ], fill=COLOR_FG_WHITE)
    save_scaled(img, "frame_retry.png")

if __name__ == "__main__":
    generate_frames()
    print("All tile asset frames generated successfully.")
