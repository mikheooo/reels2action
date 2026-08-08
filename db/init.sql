-- Типы данных
CREATE TYPE tier_enum AS ENUM ('free', 'basic', 'pro');
CREATE TYPE task_status AS ENUM ('queued', 'active', 'done', 'delayed', 'deleted', 'failed');
CREATE TYPE category_enum AS ENUM ('HEALTH_AND_FITNESS', 'FOOD_AND_RECIPES', 'FINANCE_AND_CRYPTO', 'PRODUCT_REVIEWS', 'LIFEHACKS_AND_DIY', 'EDUCATION', 'ENTERTAINMENT_ONLY');
CREATE TYPE tx_status AS ENUM ('pending', 'success', 'failed');

-- Таблица пользователей (telegram_id как PK)
CREATE TABLE IF NOT EXISTS users (
    telegram_id BIGINT PRIMARY KEY,
    is_premium BOOLEAN DEFAULT FALSE,
    tier tier_enum DEFAULT 'free',
    limits_balance INT DEFAULT 1,
    force_subscribe_passed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Таблица задач
CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_id BIGINT REFERENCES users(telegram_id) ON DELETE CASCADE,
    url VARCHAR NOT NULL,
    category category_enum,
    analysis_data JSONB,
    status task_status DEFAULT 'queued',
    reminder_date TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Таблица транзакций
CREATE TABLE IF NOT EXISTS transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_id BIGINT REFERENCES users(telegram_id) ON DELETE CASCADE,
    amount INT NOT NULL,
    currency VARCHAR(10) NOT NULL,
    status tx_status DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Индексы
CREATE INDEX idx_tasks_user_status ON tasks (telegram_id, status) WHERE status IN ('active', 'delayed');
CREATE INDEX idx_tasks_reminder ON tasks (reminder_date) WHERE status = 'delayed';
