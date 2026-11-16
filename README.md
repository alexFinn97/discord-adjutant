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

#### Create the bot

1. In the [Discord Developer Portal](https://discord.com/developers/applications), click **New Application** and give it a name.
2. Go to **Bot**. Under **Privileged Gateway Intents**, turn on **Message Content Intent** and save. The bot won't connect without it.

#### Get the token

1. On the **Bot** page, under **Token**, click **Reset Token** and confirm (with your two-factor code if you have it on).
2. Copy the token straight away, because Discord only shows it once. It goes in `.env` as `DISCORD_TOKEN` (step 3).

Treat the token like a password. If it ever leaks, reset it here; the old one stops working immediately.

#### Install it on your server

1. Go to **OAuth2 → URL Generator**.
2. Under **Scopes**, tick `bot` and `applications.commands`.
3. Under **Bot Permissions**, tick:

   | Permission | Needed for |
   | --- | --- |
   | View Channels | Seeing the channel a command is used in |
   | Send Messages | Replying to commands |
   | Attach Files | Posting the `/gawk` image |
   | Read Message History | Finding the latest image for `/gawk` |
   | Connect | Joining voice for `/play` |
   | Speak | Playing audio |

4. Copy the **Generated URL** at the bottom, open it in your browser, choose your server and click **Authorize**. You need the **Manage Server** permission on that server.

#### Get your server ID

1. In Discord, go to **User Settings → Advanced** and turn on **Developer Mode**.
2. Right-click your server's icon and choose **Copy Server ID**. It goes in `.env` as `GUILD_ID` (step 3).

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

You should see `Server commands synced: gawk` and then `<bot name> is online!` in the console. Leave it running while you use the bot, and press **Ctrl+C** to stop it.

If the commands don't appear in Discord:

- Press **Ctrl+R** in Discord to refresh. `/play` and `/stop` are global commands, so they can take a while to show up the first time.
- Check that `GUILD_ID` matches the server you're looking at.
- Check that the invite link included the `applications.commands` scope. If not, generate a new link and authorize it again; you don't need to remove the bot first.

## Notes

- The first time `/gawk` runs, the bot downloads MediaPipe's face landmark model (about 3.6 MB) from Google into `DiscordBot\models`. That folder is ignored by git.
- `/play` replaces whatever is playing; there's no queue yet.

## License

[MIT](LICENSE)
