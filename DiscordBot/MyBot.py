import hashlib
import io
import os
import time
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import yt_dlp
import asyncio
import cv2
import requests
import threading
import numpy as np

load_dotenv();
TOKEN = os.getenv("DISCORD_TOKEN");
GUILD_ID = os.getenv("GUILD_ID");
FORCE_SYNC = os.getenv("FORCE_SYNC", "0") == "1";

if not TOKEN:
    raise SystemExit("DISCORD_TOKEN is not set. Add it to the .env file.");
if not GUILD_ID or not GUILD_ID.isdigit():
    raise SystemExit("GUILD_ID is not set to a server ID. Add it to the .env file.");

GUILD = discord.Object(id=int(GUILD_ID));

# FFmpeg: use FFMPEG_PATH if set, otherwise the bundled Windows build next to this file,
# otherwise whatever "ffmpeg" is on the PATH.
BASE_DIR = os.path.dirname(os.path.abspath(__file__));
BUNDLED_FFMPEG = os.path.join(BASE_DIR, "bin", "ffmpeg", "ffmpeg.exe");
FFMPEG_PATH = os.getenv("FFMPEG_PATH") or (BUNDLED_FFMPEG if os.path.isfile(BUNDLED_FFMPEG) else "ffmpeg");

# Face detection model for /gawk (OpenCV's YuNet), downloaded on first use if it isn't there yet.
# It runs on any x86_64 or ARM64 CPU; MediaPipe, used before, needs AVX, which some VMs hide.
FACE_MODEL_URL = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx";
FACE_MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4";
FACE_MODEL_PATH = os.getenv("FACE_MODEL_PATH") or os.path.join(BASE_DIR, "models", "face_detection_yunet_2023mar.onnx");
_face_detector = None;
_face_detector_lock = threading.Lock();

# YuNet finds faces best when they're up to a few hundred pixels across, so it looks at
# shrunken copies of the image: first this size, then smaller for close-ups.
FACE_DETECT_SIDES = (640, 320);
FACE_MIN_SCORE = 0.8;

# /gawk shrinks bigger images to this many pixels on their longest side before warping.
# The warp builds several full-size arrays, so a 12 MP phone photo would otherwise peak at ~700 MB.
GAWK_MAX_SIDE = 2048;

async def search_ytdlp_async(query, ydl_opts):
    loop = asyncio.get_running_loop();
    return await loop.run_in_executor(None, lambda: _extract(query, ydl_opts));

def _extract(query, ydl_opts):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(query, download=False);

def _get_face_detector():
    """Creates the YuNet face detector once, downloading its model on first use."""
    global _face_detector;
    if _face_detector is None:
        if not os.path.isfile(FACE_MODEL_PATH):
            response = requests.get(FACE_MODEL_URL, timeout=60);
            response.raise_for_status();
            if hashlib.sha256(response.content).hexdigest() != FACE_MODEL_SHA256:
                raise RuntimeError("Downloaded face model doesn't match its expected checksum.");
            os.makedirs(os.path.dirname(FACE_MODEL_PATH), exist_ok=True);
            with open(FACE_MODEL_PATH, "wb") as f:
                f.write(response.content);

        _face_detector = cv2.FaceDetectorYN.create(FACE_MODEL_PATH, "", (320, 320), FACE_MIN_SCORE);
    return _face_detector;

