import discord
from discord.ext import tasks, commands
import aiohttp
import json
import os

CONFIG_FILE = "config.json"

# Domyślna konfiguracja, jeśli plik nie istnieje
DEFAULT_CONFIG = {
    "main_channel_id": None,
    "big_channel_id": None,
    "high_value_threshold": 15000000,  # 15 mln silvera
    "server_region": "europe",          # europe, live, asia
    "tracked_players": []
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Uzupełnij brakujące klucze jeśli stary config był uboższy
                for key, val in DEFAULT_CONFIG.items():
                    if key not in data:
                        data[key] = val
                return data
        except Exception:
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()

def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)

config = load_config()
seen_event_ids = set()

API_URLS = {
    "live": "https://gameinfo.albiononline.com/api/gameinfo",
    "europe": "https://gameinfo-ams.albiononline.com/api/gameinfo",
    "asia": "https://gameinfo-sgp.albiononline.com/api/gameinfo"
}

PRICES_API_URLS = {
    "live": "https://west.albion-online-data.com/api/v2/stats/prices",
    "europe": "https://europe.albion-online-data.com/api/v2/stats/prices",
    "asia": "https://east.albion-online-data.com/api/v2/stats/prices"
}

# Inicjalizacja bota
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# ==========================================
# FUNKCJE POMOCNICZE / API
# ==========================================
async def get_item_prices(session, item_ids, region):
    if not item_ids:
        return {}
    unique_ids = list(set(item_ids))
    ids_str = ",".join(unique_ids)
    prices_url = PRICES_API_URLS.get(region, PRICES_API_URLS["europe"])
    url = f"{prices_url}/{ids_str}.json"
    
    prices = {}
    try:
        async with session.get(url, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.json()
                for entry in data:
                    item_id = entry.get("item_id")
                    price = entry.get("sell_price_min") or entry.get("buy_price_max") or 0
                    if price > 0:
                        if item_id not in prices or price < prices[item_id]:
                            prices[item_id] = price
    except Exception as e:
        print(f"[Ceny] Błąd pobierania cen: {e}")
    return prices

def calculate_equipment_value(equipment_dict, prices):
    total_val = 0
    if not equipment_dict:
        return 0
    for slot, item in equipment_dict.items():
        if item:
            item_type = item.get("Type")
            count = item.get("Count", 1)
            total_val += prices.get(item_type, 0) * count
    return total_val

# ==========================================
# PĘTLA SPRAWDZAJĄCA KILLBOARD
# ==========================================
@tasks.loop(seconds=30)
async def check_albion_kills():
    tracked = config.get("tracked_players", [])
    main_chan_id = config.get("main_channel_id")
    
    if not tracked or not main_chan_id:
        return

    main_channel = bot.get_channel(main_chan_id)
    big_chan_id = config.get("big_channel_id")
    big_channel = bot.get_channel(big_chan_id) if big_chan_id else None

    if not main_channel:
        return

    region = config.get("server_region", "europe")
    base_url = API_URLS.get(region, API_URLS["europe"])

    async with aiohttp.ClientSession() as session:
        url = f"{base_url}/events?limit=51&offset=0"
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return
                events = await resp.json()
        except Exception as e:
            print(f"[API Error] {e}")
            return

        for event in reversed(events):
            event_id = event.get("EventId")
            if event_id in seen_event_ids:
                continue
                
            seen_event_ids.add(event_id)
            if len(seen_event_ids) > 1000:
                seen_event_ids.pop()

            killer_name = event.get("Killer", {}).get("Name", "")
            victim_name = event.get("Victim", {}).get("Name", "")
            participants = event.get("Participants", [])
            participant_names = [p.get("Name") for p in participants if p.get("Name")]

            # Sprawdzenie czy ktoś z listy PK uczestniczył w zabójstwie
            is_tracked = any(
                p_name in tracked 
                for p_name in [killer_name] + participant_names
            )

            if not is_tracked:
                continue

            victim_equip = event.get("Victim", {}).get("Equipment", {})
            item_ids = [item.get("Type") for item in victim_equip.values() if item]
            prices = await get_item_prices(session, item_ids, region)
            total_estimated_value = calculate_equipment_value(victim_equip, prices)

            threshold = config.get("high_value_threshold", 15000000)

            embed = discord.Embed(
                title=f"⚔️ {killer_name} zabił {victim_name}!",
                url=f"https://albiononline.com/killboard/kill/{event_id}",
                color=discord.Color.gold() if total_estimated_value >= threshold else discord.Color.red()
            )

            # Ofiara
            victim_ip = round(event.get("Victim", {}).get("AverageItemPower", 0))
            embed.add_field(
                name="🎯 Ofiara",
                value=f"**Nick:** {victim_name}\n"
                      f"**Gildia:** {event.get('Victim', {}).get('GuildName') or 'Brak'}\n"
                      f"**IP:** {victim_ip}\n"
                      f"**Est. Wartość:** {total_estimated_value:,.0f} Silver",
                inline=False
            )

            # Ekwipunek
            gear_text = ""
            for slot, item in victim_equip.items():
                if item:
                    item_name = item.get("Type", "").replace("T", "Tier ").replace("_", " ")
                    count = item.get("Count", 1)
                    gear_text += f"• **{slot}:** {item_name} (x{count})\n"
            if not gear_text:
                gear_text = "Brak ekwipunku"

            embed.add_field(name="🎒 Ekwipunek Ofiary", value=gear_text[:1024], inline=False)

            # Asysty i obrażenia
            total_damage = sum(p.get("DamageDone", 0) for p in participants)
            assists_text = ""
            for p in participants:
                p_name = p.get("Name")
                p_damage = round(p.get("DamageDone", 0))
                p_ip = round(p.get("AverageItemPower", 0))
                damage_pct = (p_damage / total_damage * 100) if total_damage > 0 else 0
                
                main_hand = p.get("Equipment", {}).get("MainHand")
                weapon_name = main_hand.get("Type", "Brak broni").replace("_", " ") if main_hand else "Gołe ręce"

                icon = "⭐" if p_name in tracked else "🗡️"
                assists_text += (
                    f"{icon} **{p_name}** ({p_ip} IP)\n"
                    f"┗ 🪓 *{weapon_name}*\n"
                    f"┗ 💥 Dmg: **{p_damage:,}** ({damage_pct:.1f}%)\n\n"
                )

            embed.add_field(
                name=f"👥 Uczestnicy ({len(participants)})",
                value=assists_text[:1024] if assists_text else "Brak",
                inline=False
            )

            victim_weapon = victim_equip.get("MainHand")
            if victim_weapon:
                embed.set_thumbnail(url=f"https://render.albiononline.com/v1/item/{victim_weapon.get('Type')}.png")

            embed.set_footer(text=f"Albion Online Killbot • Region: {region.upper()} • Event ID: {event_id}")

            await main_channel.send(embed=embed)
            if total_estimated_value >= threshold and big_channel:
                await big_channel.send(
                    content=f"🚨 **BIG KILL / LEGENDARNY LOOT (> {threshold/1_000_000:.0f}M)!** 🚨",
                    embed=embed
                )

# ==========================================
# PANEL STEROWANIA - SLASH COMMANDS
# ==========================================

@bot.tree.command(name="dodaj", description="Dodaj nowego gracza do śledzenia")
async def dodaj(interaction: discord.Interaction, nick: str):
    if nick in config["tracked_players"]:
        await interaction.response.send_message(f"⚠️ Gracz **{nick}** jest już na liście.", ephemeral=True)
        return
    
    config["tracked_players"].append(nick)
    save_config(config)
    await interaction.response.send_message(f"✅ Dodano gracza **{nick}** do monitorowania PK.", ephemeral=False)

@bot.tree.command(name="usun", description="Usuń gracza ze śledzenia")
async def usun(interaction: discord.Interaction, nick: str):
    if nick not in config["tracked_players"]:
        await interaction.response.send_message(f"❌ Gracz **{nick}** nie znajduje się na liście.", ephemeral=True)
        return
    
    config["tracked_players"].remove(nick)
    save_config(config)
    await interaction.response.send_message(f"🗑️ Usunięto gracza **{nick}** ze śledzenia.", ephemeral=False)

@bot.tree.command(name="lista", description="Wyświetl listę śledzonych graczy i konfigurację")
async def lista(interaction: discord.Interaction):
    players = config["tracked_players"]
    players_str = "\n".join([f"• {p}" for p in players]) if players * 1 else "Brak graczy"
    
    main_ch = f"<#{config['main_channel_id']}>" if config['main_channel_id'] else "Nie ustawiono"
    big_ch = f"<#{config['big_channel_id']}>" if config['big_channel_id'] else "Brak (wyłączone)"
    threshold_mln = config['high_value_threshold'] / 1_000_000

    embed = discord.Embed(title="⚙️ Konfiguracja Killbota Albion", color=discord.Color.blurple())
    embed.add_field(name="🌍 Region Serwera", value=config['server_region'].upper(), inline=True)
    embed.add_field(name="💰 Próg Big Kill", value=f"{threshold_mln:.1f}M Silver", inline=True)
    embed.add_field(name="📢 Kanał Główny", value=main_ch, inline=False)
    embed.add_field(name="🚨 Kanał Big Kills", value=big_ch, inline=False)
    embed.add_field(name=f"👥 Śledzeni graczi ({len(players)})", value=players_str[:1024], inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=False)

@bot.tree.command(name="ustaw_kanaly", description="Ustaw kanały powiadomień")
async def ustaw_kanaly(interaction: discord.Interaction, glowny: discord.TextChannel, duze_kille: discord.TextChannel = None):
    config["main_channel_id"] = glowny.id
    config["big_channel_id"] = duze_kille.id if duze_kille else None
    save_config(config)
    
    msg = f"✅ Ustawiono główny kanał powiadomień na {glowny.mention}!"
    if duze_kille:
        msg += f"\n🚨 Ustawiono kanał na kille > {config['high_value_threshold']/1_000_000:.0f}M na {duze_kille.mention}!"
    
    await interaction.response.send_message(msg, ephemeral=False)

@bot.tree.command(name="ustaw_prog", description="Zmień próg silvera dla dużych killi (np. 15000000 dla 15M)")
async def ustaw_prog(value: int, interaction: discord.Interaction):
    config["high_value_threshold"] = value
    save_config(config)
    await interaction.response.send_message(f"💰 Zaktualizowano próg dużego killa na **{value:,.0f} Silver** ({value/1_000_000:.1f}M).", ephemeral=False)

@bot.tree.command(name="ustaw_region", description="Zmień region serwera gry")
async def ustaw_region(interaction: discord.Interaction, region: str):
    region = region.lower()
    if region not in ["europe", "live", "asia"]:
        await interaction.response.send_message("❌ Nieprawidłowy region. Wybierz: `europe`, `live` (Ameryka) lub `asia`.", ephemeral=True)
        return
    
    config["server_region"] = region
    save_config(config)
    await interaction.response.send_message(f"🌍 Zmieniono region serwera na: **{region.upper()}**.", ephemeral=False)

@bot.event
async def on_ready():
    print(f"✅ Zalogowano jako: {bot.user.name}")
    try:
        synced = await bot.tree.sync()
        print(f"🔄 Zsynchronizowano {len(synced)} komend.")
    except Exception as e:
        print(f"Błąd synchronizacji: {e}")
        
    check_albion_kills.start()

# Pobiera token ze zmiennych środowiskowych systemu (np. z panelu Render)
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    print("❌ BŁĄD: Brak zmiennej środowiskowej DISCORD_TOKEN!")
else:
    bot.run(TOKEN)

