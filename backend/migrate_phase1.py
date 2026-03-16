"""One-time migration: add Phase 1 tables and columns to existing DB."""
import sqlite3
import sys


def migrate(db_path: str = "sports_picks.db"):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # New tables
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS calibration_history (
            id INTEGER PRIMARY KEY,
            date DATE NOT NULL,
            sport TEXT NOT NULL,
            confidence_tier INTEGER NOT NULL,
            predicted_win_rate REAL,
            actual_win_rate REAL,
            sample_size INTEGER,
            old_threshold REAL,
            new_threshold REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS model_metrics (
            id INTEGER PRIMARY KEY,
            date DATE NOT NULL,
            sport TEXT NOT NULL,
            model_version TEXT NOT NULL,
            accuracy REAL,
            log_loss REAL,
            feature_importances TEXT,
            training_games INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # New columns on existing tables
    for stmt in [
        "ALTER TABLE user_profiles ADD COLUMN current_streak INTEGER DEFAULT 0",
        "ALTER TABLE user_profiles ADD COLUMN best_streak INTEGER DEFAULT 0",
        "ALTER TABLE user_profiles ADD COLUMN streak_type TEXT DEFAULT 'none'",
        "ALTER TABLE paper_picks ADD COLUMN graded_at DATETIME",
    ]:
        try:
            cursor.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                pass
            else:
                raise

    conn.commit()
    conn.close()
    print(f"Migration complete: {db_path}")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "sports_picks.db"
    migrate(path)