def _find_face(img):
    """Finds the biggest face in the image.
    Returns (eye, eye, mouth centre, face width, face box as x, y, w, h) in the image's pixels,
    or None if there's no face."""
    height, width = img.shape[:2];
    longest_side = max(height, width);
    scales = sorted({min(1.0, side / longest_side) for side in FACE_DETECT_SIDES}, reverse=True);

    for scale in scales:
        small = img if scale == 1.0 else cv2.resize(
            img, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_AREA);

        # The detector is shared between commands, so only one image is processed at a time.
        with _face_detector_lock:
            detector = _get_face_detector();
            detector.setInputSize((small.shape[1], small.shape[0]));
            _, faces = detector.detect(small);

        if faces is not None and len(faces):
            # Each row: box x, y, w, h, then eye, eye, nose, mouth corner, mouth corner (x, y), then score.
            face = max(faces, key=lambda f: f[2] * f[3]) / scale;
            face_width = face[2];
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY);
            eye_1 = _centre_on_iris(gray, face[4:6], face_width);
            eye_2 = _centre_on_iris(gray, face[6:8], face_width);
            mouth = (face[10:12] + face[12:14]) / 2;
            return eye_1, eye_2, mouth, face_width, face[0:4];

    return None;

def _centre_on_iris(gray, eye, face_width):
    """YuNet's eye points can be a few percent of the face off the pupil, which smears the bulge.
    This moves the point to the darkest iris-sized spot close to it."""
    rx = max(2, round(face_width * 0.07));
    ry = max(2, round(face_width * 0.04));  # shorter vertically, to stay clear of the eyebrow
    x, y = round(float(eye[0])), round(float(eye[1]));
    x0, y0 = max(0, x - rx), max(0, y - ry);
    roi = gray[y0:y + ry + 1, x0:x + rx + 1].astype(np.float32);
    if roi.size == 0:
        return eye;

    roi = cv2.GaussianBlur(roi, (0, 0), max(1.0, face_width * 0.02));
    # Favour spots near the original point, so dark lashes or eyebrows at the edge don't win.
    yy, xx = np.mgrid[y0:y0 + roi.shape[0], x0:x0 + roi.shape[1]];
    roi += 40 * (((xx - eye[0]) / rx) ** 2 + ((yy - eye[1]) / ry) ** 2);
    _, _, (min_x, min_y), _ = cv2.minMaxLoc(roi);
    return np.array([x0 + min_x, y0 + min_y], dtype=np.float32);

def _bulge(img, bulges, strength):
    """Magnifies circular areas of the image, like a funhouse mirror.
    bulges is a list of (centre, radius); strength 0 does nothing, higher values bulge more."""
    height, width = img.shape[:2];
    map_x, map_y = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32));

    for (cx, cy), radius in bulges:
        dx = map_x - cx;
        dy = map_y - cy;
        distance = np.sqrt(dx * dx + dy * dy);
        inside = distance < radius;
        # Inside the circle, sample from closer to the centre, which enlarges it.
        # The scale reaches 1 at the edge, so the bulge blends into the rest of the image.
        scale = np.ones_like(distance);
        scale[inside] = (distance[inside] / radius) ** strength;
        map_x = cx + dx * scale;
        map_y = cy + dy * scale;

    return cv2.remap(img, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT);

def _warp_face(image_data):
    """Warps the first face found in the image. Returns PNG bytes, or None if no face is found.
    Raises ValueError if the image can't be decoded."""
    image_bytes = np.frombuffer(image_data, dtype=np.uint8);
    img = cv2.imdecode(image_bytes, cv2.IMREAD_COLOR);

    if img is None:
        raise ValueError("Image could not be decoded.");

    height, width = img.shape[:2];
    longest_side = max(height, width);
    if longest_side > GAWK_MAX_SIDE:
        scale = GAWK_MAX_SIDE / longest_side;
        new_size = (max(1, round(width * scale)), max(1, round(height * scale)));
        img = cv2.resize(img, new_size, interpolation=cv2.INTER_AREA);

    face = _find_face(img);
    if face is None:
        return None;

    # Bulge the eyes and mouth, sized relative to the width of the face.
    eye_1, eye_2, mouth, face_width, _ = face;
    bulges = [
        (eye_1, face_width * 0.22),
        (eye_2, face_width * 0.22),
        (mouth, face_width * 0.30),
    ];
    warped_image = _bulge(img, bulges, strength=0.7);

    ok, png = cv2.imencode(".png", warped_image);
    if not ok:
        raise ValueError("Warped image could not be encoded.");
    return png.tobytes();

