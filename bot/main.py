import asyncio
import os
import re
from aiogram import Bot, Dispatcher, F, types, BaseMiddleware
from aiogram.filters import CommandStart
import asyncpg
from arq import create_pool
from arq.connections import RedisSettings

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_DSN = os.getenv("DB_DSN", "postgresql://reels_user:***@postgres:5432/reels_db")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

URL_PATTERN = re.compile(r"https?://(?:www\.)?(?:tiktok\.com|instagram\.com/reels?|youtube\.com/shorts?|youtu\.be)[\S]*")

class ServicesMiddleware(BaseMiddleware):
    def __init__(self, db_pool, redis_pool=None):
        self.db_pool = db_pool
        self.redis_pool = redis_pool

    async def __call__(self, handler, event, data):
        data["db_pool"] = self.db_pool
        if self.redis_pool:
            data["redis_pool"] = self.redis_pool
        return await handler(event, data)

async def get_db_pool():
    return await asyncpg.create_pool(dsn=DB_DSN)

async def get_redis_pool():
    return await create_pool(RedisSettings(host=REDIS_HOST))

@dp.message(CommandStart())
async def cmd_start(message: types.Message, db_pool: asyncpg.Pool):
    query = """
        INSERT INTO users (telegram_id, is_premium, limits_balance) 
        VALUES ($1, $2, 1)
        ON CONFLICT (telegram_id) DO NOTHING;
    """
    async with db_pool.acquire() as conn:
        await conn.execute(query, message.from_user.id, message.from_user.is_premium or False)
        
    await message.answer(
        "👋 Привет! Я превращаю рилсы в конкретные To-Do.\n"
        "Скинь мне ссылку на видео (TikTok/Insta/YT Shorts).\n"
        "🎁 У тебя есть 1 бесплатный разбор для теста."
    )

@dp.message(F.text.regexp(URL_PATTERN))
async def handle_video_url(message: types.Message, db_pool: asyncpg.Pool, redis_pool):
    url = URL_PATTERN.search(message.text).group(0)
    tg_id = message.from_user.id

    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (telegram_id, is_premium, limits_balance) 
            VALUES ($1, $2, 1)
            ON CONFLICT (telegram_id) DO NOTHING;
        """, tg_id, message.from_user.is_premium or False)

        deduct_query = """
            UPDATE users 
            SET limits_balance = limits_balance - 1 
            WHERE telegram_id = $1 AND limits_balance > 0 
            RETURNING limits_balance, force_subscribe_passed;
        """
        row = await conn.fetchrow(deduct_query, tg_id)
        
        if not row:
            user = await conn.fetchrow("SELECT force_subscribe_passed FROM users WHERE telegram_id = $1", tg_id)
            if user and not user['force_subscribe_passed']:
                await message.answer(
                    "❌ Твой тестовый разбор исчерпан.\n"
                    "👉 Подпишись на наш канал @channel, чтобы получить еще 4 разбора бесплатно!"
                )
            else:
                await message.answer(
                    "❌ Бесплатные разборы закончились.\n"
                    "⭐️ Купи пакет из 100 разборов за 200 Stars, чтобы продолжить."
                )
            return

        balance = row['limits_balance']
        insert_task_query = """
            INSERT INTO tasks (telegram_id, url, status) 
            VALUES ($1, $2, 'queued') 
            RETURNING id;
        """
        task_id = await conn.fetchval(insert_task_query, tg_id, url)

    await redis_pool.enqueue_job('analyze_video_job', task_id=str(task_id), url=url, tg_id=tg_id)
    await message.answer(f"⚙️ Взял в работу. Смотрю видео... (Осталось разборов: {balance})")

@dp.callback_query(F.data.startswith("action:"))
async def handle_kanban_action(callback: types.CallbackQuery, db_pool: asyncpg.Pool):
    _, action, task_id = callback.data.split(":")
    
    async with db_pool.acquire() as conn:
        if action == "done":
            await conn.execute("UPDATE tasks SET status = 'done' WHERE id = $1::uuid", task_id)
            await callback.message.edit_text(f"~~{callback.message.text}~~ \n\n✅ Задача отмечена как выполненная.")
        elif action == "delay":
            await conn.execute("UPDATE tasks SET status = 'delayed', reminder_date = NOW() + INTERVAL '3 days' WHERE id = $1::uuid", task_id)
            await callback.message.edit_text(f"{callback.message.text}\n\n⏳ Отложено. Напомню позже.")
        elif action == "delete":
            await conn.execute("UPDATE tasks SET status = 'deleted' WHERE id = $1::uuid", task_id)
            await callback.message.edit_text(f"{callback.message.text}\n\n❌ Сброшено в архив.")
            
    await callback.answer()

async def main():
    db_pool = await get_db_pool()
    redis_pool = await get_redis_pool()
    
    me = await bot.get_me()
    expected = os.getenv("EXPECTED_BOT_USERNAME", "").lstrip("@").lower()
    actual = (me.username or "").lower()
    if expected and actual != expected:
        raise RuntimeError(f"Telegram bot identity mismatch: expected @{expected}, got @{actual or 'no_username'}")
    print(f"Telegram identity verified: @{me.username} ({me.id})", flush=True)

    dp.message.middleware(ServicesMiddleware(db_pool, redis_pool))
    dp.callback_query.middleware(ServicesMiddleware(db_pool))
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
