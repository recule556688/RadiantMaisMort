import os
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
from dotenv import load_dotenv
from datetime import datetime

# ——— Load & verify environment ———
load_dotenv(override=True)
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
RIOT_API_KEY = os.getenv("RIOT_API_KEY", "").strip()
REGION = os.getenv("RIOT_REGION", "americas").strip().lower()
GUILD_ID_STR = os.getenv("GUILD_ID", "").strip()

if not DISCORD_TOKEN or not RIOT_API_KEY or not GUILD_ID_STR:
    raise RuntimeError("⚠️ Missing DISCORD_TOKEN, RIOT_API_KEY or GUILD_ID in your .env")

try:
    GUILD_ID = int(GUILD_ID_STR)
except ValueError:
    raise RuntimeError("⚠️ GUILD_ID must be an integer in your .env")

print(f"🚀 Starting bot with REGION={REGION}, API_KEY length={len(RIOT_API_KEY)}")

# ——— Tier map ———
TIER_MAP = {
    0: "Unrated",
    1: "Iron 1",
    2: "Iron 2",
    3: "Iron 3",
    4: "Bronze 1",
    5: "Bronze 2",
    6: "Bronze 3",
    7: "Silver 1",
    8: "Silver 2",
    9: "Silver 3",
    10: "Gold 1",
    11: "Gold 2",
    12: "Gold 3",
    13: "Platinum 1",
    14: "Platinum 2",
    15: "Platinum 3",
    16: "Diamond 1",
    17: "Diamond 2",
    18: "Diamond 3",
    19: "Ascendant 1",
    20: "Ascendant 2",
    21: "Ascendant 3",
    22: "Immortal 1",
    23: "Immortal 2",
    24: "Immortal 3",
    25: "Radiant",
}

# ——— Discord setup ———
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    # Sync only to your test guild
    await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")


# ——— Riot helpers ———


async def get_puuid(
    session: aiohttp.ClientSession, game_name: str, tag_line: str
) -> str:
    url = (
        f"https://{REGION}.api.riotgames.com"
        f"/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
    )
    headers = {"X-Riot-Token": RIOT_API_KEY}
    # Debug
    print(f"[DEBUG] GET {url}")
    print(f"[DEBUG] HEADERS: {headers}")
    async with session.get(url, headers=headers) as resp:
        text = await resp.text()
        print(f"[DEBUG] get_puuid → {resp.status} {text}")
        resp.raise_for_status()
        data = await resp.json()
        return data["puuid"]


async def get_last_competitive_match_id(
    session: aiohttp.ClientSession, puuid: str
) -> str:
    url = (
        f"https://{REGION}.api.riotgames.com"
        f"/val/match/v1/matchlists/by-puuid/{puuid}"
        f"?queue=competitive&count=1"
    )
    headers = {"X-Riot-Token": RIOT_API_KEY}
    async with session.get(url, headers=headers) as resp:
        text = await resp.text()
        print(f"[DEBUG] get_last_match_id → {resp.status} {text[:200]}…")
        resp.raise_for_status()
        data = await resp.json()
        history = data.get("history", [])
        if not history:
            raise ValueError("No competitive matches found for this player.")
        return history[0]["matchId"]


async def get_match_details(session: aiohttp.ClientSession, match_id: str) -> dict:
    url = f"https://{REGION}.api.riotgames.com/val/match/v1/matches/{match_id}"
    headers = {"X-Riot-Token": RIOT_API_KEY}
    async with session.get(url, headers=headers) as resp:
        print(f"[DEBUG] get_match_details → {resp.status}")
        resp.raise_for_status()
        return await resp.json()


async def get_current_rank(session: aiohttp.ClientSession, puuid: str) -> str:
    url = f"https://{REGION}.api.riotgames.com/val/ranked/v1/players/{puuid}"
    headers = {"X-Riot-Token": RIOT_API_KEY}
    async with session.get(url, headers=headers) as resp:
        text = await resp.text()
        print(f"[DEBUG] get_current_rank → {resp.status} {text}")
        resp.raise_for_status()
        data = await resp.json()
        return TIER_MAP.get(data.get("competitiveTier", 0), "Unknown")


# ——— Slash command ———


@bot.tree.command(
    name="lastmatch", description="Show a Valorant player's last competitive match"
)
@app_commands.describe(
    game_name="Their Riot in-game name (without # and tagline)",
    tag_line="Their Riot ID tagline (e.g. 1234)",
)
async def lastmatch(interaction: discord.Interaction, game_name: str, tag_line: str):
    await interaction.response.defer()
    try:
        async with aiohttp.ClientSession() as session:
            puuid = await get_puuid(session, game_name, tag_line)
            match_id = await get_last_competitive_match_id(session, puuid)
            match = await get_match_details(session, match_id)
            rank = await get_current_rank(session, puuid)

        me = next(p for p in match["players"]["all_players"] if p["puuid"] == puuid)
        stats = me["stats"]
        meta = match["metadata"]
        date = datetime.fromisoformat(meta["gameStartTime"].rstrip("Z"))

        embed = discord.Embed(
            title=f"{game_name}#{tag_line} — {rank}",
            description=(
                f"Result: **{'Win' if stats['win'] else 'Loss'}** • "
                f"Map: **{meta['mapName']}** • "
                f"{date.strftime('%Y-%m-%d %H:%M:%S')}"
            ),
            color=0x00FF00 if stats["win"] else 0xFF0000,
        )

        for k, v in stats.items():
            if k == "win":
                continue
            embed.add_field(name=k.replace("_", " ").title(), value=str(v), inline=True)

        embed.set_footer(text="Data via Riot Games API")
        embed.timestamp = datetime.utcnow()
        await interaction.followup.send(embed=embed)

    except aiohttp.ClientResponseError as e:
        await interaction.followup.send(
            f"❌ Riot API error (status {e.status}). Check console logs for details."
        )
    except ValueError as e:
        await interaction.followup.send(f"⚠️ {e}")
    except Exception as e:
        print("❌ Unexpected error:", e)
        await interaction.followup.send(
            "❌ Something went wrong. Double-check your `.env` and region, then try again."
        )


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