# /soyjak redraws images at this many pixels on their longest side, so the lines come out
# the same thickness whatever size the photo was.
SOYJAK_SIDE = 1024;
# Cutting out the background (GrabCut) is slow, so it works on a smaller copy this size.
SOYJAK_CUTOUT_SIDE = 400;
# Line art: how much an edge has to darken to become a line (0-1), and the smallest blob of
# ink, in pixels, that's kept rather than treated as noise.
SOYJAK_EDGE_THRESHOLD = 0.015;
SOYJAK_MIN_INK_AREA = 120;
SOYJAK_FACE_INK_PERCENT = 10;
# Roughly how many stubble strokes overlap any one spot of the jaw.
SOYJAK_STUBBLE_DENSITY = 1.2;
# Line thickness, as a share of the image's longest side.
SOYJAK_LINE_SIGMA = 0.0022;

def _resize_longest_side(img, side):
    height, width = img.shape[:2];
    scale = side / max(height, width);
    interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC;
    return cv2.resize(img, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=interpolation);

def _face_axes(face):
    """Returns (point between the eyes, unit vector to the face's right, unit vector down
    towards its chin, tilt in degrees), so drawings can follow a tilted head."""
    eye_1, eye_2 = face[0], face[1];
    eye_left, eye_right = sorted((eye_1, eye_2), key=lambda e: e[0]);
    across = eye_right - eye_left;
    tilt = float(np.degrees(np.arctan2(across[1], across[0])));
    right = across / max(1e-6, float(np.linalg.norm(across)));
    down = np.array([-right[1], right[0]], dtype=np.float32);
    return (eye_left + eye_right) / 2, right, down, tilt;

def _person_mask(img, face):
    """Guesses which pixels belong to the person, using GrabCut seeded from the face.
    Returns a uint8 mask the size of img: 1 for the person, 0 for the background."""
    height, width = img.shape[:2];
    scale = min(1.0, SOYJAK_CUTOUT_SIDE / max(height, width));
    small = img if scale == 1.0 else _resize_longest_side(img, SOYJAK_CUTOUT_SIDE);
    small_height, small_width = small.shape[:2];
    x, y, box_width, box_height = (float(v) * scale for v in face[4]);
    centre = np.array([x + box_width / 2, y + box_height / 2], dtype=np.float32);
    _, right, down, tilt = _face_axes(face);

    def at(across, along):
        """A point measured from the middle of the face, in face widths and heights, following its tilt."""
        p = centre + right * across * box_width + down * along * box_height;
        return (round(float(p[0])), round(float(p[1])));

    # The person is probably inside a bust shape around the face: a head with room for hair,
    # then a neck widening into shoulders down past the bottom of the image. If GrabCut can't
    # tell them from the background, the cut-out is still this shape rather than a box.
    # The middle of the face surely is them.
    mask = np.full((small_height, small_width), cv2.GC_BGD, dtype=np.uint8);
    cv2.ellipse(mask, at(0, -0.05), (round(box_width * 0.75), round(box_height * 0.95)), tilt, 0, 360, cv2.GC_PR_FGD, -1);
    bust = np.array([at(-0.45, 0.5), at(0.45, 0.5), at(1.3, 1.1), at(1.3, 8), at(-1.3, 8), at(-1.3, 1.1)], dtype=np.int32);
    cv2.fillPoly(mask, [bust], cv2.GC_PR_FGD);
    cv2.ellipse(mask, at(0, 0.05), (max(1, round(box_width * 0.42)), max(1, round(box_height * 0.48))), tilt, 0, 360, cv2.GC_FGD, -1);

    # With hardly any background left to learn from (a close-up), GrabCut can't work,
    # so fall back to the bust shape itself; it still blanks the corners around the head.
    bust_shape = np.isin(mask, (cv2.GC_FGD, cv2.GC_PR_FGD)).astype(np.uint8);
    if np.count_nonzero(mask == cv2.GC_BGD) >= small.size // 3 * 0.01:
        try:
            # GrabCut starts from random guesses; a fixed seed makes the same picture cut out the same way.
            cv2.setRNGSeed(0);
            cv2.grabCut(small, mask, None, np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64), 5, cv2.GC_INIT_WITH_MASK);
        except cv2.error:
            mask = None;
    else:
        mask = None;

    person = bust_shape if mask is None else np.isin(mask, (cv2.GC_FGD, cv2.GC_PR_FGD)).astype(np.uint8);
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5));
    person = cv2.morphologyEx(person, cv2.MORPH_OPEN, kernel);
    person = cv2.morphologyEx(person, cv2.MORPH_CLOSE, kernel);

    # Keep only the biggest piece, with any holes in it filled.
    contours, _ = cv2.findContours(person, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE);
    if not contours:
        return np.ones((height, width), dtype=np.uint8);
    person = np.zeros_like(person);
    cv2.drawContours(person, [max(contours, key=cv2.contourArea)], -1, 1, -1);

    # Scale back up with smooth edges rather than blocky ones.
    person = cv2.resize(person.astype(np.float32), (width, height), interpolation=cv2.INTER_LINEAR);
    person = cv2.GaussianBlur(person, (0, 0), max(1.0, max(height, width) * 0.004));
    return (person > 0.5).astype(np.uint8);

