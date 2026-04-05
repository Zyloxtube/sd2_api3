import discord
from discord import app_commands
import asyncio
import requests
import time
import re
import random
import string
import os
from html.parser import HTMLParser
from typing import Optional
import functools

TOKEN = os.environ["DISCORD_BOT_TOKEN"]

# ========== Guerrilla Mail ==========
DOMAIN_OPTIONS = ["sharklasers.com", "guerrillamail.net", "guerrillamail.com"]
API_BASE = "https://api.guerrillamail.com/ajax.php"


def generate_temp_email():
    response = requests.get(f"{API_BASE}?f=get_email_address")
    data = response.json()
    if "email_addr" not in data:
        raise Exception(f"Failed to generate temp email. Response: {data}")
    sid_token = data["sid_token"]
    local_part = data["email_addr"].split("@")[0]
    email = f"{local_part}@{DOMAIN_OPTIONS[0]}"
    return email, sid_token


def generate_random_password():
    upper = random.choice(string.ascii_uppercase)
    lower = "".join(random.choices(string.ascii_lowercase, k=3))
    nums = str(random.randint(1000, 9999))
    return upper + lower + nums


def send_verification_code(email):
    response = requests.post(
        "https://api.buzzy.now/api/v1/user/send-email-code",
        json={"email": email, "type": 1},
        headers={"Content-Type": "application/json"},
    )
    data = response.json()
    if data.get("code") != 200:
        raise Exception(f"Failed to send verification code. Response: {data}")
    return True


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []

    def handle_data(self, data):
        self._parts.append(data)

    def get_text(self):
        return " ".join(self._parts)


def strip_html(html):
    if not html:
        return ""
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html)
        return parser.get_text()
    except Exception:
        return html


