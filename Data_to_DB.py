import sqlite3
import csv
import sys
import time
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "minecraft_data.db"
SPAWN_CSV = DATA_DIR / "spawn_chunk_biomes.csv"
RANDOM_CSV = DATA_DIR / "random_chunk_biomes.csv"

missing = [p for p in (SPAWN_CSV, RANDOM_CSV) if not p.exists()]
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

def load_csv(filename, table_name, has_spawn_cols):
    print(f"Loading {filename} into {table_name}...")
    
    # Create the table fresh
    if has_spawn_cols:
        c.execute(f'''CREATE TABLE {table_name} (
            seed INTEGER, spawn_x INTEGER, spawn_z INTEGER, 
            chunk_x INTEGER, chunk_z INTEGER, biome_id INTEGER, biome_name TEXT
        )''')
    else:
        c.execute(f'''CREATE TABLE {table_name} (
            seed INTEGER, chunk_x INTEGER, chunk_z INTEGER, 
            biome_id INTEGER, biome_name TEXT
        )''')
    
    start = time.time()
    with open(filename, 'r') as f:
        reader = csv.reader(f)
        next(reader)  # Skip header
        batch = []
        for row in reader:
            batch.append(row)
            if len(batch) >= 100000:
                if has_spawn_cols: 
                    c.executemany(f'INSERT INTO {table_name} VALUES (?,?,?,?,?,?,?)', batch)
                else: 
                    c.executemany(f'INSERT INTO {table_name} VALUES (?,?,?,?,?)', batch)
                batch = []
        if batch:
            if has_spawn_cols: 
                c.executemany(f'INSERT INTO {table_name} VALUES (?,?,?,?,?,?,?)', batch)
            else: 
                c.executemany(f'INSERT INTO {table_name} VALUES (?,?,?,?,?)', batch)
                
    print(f"Done! Took {time.time() - start:.2f} seconds.")

load_csv(SPAWN_CSV, "spawn_chunks", True)
load_csv(RANDOM_CSV, "random_chunks", False)

conn.commit()
conn.close()
print("Database rebuild complete! You can now restart your Flask app.")