def _line_art(img, face_box=None):
    """Turns a photo into black lines on white, like a drawing. Returns a bool array, True for ink.
    With a face box, the line threshold adapts so the face always gets some lines."""
    # Even out the contrast first, so dark or washed-out photos still get lines.
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB);
    lab[:, :, 0] = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(lab[:, :, 0]);
    img = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR);
    # Flatten skin, fabric and foliage into plain areas, keeping only the bigger shapes' edges.
    flat = cv2.edgePreservingFilter(img, flags=cv2.RECURS_FILTER, sigma_s=40, sigma_r=0.35);
    gray = cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255;

    # Difference of Gaussians: negative just on the dark side of an edge, which is where a pen goes.
    sigma = max(img.shape[:2]) * SOYJAK_LINE_SIGMA;
    edges = cv2.GaussianBlur(gray, (0, 0), sigma) - cv2.GaussianBlur(gray, (0, 0), sigma * 1.6);
    threshold = SOYJAK_EDGE_THRESHOLD;
    if face_box is not None:
        # Measured on the middle of the face (eyes, nose, mouth), not the background at its sides.
        x, y, w, h = (float(v) for v in face_box);
        x0, x1 = max(0, round(x + w * 0.2)), max(0, round(x + w * 0.8));
        y0, y1 = max(0, round(y + h * 0.25)), max(0, round(y + h * 0.9));
        face_edges = edges[y0:y1, x0:x1];
        if face_edges.size:
            # Low-contrast faces: lower the bar until this share of the face is ink.
            threshold = min(threshold, max(0.002, -float(np.percentile(face_edges, SOYJAK_FACE_INK_PERCENT))));
    ink = (edges < -threshold).astype(np.uint8);

    # Drop specks of noise.
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8);
    keep = stats[:, cv2.CC_STAT_AREA] >= SOYJAK_MIN_INK_AREA;
    keep[0] = False;  # label 0 is the paper
    ink = keep[labels].astype(np.float32);

    # Round off the jagged pixel edges so the lines look inked rather than traced.
    ink = cv2.GaussianBlur(ink, (0, 0), max(1.0, max(img.shape[:2]) * 0.0012));
    return ink > 0.4;

