# discord-adjutant

A Discord bot running on a private server with my friends, written in Python with [discord.py](https://discordpy.readthedocs.io/). It adds random and silly slash commands when needed: so far, a music player and a face-warping image command.

## Commands

| Command | What it does |
| --- | --- |
| `/play <song_query>` | Joins your voice channel, searches YouTube for the query and streams the top result. |
| `/stop` | Disconnects the bot from voice, which stops playback. |
| `/gawk` | Finds the most recent image in the channel (within the last 50 messages), detects a face and blows up its eyes and mouth, then posts the result. Only registered on the server set in `GUILD_ID`. |

## How it works

- **Slash commands** use discord.py's `app_commands` and are synced with Discord when the bot starts.
- **Music:** [yt-dlp](https://github.com/yt-dlp/yt-dlp) searches YouTube and resolves the audio stream URL. The search runs in a thread-pool executor so it doesn't block the bot's event loop. FFmpeg then streams the audio into the voice channel.
- **Gawk:** the image is downloaded and decoded with OpenCV. The [MediaPipe Face Landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker) finds the face landmarks, then a funhouse-mirror "bulge" warp (built with OpenCV's `remap`) magnifies the eyes and mouth, sized to the width of the face. The image work runs in a background thread so the bot stays responsive, and the result is sent from memory without writing a file.

## Tech

Python 3.14 · discord.py 2.7 · yt-dlp · FFmpeg · OpenCV · MediaPipe 0.10 · NumPy · python-dotenv

## Running it locally

These steps are for Windows. A Windows build of FFmpeg is included in `DiscordBot\bin\ffmpeg`; on other systems, install FFmpeg and the bot will use the one on your PATH.

You'll need Python 3.14 and either [Node.js](https://nodejs.org/) or [Deno](https://deno.com/) installed. yt-dlp uses one of them to read YouTube's player code.

### 1. Set up the bot in Discord

1. Create an application and a bot user in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Under **Bot**, turn on the **Message Content Intent**.
3. Invite the bot to your server with the `bot` and `applications.commands` scopes, and the **Connect** and **Speak** voice permissions.

### 2. Install

```powershell
git clone https://github.com/alexFinn97/discord-adjutant.git
cd discord-adjutant\DiscordBot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```
### 3. Configure

Create a `.env` file in the `DiscordBot` folder. It's already in `.gitignore`, so it won't be committed.

```env
DISCORD_TOKEN=your-bot-token
GUILD_ID=your-server-id
FORCE_SYNC=0
```

| Variable | Purpose |
| --- | --- |
| `DISCORD_TOKEN` | The bot token from the Developer Portal. Keep it secret. |
| `GUILD_ID` | The ID of your server. `/gawk` is registered to this server only. |
| `FORCE_SYNC` | `1` copies all commands to your server and syncs them there, so changes appear straight away. `0` (the default) syncs them globally, which can take a while to show up. |
| `FFMPEG_PATH` | Optional. Path to an FFmpeg executable, if you don't want the bundled one or the one on your PATH. |
| `FACE_MODEL_PATH` | Optional. Where to keep the face landmark model. Defaults to `DiscordBot\models\face_landmarker.task`. |

### 4. Run

```powershell
python MyBot.py
```

You should see `<bot name> is online!` in the console.

## Notes

- The first time `/gawk` runs, the bot downloads MediaPipe's face landmark model (about 3.6 MB) from Google into `DiscordBot\models`. That folder is ignored by git.
- `/play` replaces whatever is playing; there's no queue yet.

## License

[MIT](LICENSE)
