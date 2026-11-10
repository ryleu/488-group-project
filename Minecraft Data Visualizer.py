import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, render_template_string

DB_PATH = Path(__file__).resolve().parent / "data" / "minecraft_data.db"

app = Flask(__name__)

def get_db_connection():
    return sqlite3.connect(DB_PATH)

TREE_HEAVY = [
    'forest', 'flower_forest', 'birch_forest', 'dark_forest', 
    'jungle', 'bamboo_jungle', 'taiga', 'swamp', 
    'mangrove_swamp', 'wooded_badlands', 'old_growth_birch_forest',
    'snowy_taiga'
]
TREE_LIGHT = [
    'plains', 'sunflower_plains', 'savanna', 'desert', 
    'badlands', 'snowy_tundra', 'ice_spikes', 'mushroom_fields',
    'snowy_plains'
]
WATERMASS = [
    'river', 'frozen_river', 'ocean', 'deep_ocean', 'warm_ocean', 'lukewarm_ocean', 
    'deep_lukewarm_ocean', 'cold_ocean', 'deep_cold_ocean', 'frozen_ocean', 
    'deep_frozen_ocean', 'beach', 'snowy_beach', 'stony_shore'
]

@app.route('/api/top-biomes')
def top_biomes():
    conn = get_db_connection()
    query = "SELECT biome_name, COUNT(*) as count FROM spawn_chunks GROUP BY biome_name ORDER BY count DESC LIMIT 15"
    df = pd.read_sql_query(query, conn)
    conn.close()
    df['biome_name'] = df['biome_name'].fillna('Unknown')
    return jsonify(df.to_dict(orient='records'))

@app.route('/api/spawn-heatmap')
def spawn_heatmap():
    conn = get_db_connection()
    query = "SELECT DISTINCT spawn_x, spawn_z FROM spawn_chunks"
    df = pd.read_sql_query(query, conn)
    conn.close()
    return jsonify(df.to_dict(orient='records'))