def _open_mouth(img, mouth, face_width):
    """Looks for the dark hole of an open mouth around YuNet's mouth point.
    Returns (centre, half width, half height) of it, or None if the mouth looks closed."""
    height, width = img.shape[:2];
    reach = face_width * 0.3;
    x0, y0 = max(0, round(mouth[0] - reach)), max(0, round(mouth[1] - reach * 0.7));
    x1, y1 = min(width, round(mouth[0] + reach)), min(height, round(mouth[1] + reach * 1.2));
    if x1 - x0 < 5 or y1 - y0 < 5:
        return None;

    gray = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY);
    gray = cv2.GaussianBlur(gray, (0, 0), max(1.0, face_width * 0.01));
    # The inside of an open mouth is the darkest thing around it, well below the skin.
    dark = ((gray <= np.percentile(gray, 12)) & (gray < np.median(gray) * 0.6)).astype(np.uint8);
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(dark, connectivity=8);

    best = None;
    for i in range(1, count):
        if stats[i, cv2.CC_STAT_AREA] < (face_width * 0.06) ** 2:
            continue;
        centroid = centroids[i] + (x0, y0);
        distance = float(np.linalg.norm(centroid - mouth));
        if distance < face_width * 0.15 and (best is None or distance < best[0]):
            best = (distance, centroid, stats[i, cv2.CC_STAT_WIDTH] / 2, stats[i, cv2.CC_STAT_HEIGHT] / 2);

    return None if best is None else best[1:];

def _shadows(img, face, person):
    """Finds the darkest parts of the face (an open mouth, pupils, deep shadows), to fill in
    solid black like the spot blacks in an ink drawing. Returns a bool array the size of img."""
    height, width = img.shape[:2];
    face_width = face[3];
    x, y, w, h = (float(v) for v in face[4]);
    x0, x1 = max(0, round(x - w * 0.05)), min(width, round(x + w * 1.05));
    y0, y1 = max(0, round(y)), min(height, round(y + h * 1.1));
    shadows = np.zeros((height, width), dtype=bool);
    if x1 - x0 < 5 or y1 - y0 < 5:
        return shadows;

    gray = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY);
    gray = cv2.GaussianBlur(gray, (0, 0), max(1.0, face_width * 0.006));
    # Only the middle of the face (eyes, nostrils, mouth), so dark background or shadows
    # along the jaw stay as lines rather than becoming black blobs.
    middle = np.zeros((height, width), dtype=np.uint8);
    cv2.ellipse(middle, (round(x + w / 2), round(y + h * 0.55)), (round(w * 0.36), round(h * 0.4)),
                _face_axes(face)[3], 0, 360, 1, -1);
    inside = (person[y0:y1, x0:x1] > 0) & (middle[y0:y1, x0:x1] > 0);
    if not inside.any():
        return shadows;
    skin = gray[inside];
    dark = (gray <= min(np.percentile(skin, 4), np.median(skin) * 0.45)) & inside;

    # Keep blobs, not thin slivers along edges.
    count, labels, stats, _ = cv2.connectedComponentsWithStats(dark.astype(np.uint8), connectivity=8);
    keep = stats[:, cv2.CC_STAT_AREA] >= (face_width * 0.04) ** 2;
    keep[0] = False;
    shadows[y0:y1, x0:x1] = keep[labels];
    return shadows;

