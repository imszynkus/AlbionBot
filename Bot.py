import discord
from discord.ext import tasks, commands
import aiohttp
import json
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# ==========================================
# SERWER HTTP (DLA WEBSERVICE RENDER)
# ==========================================
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Albion Killbot is running!")

    def log_message(self, format, *args):
        return

def run_web_server():
    server = HTTPServer(("0.0.0.0", 8080), SimpleHandler)
    server.serve_forever()

# ==========================================
# KONFIGURACJA ZAPISU I ODCZYTU
# ==========================================
CONFIG_FILE = "config.json"

# WPISZ TUTAJ SWÓJ KANAŁ I NICK, ABY BIAŁA LISTA NIE ZNIKANIAŁA PO REBOOTACH RENDERA:
DEFAULT_CONFIG = {
    "main_channel_id": 1560358432165466303,             # Np. 123456789012345678 (lub ustaw komendą /ustaw_kanaly)
    "big_channel_id": None,
    "high_value_threshold": 15000000,
    "server_region": "europe",           # europe, live, asia
    "tracked_players": ["Szynkus"]                # Np. ["Szynkus"] (lub dodaj komendą /dodaj)
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for key, val in DEFAULT_CONFIG.items():
                    if key not in data:
                        data[key] = val
                return data
        except Exception:
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)

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

intents = discord.Intents.default()
intents.message_content = True
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
        print(f"[Ceny Error] {e}", flush=True)
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
    global config
    config = load_config()  # Dynamiczne odświeżenie konfiguracji przy każdym obrocie pętli
    
    tracked = config.get("tracked_players", [])
    main_chan_id = config.get("main_channel_id")
    
    print(f"[PĘTLA] Śledzeni gracze: {tracked} | Kanał ID: {main_chan_id}", flush=True)

    if not tracked or not main_chan_id:
        print("[PĘTLA] Pomijam – brak śledzonych graczy lub kanału w configu.", flush=True)
        return

    main_channel = bot.get_channel(main_chan_id)
    big_chan_id = config.get("big_channel_id")
    big_channel = bot.get_channel(big_chan_id) if big_chan_id else None

    if not main_channel:
        print(f"[ERROR] Nie odnaleziono kanału na Discordzie dla ID: {main_chan_id}", flush=True)
        return

    region = config.get("server_region", "europe")
    base_url = API_URLS.get(region, API_URLS["europe"])

    async with aiohttp.ClientSession() as session:
        url = f"{base_url}/events?limit=51&offset=0"
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    print(f"[API ERROR] Kod odpowiedzi API: {resp.status}", flush=True)
                    return
                events = await resp.json()
                print(f"[API OK] Pobrano {len(events)} ostatnich zdarzeń z serwera ({region.upper()}).", flush=True)
        except Exception as e:
            print(f"[API ERROR] Błąd połączenia: {e}", flush=True)
            return

        tracked_lower = [t.lower() for t in tracked]

        for event in reversed(events):
            event_id = event.get("EventId")
            if event_id in seen_event_ids:
                continue
                
            seen_event_ids.add(event_id)
            if len(seen_event_ids) > 2000:
                seen_event_ids.pop()

            killer_name = event.get("Killer", {}).get("Name", "")
            victim_name = event.get("Victim", {}).get("Name", "")
            participants = event.get("Participants", [])
            participant_names = [p.get("Name") for p in participants if p.get("Name")]

            is_killer = killer_name.lower() in tracked_lower
            is_victim = victim_name.lower() in tracked_lower
            is_participant = any(p_name.lower() in tracked_lower for p_name in participant_names)

            if not (is_killer or is_victim or is_participant):
                continue

            print(f"[KILLS] WYKRYTO KILL! Zabójca: {killer_name} | Ofiara: {victim_name}", flush=True)

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

            victim_ip = round(event.get("Victim", {}).get("AverageItemPower", 0))
            embed.add_field(
                name="🎯 Ofiara",
                value=f"**Nick:** {victim_name}\n"
                      f"**Gildia:** {event.get('Victim', {}).get('GuildName') or 'Brak'}\n"
                      f"**IP:** {victim_ip}\n"
                      f"**Est. Wartość:** {total_estimated_value:,.0f} Silver",
                inline=False
            )

            gear_text = ""
            for slot, item in victim_equip.items():
                if item:
                    item_name = item.get("Type", "").replace("T", "Tier ").replace("_", " ")
                    count = item.get("Count", 1)
                    gear_text += f"• **{slot}:** {item_name} (x{count})\n"
            if not gear_text:
                gear_text = "Brak ekwipunku"

            embed.add_field(name="🎒 Ekwipunek Ofiary", value=gear_text[:1024], inline=False)

            total_damage = sum(p.get("DamageDone", 0) for p in participants)
            assists_text = ""
            for p in participants:
                p_name = p.get("Name")
                p_damage = round(p.get("DamageDone", 0))
                p_ip = round(p.get("AverageItemPower", 0))
                damage_pct = (p_damage / total_damage * 100) if total_damage > 0 else 0
                
                main_hand = p.get("Equipment", {}).get("MainHand")
                weapon_name = main_hand.get("Type", "Brak broni").replace("_", " ") if main_hand else "Gołe ręce"

                icon = "⭐" if p_name.lower() in tracked_lower else "🗡️"
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

            try:
                await main_channel.send(embed=embed)
                print(f"[SUCCESS] Wysłano powiadomienie o killu na Discord!", flush=True)
            except Exception as send_error:
                print(f"[DISCORD ERROR] Nie udało się wysłać wiadomości: {send_error}", flush=True)

            if total_estimated_value >= threshold and big_channel:
                await big_channel.send(
                    content=f"🚨 **BIG KILL / LEGENDARNY LOOT (> {threshold/1_000_000:.0f}M)!** 🚨",
                    embed=embed
                )