@app.route('/api/diversity')
def diversity():
    conn = get_db_connection()
    query = """
        SELECT seed, COUNT(DISTINCT biome_name) as unique_biomes
        FROM spawn_chunks GROUP BY seed
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    counts = df['unique_biomes'].value_counts().sort_index().reset_index()
    counts.columns = ['unique_biomes', 'seed_count']
    return jsonify(counts.to_dict(orient='records'))

@app.route('/api/playability')
def playability():
    conn = get_db_connection()
    df_spawn = pd.read_sql_query("SELECT biome_name, COUNT(*) as count FROM spawn_chunks GROUP BY biome_name", conn)
    df_rand = pd.read_sql_query("SELECT biome_name, COUNT(*) as count FROM random_chunks GROUP BY biome_name", conn)
    conn.close()
    
    def categorize(df):
        def get_cat(biome):
            if pd.isna(biome): return 'Barren/Other'
            clean = str(biome).lower().replace('minecraft:', '').strip()
            if clean in TREE_HEAVY: return 'Tree-Heavy'
            if clean in TREE_LIGHT: return 'Tree-Light'
            if clean in WATERMASS: return 'Watermass'
            return 'Barren/Other'
        df['category'] = df['biome_name'].apply(get_cat)
        return df.groupby('category')['count'].sum().reset_index()
        
    spawn_res = categorize(df_spawn)
    rand_res = categorize(df_rand)
    
    categories = ['Tree-Heavy', 'Tree-Light', 'Watermass', 'Barren/Other']
    spawn_counts = {row['category']: row['count'] for _, row in spawn_res.iterrows()}
    rand_counts = {row['category']: row['count'] for _, row in rand_res.iterrows()}
    
    return jsonify({
        'categories': categories,
        'spawn': [spawn_counts.get(c, 0) for c in categories],
        'random': [rand_counts.get(c, 0) for c in categories]
    })

@app.route('/api/tree-heaviness')
def tree_heaviness():
    conn = get_db_connection()
    query = "SELECT chunk_x, chunk_z, spawn_x, spawn_z, biome_name FROM spawn_chunks"
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    # Calculate relative chunk coordinates (0,0 is the spawn chunk)
    df['rel_x'] = df['chunk_x'] - (df['spawn_x'] // 16)
    df['rel_z'] = df['chunk_z'] - (df['spawn_z'] // 16)
    
    def get_score(biome):
        if pd.isna(biome): return 0.0
        clean = str(biome).lower().replace('minecraft:', '').strip()
        if clean in TREE_HEAVY: return 1.0
        if clean in TREE_LIGHT: return 0.5
        return 0.0
        
    df['score'] = df['biome_name'].apply(get_score)
    
    # Average score across all seeds for each relative coordinate
    pivot = df.groupby(['rel_z', 'rel_x'])['score'].mean().unstack()
    pivot = pivot.fillna(0.5).sort_index(ascending=False).sort_index(axis=1, ascending=True)
    
    return jsonify({
        'x': pivot.columns.tolist(),
        'y': pivot.index.tolist(),
        'z': pivot.values.tolist()
    })

@app.route('/')
def index():
    html_template = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Minecraft Big Data Dashboard</title>
        <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
        <style>
            /* CSS Updated to allow scrolling for all charts */
            body { 
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                background-color: #121212; 
                color: #e0e0e0; 
                margin: 0; 
                padding: 20px; 
                overflow-y: scroll; /* Forces a scrollbar */
            }
            h2 { text-align: center; font-weight: 300; }
            .dashboard { 
                display: grid; 
                grid-template-columns: 1fr 1fr; 
                gap: 20px; 
                max-width: 1400px; 
                margin: auto; 
                padding-bottom: 50px; /* Space at the bottom */
            }
            .card { 
                background-color: #1e1e1e; 
                border-radius: 8px; 
                padding: 15px; 
                box-shadow: 0 4px 6px rgba(0,0,0,0.3); 
            }
            .full-width { grid-column: 1 / -1; }
            .chart-container { width: 100%; height: 450px; }
            .list-container { width: 100%; height: 450px; overflow-y: auto; padding-right: 10px; }
            .biome-item { display: flex; justify-content: space-between; align-items: center; padding: 12px 15px; margin-bottom: 8px; background-color: #2a2a2a; border-radius: 6px; border-left: 4px solid #4CAF50; font-size: 1.1em; }
            .rank { font-weight: bold; color: #888; margin-right: 15px; width: 30px; }
            .biome-name { flex-grow: 1; text-transform: capitalize; letter-spacing: 1px; }
            .count { font-weight: bold; color: #4CAF50; font-family: monospace; font-size: 1.1em; }
        </style>
    </head>
    <body>
        <div class="dashboard">
            <div class="card">
                <h2>Top 15 Spawn Biomes</h2>
                <div id="biomeList" class="list-container"><div style="text-align:center; color:#888; margin-top:20%;">Querying Database...</div></div>
            </div>
            
            <div class="card">
                <h2>Spawn vs Global Resource Distribution</h2>
                <div id="playabilityChart" class="chart-container"></div>
            </div>
            
            <div class="card">
                <h2>Spawn Point Density Heatmap</h2>
                <div id="heatmapChart" class="chart-container"></div>
            </div>
            
            <div class="card">
                <h2>Biome Diversity per Spawn Area</h2>
                <div id="histChart" class="chart-container"></div>
            </div>

            <!-- Restored: Full width chunk heatmap -->
            <div class="card full-width">
                <h2>Average Spawn Area Tree-Heaviness (1 Pixel = 1 Chunk)</h2>
                <div id="treeHeatmapChart" class="chart-container"></div>
            </div>
        </div>

        <script>
            const layoutTemplate = {
                paper_bgcolor: '#1e1e1e', plot_bgcolor: '#1e1e1e', font: { color: '#e0e0e0' },
                margin: { t: 40, b: 60, l: 60, r: 40 }
            };

            fetch('/api/top-biomes').then(res => res.json()).then(data => {
                const container = document.getElementById('biomeList');
                container.innerHTML = ''; 
                data.forEach((item, index) => {
                    const cleanName = String(item.biome_name).replace('minecraft:', '').replace(/_/g, ' ');
                    const formattedCount = Number(item.count).toLocaleString();
                    const row = document.createElement('div');
                    row.className = 'biome-item';
                    row.innerHTML = `<div style="display:flex; align-items:center;"><span class="rank">#${index + 1}</span><span class="biome-name">${cleanName}</span></div><span class="count">${formattedCount}</span>`;
                    container.appendChild(row);
                });
            });

            fetch('/api/playability').then(res => res.json()).then(data => {
                const traceSpawn = { x: data.categories, y: data.spawn, name: 'Spawn Chunks', type: 'bar', marker: { color: '#4CAF50' } };
                const traceRandom = { x: data.categories, y: data.random, name: 'Random World', type: 'bar', marker: { color: '#2196F3' } };
                Plotly.newPlot('playabilityChart', [traceSpawn, traceRandom], {
                    ...layoutTemplate, barmode: 'group', yaxis: { title: 'Number of Chunks' }
                });
            });

            fetch('/api/spawn-heatmap').then(res => res.json()).then(data => {
                if (data.length === 0) return;
                const x = data.map(d => Number(d.spawn_x));
                const y = data.map(d => Number(d.spawn_z));
                const trace = { 
                    x: x, y: y, type: 'histogram2d',
                    colorscale: [[0.0, '#00CC00'], [0.05, '#88FF00'], [0.15, '#FFFF00'], [0.35, '#FF8800'], [1.0, '#FF0000']],
                    zmin: 0, zmax: 80, colorbar: { title: 'Seeds' }, nbinsx: 100, nbinsy: 100 
                };
                Plotly.newPlot('heatmapChart', [trace], {
                    ...layoutTemplate,
                    xaxis: { title: 'X Coordinate', range: [-800, 800] },
                    yaxis: { title: 'Z Coordinate', range: [-800, 800], scaleanchor: 'x', scaleratio: 1 }
                });
            });

            fetch('/api/diversity').then(res => res.json()).then(data => {
                if (data.length === 0) return;
                const x = data.map(d => Number(d.unique_biomes)); 
                const y = data.map(d => Number(d.seed_count));   
                const trace = { x: x, y: y, type: 'bar', marker: { color: '#FF9800' } };
                Plotly.newPlot('histChart', [trace], { 
                    ...layoutTemplate, 
                    xaxis: { title: 'Number of Unique Biomes at Spawn', type: 'category' }, 
                    yaxis: { title: 'Number of Seeds' } 
                });
            });

            fetch('/api/tree-heaviness').then(res => res.json()).then(data => {
                if (!data.z || data.z.length === 0) return;
                const trace = {
                    x: data.x, y: data.y, z: data.z, type: 'heatmap',
                    colorscale: [[0.0, '#2196F3'], [0.5, '#FFC107'], [1.0, '#4CAF50']],
                    zmin: 0, zmax: 1, colorbar: { title: 'Avg Score', tickvals: [0, 0.5, 1], ticktext: ['Water', 'Light', 'Heavy'] }
                };
                Plotly.newPlot('treeHeatmapChart', [trace], {
                    ...layoutTemplate,
                    xaxis: { title: 'Relative Chunk X (0 = Spawn)', dtick: 2 },
                    yaxis: { title: 'Relative Chunk Z', dtick: 2, scaleanchor: 'x', scaleratio: 1 }
                });
            });
        </script>
    </body>
    </html>
    """
    return render_template_string(html_template)

if __name__ == '__main__':
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        sys.exit(1)

    port = int(os.environ.get("MC_PORT", "5000"))

    print("Starting Big Data Visualizer...")
    print(f"Using database: {DB_PATH}")
    print(f"Open http://127.0.0.1:{port}")
    app.run(debug=True, port=port)
