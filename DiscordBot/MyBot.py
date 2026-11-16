import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import yt_dlp
import asyncio
import cv2
import requests
import numpy as np
import mediapipe as mp

load_dotenv();
TOKEN = os.getenv("DISCORD_TOKEN");
GUILD_ID = os.getenv("GUILD_ID");
FORCE_SYNC = os.getenv("FORCE_SYNC");

async def search_ytdlp_async(query, ydl_opts):
    loop = asyncio.get_running_loop();
    return await loop.run_in_executor(None, lambda: _extract(query, ydl_opts));

def _extract(query, ydl_opts):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(query, download=False);

intents = discord.Intents.default();
intents.message_content = True;

bot = commands.Bot(command_prefix="!", intents=intents);

@bot.event
async def on_ready():
    if FORCE_SYNC == 1:
        guild = discord.Object(id=GUILD_ID);
        bot.tree.copy_global_to(guild=guild);
        await bot.tree.sync(guild=guild);
        print("Force synced commands complete.");
    else:
        await bot.tree.sync();
        print("Commands synced!");
    
    print(f"{bot.user} is online!");

@bot.tree.command(name="magik", description="Fuck up the last posted image.")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def magik(interaction: discord.Interaction):
    await interaction.response.defer();

    imageUrl = "";
    async for message in interaction.channel.history(limit=50):
        for attachment in message.attachments:
            if attachment.content_type and "image" in attachment.content_type:
                imageUrl = attachment.url;
                break;

        for embed in message.embeds:
            if embed.image:
                imageUrl = embed.image.url;
                break;

        if imageUrl:
            break;

    if not imageUrl:
        await interaction.followup.send("No recent images found.");
        return;

    response = requests.get(imageUrl);
    image_bytes = np.asarray(bytearray(response.content), dtype=np.uint8);

    img = cv2.imdecode(image_bytes, cv2.IMREAD_COLOR);

    if img is None:
        await interaction.followup.send("Failed to load image.");
        return;

    mp_face_mesh = mp.solutions.face_mesh
    face_mesh = mp_face_mesh.FaceMesh(static_image_mode=True)

    rgb_image = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    results = face_mesh.process(rgb_image)

    # 1. Detect landmarks (already done)
    landmarks = results.multi_face_landmarks[0]  # assuming 1 face

    # 2. Select points to warp (e.g., eyes, cheeks, mouth)
    # Convert normalized coordinates to pixel coordinates
    points = []
    for lm in landmarks.landmark:
        x = int(lm.x * img.shape[1])
        y = int(lm.y * img.shape[0])
        points.append([x, y])

    # 3. Define destination points for the warp (example: push cheeks out)
    points = np.array(points, dtype=np.float32)
    dest_points = points.copy()

    # Example: move eye points slightly outward
    dest_points[33] += [5, 0]  # left eye landmark
    dest_points[263] += [-5, 0]  # right eye landmark

    # 4. Apply warp using piecewise affine / thin plate spline
    tform = cv2.createThinPlateSplineShapeTransformer()
    matches = [cv2.DMatch(i, i, 0) for i in range(len(points))]
    tform.estimateTransformation(dest_points.reshape(-1,1,2), points.reshape(-1,1,2), matches)
    warped_image = tform.warpImage(img)

    cv2.imwrite("output.png", warped_image);

    await interaction.followup.send(file=discord.File("output.png"));

    return;

@bot.tree.command(name="play", description="Play a song or add it to the queue.")
@app_commands.describe(song_query="Search query")
async def play(interaction: discord.Interaction, song_query: str):
    await interaction.response.defer();

    voice_channel = interaction.user.voice.channel;

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
        "remote_components": "ejs:github"
    };

    query = "ytsearch1:" + song_query;
    results = await search_ytdlp_async(query, ydl_options);
    tracks = results.get("entries", []);

    if tracks is None:
        await interaction.followup.send("No results found.");
        return;

    first_track = tracks[0];
    audio_url = first_track["url"] or first_track["webpage_url"];
    title = first_track.get("title", "Untitled");

    source = discord.FFmpegPCMAudio(
        audio_url,
        before_options = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        options = "-vn",
        executable = "bin\\ffmpeg\\ffmpeg.exe"
    );

    voice_client.play(source);

    await interaction.followup.send(f"Now Playing: {title}");

    return;

@bot.tree.command(name="stop", description="Stop if a song is currently playing.")
async def stop(interaction: discord.Interaction):
    await interaction.response.defer();

    voice_channel = interaction.user.voice.channel;

    if voice_channel is None:
        await interaction.followup.send("You must be in a voice channel.");
        return;
    
    voice_client = interaction.guild.voice_client;

    if voice_client is None:
        await interaction.followup.send("Nothing is currently playing.")

    await voice_client.disconnect();

    await interaction.followup.send("Music stopped.");

    return;


bot.run(TOKEN);