import sqlite3
import csv
import time
import os

DB_PATH = "minecraft_data.db"

# Delete the old database file if it exists to guarantee a clean slate
if os.path.exists(DB_PATH):
    print(f"Deleting old database: {DB_PATH}")
    os.remove(DB_PATH)

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

load_csv("spawn_chunk_biomes.csv", "spawn_chunks", True)
load_csv("random_chunk_biomes.csv", "random_chunks", False)

conn.commit()
conn.close()
print("Database rebuild complete! You can now restart your Flask app.")