def _draw_soyjak_features(canvas, img, face, person, rng):
    """Draws stubble and forehead wrinkles onto canvas (0 ink, 255 paper).
    img is the photo, used to keep the stubble off the real mouth."""
    _, _, mouth, face_width, _ = face;
    eye_middle, right, down, tilt = _face_axes(face);

    def point(origin, along_right, along_down):
        p = origin + right * along_right * face_width + down * along_down * face_width;
        return (round(float(p[0])), round(float(p[1])));

    # Where the mouth is, so the stubble stays off it: the open hole if there is one,
    # otherwise a lip-sized area around YuNet's mouth point.
    opening = _open_mouth(img, mouth, face_width);
    if opening is None:
        mouth_centre = point(mouth, 0, 0);
        mouth_axes = (face_width * 0.2, face_width * 0.1);
    else:
        centre, half_width, half_height = opening;
        mouth_centre = (round(float(centre[0])), round(float(centre[1])));
        mouth_axes = (max(half_width, face_width * 0.1), max(half_height, face_width * 0.06));

    # Stubble: short strokes on the chin, jaw and upper lip, avoiding the mouth.
    stubble = np.zeros(canvas.shape, dtype=np.uint8);
    jaw_centre = point(mouth, 0, 0.02);
    cv2.ellipse(stubble, jaw_centre, (round(face_width * 0.45), round(face_width * 0.42)), tilt, 0, 360, 1, -1);
    # Not above the nostrils.
    nose_line = eye_middle + down * face_width * 0.42;
    cv2.ellipse(stubble, mouth_centre, (round(mouth_axes[0] * 1.35), round(mouth_axes[1] * 1.25)), tilt, 0, 360, 0, -1);
    ys, xs = np.nonzero(stubble & (person > 0));
    if len(xs):
        above_nose = ((np.stack([xs, ys], axis=1) - nose_line) @ down) < 0;
        xs, ys = xs[~above_nose], ys[~above_nose];
    if len(xs):
        # Enough strokes to cover the same share of the jaw on any size of face.
        # Hairs grow with the face, up to a cap, so close-ups get fine stubble rather than sticks.
        stroke = min(face_width * 0.02, max(canvas.shape) * 0.009);
        count = min(len(xs), round(len(xs) * SOYJAK_STUBBLE_DENSITY / stroke ** 2));
        picks = rng.choice(len(xs), size=count, replace=False);
        hair = 1 if stroke < 8 else 2;
        for i in picks:
            angle = rng.uniform(0, np.pi);
            length = stroke * rng.uniform(0.4, 1.2);
            dx, dy = np.cos(angle) * length / 2, np.sin(angle) * length / 2;
            cv2.line(canvas, (round(xs[i] - dx), round(ys[i] - dy)), (round(xs[i] + dx), round(ys[i] + dy)), 0, hair, cv2.LINE_AA);

    # Forehead wrinkles: a few wavy lines above the brows, uneven like hand-drawn ones,
    # as thick as the rest of the line art.
    line = max(2, round(max(canvas.shape) * 0.003));
    for i in range(3):
        half_length = rng.uniform(0.12, 0.2);
        shift = rng.uniform(-0.04, 0.04);
        height = -0.22 - i * 0.065 + rng.uniform(-0.01, 0.01);
        phase = rng.uniform(0, 2 * np.pi);
        along = np.linspace(-half_length, half_length, 24);
        # Arched up in the middle, with a small wobble along the way.
        rise = height - 0.03 * (1 - (along / half_length) ** 2) + 0.008 * np.sin(along * 40 + phase);
        points = np.array([point(eye_middle, shift + a, r) for a, r in zip(along, rise)], dtype=np.int32);
        cv2.polylines(canvas, [points], False, 0, line, cv2.LINE_AA);

def _soyjakify(image_data):
    """Redraws the image as a black-and-white soyjak. Returns PNG bytes.
    Without a face it's just line art. Raises ValueError if the image can't be decoded."""
    img = cv2.imdecode(np.frombuffer(image_data, dtype=np.uint8), cv2.IMREAD_COLOR);
    if img is None:
        raise ValueError("Image could not be decoded.");
    img = _resize_longest_side(img, SOYJAK_SIDE);

    face = _find_face(img);
    ink = _line_art(img, None if face is None else face[4]);

    if face is not None:
        person = _person_mask(img, face);
        ink &= person > 0;
        # Outline the cut-out, except along the edges of the picture.
        outline = cv2.morphologyEx(person, cv2.MORPH_GRADIENT, np.ones((5, 5), np.uint8)) > 0;
        outline[:2, :] = outline[-2:, :] = False;
        outline[:, :2] = outline[:, -2:] = False;
        ink |= outline;
        shadows = _shadows(img, face, person);
        ink |= shadows;

    canvas = np.where(ink, 0, 255).astype(np.uint8);

    if face is not None:
        # Same image, same stubble.
        rng = np.random.default_rng(int.from_bytes(hashlib.sha256(image_data).digest()[:8], "little"));
        _draw_soyjak_features(canvas, img, face, person & ~shadows, rng);

    ok, png = cv2.imencode(".png", canvas);
    if not ok:
        raise ValueError("Soyjak image could not be encoded.");
    return png.tobytes();

