import os
import sys
from pathlib import Path
import logging
from logging.config import fileConfig

from dotenv import load_dotenv # <-- 確保有這一行

from alembic import context
from sqlalchemy import engine_from_config, pool
from flask import Flask
from config import Config
from models import db

# 將專案根目錄添加到 Python 路徑中，以便可以找到 models.py 和 config.py
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

# 定義 .env 檔案的路徑
dotenv_path = project_root / '.env'

# 在執行 Alembic 之前，先確保環境變數被加載
if dotenv_path.is_file():
    load_dotenv(dotenv_path)

# 從環境變數中獲取 DATABASE_URL，確保它在 .env 加載後可用
# 這將是 Alembic 實際使用的資料庫 URL
DATABASE_URL_FOR_ALEMBIC = os.getenv('DATABASE_URL')
if DATABASE_URL_FOR_ALEMBIC is None:
    # 如果 DATABASE_URL 仍然為 None，這表示 .env 檔案有問題或未被正確讀取
    # 這裡可以設置一個錯誤提示或一個硬編碼的備用值（用於除錯）
    print("CRITICAL ERROR: DATABASE_URL environment variable is not set!")
    # 為了不中斷 Alembic 運行，您可以暫時提供一個硬編碼的 URL 進行測試
    # 但請確保這與您的實際憑證匹配，否則仍會認證失敗
    DATABASE_URL_FOR_ALEMBIC = "postgresql+psycopg2://ab000641:Ab781178@localhost:5433/air_quality_db"
    # 如果您使用這個硬編碼，請確認用戶名和密碼與您的 Docker Compose 完全一致！

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# 覆寫 alembic.ini 中的 sqlalchemy.url，確保使用 .env 中的 DATABASE_URL
config.set_main_option('sqlalchemy.url', DATABASE_URL_FOR_ALEMBIC) # <-- 關鍵修改

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import Base
# target_metadata = Base.metadata
target_metadata = db.metadata # <-- 修改這裡，指向 Flask-SQLAlchemy 的 metadata

# other values from the config, defined by the needs of env.py,
# can be acquired here.
# my_important_option = config.get_main_option("my_important_option")
# ...

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # 初始化一個 Flask app context 以便 SQLAlchemy 能夠讀取配置
    app = Flask(__name__)
    app.config.from_object(Config)
    db.init_app(app) # 初始化 db

    with app.app_context(): # <-- 確保這裡有 Flask app context
        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )

        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True # <-- 新增這一行，對於某些操作更安全
            )

            with context.begin_transaction():
                context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()