# Hosting on a friend's home server

This is how to run the bot on a friend's home server for now, set up so that moving it to a Raspberry Pi later only means changing one GitHub secret.

Each step says who does it: **Friend** (whoever owns the server) or **You** (the bot's owner).

## How it fits together

```
push to master ──► GitHub Actions builds the image ──► ghcr.io/alexfinn97/discord-adjutant (public)
                          │
                          └─ deploy job joins your tailnet as a temporary tag:ci node
                             └─ SSH (port 22, over Tailscale only) to the bot host
                                └─ docker compose pull && docker compose up -d
```

- **No ports are opened on the friend's router.** The bot only makes outbound connections to Discord and YouTube. Tailscale also connects outbound only.
- **Only the bot host joins your tailnet.** The rest of the friend's network isn't reachable through it, because the host advertises no subnet routes.
- **Deploys can only reach port 22 on that host.** That's because of your Tailscale policy.

**If the host is already set up differently:**
- **It already has an account for you.** You can use that account in place of `deploy`. It needs to be in the `docker` group. Give GitHub its own SSH key on that account (step 4), so you can revoke it without losing your own access, and set `DEPLOY_USER` to that account's name.
- **It already joined your tailnet** (for example with an auth key you created). Don't join it again. Add the tags to your policy (step 3), then in the admin console go to **Machines → the host → Edit ACL tags** and add `tag:bothost`.

## 1. What the friend is providing

| Need | Amount |
|---|---|
| Machine | x86_64 or ARM64, running Linux. Best as its own VM or LXC (see step 2). |
| Software | Docker Engine with the Compose plugin, an SSH server, and Tailscale |
| RAM / CPU | Capped at 2 GB and 1 CPU by `deploy/compose.yaml`. The bot peaks at about 320 MB today, so 1 GB of RAM is enough if you lower the cap. |
| Disk | About 3 GB for the image, logs and face model |
| Network | Outbound internet only. Nothing forwarded. |

What the friend is trusting you with:
- **Docker access.** The `deploy` user can run Docker, which is effectively root on whatever machine it's on. That's the main reason to use a separate VM or LXC.
- **The bot token and traffic.** The bot token lives on that machine. `/play` traffic to YouTube comes from the friend's home IP address.

## 2. Pick where it runs (Friend)

**Preferred: a small VM or LXC just for the bot.** It keeps the bot and your access away from everything else on the server, and it's set up the same way the Pi will be later.

- **Proxmox:**
  - **Option 1, a Debian 13 VM** (1 GB RAM, 1–2 cores, 8 GB disk). This is the simplest.
  - **Option 2, a Debian 13 LXC.** Docker inside an unprivileged LXC needs **Options → Features → `nesting` and `keyctl`** turned on. Tailscale inside an LXC also needs the TUN device. Add these lines to `/etc/pve/lxc/<id>.conf` on the Proxmox host, then restart the container:

    ```
    lxc.cgroup2.devices.allow: c 10:200 rwm
    lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
    ```
- **Unraid or TrueNAS:** a small Debian VM.
- **Any Linux box:** a VM if possible.

**Fallback: directly on the friend's existing Docker host.** This works the same way, but the `deploy` user's Docker access then covers the whole server. Tailscale also has to be installed on that server, though with the policy below the bot host still can't reach your devices.

Then install the basics (Debian/Ubuntu):

```bash
sudo apt update && sudo apt install -y curl openssh-server
curl -fsSL https://get.docker.com | sudo sh   # Docker Engine + Compose plugin
curl -fsSL https://tailscale.com/install.sh | sh
```

## 3. Tailscale (You, then Friend)

Tailscale's free Personal plan covers this.

**You:** edit your tailnet's policy file (**Access controls** in the admin console).
- Add the two tags.
- Replace the default allow-all rule with rules that keep your own devices connected to each other and let only you and CI reach the bot host on SSH.
- Keep any other sections, such as `ssh`, as they are.

```json
{
  "tagOwners": {
    "tag:bothost": ["autogroup:admin"],
    "tag:ci":      ["autogroup:admin"]
  },
  "grants": [
    { "src": ["autogroup:member"], "dst": ["autogroup:self"], "ip": ["*"] },
    { "src": ["autogroup:member"], "dst": ["tag:bothost"],    "ip": ["tcp:22"] },
    { "src": ["tag:ci"],           "dst": ["tag:bothost"],    "ip": ["tcp:22"] }
  ]
}
```

Nothing allows `tag:bothost` to start a connection to your devices.

**Friend:** on the bot host, run:

```bash
sudo tailscale up --advertise-tags=tag:bothost
```

It prints a login URL. Send it to **you**, and you open it and approve it with your Tailscale account. Don't add `--advertise-routes` or `--advertise-exit-node`. Tagged devices don't expire, so the host stays connected.

**Friend:** run `tailscale ip -4` and send you the `100.x.y.z` address.

**You:** create an OAuth client for GitHub in the admin console (**Settings → OAuth clients**).
- Scope: **Auth Keys → Write**.
- Tag: **`tag:ci`**.
- Keep the client ID and secret for step 5. See [tailscale/github-action](https://github.com/tailscale/github-action) for details.

## 4. Deploy user and bot settings (You and Friend)

**You:** on your PC, create an SSH key just for deploys. Press Enter twice when asked, so it has no passphrase.

```powershell
ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\adjutant_deploy" -C adjutant-deploy
```

Send the friend the **public** key: the contents of `adjutant_deploy.pub`.

**Friend:** create the user and install the key:

```bash
sudo adduser --disabled-password --gecos "" deploy
sudo usermod -aG docker deploy
sudo install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
echo "<paste the public key here>" | sudo tee /home/deploy/.ssh/authorized_keys
sudo chown deploy:deploy /home/deploy/.ssh/authorized_keys && sudo chmod 600 /home/deploy/.ssh/authorized_keys
```

**You:** SSH in over Tailscale and create the bot's settings file. This way the token never passes through the friend's hands.

```powershell
ssh -i "$env:USERPROFILE\.ssh\adjutant_deploy" deploy@100.x.y.z
```

```bash
mkdir -p ~/discord-adjutant && cd ~/discord-adjutant
nano .env        # DISCORD_TOKEN=..., GUILD_ID=..., FORCE_SYNC=0
chmod 600 .env
```

## 5. GitHub secrets (You)

In **Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `DEPLOY_HOST` | The bot host's Tailscale IP (`100.x.y.z`) |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_SSH_KEY` | The **private** key, i.e. the contents of `adjutant_deploy` |
| `TS_OAUTH_CLIENT_ID` | From step 3 |
| `TS_OAUTH_SECRET` | From step 3 |

## 6. First deploy (You)

1. **Stop the bot on your PC.** Only one copy should run per token, or both will try to answer each command.
2. In GitHub, go to **Actions → Build and deploy → Run workflow** on `master`.
3. Check the run:
   - **Join tailnet** succeeds.
   - A `tag:ci` machine appears briefly in the Tailscale admin console, then disappears once the job ends.
   - **Copy compose file** and **Pull the new image** succeed.
4. On the host, check the bot is running:

   ```bash
   cd ~/discord-adjutant
   docker compose ps
   docker compose logs -f   # expect "Server commands synced: gawk" then "... is online!"
   ```

5. Try `/gawk` and `/play` in Discord.

## 7. Day to day

- **You:** pushing to `master` builds and deploys automatically.
- **Friend:**
  - Stop the bot: `cd /home/deploy/discord-adjutant && sudo -u deploy docker compose down`
  - Start it again: the same, with `up -d`.
  - Check its resource use: `docker stats`
- **Restarts:** the container restarts by itself after a crash or a reboot (`restart: unless-stopped`), as long as Docker starts on boot, which it does by default.

## 8. Keeping the host patched (You)

[`deploy/host/setup-auto-updates.sh`](../deploy/host/setup-auto-updates.sh) sets up Debian's `unattended-upgrades` (see Debian's [AutomatedUpgrade](https://wiki.debian.org/AutomatedUpgrade) page) to patch the box once a week while everyone's asleep. Run it once, with an account on the box that has `sudo` (not `deploy`).

**Set it up.** From the repo on your PC, copy the script over Tailscale, then run it:

```powershell
scp deploy/host/setup-auto-updates.sh <your-user>@100.x.y.z:
ssh <your-user>@100.x.y.z
```

```bash
sudo bash setup-auto-updates.sh
```

**Catch up now.** The box may never have been patched, so don't wait for Sunday:

```bash
sudo unattended-upgrade -v
[ -f /var/run/reboot-required ] && sudo reboot
```

**What happens each week** (in the box's own timezone, shown by `timedatectl`):

| When | What |
|---|---|
| Sunday 03:30 | Refresh the package lists |
| Sunday 04:00 | Install updates from Debian (security and stable updates), Docker and Tailscale |
| Sunday 04:30 | Reboot, only if an update needs it |

- If the box is off on Sunday night, the run happens the next time it boots.
- A Docker update or a reboot restarts the bot. It comes back by itself, usually within a minute.
- **Check the schedule:** `systemctl list-timers 'apt-daily*'`
- **See what was installed:** `/var/log/unattended-upgrades/unattended-upgrades.log`
- **Undo it:** the script prints the commands at the end.

## 9. Moving to the Raspberry Pi later

The bot keeps almost no state: just `.env`, and a face model it downloads again by itself. So moving it is mostly a repeat of the steps above.

1. **Set up the Pi.** Flash **64-bit Raspberry Pi OS Lite** and enable SSH in Raspberry Pi Imager.
2. **Repeat the earlier steps on the Pi.** Do step 2's install commands, step 3 (`tailscale up --advertise-tags=tag:bothost`, then approve it), step 4 (the `deploy` user, the same public key, and `.env`), and step 8 (weekly patching; it works on Raspberry Pi OS too, though it only adds Debian's, Docker's and Tailscale's update sources, not the Pi's own).
3. **Stop the bot on the friend's server:** `docker compose down`.
4. **Point GitHub at the Pi.** Change the `DEPLOY_HOST` secret to the Pi's Tailscale IP.
5. **Deploy.** Run **Build and deploy**, then check `docker compose ps`, the logs, `/gawk` and `/play`.
6. **Clean up on the friend's side.** The friend removes `/home/deploy/discord-adjutant`, the `deploy` user and the image (`docker image rm ghcr.io/alexfinn97/discord-adjutant`). You delete the friend's machine from your tailnet in the admin console.
7. **Optionally reset the bot token.** The friend's machine held it, so reset it in the Discord Developer Portal and update `.env` on the Pi.

Nothing in the repo needs to change for the move.