def extract_code_from_text(text):
    if not text:
        return None
    m = re.search(r"(\d{6})", text)
    if m:
        return m.group(1)
    m = re.search(r"(\d{5})", text)
    if m:
        return m.group(1)
    m = re.search(
        r"(?:verification\s+code|verification|code|otp)[^\d]{0,20}?(\d{4})",
        text,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    m = re.search(r"(\d{4})", text)
    return m.group(1) if m else None


def wait_for_code(sid_token, max_attempts=30, interval=4):
    current_seq = 0
    seen_ids = set()
    for attempt in range(max_attempts):
        response = requests.get(
            f"{API_BASE}?f=check_email&sid_token={sid_token}&seq={current_seq}"
        )
        data = response.json()
        if "seq" in data:
            current_seq = data["seq"]

        for mail in data.get("list", []):
            mail_id = mail.get("mail_id")
            if mail_id in seen_ids:
                continue
            seen_ids.add(mail_id)

            code = extract_code_from_text(
                mail.get("mail_subject", "")
            ) or extract_code_from_text(mail.get("mail_from", ""))

            if not code:
                try:
                    full = requests.get(
                        f"{API_BASE}?f=fetch_email&email_id={mail_id}&sid_token={sid_token}"
                    ).json()
                    body = full.get("mail_body", "") or full.get("mail_excerpt", "")
                    code = extract_code_from_text(
                        strip_html(body)
                    ) or extract_code_from_text(body)
                except Exception:
                    pass

            if code:
                return code

        time.sleep(interval)
    return None


def register_user(email, password, email_code):
    response = requests.post(
        "https://api.buzzy.now/api/v1/user/register",
        json={"email": email, "password": password, "emailCode": email_code},
        headers={"Content-Type": "application/json"},
    )
    data = response.json()
    if data.get("code") == 200:
        return data["data"]["token"]
    raise Exception(f"Registration failed. Response: {data}")


def create_video_project(token, prompt):
    response = requests.post(
        "https://api.buzzy.now/api/app/v1/project/create",
        json={
            "name": "Untitled",
            "workflowType": "SOTA",
            "instructionSegments": [{"type": "text", "content": prompt}],
            "imageUrls": [],
            "duration": 10,
            "aspectRatio": "16:9",
            "prompt": prompt,
        },
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    data = response.json()
    if data.get("code") == 201:
        return data["data"]["id"]
    raise Exception(f"Failed to create video project. Response: {data}")


def poll_for_video(token, project_id, interval=5, status_callback=None):
    poll_count = 0

    while True:
        poll_count += 1

        response = requests.get(
            "https://api.buzzy.now/api/app/v1/project/list?pageNumber=1&pageSize=100",
            headers={
                "Authorization": f"Bearer {token}",
                "accept": "application/json, text/plain, */*",
                "accept-language": "en-US,en;q=0.9",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        )

        data = response.json()

        if data.get("code") != 200:
            if status_callback:
                status_callback(f"⚠️ API returned non-200 code: {data.get('code')}")
            print(f"[poll #{poll_count}] API returned non-200 code: {data.get('code')}")
            time.sleep(interval)
            continue

        records = data.get("data", {}).get("records", [])
        target = next((p for p in records if p.get("id") == project_id), None)

        if target:
            status = target.get("status", "unknown")
            print(f"[poll #{poll_count}] project={project_id} status={status}")

            if status_callback:
                status_callback(f"📡 Poll #{poll_count}: Status = {status}")

            if status == "success":
                results = target.get("results", [])
                if results and len(results) > 0:
                    video_url = results[0].get("videoUrl")
                    if video_url:
                        if status_callback:
                            status_callback(f"✅ Video found in results array!")
                        print(
                            f"[poll #{poll_count}] Found video URL in results: {video_url}"
                        )
                        return video_url

                video_urls = target.get("videoUrls", [])
                if video_urls and len(video_urls) > 0:
                    video_url = video_urls[0]
                    if video_url:
                        if status_callback:
                            status_callback(f"✅ Video found in videoUrls array!")
                        print(
                            f"[poll #{poll_count}] Found video URL in videoUrls: {video_url}"
                        )
                        return video_url

                if status_callback:
                    status_callback(
                        f"⏳ Status=success but no video URL yet, continuing to poll..."
                    )
                print(
                    f"[poll #{poll_count}] status=success but no videoUrl found yet, continuing..."
                )

            elif status == "failed":
                error_msg = f"❌ Video generation failed!"
                if status_callback:
                    status_callback(error_msg)
                raise Exception(f"Video generation failed. Project response: {target}")
            else:
                if status_callback:
                    status_callback(f"⏳ Status: {status} - Still processing...")
                print(
                    f"[poll #{poll_count}] status={status}, still processing, waiting {interval}s..."
                )
        else:
            if status_callback:
                status_callback(
                    f"📡 Poll #{poll_count}: Project not found in list yet..."
                )
            print(
                f"[poll #{poll_count}] project {project_id} not found in list yet, waiting..."
            )

        time.sleep(interval)


def run_full_pipeline(prompt, status_callback=None):
    if status_callback:
        status_callback(f"🚀 Starting video generation for: {prompt[:50]}...")
    print(f"[pipeline] Starting for prompt: {prompt!r}")

    if status_callback:
        status_callback(f"📧 Step 1/7: Generating temporary email...")
    print("[pipeline] Generating temp email...")
    email, sid_token = generate_temp_email()
    if status_callback:
        status_callback(f"✅ Generated: {email}")
    print(f"[pipeline] Email: {email}")

    if status_callback:
        status_callback(f"🔑 Step 2/7: Generating random password...")
    password = generate_random_password()
    if status_callback:
        status_callback(f"✅ Password generated (hidden)")
    print(f"[pipeline] Password generated")

    if status_callback:
        status_callback(f"📨 Step 3/7: Sending verification code to {email}...")
    print("[pipeline] Sending verification code...")
    send_verification_code(email)
    if status_callback:
        status_callback(f"✅ Verification code sent!")

    if status_callback:
        status_callback(
            f"⏳ Step 4/7: Waiting for verification code in inbox (up to 2 minutes)..."
        )
    print("[pipeline] Waiting for verification code in inbox...")
    code = wait_for_code(sid_token)
    if not code:
        error_msg = "❌ Did not receive verification code after waiting"
        if status_callback:
            status_callback(error_msg)
        raise Exception(
            "Did not receive a verification code in the temp email after waiting"
        )
    if status_callback:
        status_callback(f"✅ Received verification code: {code}")
    print(f"[pipeline] Got code: {code}")

    if status_callback:
        status_callback(f"👤 Step 5/7: Registering user with Buzzy...")
    print("[pipeline] Registering user...")
    token = register_user(email, password, code)
    if status_callback:
        status_callback(f"✅ Registration successful! Token obtained")
    print("[pipeline] Registered successfully")

    if status_callback:
        status_callback(f"🎬 Step 6/7: Creating video project with prompt...")
    print("[pipeline] Creating video project...")
    project_id = create_video_project(token, prompt)
    if status_callback:
        status_callback(f"✅ Project created! ID: {project_id[:8]}...")
    print(f"[pipeline] Project created: {project_id}")

    if status_callback:
        status_callback(
            f"🎥 Step 7/7: Waiting for video generation (this may take several minutes)..."
        )
        status_callback(f"📡 Starting polling every 5 seconds...")
    print("[pipeline] Polling for video result (no timeout)...")
    video_url = poll_for_video(token, project_id, status_callback=status_callback)
    if status_callback:
        status_callback(f"✅ Video generation complete!")
    print(f"[pipeline] Done! URL: {video_url}")
    return video_url


# ========== Discord Bot ==========

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


@client.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {client.user} — slash commands synced")


@tree.command(
    name="generate", description="Generate a video from a text prompt using Buzzy"
)
@app_commands.describe(prompt="The prompt to generate a video from")
async def generate(interaction: discord.Interaction, prompt: str):
    await interaction.response.send_message(
        f"🎬 **Video Generation Started**\nPrompt: `{prompt}`\n\nStarting process..."
    )

    start_time = time.time()
    result_container = {"url": None, "error": None, "done": False}
    status_messages = []

    status_queue = asyncio.Queue()

    def add_status_message(msg):
        asyncio.run_coroutine_threadsafe(status_queue.put(msg), client.loop)

    async def update_discord_message():
        while not result_container["done"]:
            try:
                try:
                    while True:
                        msg = await asyncio.wait_for(status_queue.get(), timeout=1.0)
                        status_messages.append(msg)
                        if len(status_messages) > 20:
                            status_messages.pop(0)
                except asyncio.TimeoutError:
                    pass

                if not result_container["done"]:
                    elapsed = int(time.time() - start_time)
                    status_text = "\n".join(status_messages[-15:])
                    content = f"🎬 **Generating Video**\nPrompt: `{prompt}`\nTime: `{elapsed}s`\n\n{status_text}"

                    try:
                        await interaction.edit_original_response(content=content)
                    except Exception as e:
                        print(f"Failed to update Discord message: {e}")

            except Exception as e:
                print(f"Error in update_discord_message: {e}")
                await asyncio.sleep(1)

    async def run_pipeline():
        loop = asyncio.get_event_loop()
        try:
            url = await loop.run_in_executor(
                None, functools.partial(run_full_pipeline, prompt, add_status_message)
            )
            result_container["url"] = url
            add_status_message(f"✅ **COMPLETE!** Video URL received")
        except Exception as e:
            error_msg = str(e)
            result_container["error"] = error_msg
            add_status_message(f"❌ **ERROR:** {error_msg}")
        finally:
            result_container["done"] = True

    update_task = asyncio.create_task(update_discord_message())
    pipeline_task = asyncio.create_task(run_pipeline())

    await pipeline_task

    await asyncio.sleep(1)
    update_task.cancel()
    try:
        await update_task
    except asyncio.CancelledError:
        pass

    elapsed = int(time.time() - start_time)

    if result_container["error"]:
        final_content = (
            f"❌ **Video Generation Failed**\n"
            f"Prompt: `{prompt}`\n"
            f"Time taken: `{elapsed} seconds`\n\n"
            f"**Error:**\n{result_container['error']}\n\n"
            f"**Last status:**\n" + "\n".join(status_messages[-10:])
        )
        await interaction.edit_original_response(content=final_content)
    elif result_container["url"]:
        final_content = (
            f"✅ **Video Ready!**\n"
            f"**Prompt:** `{prompt}`\n"
            f"**Time taken:** `{elapsed} seconds`\n"
            f"**Steps completed:** {len(status_messages)}\n\n"
            f"🎥 **Video URL:**\n{result_container['url']}\n\n"
            f"**Process Log:**\n" + "\n".join(status_messages[-10:])
        )
        await interaction.edit_original_response(content=final_content)
    else:
        final_content = (
            f"⚠️ **Generation Incomplete**\n"
            f"Prompt: `{prompt}`\n"
            f"Time taken: `{elapsed} seconds`\n\n"
            f"No video URL was returned.\n\n"
            f"**Last status:**\n" + "\n".join(status_messages[-10:])
        )
        await interaction.edit_original_response(content=final_content)


client.run(TOKEN)