# ==========================================
# COMMANDS / SLASH COMMANDS
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
    players_str = "\n".join([f"• {p}" for p in players]) if players else "Brak graczy"
    
    main_ch = f"<#{config['main_channel_id']}>" if config['main_channel_id'] else "Nie ustawiono"
    big_ch = f"<#{config['big_channel_id']}>" if config['big_channel_id'] else "Brak (wyłączone)"
    threshold_mln = config['high_value_threshold'] / 1_000_000

    embed = discord.Embed(title="⚙️ Konfiguracja Killbota Albion", color=discord.Color.blurple())
    embed.add_field(name="🌍 Region Serwera", value=config['server_region'].upper(), inline=True)
    embed.add_field(name="💰 Próg Big Kill", value=f"{threshold_mln:.1f}M Silver", inline=True)
    embed.add_field(name="📢 Kanał Główny", value=main_ch, inline=False)
    embed.add_field(name="🚨 Kanał Big Kills", value=big_ch, inline=False)
    embed.add_field(name=f"👥 Śledzeni gracze ({len(players)})", value=players_str[:1024], inline=False)
    
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

@bot.tree.command(name="ustaw_prog", description="Zmień próg silvera dla dużych killi")
async def ustaw_prog(interaction: discord.Interaction, value: int):
    config["high_value_threshold"] = value
    save_config(config)
    await interaction.response.send_message(f"💰 Zaktualizowano próg dużego killa na **{value:,.0f} Silver**.", ephemeral=False)

@bot.tree.command(name="ustaw_region", description="Zmień region serwera gry")
async def ustaw_region(interaction: discord.Interaction, region: str):
    region = region.lower()
    if region not in ["europe", "live", "asia"]:
        await interaction.response.send_message("❌ Nieprawidłowy region. Wybierz: `europe`, `live` lub `asia`.", ephemeral=True)
        return
    
    config["server_region"] = region
    save_config(config)
    await interaction.response.send_message(f"🌍 Zmieniono region serwera na: **{region.upper()}**.", ephemeral=False)

@bot.event
async def on_ready():
    print(f"✅ Zalogowano jako: {bot.user.name}", flush=True)
    try:
        synced = await bot.tree.sync()
        print(f"🔄 Zsynchronizowano {len(synced)} komend.", flush=True)
    except Exception as e:
        print(f"Błąd synchronizacji: {e}", flush=True)
        
    if not check_albion_kills.is_running():
        check_albion_kills.start()
        print("⚔️ Pętla sprawdzająca kille została uruchomiona!", flush=True)

if __name__ == "__main__":
    t = threading.Thread(target=run_web_server)
    t.daemon = True
    t.start()

    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        print("❌ BŁĄD: Brak zmiennej środowiskowej DISCORD_TOKEN!", flush=True)
    else:
        bot.run(TOKEN)