def _user_voice_channel(interaction: discord.Interaction):
    voice_state = getattr(interaction.user, "voice", None);
    return voice_state.channel if voice_state else None;

# The bot leaves voice after this long with nothing playing. It also leaves straight away
# once nobody else is in its channel (see on_voice_state_update).
IDLE_DISCONNECT_SECONDS = 180;
_idle_timers = {};  # guild ID -> task that disconnects when the time is up

def _cancel_idle_timer(guild_id):
    task = _idle_timers.pop(guild_id, None);
    if task:
        task.cancel();

def _start_idle_timer(guild):
    _cancel_idle_timer(guild.id);
    _idle_timers[guild.id] = asyncio.create_task(_leave_when_idle(guild));

async def _leave_when_idle(guild):
    await asyncio.sleep(IDLE_DISCONNECT_SECONDS);
    _idle_timers.pop(guild.id, None);
    voice_client = guild.voice_client;
    # A newer /play may have started since this timer did; only leave if it's really quiet.
    if voice_client and not (voice_client.is_playing() or voice_client.is_paused()):
        await voice_client.disconnect();

intents = discord.Intents.default();
intents.message_content = True;

bot = commands.Bot(command_prefix="!", intents=intents);

@bot.event
async def on_ready():
    if FORCE_SYNC:
        # Copy the global commands to the server too, so changes show up straight away.
        bot.tree.copy_global_to(guild=GUILD);
    else:
        await bot.tree.sync();
        print("Global commands synced.");

    # /gawk and /soyjak are server-only commands, so the server's commands always need syncing as well.
    synced = await bot.tree.sync(guild=GUILD);
    print(f"Server commands synced: {', '.join(sorted(c.name for c in synced))}");

    print(f"{bot.user} is online!");

@bot.event
async def on_voice_state_update(member, before, after):
    voice_client = member.guild.voice_client;
    if voice_client is None or not voice_client.is_connected():
        return;

    if member.id == bot.user.id:
        if after.channel is None:
            # Someone disconnected the bot by hand.
            _cancel_idle_timer(member.guild.id);
        return;

    # Only check when someone leaves the bot's channel.
    if before.channel != voice_client.channel or after.channel == voice_client.channel:
        return;

    if not any(not m.bot for m in voice_client.channel.members):
        _cancel_idle_timer(member.guild.id);
        await voice_client.disconnect();

async def _latest_image_url(channel):
    """Returns the URL of the newest image attachment or embed in the last 50 messages, or ""."""
    async for message in channel.history(limit=50):
        for attachment in message.attachments:
            if attachment.content_type and "image" in attachment.content_type:
                return attachment.url;

        for embed in message.embeds:
            if embed.image:
                return embed.image.url;

    return "";

@bot.tree.command(name="gawk", description="Give the face in the most recent image big eyes and a gaping mouth.")
@app_commands.guilds(GUILD)
async def gawk(interaction: discord.Interaction):
    await interaction.response.defer();

    imageUrl = await _latest_image_url(interaction.channel);
    if not imageUrl:
        await interaction.followup.send("No recent images found.");
        return;

    try:
        response = await asyncio.to_thread(requests.get, imageUrl, timeout=15);
        response.raise_for_status();
        # Image processing is CPU-heavy, so keep it off the event loop.
        png = await asyncio.to_thread(_warp_face, response.content);
    except (requests.RequestException, ValueError):
        await interaction.followup.send("Failed to load image.");
        return;
    except Exception as e:
        print(f"/gawk failed: {e!r}");
        await interaction.followup.send("Something went wrong warping that image.");
        return;

    if png is None:
        await interaction.followup.send("No face found in the most recent image.");
        return;

    await interaction.followup.send(file=discord.File(io.BytesIO(png), filename="gawk.png"));

