import aiosqlite
import hashlib
import aiohttp

DB_PATH = "ulps_data.db"

class ULPDatabase:
    def __init__(self):
        self.db_path = DB_PATH

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode = WAL")
            await db.execute("PRAGMA synchronous = OFF")
            await db.execute("PRAGMA cache_size = -64000")

            await db.execute("""
                CREATE TABLE IF NOT EXISTS ulps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT, login TEXT, password TEXT,
                    hash TEXT UNIQUE, source TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    is_banned INTEGER DEFAULT 0
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY,
                    filename  TEXT,
                    ulp_count INTEGER DEFAULT 0,
                    added_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute("CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT)")

            # Migrations — add missing columns safely
            for table, col, definition in [
                ("users", "is_banned", "INTEGER DEFAULT 0"),
                ("ulps",  "source",    "TEXT"),
            ]:
                cur = await db.execute(f"PRAGMA table_info({table})")
                cols = [r[1] for r in await cur.fetchall()]
                if col not in cols:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")

            # Migrate/repair settings table if an old incompatible schema exists.
            cur = await db.execute("PRAGMA table_info(settings)")
            settings_cols = [r[1] for r in await cur.fetchall()]
            if "name" not in settings_cols or "value" not in settings_cols:
                await db.execute("DROP TABLE IF EXISTS settings")
                await db.execute("CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT)")

            await db.execute("INSERT OR IGNORE INTO settings (name, value) VALUES ('maintenance', '0')")

            await db.execute("CREATE INDEX IF NOT EXISTS idx_url    ON ulps(url)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_source ON ulps(source)")
            await db.commit()

    # ── Batch insert ──────────────────────────────────────────
    async def add_ulps_batch(self, data_list):
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany(
                "INSERT OR IGNORE INTO ulps (url, login, password, hash, source) VALUES (?,?,?,?,?)",
                data_list
            )
            added = db.total_changes
            await db.commit()
            return added

    # ── Register a new source after upload ───────────────────
    async def register_source(self, source_id: str, filename: str, count: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO sources (source_id, filename, ulp_count) VALUES (?,?,?)",
                (source_id, filename, count)
            )
            await db.commit()

    # ── Get all sources (for "My Files" list) ────────────────
    async def get_all_sources(self):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT source_id, filename, ulp_count, added_at FROM sources ORDER BY added_at DESC"
            )
            return await cur.fetchall()

    # ── Delete all ULPs by source_id ─────────────────────────
    async def delete_by_source(self, source_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM ulps WHERE source = ?", (source_id,))
            count = db.total_changes
            await db.execute("DELETE FROM sources WHERE source_id = ?", (source_id,))
            await db.commit()
            return count

    # ── URL upload ────────────────────────────────────────────
    async def add_ulp_from_url(self, url: str, source_id: str = "URL_UPLOAD"):
        try:
            batch = []
            added = 0
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    async for raw_line in resp.content:
                        line = raw_line.decode("utf-8", errors="ignore").strip()
                        if not line:
                            continue
                        p = line.split(":")
                        if len(p) >= 3:
                            h = hashlib.md5(f"{p[0]}:{p[1]}:{p[2]}".encode()).hexdigest()
                            batch.append((p[0], p[1], p[2], h, source_id))
                        if len(batch) >= 5000:
                            added += await self.add_ulps_batch(batch)
                            batch = []
            if batch:
                added += await self.add_ulps_batch(batch)
            return added
        except Exception:
            return 0

    # ── Search ────────────────────────────────────────────────
    async def search_ulps(self, query: str):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            q = f"%{query}%"
            cur = await db.execute(
                "SELECT url, login, password FROM ulps WHERE url LIKE ? OR login LIKE ? LIMIT 50000",
                (q, q)
            )
            return await cur.fetchall()

    # ── Delete by keyword ────────────────────────────────────
    async def delete_single_ulp(self, query: str):
        async with aiosqlite.connect(self.db_path) as db:
            q = f"%{query}%"
            await db.execute("DELETE FROM ulps WHERE url LIKE ? OR login LIKE ?", (q, q))
            count = db.total_changes
            await db.commit()
            return count

    # ── Users ─────────────────────────────────────────────────
    async def add_user(self, user_id: int, username: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO users (user_id, username) VALUES (?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET username=?",
                (user_id, username, username)
            )
            await db.commit()

    async def ban_user(self, user_id: int, status: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE users SET is_banned=? WHERE user_id=?", (status, user_id))
            await db.commit()

    async def get_all_users(self):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT user_id FROM users WHERE is_banned=0") as cur:
                return [r[0] for r in await cur.fetchall()]

    # ── Stats ─────────────────────────────────────────────────
    async def get_total_count(self):
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM ulps") as cur:
                r = await cur.fetchone()
                return r[0] if r else 0

    async def get_bot_stats(self):
        async with aiosqlite.connect(self.db_path) as db:
            u = await (await db.execute("SELECT COUNT(*) FROM users")).fetchone()
            c = await (await db.execute("SELECT COUNT(*) FROM ulps")).fetchone()
            return {"users": u[0], "ulps": c[0]}

    # ── Maintenance ───────────────────────────────────────────
    async def get_maintenance(self):
        async with aiosqlite.connect(self.db_path) as db:
            try:
                async with db.execute("SELECT value FROM settings WHERE name='maintenance'") as cur:
                    r = await cur.fetchone()
                    return r[0] == "1" if r else False
            except Exception:
                # Self-heal in case DB has old/broken settings schema.
                await db.execute("DROP TABLE IF EXISTS settings")
                await db.execute("CREATE TABLE settings (name TEXT PRIMARY KEY, value TEXT)")
                await db.execute("INSERT INTO settings (name, value) VALUES ('maintenance', '0')")
                await db.commit()
                return False

    async def set_maintenance(self, status: bool):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO settings (name, value) VALUES ('maintenance', ?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                ("1" if status else "0",)
            )
            await db.commit()

    # ── Wipe all ─────────────────────────────────────────────
    async def clear_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM ulps")
            await db.execute("DELETE FROM sources")
            await db.commit()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("VACUUM")

db_ulp = ULPDatabase()
