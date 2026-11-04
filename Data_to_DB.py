import sqlite3
import csv
import time

CSV_PATH = "spawn_chunk_biomes.csv"
DB_PATH = "minecraft_data.db"

print("Creating SQLite database... (This may take a minute or two)")
start = time.time()

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

# Create the table
c.execute('''
    CREATE TABLE IF NOT EXISTS chunks (
        seed INTEGER, spawn_x INTEGER, spawn_z INTEGER, 
        chunk_x INTEGER, chunk_z INTEGER, biome_id INTEGER, biome_name TEXT
    )
''')

# Read CSV and insert in batches for speed
with open(CSV_PATH, 'r') as f:
    reader = csv.reader(f)
    next(reader) # Skip header
    
    batch = []
    for row in reader:
        batch.append(row)
        if len(batch) >= 100000:
            c.executemany('INSERT INTO chunks VALUES (?,?,?,?,?,?,?)', batch)
            batch = []
            
    if batch:
        c.executemany('INSERT INTO chunks VALUES (?,?,?,?,?,?,?)', batch)

conn.commit()
conn.close()
print(f"Done! Database created in {time.time() - start:.2f} seconds.")