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
    Returns (eye, eye, mouth centre, face width) in the image's pixels, or None if there's no face."""
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
            return eye_1, eye_2, mouth, face_width;

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
    eye_1, eye_2, mouth, face_width = face;
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

    # /gawk is a server-only command, so the server's commands always need syncing as well.
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

@bot.tree.command(name="gawk", description="Give the face in the most recent image big eyes and a gaping mouth.")
@app_commands.guilds(GUILD)
async def gawk(interaction: discord.Interaction):
    await interaction.response.defer();

    imageUrl = "";
    async for message in interaction.channel.history(limit=50):
        for attachment in message.attachments:
            if attachment.content_type and "image" in attachment.content_type:
                imageUrl = attachment.url;
                break;

        if not imageUrl:
            for embed in message.embeds:
                if embed.image:
                    imageUrl = embed.image.url;
                    break;

        if imageUrl:
            break;

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


bot.run(TOKEN);