@bot.tree.command(name="soyjak", description="Redraw the most recent image as a soyjak.")
@app_commands.guilds(GUILD)
async def soyjak(interaction: discord.Interaction):
    await interaction.response.defer();

    imageUrl = await _latest_image_url(interaction.channel);
    if not imageUrl:
        await interaction.followup.send("No recent images found.");
        return;

    try:
        response = await asyncio.to_thread(requests.get, imageUrl, timeout=15);
        response.raise_for_status();
        # Image processing is CPU-heavy, so keep it off the event loop.
        png = await asyncio.to_thread(_soyjakify, response.content);
    except (requests.RequestException, ValueError):
        await interaction.followup.send("Failed to load image.");
        return;
    except Exception as e:
        print(f"/soyjak failed: {e!r}");
        await interaction.followup.send("Something went wrong drawing that image.");
        return;

    await interaction.followup.send(file=discord.File(io.BytesIO(png), filename="soyjak.png"));

@bot.tree.command(name="play", description="Play a song in your voice channel.")
@app_commands.describe(song_query="Search query")
async def play(interaction: discord.Interaction, song_query: str):
    await interaction.response.defer();

    voice_channel = _user_voice_channel(interaction);

    if voice_channel is None:
        await interaction.followup.send("You must be in a voice channel.");
        return;

    voice_client = interaction.guild.voice_client;

    if voice_client is None:
        voice_client = await voice_channel.connect();
    elif voice_channel != voice_client.channel:
        await voice_client.move_to(voice_channel);

    ydl_options = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "default_search": "ytsearch",
        "quiet": True,
        "remote_components": ["ejs:github"],
        # YouTube needs a JavaScript runtime; use Deno or Node, whichever is installed.
        "js_runtimes": {"deno": {}, "node": {}}
    };

    query = "ytsearch1:" + song_query;
    results = await search_ytdlp_async(query, ydl_options);
    tracks = (results or {}).get("entries") or [];

    if not tracks:
        await interaction.followup.send("No results found.");
        return;

    first_track = tracks[0];
    audio_url = first_track.get("url") or first_track.get("webpage_url");
    title = first_track.get("title", "Untitled");

    # YouTube's stream links only start working a few seconds after they're issued, and refuse
    # (403) any earlier request, so wait until the time yt-dlp reports. Live streams have none.
    available_at = first_track.get("available_at");
    if available_at:
        await asyncio.sleep(max(0, available_at - time.time()));

    source = discord.FFmpegPCMAudio(
        audio_url,
        before_options = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        options = "-vn",
        executable = FFMPEG_PATH
    );

    # Replace whatever is playing; there is no queue yet.
    if voice_client.is_playing() or voice_client.is_paused():
        voice_client.stop();

    loop = asyncio.get_running_loop();
    guild = interaction.guild;

    def finished(error):
        if error:
            print(f"/play stopped with an error: {error!r}");
        # This runs on discord.py's audio thread, so start the idle timer on the event loop.
        loop.call_soon_threadsafe(_start_idle_timer, guild);

    _cancel_idle_timer(guild.id);
    voice_client.play(source, after=finished);

    await interaction.followup.send(f"Now Playing: {title}");

@bot.tree.command(name="stop", description="Stop the music and leave the voice channel.")
async def stop(interaction: discord.Interaction):
    await interaction.response.defer();

    voice_client = interaction.guild.voice_client;

    if voice_client is None:
        await interaction.followup.send("Nothing is currently playing.");
        return;

    voice_client.stop();
    await voice_client.disconnect();
    _cancel_idle_timer(interaction.guild.id);

    await interaction.followup.send("Music stopped.");


if __name__ == "__main__":
    bot.run(TOKEN);
