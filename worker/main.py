import os
import asyncio
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field, ValidationError
from enum import Enum

import httpx
import asyncpg
from arq.connections import RedisSettings
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from google import genai
from google.genai import types

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_DSN = os.getenv("DB_DSN", "postgresql://reels_user:***@postgres:5432/reels_db")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

MAX_VIDEO_DURATION_SECONDS = 600

COBALT_INSTANCES = [
    "http://cobalt-api:9000/",  
    "https://dwnld.nichind.dev/",     
    "https://co.eepy.today/"          
]

class CategoryEnum(str, Enum):
    HEALTH_AND_FITNESS = "HEALTH_AND_FITNESS"
    FOOD_AND_RECIPES = "FOOD_AND_RECIPES"
    FINANCE_AND_CRYPTO = "FINANCE_AND_CRYPTO"
    PRODUCT_REVIEWS = "PRODUCT_REVIEWS"
    LIFEHACKS_AND_DIY = "LIFEHACKS_AND_DIY"
    EDUCATION = "EDUCATION"
    ENTERTAINMENT_ONLY = "ENTERTAINMENT_ONLY"

class EvidenceLevelEnum(str, Enum):
    shown = "shown"
    mentioned_only = "mentioned_only"

class PerceptionResult(BaseModel):
    raw_transcript: str = Field(description="Полная стенограмма видео")
    primary_category: CategoryEnum
    secondary_categories: List[CategoryEnum]
    evidence_level: EvidenceLevelEnum = Field(description="Показан ли процесс/интерфейс в кадре (shown) или только упомянут голосом (mentioned_only)")
    ui_elements: str = Field(description="Текст с кнопок, ценников или интерфейсов в кадре")

def build_extractor_prompt(perception: PerceptionResult) -> str:
    base_prompt = f"""
    You are Reels2Action SaaS AI. Convert this short video into a concrete To-Do item.
    
    TRANSCRIPT:
    {perception.raw_transcript}
    
    ON-SCREEN TEXT / UI:
    {perception.ui_elements}
    
    === STRICT RULES ===
    1. TAGGING SYSTEM: You MUST use these tags:
       📺 [ФАКТ] - Visually confirmed UI, texts, or physical actions.
       🧠 [АНАЛИЗ] - Your logical deductions or checks.
    2. TOOL BLINDNESS: If the Perception data states that a UI is unreadable/blurry, DO NOT guess its name. You MUST output "⚠️ [ПРЕДПОЛОЖЕНИЕ]: Нечитаемый интерфейс, возможно <тип сервиса>".
    3. LEGAL/SAFETY DIRECTIVE: DO NOT use defamatory terms (e.g., 'scam'). Critique claims objectively.
    """

    if perception.primary_category not in (CategoryEnum.FINANCE_AND_CRYPTO, CategoryEnum.ENTERTAINMENT_ONLY):
        base_prompt += """
    4. OPTIONAL 'BETTER WAY' BLOCK: If (and ONLY if) you are absolutely certain there is a faster, cheaper, or more modern tool/method to achieve the author's goal, append a "**🚀 МОЖНО ЛУЧШЕ:** [Твой вариант]" block at the very end. If the video's method is already optimal or you are unsure, omit this block entirely.
        """

    if perception.evidence_level == EvidenceLevelEnum.mentioned_only:
        base_prompt += "\n\n🚨 EVIDENCE LOCK TRIGGERED: The author ONLY mentioned the process/tool but DID NOT show it on screen. DO NOT invent UI steps, schemas, or architectures. State clearly that visual proof is missing."

    action_block = ""
    disclaimer = ""
    
    if perception.primary_category == CategoryEnum.FOOD_AND_RECIPES:
        action_block = "**🎯 ЧТО С ЭТИМ ДЕЛАТЬ (Приготовить):**\n- [ ] Ингредиенты\n- [ ] Шаги"
    elif perception.primary_category == CategoryEnum.HEALTH_AND_FITNESS:
        action_block = "**🎯 ЧТО С ЭТИМ ДЕЛАТЬ (Выполнить):**\n- [ ] Подходы/Техника"
    elif perception.primary_category == CategoryEnum.FINANCE_AND_CRYPTO:
        action_block = "**🎯 ЧТО С ЭТИМ ДЕЛАТЬ (Оценить риски):**\n- [ ] Чеклист проверки"
        disclaimer = "\n\n*«⚠️ Внимание: Данный разбор не является индивидуальной финансовой или инвестиционной рекомендацией. Вся информация сгенерирована ИИ на основе открытых данных и носит исключительно ознакомительный характер. Любые финансовые решения вы принимаете на свой страх и риск.»*"
    elif perception.primary_category == CategoryEnum.PRODUCT_REVIEWS:
        action_block = "**🎯 ЧТО С ЭТИМ ДЕЛАТЬ (Обдумать покупку):**\n- [ ] Плюсы/Минусы\n- [ ] Аналоги"
    elif perception.primary_category == CategoryEnum.LIFEHACKS_AND_DIY:
        action_block = "**🎯 ЧТО С ЭТИМ ДЕЛАТЬ (Попробовать):**\n- [ ] Необходимые условия"
    elif perception.primary_category == CategoryEnum.ENTERTAINMENT_ONLY:
        action_block = ""
    else:
        action_block = "**🎯 ЧТО С ЭТИМ ДЕЛАТЬ:**\n- [ ] Вывод"

    format_section = "\n=== REQUIRED FORMAT ===\n**📌 Суть:** 📺 [ФАКТ] ...\n\n"
    if action_block:
        format_section += f"{action_block}\n\n"
    format_section += "**🔍 Фактчекинг и Риски:** 🧠 [АНАЛИЗ] ..."
    if disclaimer:
        format_section += disclaimer

    return base_prompt + format_section

