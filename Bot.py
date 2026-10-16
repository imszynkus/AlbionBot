import discord
from discord.ext import tasks, commands
import aiohttp
import json
import os
import io
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from PIL import Image, ImageDraw, ImageFont

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

DEFAULT_CONFIG = {
    "main_channel_id": 1560358432165466303,
    "big_channel_id": None,
    "high_value_threshold": 15000000,
    "server_region": "europe",
    "tracked_players": ["Szynkus"]
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
# POMOCNICZE: POBIERANIE CEN I IKON
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

async def fetch_item_image(session, item_type, quality=1):
    if not item_type:
        return None
    url = f"https://render.albiononline.com/v1/item/{item_type}.png?quality={quality}&size=100"
    try:
        async with session.get(url, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.read()
                img = Image.open(io.BytesIO(data)).convert("RGBA")
                return img
    except Exception:
        pass
    return None

# ==========================================
# GENERATOR STYLU "KILLBOARD#1" (PILLOW)
# ==========================================
async def generate_kill_image(session, killer_name, victim_name, killer_guild, victim_guild, 
                                killer_equip, victim_equip, victim_inventory, 
                                kill_fame=0, total_value=0, time_str=""):

    canvas_w, canvas_h = 700, 520
    # Jasne tło papierowe w stylu KillBoard#1
    bg_color = (219, 203, 180, 255)
    img = Image.new("RGBA", (canvas_w, canvas_h), bg_color)
    draw = ImageDraw.Draw(img)

    slot_size = 54
    spacing = 5

    try:
        font_name = ImageFont.truetype("arial.ttf", 16)
        font_guild = ImageFont.truetype("arial.ttf", 12)
        font_stats = ImageFont.truetype("arial.ttf", 14)
        font_small = ImageFont.truetype("arial.ttf", 10)
    except Exception:
        font_name = font_guild = font_stats = font_small = ImageFont.load_default()

    # 1. NICKI I GILDIE
    # Zabójca
    draw.text((80, 15), f"{killer_name}", fill=(0, 0, 0, 255), font=font_name)
    draw.text((80, 35), f"{killer_guild}", fill=(80, 80, 80, 255), font=font_guild)

    # Ofiara
    draw.text((500, 15), f"{victim_name}", fill=(0, 0, 0, 255), font=font_name)
    draw.text((500, 35), f"{victim_guild}", fill=(80, 80, 80, 255), font=font_guild)

    # 2. EKWIPUNEK (UKŁAD SIATKI ALBIONA)
    layout = {
        "Head": (0, 0), "Armor": (0, 1), "Shoes": (0, 2),
        "MainHand": (1, 1), "OffHand": (2, 1),
        "Cape": (2, 0), "Mount": (0, 3),
        "Food": (1, 2), "Potion": (2, 2)
    }

    start_y = 60
    start_x_killer = 40
    start_x_victim = 480

    async def draw_grid(start_x, equip):
        for slot, pos in layout.items():
            x = start_x + pos[0] * (slot_size + spacing)
            y = start_y + pos[1] * (slot_size + spacing)
            
            # Ramka slofów z zakrzywionym brzegiem
            draw.rounded_rectangle([x, y, x + slot_size, y + slot_size], radius=4, fill=(195, 178, 155, 255), outline=(160, 142, 118, 255), width=2)
            
            item = equip.get(slot) if equip else None
            if item and item.get("Type"):
                item_img = await fetch_item_image(session, item.get("Type"), item.get("Quality", 1))
                if item_img:
                    item_img = item_img.resize((slot_size + 10, slot_size + 10))
                    img.paste(item_img, (x - 5, y - 5), item_img)

    await draw_grid(start_x_killer, killer_equip)
    await draw_grid(start_x_victim, victim_equip)

    # 3. ŚRODKOWA SEKCJA STATYSTYK (KILLBOARD STYLE)
    center_x = canvas_w // 2

    # Fame
    fame_val_str = f"{kill_fame:,}"
    draw.text((center_x - 30, start_y + 20), "💎 Fame", fill=(120, 30, 30, 255), font=font_small)
    draw.text((center_x - 30, start_y + 35), f"{fame_val_str}", fill=(0, 0, 0, 255), font=font_stats)

    # Loot
    val_str = f"{total_value:,}"
    draw.text((center_x - 30, start_y + 70), "💰 Silver", fill=(100, 80, 20, 255), font=font_small)
    draw.text((center_x - 30, start_y + 85), f"{val_str}", fill=(0, 0, 0, 255), font=font_stats)

    # Czas
    draw.text((center_x - 35, start_y + 130), f"{time_str or 'Zabójstwo'}", fill=(80, 80, 80, 255), font=font_small)

    # 4. PLECAK (VICTIM'S INVENTORY) NA DOLE
    inv_y = 330
    draw.text((center_x - 60, inv_y - 20), "VICTIM'S INVENTORY", fill=(60, 60, 60, 255), font=font_small)

    inv_slot_size = 46
    inv_spacing = 4
    cols = 10
    rows = 3

    inv_start_x = (canvas_w - (cols * (inv_slot_size + inv_spacing))) // 2

    for r in range(rows):
        for c in range(cols):
            idx = r * cols + c
            x = inv_start_x + c * (inv_slot_size + inv_spacing)
            y = inv_y + r * (inv_slot_size + inv_spacing)

            draw.rounded_rectangle([x, y, x + inv_slot_size, y + inv_slot_size], radius=3, fill=(195, 178, 155, 255), outline=(160, 142, 118, 255))

            if victim_inventory and idx < len(victim_inventory):
                item = victim_inventory[idx]
                if item and item.get("Type"):
                    item_img = await fetch_item_image(session, item.get("Type"), item.get("Quality", 1))
                    if item_img:
                        item_img = item_img.resize((inv_slot_size + 6, inv_slot_size + 6))
                        img.paste(item_img, (x - 3, y - 3), item_img)

                    cnt = item.get("Count", 1)
                    if cnt > 1:
                        draw.text((x + 2, y + inv_slot_size - 12), f"{cnt}", fill=(255, 255, 255, 255), font=font_small)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer

# ==========================================
# PĘTLA SPRAWDZAJĄCA KILLBOARD
# ==========================================
@tasks.loop(seconds=30)
async def check_albion_kills():
    global config
    config = load_config()
    
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
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    print(f"[API ERROR] Kod odpowiedzi API: {resp.status}", flush=True)
                    return
                events = await resp.json()
                print(f"[API OK] Pobrano {len(events)} ostatnich zdarzeń z serwera ({region.upper()}).", flush=True)
        except Exception as e:
            print(f"[API ERROR] Błąd połączenia z API: {e}", flush=True)
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
            killer_equip = event.get("Killer", {}).get("Equipment", {})
            victim_inventory = [item for item in event.get("Victim", {}).get("Inventory", []) if item]

            all_items = [item.get("Type") for item in victim_equip.values() if item]
            all_items += [item.get("Type") for item in victim_inventory if item]
            prices = await get_item_prices(session, all_items, region)
            
            equip_value = calculate_equipment_value(victim_equip, prices)
            inv_value = sum(prices.get(i.get("Type"), 0) * i.get("Count", 1) for i in victim_inventory)
            total_value = equip_value + inv_value
            kill_fame = event.get("TotalVictimKillFame", 0)

            k_guild = event.get('Killer', {}).get('GuildName') or ''
            v_guild = event.get('Victim', {}).get('GuildName') or ''
            time_raw = event.get("TimeStamp", "")
            time_formatted = time_raw[:16].replace("T", " ") if time_raw else ""

            threshold = config.get("high_value_threshold", 15000000)

            img_buffer = await generate_kill_image(
                session, killer_name, victim_name, k_guild, v_guild,
                killer_equip, victim_equip, victim_inventory,
                kill_fame, total_value, time_formatted
            )
            file = discord.File(fp=img_buffer, filename="killboard.png")

            embed_color = discord.Color.green() if is_killer else discord.Color.red() if is_victim else discord.Color.gold()
            embed = discord.Embed(
                title=f"⚔️ {killer_name}  ➔  {victim_name}",
                url=f"https://albiononline.com/killboard/kill/{event_id}",
                color=embed_color
            )
            embed.set_image(url="attachment://killboard.png")

            try:
                await main_channel.send(file=file, embed=embed)
                print(f"[SUCCESS] Wysłano wyrenderowany killboard w stylu KillBoard#1!", flush=True)
            except Exception as e:
                print(f"[DISCORD ERROR] {e}", flush=True)

            if total_value >= threshold and big_channel:
                img_buffer.seek(0)
                big_file = discord.File(fp=img_buffer, filename="killboard.png")
                await big_channel.send(
                    content=f"🚨 **BIG KILL / LEGENDARNY LOOT!** 🚨",
                    file=big_file,
                    embed=embed
                )

# ==========================================
# KOMENDY DISCORDA
# ==========================================
@bot.tree.command(name="test_kill", description="Wygeneruj testowy obrazek w stylu KillBoard#1")
async def test_kill(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    mock_killer_equip = {
        "MainHand": {"Type": "T8_MAIN_SWORD", "Quality": 3},
        "Armor": {"Type": "T8_ARMOR_LEATHER_SET", "Quality": 2},
        "Head": {"Type": "T8_HEAD_LEATHER", "Quality": 1},
        "Shoes": {"Type": "T8_SHOES_LEATHER", "Quality": 1},
        "Cape": {"Type": "T8_CAPE", "Quality": 1},
        "Potion": {"Type": "T8_POTION_HEAL", "Quality": 1},
        "Food": {"Type": "T8_MEAL_STEW", "Quality": 1}
    }
    mock_victim_equip = {
        "MainHand": {"Type": "T7_MAIN_AXE", "Quality": 2},
        "Armor": {"Type": "T7_ARMOR_PLATE", "Quality": 1},
        "Head": {"Type": "T7_HEAD_PLATE", "Quality": 1},
        "Shoes": {"Type": "T7_SHOES_PLATE", "Quality": 1},
        "Mount": {"Type": "T5_MOUNT_HORSE", "Quality": 1},
        "Food": {"Type": "T7_MEAL_STEW", "Quality": 1}
    }
    mock_inventory = [
        {"Type": "T8_ORE", "Count": 120},
        {"Type": "T6_POTION_HEAL", "Count": 15},
        {"Type": "T7_MEAL_STEW", "Count": 5},
        {"Type": "T8_BAG", "Count": 1}
    ]

    async with aiohttp.ClientSession() as session:
        img_buffer = await generate_kill_image(
            session, 
            killer_name="AwesomeNinja", 
            victim_name="MitchDagger", 
            killer_guild="BYOT", 
            victim_guild="G3rm Craft", 
            killer_equip=mock_killer_equip, 
            victim_equip=mock_victim_equip, 
            victim_inventory=mock_inventory,
            kill_fame=2853543,
            total_value=102530668,
            time_str="22:49 03 Jan 2026"
        )
        file = discord.File(fp=img_buffer, filename="killboard.png")
        
        embed = discord.Embed(
            title="🧪 TESTOWY KILLBOARD (Styl KillBoard#1)",
            color=discord.Color.blurple()
        )
        embed.set_image(url="attachment://killboard.png")
        
        await interaction.followup.send(file=file, embed=embed)

@bot.tree.command(name="dodaj", description="Dodaj gracza do śledzenia")
async def dodaj(interaction: discord.Interaction, nick: str):
    if nick in config["tracked_players"]:
        await interaction.response.send_message(f"⚠️ Gracz **{nick}** już jest śledzony.", ephemeral=True)
        return
    config["tracked_players"].append(nick)
    save_config(config)
    await interaction.response.send_message(f"✅ Dodano gracza **{nick}**.", ephemeral=False)

@bot.tree.command(name="usun", description="Usuń gracza ze śledzenia")
async def usun(interaction: discord.Interaction, nick: str):
    if nick not in config["tracked_players"]:
        await interaction.response.send_message(f"❌ Gracz **{nick}** nie jest śledzony.", ephemeral=True)
        return
    config["tracked_players"].remove(nick)
    save_config(config)
    await interaction.response.send_message(f"🗑️ Usunięto gracza **{nick}**.", ephemeral=False)

@bot.tree.command(name="lista", description="Lista śledzonych graczy")
async def lista(interaction: discord.Interaction):
    players = config["tracked_players"]
    players_str = "\n".join([f"• {p}" for p in players]) if players else "Brak"
    embed = discord.Embed(title="⚙️ Konfiguracja", color=discord.Color.blurple())
    embed.add_field(name="Śledzeni gracze", value=players_str, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=False)

@bot.tree.command(name="ustaw_kanaly", description="Ustaw kanał powiadomień")
async def ustaw_kanaly(interaction: discord.Interaction, glowny: discord.TextChannel):
    config["main_channel_id"] = glowny.id
    save_config(config)
    await interaction.response.send_message(f"✅ Ustawiono kanał główny na {glowny.mention}!", ephemeral=False)

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
        print("❌ BŁĄD: Brak DISCORD_TOKEN!", flush=True)
    else:
        bot.run(TOKEN)
