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
- **Gawk:** the image is downloaded and decoded with OpenCV, and anything larger than 2048 pixels on its longest side is scaled down to that size to keep memory use low. OpenCV's [YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) face detector finds the biggest face along with its eyes and mouth corners, and each eye point is nudged onto the darkest iris-sized spot nearby. Then a funhouse-mirror "bulge" warp (built with OpenCV's `remap`) magnifies the eyes and mouth, sized to the width of the face. The image work runs in a background thread so the bot stays responsive, and the result is sent from memory without writing a file.

## Tech

Python 3.14 · discord.py 2.7 · yt-dlp · FFmpeg · OpenCV (YuNet) · NumPy · python-dotenv · Docker

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
| `FACE_MODEL_PATH` | Optional. Where to keep the face detection model. Defaults to `DiscordBot\models\face_detection_yunet_2023mar.onnx`. |

### 4. Run

```powershell
python MyBot.py
```

You should see `Server commands synced: gawk` and then `<bot name> is online!` in the console. Leave it running while you use the bot, and press **Ctrl+C** to stop it.

If the commands don't appear in Discord:

- Press **Ctrl+R** in Discord to refresh. `/play` and `/stop` are global commands, so they can take a while to show up the first time.
- Check that `GUILD_ID` matches the server you're looking at.
- Check that the invite link included the `applications.commands` scope. If not, generate a new link and authorize it again; you don't need to remove the bot first.

## Deploying to a Linux VM

The bot runs as a Docker container on any Linux VM, x86_64 or ARM64. For a comparison of free hosts (Azure, Oracle Cloud, home hosting) and why GitHub itself can't host it, see [docs/hosting-options.md](docs/hosting-options.md). To run it on a friend's home server, and later move it to a Raspberry Pi, see [docs/home-server-hosting.md](docs/home-server-hosting.md).

### How it works

On every push to `master` that touches the bot, [.github/workflows/deploy.yml](.github/workflows/deploy.yml):

1. Builds the image from the [Dockerfile](Dockerfile) for `linux/amd64` and `linux/arm64`.
2. Pushes it to GitHub Container Registry as `ghcr.io/alexfinn97/discord-adjutant`, tagged `latest` and with the commit SHA.
3. If the `DEPLOY_HOST` secret is set, copies [deploy/compose.yaml](deploy/compose.yaml) to `~/discord-adjutant` on the VM over SSH, pulls the new image and restarts the bot.

You can also run it by hand from the **Actions** tab (**Build and deploy → Run workflow**).

### One-time VM setup

1. Install Docker Engine and the Compose plugin ([instructions](https://docs.docker.com/engine/install/)), then let your user run it: `sudo usermod -aG docker $USER` and log in again.
2. On a VM with 1 GB of RAM, add swap so `/gawk` has room:

   ```bash
   sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
   sudo mkswap /swapfile && sudo swapon /swapfile
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   ```

3. Create `~/discord-adjutant/.env` with the same values as the local `.env` (`DISCORD_TOKEN`, `GUILD_ID`, `FORCE_SYNC`). The token lives only on the VM, not in GitHub or the image.
4. If the GHCR package is private, log the VM in once with a [personal access token](https://github.com/settings/tokens) that has only `read:packages`: `docker login ghcr.io -u <github-username>`. If you make the package public (**Packages → discord-adjutant → Package settings**), skip this.
5. Create an SSH key pair for deploys and add the public key to `~/.ssh/authorized_keys` on the VM.

### Repository secrets

In **Settings → Secrets and variables → Actions**, add:

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST` | The VM's IP address or hostname |
| `DEPLOY_USER` | The SSH user from step 1 |
| `DEPLOY_SSH_KEY` | The private key from step 5 |
| `TS_OAUTH_CLIENT_ID` | Optional. A Tailscale OAuth client ID, for a host with no public address (e.g. on a home network). |
| `TS_OAUTH_SECRET` | Optional. That client's secret. |

Until `DEPLOY_HOST` is set, the workflow only builds and pushes the image. With the two Tailscale secrets set, the deploy job joins your tailnet first, and `DEPLOY_HOST` is the host's Tailscale IP. For the full setup on someone else's home server, see [docs/home-server-hosting.md](docs/home-server-hosting.md).

### Checking on it

```bash
cd ~/discord-adjutant
docker compose ps
docker compose logs -f
```

Only run one copy of the bot per token. Stop the local one before the VM's starts.

## Notes

- The first time `/gawk` runs, the bot downloads OpenCV's YuNet face detection model (about 230 KB) from the [opencv_zoo](https://github.com/opencv/opencv_zoo) repository into `DiscordBot\models`, and checks it against a SHA-256 checksum. That folder is ignored by git.
- `/play` replaces whatever is playing; there's no queue yet.

## License

[MIT](LICENSE)