async def startup(ctx: Dict[Any, Any]):
    ctx['db_pool'] = await asyncpg.create_pool(dsn=DB_DSN)
    ctx['bot'] = Bot(token=BOT_TOKEN)
    ctx['genai_client'] = genai.Client(api_key=GEMINI_API_KEY)

async def shutdown(ctx: Dict[Any, Any]):
    await ctx['db_pool'].close()
    await ctx['bot'].session.close()

async def download_video(url: str, task_id: str) -> Optional[str]:
    file_path = f"/tmp/{task_id}.mp4"
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    payload = {"url": url}

    async with httpx.AsyncClient(timeout=30.0) as client:
        for instance_url in COBALT_INSTANCES:
            try:
                resp = await client.post(instance_url, json=payload, headers=headers)
                if resp.status_code != 200:
                    print(f"[Cobalt] Error {resp.status_code} on {instance_url}: {resp.text}", flush=True)
                    continue
                
                data = resp.json()
                
                download_url = data.get("url")
                if not download_url:
                    print(f"[Cobalt] No URL in response from {instance_url}: {data}", flush=True)
                    continue

                async with client.stream("GET", download_url) as stream_resp:
                    stream_resp.raise_for_status()
                    with open(file_path, "wb") as f:
                        async for chunk in stream_resp.aiter_bytes():
                            f.write(chunk)
                return file_path
            except Exception as e:
                print(f"[Cobalt] Exception on {instance_url}: {type(e).__name__} - {e}", flush=True)
                continue
                
    # --- Fallback to yt-dlp ---
    print(f"[yt-dlp] Cobalt failed. Trying yt-dlp fallback for {url}", flush=True)
    import yt_dlp
    def _download_ytdlp():
        ydl_opts = {
            'outtmpl': file_path,
            'format': 'best',
            'quiet': True,
            'no_warnings': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
    try:
        await asyncio.to_thread(_download_ytdlp)
        if os.path.exists(file_path):
            return file_path
    except Exception as e:
        print(f"[yt-dlp] Failed: {e}", flush=True)
        
    return None

async def get_video_duration(file_path: str) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration", 
        "-of", "default=noprint_wrappers=1:nokey=1", file_path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    try:
        return float(stdout.decode().strip())
    except ValueError:
        return 0.0

async def analyze_video_job(ctx: Dict[Any, Any], task_id: str, url: str, tg_id: int):
    db: asyncpg.Pool = ctx['db_pool']
    bot: Bot = ctx['bot']
    client: genai.Client = ctx['genai_client']
    
    local_file_path = None
    gemini_file = None

    try:
        local_file_path = await download_video(url, task_id)
        if not local_file_path:
            await db.execute("UPDATE tasks SET status = 'failed' WHERE id = $1::uuid", task_id)
            await db.execute("UPDATE users SET limits_balance = limits_balance + 1 WHERE telegram_id = $1", tg_id)
            await bot.send_message(tg_id, "❌ Сервис временно недоступен из-за блокировок соцсети. Попробуйте ссылку позже.")
            return

        duration = await get_video_duration(local_file_path)
        if duration > MAX_VIDEO_DURATION_SECONDS or duration == 0.0:
            await db.execute("UPDATE tasks SET status = 'failed' WHERE id = $1::uuid", task_id)
            await bot.send_message(tg_id, "❌ Видео слишком длинное для анализа. Принимаются ролики до 10 минут.")
            return

        gemini_file = await client.aio.files.upload(file=local_file_path)
        
        while gemini_file.state.name == "PROCESSING":
            await asyncio.sleep(2)
            gemini_file = await client.aio.files.get(name=gemini_file.name)

        perception_prompt = """
        Analyze this video and extract structured data according to the schema.

        CRITICAL INSTRUCTIONS:
        1. EVIDENCE LEVEL DEFINITION:
           - Select "shown" ONLY if the actual interface, code, dashboard, or physical process is clearly visible in the frame.
           - Select "mentioned_only" if the author merely talks about the topic conceptually, without providing visual proof.
        2. UNREADABLE UI (Perception Rule 3): If an application, tool, or interface is shown on screen but the text or buttons are blurry/unreadable, DO NOT guess its name. You MUST explicitly state "Показан нечитаемый интерфейс [тип сервиса]" in the `ui_elements` field.
        3. CATEGORIES: Choose the most accurate `primary_category`. If the video spans multiple domains, list the overlapping ones in `secondary_categories`.
        4. TRANSCRIPT: Provide an exact speech transcript.
        """

        # --- Retry helper for Gemini API ---
        async def _generate_with_retry(model_name, contents, config, max_retries=4):
            import asyncio
            for attempt in range(max_retries):
                try:
                    return await client.aio.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=config
                    )
                except Exception as ex:
                    err_str = str(ex)
                    if ("503" in err_str or "429" in err_str or "UNAVAILABLE" in err_str) and attempt < max_retries - 1:
                        await asyncio.sleep(5 * (attempt + 1))
                        continue
                    raise

        perception_response = await _generate_with_retry(
            model_name='gemini-3.6-flash',
            contents=[gemini_file, perception_prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=PerceptionResult,
                temperature=0.2
            )
        )
        
        perception_data = PerceptionResult.model_validate_json(perception_response.text)

        extractor_prompt = build_extractor_prompt(perception_data)
        
        extractor_response = await _generate_with_retry(
            model_name='gemini-3.6-flash',
            contents=extractor_prompt,
            config=types.GenerateContentConfig(temperature=0.4)
        )
        
        final_text = extractor_response.text

        analysis_json = perception_data.model_dump_json()
        await db.execute(
            """UPDATE tasks 
               SET status = 'active', category = $1, analysis_data = $2::jsonb 
               WHERE id = $3::uuid""",
            perception_data.primary_category.value, analysis_json, task_id
        )

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Выполнено", callback_data=f"action:done:{task_id}")],
            [
                InlineKeyboardButton(text="⏳ Отложить", callback_data=f"action:delay:{task_id}"),
                InlineKeyboardButton(text="❌ Удалить", callback_data=f"action:delete:{task_id}")
            ]
        ])

        try:
            video_file = FSInputFile(local_file_path)
            await bot.send_video(chat_id=tg_id, video=video_file)
        except Exception as ve:
            print(f"[Worker Error | Video Upload] Failed to send video {task_id}: {ve}", flush=True)

        await bot.send_message(tg_id, final_text, reply_markup=kb, parse_mode="Markdown")

    except ValidationError as e:
        print(f"[Worker Error | JSON Validation] Task {task_id}: {e.errors()}", flush=True)
        await db.execute("UPDATE tasks SET status = 'failed' WHERE id = $1::uuid", task_id)
        await db.execute("UPDATE users SET limits_balance = limits_balance + 1 WHERE telegram_id = $1", tg_id)
        await bot.send_message(tg_id, "❌ Модель не смогла структурировать ответ. Лимит возвращен.")

    except httpx.HTTPError as e:
        print(f"[Worker Error | Network] Task {task_id}: {e}", flush=True)
        await db.execute("UPDATE tasks SET status = 'failed' WHERE id = $1::uuid", task_id)
        await db.execute("UPDATE users SET limits_balance = limits_balance + 1 WHERE telegram_id = $1", tg_id)
        await bot.send_message(tg_id, "❌ Сетевая ошибка при обработке видео. Лимит возвращен.")

    except Exception as e:
        print(f"[Worker Error | Unknown/GenAI] Task {task_id}: {type(e).__name__} - {e}", flush=True)
        await db.execute("UPDATE tasks SET status = 'failed' WHERE id = $1::uuid", task_id)
        await db.execute("UPDATE users SET limits_balance = limits_balance + 1 WHERE telegram_id = $1", tg_id)
        await bot.send_message(tg_id, "❌ Произошла системная ошибка при анализе ИИ-моделью. Баланс восстановлен.")

    finally:
        if local_file_path and os.path.exists(local_file_path):
            os.remove(local_file_path)
            
        if gemini_file:
            try:
                await client.aio.files.delete(name=gemini_file.name)
            except Exception:
                pass

class WorkerSettings:
    functions = [analyze_video_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings(host=REDIS_HOST)
    max_jobs = 10
