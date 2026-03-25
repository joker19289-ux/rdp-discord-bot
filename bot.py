import os
import json
import asyncio
from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks
from docker import from_env as docker_from_env
from flask import Flask, jsonify
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

# Load .env
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = os.getenv("PREFIX", "!")
WINDOWS_IMAGE = os.getenv("WINDOWS_IMAGE")
NO_VNC_USER = os.getenv("NO_VNC_USER")
NO_VNC_PASSWORD = os.getenv("NO_VNC_PASSWORD")
TEMP_SERVER_DURATION_DAYS = int(os.getenv("TEMP_SERVER_DURATION_DAYS", "30"))

docker_client = docker_from_env()
db_path = "db.json"

intents = discord.Intents.default()
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

app = Flask(__name__)

# -------------------------
# Работа с базой данных
# -------------------------
def load_db():
    if not os.path.exists(db_path):
        return {"servers": []}
    with open(db_path, "r") as f:
        return json.load(f)

def save_db(data):
    with open(db_path, "w") as f:
        json.dump(data, f, indent=4)

# -------------------------
# Создание Windows сервера
# -------------------------
async def create_temp_server():
    container = docker_client.containers.run(
        WINDOWS_IMAGE,
        detach=True,
        tty=True,
        stdin_open=True,
        environment={
            "VNC_USER": NO_VNC_USER,
            "VNC_PASSWORD": NO_VNC_PASSWORD
        },
        cpu_count=int(os.getenv("SERVER_CPU", 2)),
        mem_limit=os.getenv("SERVER_RAM", "8g")),
        name=f"winrdp_{int(datetime.now().timestamp())}",
        ports={'3389/tcp': None, '5900/tcp': None},  # RDP и noVNC
    )

    container.reload()
    ip = container.attrs['NetworkSettings']['IPAddress']

    expires_at = datetime.now() + timedelta(days=TEMP_SERVER_DURATION_DAYS)
    server_data = {
        "ip": ip,
        "username": NO_VNC_USER,
        "password": NO_VNC_PASSWORD,
        "expires_at": expires_at.isoformat()
    }

    db = load_db()
    db["servers"].append(server_data)
    save_db(db)

    return server_data, container

# -------------------------
# Удаление сервера
# -------------------------
def remove_expired_servers():
    db = load_db()
    updated_servers = []
    for server in db["servers"]:
        expires_at = datetime.fromisoformat(server["expires_at"])
        if expires_at < datetime.now():
            # Удаляем контейнер
            try:
                container_name = f"winrdp_{int(datetime.fromtimestamp(expires_at.timestamp()))}"
                container = docker_client.containers.get(container_name)
                container.stop()
                container.remove()
                print(f"Deleted expired server {container_name}")
            except:
                pass
        else:
            updated_servers.append(server)
    db["servers"] = updated_servers
    save_db(db)

# -------------------------
# Discord команды
# -------------------------
@bot.command()
async def rdp(ctx):
    await ctx.send("Создаю временный Windows сервер...")
    server_data, _ = await create_temp_server()
    await ctx.send(
        f"✅ Сервер готов!\nIP: {server_data['ip']}\n"
        f"Username: {server_data['username']}\n"
        f"Password: {server_data['password']}\n"
        f"Срок действия: {TEMP_SERVER_DURATION_DAYS} дней"
    )

# -------------------------
# Фоновая задача авто-очистки
# -------------------------
scheduler = AsyncIOScheduler()
scheduler.add_job(remove_expired_servers, "interval", minutes=30)
scheduler.start()

# -------------------------
# Flask /status endpoint
# -------------------------
@app.route("/status")
def status():
    db = load_db()
    return jsonify({"servers": db["servers"], "total": len(db["servers"])})

# -------------------------
# Запуск Flask в отдельном потоке
# -------------------------
def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("FLASK_PORT", 5000)))

import threading
threading.Thread(target=run_flask, daemon=True).start()

# -------------------------
# Запуск Discord бота
# -------------------------
bot.run(TOKEN)
