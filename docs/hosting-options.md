# Free hosting options

Researched November 2026. Free tiers change often, so check each provider's page before you sign up.

## What the bot needs from a host

- **An always-on process.** The bot keeps a gateway websocket open to Discord and joins voice channels over UDP. That rules out anything serverless or request-driven (Azure Functions, App Service Free, Cloudflare Workers, HTTP interactions endpoints).
- **About 1 GB of RAM.** `/gawk` loads MediaPipe and OpenCV. Expect roughly 400–700 MB in use, so a 1 GB VM works only with swap.
- **An IP that YouTube accepts.** `/play` resolves streams with yt-dlp. YouTube often answers cloud and datacenter IPs with *"Sign in to confirm you're not a bot"*. This is the biggest risk for every cloud option below.
- **x86_64 or ARM64.** Both work from mediapipe 1.x onwards. 0.10.32 had no Linux ARM64 wheel.

## Comparison

| Option | Spec | Free for | Fit | Watch-outs |
|---|---|---|---|---|
| GitHub Actions / Codespaces | n/a | n/a | ✗ as a host | Banned by GitHub's terms (see below). Use Actions for CI/CD only. |
| Azure free account VM | B1s (1 vCPU, 1 GB, x86), B2ats v2 (2 vCPU, 1 GB, AMD), B2pts v2 (2 vCPU, 1 GB, ARM): 750 hrs/month each | 12 months, new accounts only | ✓ | Card required. Pay-as-you-go after 12 months. 1 GB needs swap. YouTube may block the IP. |
| Azure for Students | $100 credit, no card | Each year you're a verified student | ✓ if eligible | It's credit, not a free size, so a VM spends it down. |
| Azure Container Apps | 180,000 vCPU-s + 360,000 GiB-s free/month | Always | ~ | An always-on replica uses more than the grant in a month: cheap, not free. |
| Azure App Service F1 | Shared CPU, 1 GB | Always | ~ (workaround) | Needs a web wrapper and a keep-alive ping. 165 MB/day outbound (~2.5 hrs of music), 60 CPU min/day. Container and voice support unconfirmed. |
| Azure Functions | Per-execution | Always (monthly grant) | ✗ | Can't hold a gateway or voice connection. Could serve `/gawk` only, via an HTTP interactions endpoint. |
| Oracle Always Free: Ampere A1 (ARM) | 2 OCPU, 12 GB total | Indefinitely | ✓ best spec | Not GitHub/Azure. Idle instances can be reclaimed. Capacity is often short in popular regions. |
| Oracle Always Free: E2.1.Micro (x86) | 1/8 OCPU, 1 GB, 50 Mbps | Indefinitely | ~ | Very weak CPU, so `/gawk` will be slow. |
| Home host | Pi 5 or N150 mini PC | £128–£331 to buy, then £0.71–£1.63/month electricity | ✓ for `/play` | The only option where YouTube sees a home IP. See [Home hosting costs](#home-hosting-costs). |

## GitHub

GitHub can't host the bot. Its [additional product terms](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features) forbid using Actions or Codespaces "as part of a serverless application", for "any other activity unrelated to the production, testing, deployment, or publication of the software project", and (for Codespaces) "to host any kind of production-facing application". Restart loops of 6-hour Actions jobs break those terms.

What GitHub does well here, free:

- **Actions** builds the Docker image and deploys it to whichever VM you choose (`.github/workflows/deploy.yml`).
- **GitHub Container Registry (GHCR)** stores the image.
- **GitHub Student Developer Pack** (if you're eligible) includes Azure for Students and other cloud credits.

## Azure

### Free account VM (12 months)

The [Azure free account](https://azure.microsoft.com/en-us/pricing/free-services) includes 750 hours a month for 12 months of each of B1s, B2pts v2 (ARM) and B2ats v2 (AMD). 750 hours covers one VM running all month. It also gives $200 of credit for the first 30 days.

- A card is needed to sign up. After 12 months the VM bills at pay-as-you-go rates unless you delete it. B1s is roughly $8–10/month.
- Only one eligible VM runs free at a time. The 750 hours are shared, not per VM.
- Check **Cost Management** in the first week. Public IPs and disks have their own meters, and it isn't clear from Microsoft's pages that the public IP is covered.
- All three sizes have 1 GB RAM: add a 2 GB swap file.

### Azure for Students

[Azure for Students](https://azure.microsoft.com/en-us/free/students) gives $100 of credit with no card, renewable each year you're a verified student. Community reports on renewals are mixed. A B1s VM uses roughly $8–10 of it a month, so $100 covers most of a year.

### Container Apps

The [consumption plan](https://azure.microsoft.com/en-us/pricing/details/container-apps/) grants 180,000 vCPU-seconds and 360,000 GiB-seconds a month. A bot needs one replica always running (min replicas = 1). At the smallest useful size (0.25 vCPU, 0.5 GiB) that's about 650,000 vCPU-seconds a month, well over the grant. Idle-rate billing lowers the cost but doesn't make it free. Not recommended if the goal is to pay nothing.

### App Service F1

F1 is free indefinitely, but it's built for web apps that answer HTTP requests, not long-running processes. The [App Service limits](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/azure-subscription-service-limits#app-service-limits) for F1 are:

- 60 CPU minutes a day, and 3 in any 5-minute window. Go over and the app stops until the quota resets.
- 1 GB memory, shared CPU, a 165 MB bandwidth cap and 5 WebSockets.
- No Always On. The app unloads after a period with no HTTP requests, which would drop the bot's connection to Discord.

#### Running the bot on F1 with a keep-alive (not built yet)

This is a workaround, not what F1 is designed for, but it costs nothing.

**How it would work**

1. **Web wrapper.** The bot also serves a tiny web app, so App Service has HTTP traffic to keep it alive. discord.py already depends on aiohttp, so `MyBot.py` can run an aiohttp server alongside the bot with no new dependency. It would serve:
   - `/health`: returns 200 and whether the bot is connected to Discord.
   - Any other path, including App Service's `/robots933456.txt` warm-up request: replies with a status code. App Service restarts the container if the warm-up request gets no reply at all.

   `bot.run()` becomes an async entry point that starts the web server, then `bot.start()`.
2. **Same Docker image.** App Service pulls `ghcr.io/alexfinn97/discord-adjutant` as a custom container. Set `WEBSITES_PORT` to the wrapper's port, and give the bot `DISCORD_TOKEN` and `GUILD_ID` as App Service application settings instead of a `.env` file.
3. **Two layers of keep-alive:**
   - **Self-ping.** The bot requests its own public `https://<app>.azurewebsites.net/health` URL every 5 minutes. The request goes through Azure's front end, so it should count as traffic. It can't wake the app once it has been unloaded or restarted, though.
   - **GitHub Actions cron (backup).** A scheduled workflow requests `/health` every 10 minutes, off the top of the hour. This also wakes the app after a restart or redeploy, since without Always On the container only starts when a request arrives. GitHub says scheduled runs can be delayed or dropped under load. On public repos, schedules are switched off after 60 days with no repository activity, so re-enable the workflow if that happens.
4. **Deploys.** The existing build job pushes the image. A deploy step then tells App Service to pull it and restart, using either a publish profile secret or Azure login.

**What would limit it**

| Limit | Effect |
| --- | --- |
| 165 MB outbound/day per region | The main constraint. Go over and the app is stopped (HTTP 403) until midnight UTC. Music at discord.py's default 128 kbps uses about 1 MB/min, so roughly 2.5 hours a day. Lowering it to 64 kbps roughly doubles that, at slightly lower quality. Each `/gawk` image is 1–2 MB. |
| 60 CPU min/day, 3 per 5 min | Probably fine. An idle bot uses almost nothing, `/gawk` takes a few CPU seconds, and an hour of music is a few CPU minutes. Go over and the app stops until the quota resets. |
| 1 GB RAM | Enough for MediaPipe. |
| No Always On | Platform restarts leave the bot offline until the next ping arrives. |

**Unknowns to test before relying on it**

- **Custom containers on F1.** A 2019 article says F1 didn't support them. Community posts from 2021–22 show them working. Try creating the app before writing any code.
- **Outbound UDP for voice.** Microsoft doesn't document it. If it's blocked, `/play` can't join voice at all, though `/gawk` would still work.
- **YouTube blocking Azure's IP.** The same risk as on a VM.

### Functions

Functions run per request, so they can't hold the bot's gateway or voice connection. The one way to use them is Discord's **HTTP interactions endpoint**: Discord sends each slash command to a function URL instead of over the gateway.

- **`/gawk` could work this way.** The function would read channel history and post the image through Discord's REST API.
- **`/play` can't.** Voice needs a live gateway connection.
- **The 3-second deadline is a risk.** Discord needs a reply within 3 seconds. A cold start of a Python function that loads MediaPipe can take longer, so the command would sometimes fail.
- **It means two codebases.** The bot would split into an interactions app plus something else for music.

The Linux Consumption plan is being retired (no new apps through the portal since September 2025, fully retired after 30 September 2028). Its replacement, Flex Consumption, has a smaller monthly free grant.

## Oracle Cloud Always Free

Oracle isn't GitHub or Azure, but it's the strongest free-forever option. See [Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm).

- **Ampere A1 (ARM):** 1,500 OCPU hours and 9,000 GB hours a month. That's 2 OCPUs and 12 GB of memory, as one VM or two. Oracle cut this from 4 OCPU / 24 GB in 2026.
- **E2.1.Micro (x86):** up to two VMs with 1/8 OCPU and 1 GB each.
- **Idle reclamation:** Oracle may reclaim an Always Free instance if, over 7 days, CPU (95th percentile), network and memory (A1 only) are all under 20%. A small bot that's mostly idle can trip this. Community posts say upgrading to Pay As You Go (Always Free resources stay free) stops reclamation, but Oracle's docs don't say so.
- New A1 capacity is often short in popular regions, so creation can fail with "out of capacity" until you retry.

## Home hosting costs

A small always-on machine at home costs money up front and a little electricity, but it's the one option where `/play` gets a home IP that YouTube accepts. Both options below run the same multi-arch Docker image as the cloud hosts.

Prices were checked on 18 November 2026 and include VAT. Pi parts are from [The Pi Hut](https://thepihut.com/products/raspberry-pi-5) and mini PCs from [Amazon.co.uk](https://www.amazon.co.uk/s?k=N150+mini+pc). Memory shortages pushed Pi and RAM prices up repeatedly in 2026, so expect them to move.

### Setup: Raspberry Pi 5

| Part | 2GB build | 4GB build (recommended) |
|---|---|---|
| Raspberry Pi 5 | £74.40 | £105.60 |
| Official 27 W USB-C power supply | £11.50 | £11.50 |
| Official Pi 5 case (built-in fan, so no separate cooler) | £9.60 | £9.60 |
| 64 GB microSD with Raspberry Pi OS | £30.60 | £30.60 |
| Cat5e Ethernet cable, 2 m | £1.50 | £1.50 |
| **Total** (plus delivery) | **£127.60** | **£158.80** |

The bot uses about 0.4–0.7 GB of RAM, plus whatever the OS and Docker use. 2 GB works but is tight, and 4 GB leaves headroom. The 8 GB board (£168, so £221.20 built) is more than the bot needs.

### Setup: Intel N150 mini PC

These come with RAM, an SSD, a power supply and Windows 11, which you'd replace with Ubuntu Server or Debian.

| Model | Price | With Ethernet cable |
|---|---|---|
| GN5 N150, 12 GB / 256 GB (lesser-known brand) | £215.99 | **£217.49** |
| Beelink Mini S13 N150, 16 GB / 500 GB | £299.00 | **£300.50** |
| Beelink EQ14 N150, 16 GB / 500 GB (power supply built in) | £329.00 | **£330.50** |

### Running costs

At **27.862p/kWh**, left on 24/7:

> cost per year = watts × 8,760 hours ÷ 1,000 × £0.27862

The bot is idle most of the time, so typical draw is close to idle draw. Jeff Geerling [measured a Pi 5 at 3.2 W idle](https://jeffgeerling.com/blog/2025/intel-n100-better-value-raspberry-pi). [Reviews](https://www.cnx-software.com/2024/04/29/raspberry-pi-5-intel-n100-mini-pc-comparison-features-benchmarks-price/) put N100/N150 mini PCs at about 6–10 W idle, depending on the model and power settings.

| Machine | Draw | kWh per year | Per year | Per month |
|---|---|---|---|---|
| Pi 5: low | 3 W | 26.3 | £7.32 | £0.61 |
| Pi 5: **typical** | 3.5 W | 30.7 | **£8.54** | **£0.71** |
| Pi 5: busy | 5 W | 43.8 | £12.20 | £1.02 |
| N150: low | 6 W | 52.6 | £14.64 | £1.22 |
| N150: **typical** | 8 W | 70.1 | **£19.53** | **£1.63** |
| N150: busy | 12 W | 105.1 | £29.29 | £2.44 |

**Five years, setup plus typical electricity:**

| Machine | Total |
|---|---|
| Pi 5 2GB | £170 |
| Pi 5 4GB | £201 |
| GN5 N150 | £315 |
| Beelink Mini S13 N150 | £398 |

Other costs:
- **Broadband and standing charge:** nothing extra, because you already pay for both.
- **microSD:** constant writes can wear out the Pi's card after a few years, so budget about £30 to replace it. The mini PC's SSD doesn't have this problem.

### Pi or mini PC

- **Raspberry Pi 5:** cheapest to buy and to run. It's ARM, which the image supports. The microSD card is the weak point.
- **N150 mini PC:** x86, with an SSD and more RAM and CPU headroom, so `/gawk` runs faster. It costs about £10 a year more in electricity.

### Running the bot at home

- **Software:** install Docker, then use the same `deploy/compose.yaml` and `.env` as on a VM (see the README).
- **No static IP or port forwarding needed.** The bot only makes outbound connections: to Discord's gateway, to voice servers and to YouTube.
- **To SSH in from your PC,** use the machine's hostname (e.g. `ssh pi@raspberrypi.local`). A DHCP reservation on the router is optional.
- **Deploys:** the GitHub deploy job can join your [Tailscale](https://tailscale.com/) tailnet and SSH in without any ports being opened. Without that, run `docker compose pull && docker compose up -d` on the machine after each push.
- **Step-by-step guide:** [home-server-hosting.md](home-server-hosting.md) covers running it on a friend's server and moving it to a Pi later.

### Examples of Discord bots on a Raspberry Pi

Running a Discord bot on a Pi is common, and some well-known projects document it officially.

**Bots with official Pi install guides**
- [Red-DiscordBot: Raspberry Pi OS 12 Bookworm guide](https://github.com/Cog-Creators/Red-DiscordBot/blob/V3/develop/docs/install_guides/raspberry-pi-os-12.rst). A large Python bot with music, so the closest match to this one. It supports 64-bit and 32-bit Pis and recommends a Pi 3 or newer. There's also a [Bullseye guide](https://red-discordbot.readthedocs.io/en/latest/install_guides/raspberry-pi-os-11.html) for the older OS.
- [MusicBot: Raspberry Pi OS guide](https://itsthefae.github.io/MusicBot/installing/raspbian). A Python music bot built almost exactly like `/play`: discord.py voice, FFmpeg, yt-dlp, and Deno as yt-dlp's JavaScript runtime.

**discord.py tutorials**
- [Host your own Discord Bot on Raspberry Pi 5](https://robu.in/host-your-own-discord-bot-on-raspberry-pi-5/) (robu.in).
- [Running a Discord bot on Raspberry Pi](https://dev.to/beretests/running-a-discord-bot-on-raspberry-pi-4la4) (DEV Community).
- [How to make a Discord bot on Raspberry Pi](https://raspberrytips.com/?p=4549) (RaspberryTips).
- [Discord bot notes with a systemd service](https://chxtio.github.io/quartz-notes/Software-Documentation/Software-Tools/Discord-Bot): restarts the bot on crash and starts it on boot.
- [DanielKirshner/Discord-Bot](https://github.com/DanielKirshner/Discord-Bot): a simple discord.py bot whose README covers Pi hosting.

**Music and voice**
- [Turn a Raspberry Pi into a Discord radio station](https://maketecheasier.com/turn-raspberry-pi-into-discord-radio-station) (Make Tech Easier). Discord voice from a Pi 4 with 4 GB.
- [Hosting Red-DiscordBot with Docker Compose](https://www.itechguides.com/how-to-host-red-discordbot-with-docker-compose/). Not Pi-specific, but the same Compose approach as `deploy/compose.yaml`.

**How these compare with this bot**
- **`/play`:** the FFmpeg, yt-dlp and voice setup is well proven on Pis.
- **`/gawk`:** this is the less common part. MediaPipe has had a Linux ARM64 wheel only since 1.x. Version 1.2.3 has been tested on Windows but not yet on a Pi.
- **OS version:** most guides target Raspberry Pi OS Bookworm. The Docker image ships its own Python 3.14, so the Pi's OS version matters less.

## The `/play` problem on cloud hosts

YouTube treats most cloud and datacenter IPs as bots. On Azure, Oracle and similar hosts, expect yt-dlp to fail at least some of the time with *"Sign in to confirm you're not a bot"*. Keep yt-dlp up to date first, because some errors are fixed by new releases. If the chosen VM's IP is still blocked, the free fixes are:

1. **Route yt-dlp and FFmpeg through a home connection.** For example, put the VM and a home machine on [Tailscale](https://tailscale.com/) and use the home machine as an exit node or SOCKS proxy. YouTube then sees your home IP. This needs a small code change: a proxy setting passed to yt-dlp and FFmpeg.
2. **Give yt-dlp a cookies file** from a signed-in YouTube account. Cookies expire and the account can get flagged, so use a throwaway account. This also needs a small code change.
3. **Host at home.** No IP problem at all.

Paid residential proxies also work but aren't free.
