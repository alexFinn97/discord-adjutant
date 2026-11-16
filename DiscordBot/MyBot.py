import io
import os
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
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python import vision

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

# Face landmark model for /gawk, downloaded on first use if it isn't there yet.
FACE_MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task";
FACE_MODEL_PATH = os.getenv("FACE_MODEL_PATH") or os.path.join(BASE_DIR, "models", "face_landmarker.task");
_face_landmarker = None;
_face_landmarker_lock = threading.Lock();

async def search_ytdlp_async(query, ydl_opts):
    loop = asyncio.get_running_loop();
    return await loop.run_in_executor(None, lambda: _extract(query, ydl_opts));

def _extract(query, ydl_opts):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(query, download=False);

def _get_face_landmarker():
    """Creates the MediaPipe face landmarker once, downloading its model on first use."""
    global _face_landmarker;
    if _face_landmarker is None:
        if not os.path.isfile(FACE_MODEL_PATH):
            os.makedirs(os.path.dirname(FACE_MODEL_PATH), exist_ok=True);
            response = requests.get(FACE_MODEL_URL, timeout=60);
            response.raise_for_status();
            with open(FACE_MODEL_PATH, "wb") as f:
                f.write(response.content);

        options = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=FACE_MODEL_PATH),
            running_mode=vision.RunningMode.IMAGE,
            num_faces=1
        );
        _face_landmarker = vision.FaceLandmarker.create_from_options(options);
    return _face_landmarker;

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

    rgb_image = np.ascontiguousarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB));
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_image);

    # The landmarker is shared between commands, so only one image is processed at a time.
    with _face_landmarker_lock:
        result = _get_face_landmarker().detect(mp_image);

    if not result.face_landmarks:
        return None;

    # Use the first face found and convert its normalised landmarks to pixel coordinates.
    landmarks = result.face_landmarks[0];
    points = np.array(
        [[lm.x * img.shape[1], lm.y * img.shape[0]] for lm in landmarks],
        dtype=np.float32
    );

    # Bulge the eyes and mouth, sized relative to the width of the face.
    face_width = np.linalg.norm(points[454] - points[234]);  # cheek to cheek
    bulges = [
        (points[468], face_width * 0.22),                    # one iris centre
        (points[473], face_width * 0.22),                    # the other iris centre
        ((points[13] + points[14]) / 2, face_width * 0.30),  # centre of the mouth
    ];
    warped_image = _bulge(img, bulges, strength=0.7);

    ok, png = cv2.imencode(".png", warped_image);
    if not ok:
        raise ValueError("Warped image could not be encoded.");
    return png.tobytes();

def _user_voice_channel(interaction: discord.Interaction):
    voice_state = getattr(interaction.user, "voice", None);
    return voice_state.channel if voice_state else None;

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

    source = discord.FFmpegPCMAudio(
        audio_url,
        before_options = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        options = "-vn",
        executable = FFMPEG_PATH
    );

    # Replace whatever is playing; there is no queue yet.
    if voice_client.is_playing() or voice_client.is_paused():
        voice_client.stop();

    voice_client.play(source);

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

    await interaction.followup.send("Music stopped.");


bot.run(TOKEN);
