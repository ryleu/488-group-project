import sqlite3
import csv
import sys
import time
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "minecraft_data.db"
SPAWN_CSV = DATA_DIR / "spawn_chunk_biomes.csv"
RANDOM_CSV = DATA_DIR / "random_chunk_biomes.csv"
SAMPLE_CSV = DATA_DIR / "seed_samples.csv"

BATCH_SIZE = 100000

missing = [p for p in (SPAWN_CSV, RANDOM_CSV, SAMPLE_CSV) if not p.exists()]
if missing:
    print("Missing input CSVs:")
    for p in missing:
        print(f"  - {p}")
    sys.exit(1)

DATA_DIR.mkdir(parents=True, exist_ok=True)

# Delete the old database file if it exists to guarantee a clean slate
if DB_PATH.exists():
    print(f"Deleting old database: {DB_PATH}")
    DB_PATH.unlink()

print("Creating fresh database...")
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()


TEXT_COLUMNS = {"biome_name", "sample_type", "area_kind"}


def column_type(name):
    """Schema follows the CSV header, so new collector columns need no edit."""
    if name.startswith("dist_"):
        return "REAL"
    if name in TEXT_COLUMNS:
        return "TEXT"
    return "INTEGER"


def convert(name, value):
    if value == "":
        return None
    if name.startswith("dist_"):
        # -1 means "none within search radius"; NULL so aggregates skip it.
        v = float(value)
        return None if v < 0 else v
    if name in TEXT_COLUMNS:
        return value
    return int(value)


def load_csv(filename, table_name):
    print(f"Loading {filename.name} into {table_name}...")
    start = time.time()

    with open(filename, "r", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)

        cols = ", ".join(f"{name} {column_type(name)}" for name in header)
        c.execute(f"CREATE TABLE {table_name} ({cols})")
        insert = (
            f"INSERT INTO {table_name} VALUES "
            f"({','.join('?' * len(header))})"
        )

        batch = []
        total = 0
        for row in reader:
            batch.append(tuple(convert(n, v) for n, v in zip(header, row)))
            if len(batch) >= BATCH_SIZE:
                c.executemany(insert, batch)
                total += len(batch)
                batch = []
        if batch:
            c.executemany(insert, batch)
            total += len(batch)

    print(f"Done! {total:,} rows in {time.time() - start:.2f} seconds.")
    return header


spawn_header = load_csv(SPAWN_CSV, "spawn_chunks")
random_header = load_csv(RANDOM_CSV, "random_chunks")
sample_header = load_csv(SAMPLE_CSV, "samples")

print("Building indexes...")
c.execute("CREATE INDEX idx_spawn_seed ON spawn_chunks(seed)")
c.execute("CREATE INDEX idx_random_seed ON random_chunks(seed)")
c.execute("CREATE INDEX idx_spawn_biome ON spawn_chunks(biome_name)")
c.execute("CREATE INDEX idx_random_biome ON random_chunks(biome_name)")
c.execute("CREATE INDEX idx_samples_seed ON samples(seed)")
c.execute("CREATE INDEX idx_samples_type ON samples(sample_type)")
c.execute("CREATE INDEX idx_random_kind ON random_chunks(area_kind)")

conn.commit()
conn.close()

print("\nDatabase rebuild complete! You can now restart your Flask app.")
print(f"  spawn_chunks  : {', '.join(spawn_header)}")
print(f"  random_chunks : {', '.join(random_header)}")
print(f"  samples       : {', '.join(sample_header)}")
