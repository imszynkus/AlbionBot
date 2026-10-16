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
# PERFEKCYJNIE ZBALANOWANY GENERATOR (PILLOW)
# ==========================================
async def generate_kill_image(session, killer_name, victim_name, killer_guild, victim_guild, 
                                killer_equip, victim_equip, victim_inventory, 
                                kill_fame=0, total_value=0, time_str=""):

    canvas_w, canvas_h = 760, 480
    bg_color = (218, 206, 185, 255) # Jasne papierowe tło KillBoard#1
    img = Image.new("RGBA", (canvas_w, canvas_h), bg_color)
    draw = ImageDraw.Draw(img)

    slot_size = 54

    try:
        font_name = ImageFont.truetype("arial.ttf", 15)
        font_guild = ImageFont.truetype("arial.ttf", 11)
        font_stats = ImageFont.truetype("arial.ttf", 13)
        font_small = ImageFont.truetype("arial.ttf", 10)
        font_center = ImageFont.truetype("arial.ttf", 11)
    except Exception:
        font_name = font_guild = font_stats = font_small = font_center = ImageFont.load_default()

    # 1. NAGŁÓWKI (NICKI I GILDIE)
    draw.text((45, 18), f"{killer_name}", fill=(20, 20, 20, 255), font=font_name)
    draw.text((45, 36), f"{killer_guild or 'Brak Gildii'}", fill=(90, 85, 75, 255), font=font_guild)

    draw.text((545, 18), f"{victim_name}", fill=(20, 20, 20, 255), font=font_name)
    draw.text((545, 36), f"{victim_guild or 'Brak Gildii'}", fill=(90, 85, 75, 255), font=font_guild)

    # 2. SZTYWNE POZYCJE SLOTÓW EKWIPUNKU
    k_slots = {
        "Bag": (45, 65),       "Head": (103, 65),      "Cape": (161, 65),
        "MainHand": (45, 123), "Armor": (103, 123),     "OffHand": (161, 123),
        "Food": (45, 181),     "Shoes": (103, 181),     "Potion": (161, 181),
        "Mount": (103, 239)
    }

    v_slots = {
        "Bag": (545, 65),      "Head": (603, 65),      "Cape": (661, 65),
        "MainHand": (545, 123),"Armor": (603, 123),    "OffHand": (661, 123),
        "Food": (545, 181),    "Shoes": (603, 181),    "Potion": (661, 181),
        "Mount": (603, 239)
    }

    async def draw_slots(slots_dict, equip_data):
        for slot_name, (x, y) in slots_dict.items():
            # Ramka tła
            draw.rounded_rectangle([x, y, x + slot_size, y + slot_size], radius=4, fill=(198, 185, 162, 255), outline=(160, 145, 125, 255), width=2)
            
            item = equip_data.get(slot_name) if equip_data else None
            if item and item.get("Type"):
                item_img = await fetch_item_image(session, item.get("Type"), item.get("Quality", 1))
                if item_img:
                    # Dopasowanie ikon – skalowanie ze zdecydowanym wypełnieniem kratek
                    item_img = item_img.resize((slot_size + 12, slot_size + 12))
                    img.paste(item_img, (x - 6, y - 6), item_img)

    await draw_slots(k_slots, killer_equip)
    await draw_slots(v_slots, victim_equip)

    # 3. CENTRUM (FAME, SILVER, DEATH)
    center_x = canvas_w // 2

    # Rysowanie ikony Kryształu Fame (Czerwony diament zamiast zepsutej czcionki)
    fame_icon_x, fame_icon_y = center_x - 48, 77
    draw.polygon([(fame_icon_x+5, fame_icon_y), (fame_icon_x+10, fame_icon_y+6), (fame_icon_x+5, fame_icon_y+12), (fame_icon_x, fame_icon_y+6)], fill=(150, 40, 40, 255))
    draw.text((center_x - 32, 73), f"{kill_fame:,.0f}", fill=(120, 30, 30, 255), font=font_stats)
    if kill_fame > 0:
        draw.text((center_x - 32, 91), f"3 x {int(kill_fame/3):,.0f}", fill=(90, 85, 75, 255), font=font_small)

    # Rysowanie ikony Silvera (Złota moneta)
    coin_x, coin_y = center_x - 48, 137
    draw.ellipse([coin_x, coin_y, coin_x + 10, coin_y + 10], fill=(180, 140, 30, 255), outline=(120, 90, 20, 255))
    draw.text((center_x - 32, 133), f"{total_value:,.0f}", fill=(40, 30, 30, 255), font=font_stats)

    # Kółko DEATH
    circle_center = (center_x, 200)
    radius = 24
    draw.ellipse([circle_center[0] - radius, circle_center[1] - radius, circle_center[0] + radius, circle_center[1] + radius], fill=(45, 45, 45, 255))
    draw.text((center_x - 20, circle_center[1] - 5), "DEATH", fill=(255, 255, 255, 255), font=font_center)

    if time_str:
        draw.text((center_x - 45, 235), f"{time_str}", fill=(80, 75, 65, 255), font=font_small)

    # 4. PLECAK (VICTIM'S INVENTORY)
    if victim_inventory:
        inv_y = 310
        draw.text((center_x - 55, inv_y - 18), "VICTIM'S INVENTORY", fill=(70, 65, 55, 255), font=font_small)

        inv_slot_size = 46
        inv_spacing = 4
        
        items_to_show = victim_inventory[:10]
        cols = len(items_to_show)
        if cols < 4:
            cols = 4

        grid_w = cols * (inv_slot_size + inv_spacing) - inv_spacing
        inv_start_x = (canvas_w - grid_w) // 2

        for idx in range(cols):
            x = inv_start_x + idx * (inv_slot_size + inv_spacing)
            y = inv_y

            draw.rounded_rectangle([x, y, x + inv_slot_size, y + inv_slot_size], radius=4, fill=(198, 185, 162, 255), outline=(160, 145, 125, 255), width=1)

            if idx < len(victim_inventory):
                item = victim_inventory[idx]
                if item and item.get("Type"):
                    item_img = await fetch_item_image(session, item.get("Type"), item.get("Quality", 1))
                    if item_img:
                        item_img = item_img.resize((inv_slot_size + 8, inv_slot_size + 8))
                        img.paste(item_img, (x - 4, y - 4), item_img)

                    cnt = item.get("Count", 1)
                    if cnt > 1:
                        draw.text((x + 3, y + inv_slot_size - 13), f"x{cnt}", fill=(255, 255, 255, 255), font=font_small)

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
# ==========================================
# KOMENDA SLASH (/test_kill) DLA NOWEGO GENERATORA
# ==========================================
@bot.tree.command(name="test_kill", description="Wygeneruj testowy obrazek killboardu z pełnym ekwipunkiem")
async def test_kill(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    # Pełne 10 slotów dla Zabójcy
    mock_killer_equip = {
        "Bag": {"Type": "T8_BAG", "Quality": 2},
        "Head": {"Type": "T8_HEAD_LEATHER", "Quality": 3},
        "Cape": {"Type": "T8_CAPE", "Quality": 2},
        "MainHand": {"Type": "T8_MAIN_SWORD", "Quality": 3},
        "Armor": {"Type": "T8_ARMOR_LEATHER_SET", "Quality": 2},
        "OffHand": {"Type": "T8_OFF_TOWER_SHIELD", "Quality": 1},
        "Food": {"Type": "T8_MEAL_STEW", "Quality": 1},
        "Shoes": {"Type": "T8_SHOES_LEATHER", "Quality": 2},
        "Potion": {"Type": "T8_POTION_HEAL", "Quality": 1},
        "Mount": {"Type": "T8_MOUNT_HORSE", "Quality": 1}
    }

    # Pełne 10 slotów dla Ofiary
    mock_victim_equip = {
        "Bag": {"Type": "T7_BAG", "Quality": 1},
        "Head": {"Type": "T7_HEAD_PLATE", "Quality": 2},
        "Cape": {"Type": "T7_CAPE", "Quality": 1},
        "MainHand": {"Type": "T7_MAIN_AXE", "Quality": 2},
        "Armor": {"Type": "T7_ARMOR_PLATE", "Quality": 2},
        "OffHand": {"Type": "T7_OFF_SHIELD", "Quality": 1},
        "Food": {"Type": "T7_MEAL_STEW", "Quality": 1},
        "Shoes": {"Type": "T7_SHOES_PLATE", "Quality": 1},
        "Potion": {"Type": "T7_POTION_HEAL", "Quality": 1},
        "Mount": {"Type": "T7_MOUNT_ARMORED_HORSE", "Quality": 1}
    }

    # Duży zestaw przedmiotów w plecaku ofiary
    mock_inventory = [
        {"Type": "T8_ORE", "Count": 120},
        {"Type": "T6_POTION_HEAL", "Count": 15},
        {"Type": "T7_MEAL_STEW", "Count": 5},
        {"Type": "T8_BAG", "Count": 1},
        {"Type": "T6_HEAD_CLOTH", "Count": 1},
        {"Type": "T7_ARMOR_LEATHER", "Count": 2},
        {"Type": "T8_MOUNT_HORSE", "Count": 1},
        {"Type": "T6_RUNE", "Count": 250}
    ]

    async with aiohttp.ClientSession() as session:
        img_buffer = await generate_kill_image(
            session=session,
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
            title="🧪 KILLBOARD (Pełny Ekwipunek)",
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
