import os
import psycopg2
import psycopg2.extras
import bcrypt

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    return conn


def init_db():
    conn = get_db()
    try:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS tenants (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                tenant_id INTEGER REFERENCES tenants(id) ON DELETE CASCADE,
                username TEXT NOT NULL,
                email TEXT DEFAULT '',
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'butcher',
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                last_login TIMESTAMPTZ,
                UNIQUE(tenant_id, username)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS cuts (
                id SERIAL PRIMARY KEY,
                tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                category TEXT DEFAULT '',
                description TEXT DEFAULT '',
                active BOOLEAN DEFAULT TRUE,
                target_yield REAL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                deleted_at TIMESTAMPTZ
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS cut_entries (
                id SERIAL PRIMARY KEY,
                tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                cut_id INTEGER NOT NULL REFERENCES cuts(id),
                entry_date DATE NOT NULL,
                purchase_price REAL NOT NULL DEFAULT 0,
                purchase_weight REAL NOT NULL DEFAULT 0,
                trim_weight REAL NOT NULL DEFAULT 0,
                notes TEXT DEFAULT '',
                created_by INTEGER REFERENCES users(id),
                created_at TIMESTAMPTZ DEFAULT NOW(),
                deleted_at TIMESTAMPTZ,
                UNIQUE(cut_id, entry_date)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                key TEXT NOT NULL,
                value TEXT,
                PRIMARY KEY (tenant_id, key)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id SERIAL PRIMARY KEY,
                tenant_id INTEGER REFERENCES tenants(id),
                user_id INTEGER REFERENCES users(id),
                action TEXT NOT NULL,
                table_name TEXT NOT NULL,
                record_id INTEGER,
                details JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Indexes
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_cut_entries_date ON cut_entries(entry_date)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_cut_entries_tenant ON cut_entries(tenant_id)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_cuts_tenant ON cuts(tenant_id)
        """)

        # Create superadmin if not exists
        cur.execute("""
            SELECT id FROM users WHERE role = 'superadmin' AND tenant_id IS NULL LIMIT 1
        """)
        if cur.fetchone() is None:
            pw_hash = bcrypt.hashpw(b"superadmin", bcrypt.gensalt()).decode()
            cur.execute("""
                INSERT INTO users (tenant_id, username, email, password_hash, role, active)
                VALUES (NULL, 'superadmin', '', %s, 'superadmin', TRUE)
            """, (pw_hash,))

        conn.commit()
    finally:
        conn.close()
