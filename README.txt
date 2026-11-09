================================================================================
MINECRAFT BIG DATA: SPAWN CHUNK & BIOME ANALYSIS
================================================================================

PROJECT OVERVIEW
This project analyzes Minecraft's world generation to determine if the game 
biases certain biomes or early-game resources at the world spawn. It uses a 
C++ pipeline to generate data for thousands of random seeds, loads that data 
into a SQLite database, and uses a Flask web server to visualize the results.

TECH STACK: C++, Cubiomes Library, OpenMP, Python, SQLite, Flask, Pandas, Plotly.


================================================================================
REQUIREMENTS
================================================================================
1. MSYS2 (UCRT64 terminal) with GCC, Make, and OpenMP installed.
2. Cubiomes C Library (https://github.com/Cubitect/cubiomes).
3. Python 3.10+ with Flask, Pandas, and Plotly installed:
   Run: pip install flask pandas plotly

Note: The massive generated files (*.csv, *.db) and compiled *.exe files are 
intentionally excluded from this repo to save space and prevent file-size 
limits. You must generate them yourself using the steps below.


================================================================================
STEP 1: COMPILE AND RUN THE C++ DATA COLLECTOR
================================================================================
1. Open your MSYS2 UCRT64 terminal.
2. Clone and build the Cubiomes library:
   git clone https://github.com/Cubitect/cubiomes.git
   cd cubiomes
   make release

3. Copy "Minecraft Data Collector.cpp" into the cubiomes folder.
4. Compile the collector with OpenMP and static linking (this ensures the .exe 
   runs without needing external DLLs):
   
   cp "Minecraft Data Collector.cpp" ./collector.cpp
   g++ -std=c++17 collector.cpp libcubiomes.a -fwrapv -lm -static -fopenmp -O3 -o spawn_biomes.exe

5. Run the executable:
   ./spawn_biomes.exe
   
   * By default, this is set to MAX_SEEDS = 5000 in the code. This takes about 
     30 seconds and generates roughly 1.8 million rows.
   * It will output two files: "spawn_chunk_biomes.csv" and "random_chunk_biomes.csv".


================================================================================
STEP 2: BUILD THE SQLITE DATABASE
================================================================================
1. Move the two generated .csv files into the same folder as the Python scripts.
2. Run the database builder script:
   python Data_to_DB.py

   * This will automatically delete any existing database and create a fresh 
     "minecraft_data.db" file, loading both CSVs into separate tables.


================================================================================
STEP 3: RUN THE VISUALIZER
================================================================================
1. Start the Flask server:
   python "Minecraft Data Visualizer.py"

2. Open your web browser and go to:
   http://127.0.0.1:5000


================================================================================
DASHBOARD FEATURES
================================================================================
1. Top 15 Spawn Biomes: Ranks the most common biomes found in spawn chunks.
2. Spawn vs Global Resource Distribution: Compares early-game playability 
   categories (Tree-Heavy, Light, Water, Barren) between actual spawn points 
   and randomly sampled chunks.
3. Spawn Density Heatmap: Proves the spawn algorithm heavily clusters near (0,0).
4. Biome Diversity: Histogram showing unique biomes per spawn area.
5. Average Spawn Area Tree-Heaviness: A spatial 19x19 heatmap showing wood 
   availability relative to the exact spawn point.
