import os
import cv2
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from PIL import Image, ImageDraw, ImageFont
import io
from brain import generate_solar_image


def extract_frame_from_video(video_path: str) -> np.ndarray:
    """Extract the best frame from a video."""
    cap = cv2.VideoCapture(video_path)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.set(cv2.CAP_PROP_POS_FRAMES, total_frames // 3)
    ret, frame = cap.read()
    cap.release()
    if not ret:
        raise ValueError("Could not extract frame from video")
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def draw_canopy_on_image(
    original: Image.Image,
    area_m2: float,
    panel_count: int,
    space_type: str,
) -> Image.Image:
    """
    Draw a flat horizontal pergola-style solar canopy overlay on the original image.
    Coverage is auto-calculated based on space size.
    """
    img = original.copy().convert("RGBA")
    width, height = img.size

    # Auto-calculate coverage based on area
    if area_m2 < 30:
        coverage = 0.45
    elif area_m2 < 80:
        coverage = 0.60
    else:
        coverage = 0.72

    # Flat pergola coordinates - canopy is HORIZONTAL (not sloped)
    canopy_y = int(height * 0.30)
    canopy_thickness = int(height * 0.06)
    canopy_bottom_y = canopy_y + canopy_thickness
    ground_y = int(height * 0.78)

    left_x = int(width * (0.5 - coverage / 2))
    right_x = int(width * (0.5 + coverage / 2))

    # Slight perspective - far edge is narrower
    persp = int(width * 0.04)
    far_left_x = left_x + persp
    far_right_x = right_x - persp

    post_width = max(10, width // 55)
    post_color = (40, 40, 40, 255)

    post_positions = [
        (left_x, canopy_bottom_y, ground_y),
        (right_x - post_width, canopy_bottom_y, ground_y),
        (far_left_x, canopy_y, ground_y),           # back posts go all the way to ground
        (far_right_x - post_width, canopy_y, ground_y),
    ]

    # --- Draw back posts first ---
    post_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    post_draw = ImageDraw.Draw(post_layer)
    for px, py_top, py_bottom in post_positions[2:]:
        post_draw.rectangle([px, py_top, px + post_width, py_bottom],
                            fill=post_color, outline=(80, 80, 80, 255), width=2)
    img = Image.alpha_composite(img, post_layer)

    # --- Draw flat canopy surface ---
    canopy_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    canopy_draw = ImageDraw.Draw(canopy_layer)

    top_face = [
        (far_left_x, canopy_y),
        (far_right_x, canopy_y),
        (right_x, canopy_bottom_y),
        (left_x, canopy_bottom_y),
    ]
    canopy_draw.polygon(top_face, fill=(20, 60, 160, 230))

    cols = max(3, int(np.sqrt(panel_count * 1.8)))
    rows = max(2, int(np.ceil(panel_count / cols)))

    for r in range(1, rows):
        t = r / rows
        lx = far_left_x + t * (left_x - far_left_x)
        ly = canopy_y + t * (canopy_bottom_y - canopy_y)
        rx = far_right_x + t * (right_x - far_right_x)
        ry = canopy_y + t * (canopy_bottom_y - canopy_y)
        canopy_draw.line([(int(lx), int(ly)), (int(rx), int(ry))],
                         fill=(120, 190, 255, 255), width=2)

    for c in range(1, cols):
        t = c / cols
        tx = far_left_x + t * (far_right_x - far_left_x)
        bx = left_x + t * (right_x - left_x)
        canopy_draw.line([(int(tx), int(canopy_y)), (int(bx), int(canopy_bottom_y))],
                         fill=(120, 190, 255, 255), width=2)

    shimmer = [
        (far_left_x + (far_right_x - far_left_x) // 5, canopy_y + 2),
        (far_left_x + (far_right_x - far_left_x) * 2 // 5, canopy_y + 2),
        (left_x + (right_x - left_x) * 2 // 5, canopy_bottom_y - 3),
        (left_x + (right_x - left_x) // 5, canopy_bottom_y - 3),
    ]
    canopy_draw.polygon(shimmer, fill=(255, 255, 255, 22))

    front_face = [
        (left_x, canopy_bottom_y),
        (right_x, canopy_bottom_y),
        (right_x, canopy_bottom_y + int(height * 0.025)),
        (left_x, canopy_bottom_y + int(height * 0.025)),
    ]
    canopy_draw.polygon(front_face, fill=(15, 40, 120, 160))
    img = Image.alpha_composite(img, canopy_layer)

    # --- Draw beams ---
    rafter_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    rafter_draw = ImageDraw.Draw(rafter_layer)
    rafter_color = (40, 40, 40, 255)
    beam_w = max(3, width // 130)

    rafter_draw.line([(far_left_x, canopy_y), (far_right_x, canopy_y)],
                     fill=rafter_color, width=beam_w)
    rafter_draw.line([(left_x, canopy_bottom_y), (right_x, canopy_bottom_y)],
                     fill=rafter_color, width=beam_w + 1)
    rafter_draw.line([(far_left_x, canopy_y), (left_x, canopy_bottom_y)],
                     fill=rafter_color, width=beam_w)
    rafter_draw.line([(far_right_x, canopy_y), (right_x, canopy_bottom_y)],
                     fill=rafter_color, width=beam_w)

    for c in range(1, cols):
        t = c / cols
        tx = int(far_left_x + t * (far_right_x - far_left_x))
        bx = int(left_x + t * (right_x - left_x))
        rafter_draw.line([(tx, canopy_y), (bx, canopy_bottom_y)],
                         fill=rafter_color, width=max(2, beam_w - 1))
    img = Image.alpha_composite(img, rafter_layer)

    # --- Draw front posts on top ---
    front_post_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    front_post_draw = ImageDraw.Draw(front_post_layer)
    for px, py_top, py_bottom in post_positions[:2]:
        front_post_draw.rectangle([px, py_top, px + post_width, py_bottom],
                                   fill=post_color, outline=(80, 80, 80, 255), width=2)
    img = Image.alpha_composite(img, front_post_layer)

    background = Image.new("RGB", img.size, (26, 26, 46))
    background.paste(img, mask=img.split()[3])
    return background


def draw_solar_diagram(
    area_m2: float,
    panel_count: int,
    space_type: str,
) -> Image.Image:
    """Draw a clean top-down solar panel layout diagram."""
    panel_w = 1.0
    panel_h = 1.7
    cols = max(2, int(np.sqrt(panel_count * 1.2)))
    rows = int(np.ceil(panel_count / cols))
    gap = 0.25
    margin = 1.2

    total_w = cols * panel_w + (cols - 1) * gap + 2 * margin
    total_h = rows * panel_h + (rows - 1) * gap + 2 * margin

    fig, ax = plt.subplots(figsize=(7, 7))
    fig.patch.set_facecolor('#12122a')
    ax.set_facecolor('#12122a')

    # Draw subtle ground grid
    for gx in np.arange(0, total_w, 1.0):
        ax.axvline(gx, color='#1e1e3a', linewidth=0.5, zorder=0)
    for gy in np.arange(0, total_h, 1.0):
        ax.axhline(gy, color='#1e1e3a', linewidth=0.5, zorder=0)

    panel_num = 0
    for row in range(rows):
        for col in range(cols):
            if panel_num >= panel_count:
                break
            x = margin + col * (panel_w + gap)
            y = margin + row * (panel_h + gap)

            # Shadow
            shadow = patches.FancyBboxPatch(
                (x + 0.06, y - 0.06), panel_w, panel_h,
                boxstyle="round,pad=0.02",
                linewidth=0, facecolor='#000000', alpha=0.35, zorder=1
            )
            ax.add_patch(shadow)

            # Panel body
            panel = patches.FancyBboxPatch(
                (x, y), panel_w, panel_h,
                boxstyle="round,pad=0.02",
                linewidth=1.2, edgecolor='#5bc8fa',
                facecolor='#1a5cb0', zorder=2
            )
            ax.add_patch(panel)

            # Cell lines
            for cr in range(1, 6):
                y_line = y + cr * (panel_h / 6)
                ax.plot([x + 0.05, x + panel_w - 0.05], [y_line, y_line],
                        color='#5bc8fa', linewidth=0.35, alpha=0.6, zorder=3)
            for cc in range(1, 4):
                x_line = x + cc * (panel_w / 4)
                ax.plot([x_line, x_line], [y + 0.05, y + panel_h - 0.05],
                        color='#5bc8fa', linewidth=0.35, alpha=0.6, zorder=3)

            # Shine
            shine = patches.FancyBboxPatch(
                (x + 0.08, y + panel_h * 0.55), panel_w * 0.35, panel_h * 0.28,
                boxstyle="round,pad=0.01",
                linewidth=0, facecolor='white', alpha=0.07, zorder=4
            )
            ax.add_patch(shine)
            panel_num += 1

    ax.set_xlim(-0.3, total_w + 0.3)
    ax.set_ylim(-0.3, total_h + 0.3)
    ax.set_aspect('equal')
    ax.axis('off')

    ax.set_title('Top-Down Layout', color='#f4a800', fontsize=14,
                 fontweight='bold', pad=10)

    fig.text(0.5, 0.02,
             f'{panel_count} Panels  •  {area_m2:.0f} m²  •  {space_type}',
             ha='center', color='#aaaaaa', fontsize=10)

    # Dimension line
    dim_y = -0.15
    ax.annotate('', xy=(total_w - margin, dim_y),
                 xytext=(margin, dim_y),
                 arrowprops=dict(arrowstyle='<->', color='#f4a800', lw=1.2))
    ax.text(total_w / 2, dim_y - 0.25,
            f'{(cols * panel_w + (cols - 1) * gap):.1f}m wide',
            color='#f4a800', fontsize=8, ha='center')

    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight',
                facecolor='#12122a', dpi=150)
    plt.close()
    buf.seek(0)
    return Image.open(buf).copy()


def create_side_by_side_visualization(
    image_path: str,
    area_m2: float,
    panel_count: int,
    space_type: str,
    address: str = "",
    output_dir: str = None,
) -> dict:
    """
    Create a solar canopy visualization overlaid on the original space image,
    with a top-down diagram side by side.

    Args:
        image_path: Path to image or video file
        area_m2: Area of the space in square meters from analysis
        panel_count: Number of panels from analysis
        space_type: Type of space (backyard, courtyard, field etc)
        output_dir: Directory to save output image

    Returns:
        Dict with output path and status
    """
    try:
        image_path = image_path.replace("\\", "/")

        video_extensions = ['.mp4', '.mov', '.avi', '.mkv', '.webm']
        ext = os.path.splitext(image_path)[1].lower()

        if ext in video_extensions:
            frame_array = extract_frame_from_video(image_path)
            original_pil = Image.fromarray(frame_array)
        else:
            original_pil = Image.open(image_path).convert("RGB")

        # Try AI image generation first (same model path used by solar_mockup.py).
        # If generation fails, fall back to deterministic local canopy rendering.
        canopy_image = None
        render_engine = "local_overlay"
        prompt_location = address.strip() or f"{space_type} residential property"
        ai_bytes = generate_solar_image(prompt_location, panel_count)
        if ai_bytes:
            try:
                canopy_image = Image.open(io.BytesIO(ai_bytes)).convert("RGB")
                render_engine = "ai_model"
            except Exception:
                canopy_image = None

        if canopy_image is None:
            canopy_image = draw_canopy_on_image(
                original_pil, area_m2, panel_count, space_type
            )
        diagram_pil = draw_solar_diagram(area_m2, panel_count, space_type)

        target_height = 600
        canopy_w = int(canopy_image.width * target_height / canopy_image.height)
        diag_w = int(diagram_pil.width * target_height / diagram_pil.height)

        canopy_image = canopy_image.resize((canopy_w, target_height), Image.LANCZOS)
        diagram_pil = diagram_pil.resize((diag_w, target_height), Image.LANCZOS)

        padding = 24
        label_height = 50
        total_w = canopy_w + diag_w + padding * 3
        total_h = target_height + padding * 2 + label_height

        canvas = Image.new("RGB", (total_w, total_h), color="#0e0e1e")
        canvas.paste(canopy_image, (padding, padding + label_height))
        canvas.paste(diagram_pil, (canopy_w + padding * 2, padding + label_height))

        fig, ax = plt.subplots(figsize=(total_w / 100, total_h / 100), dpi=100)
        fig.patch.set_facecolor('#0e0e1e')
        ax.axis('off')
        ax.imshow(np.array(canvas))

        ax.axvline(x=canopy_w + padding * 1.5, color='#333355',
                   linewidth=1.5, alpha=0.8)

        ax.text(canopy_w / 2, label_height / 2,
                '🏗️  Solar Canopy Visualization',
                color='white', fontsize=14, fontweight='bold',
                ha='center', va='center', transform=ax.transData)

        ax.text(canopy_w + padding * 2 + diag_w / 2, label_height / 2,
                '☀️  Top-Down Panel Layout',
                color='#f4a800', fontsize=14, fontweight='bold',
                ha='center', va='center', transform=ax.transData)

        if output_dir is None:
            output_dir = os.path.dirname(image_path)

        output_path = os.path.join(output_dir, "solar_canopy_visualization.png")
        plt.savefig(output_path, bbox_inches='tight',
                    facecolor='#0e0e1e', dpi=150)
        plt.close()

        return {
            "status": "success",
            "output_path": output_path,
            "message": f"Solar canopy visualization saved to {output_path}",
            "panel_count": panel_count,
            "area_m2": area_m2,
            "canopy_coverage": "auto-scaled to space size",
            "render_engine": render_engine,
        }

    except Exception as e:
        return {
            "status": "failed",
            "error": str(e),
            "message": f"Failed to create visualization: {str(e)}"
        }
