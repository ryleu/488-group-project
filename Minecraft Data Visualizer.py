import sqlite3
import pandas as pd
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)
DB_PATH = "minecraft_data.db"

def get_db_connection():
    return sqlite3.connect(DB_PATH)

# Define the mapping for Dimensionality Reduction
TREE_HEAVY = [
    'minecraft:forest', 'minecraft:flower_forest', 'minecraft:birch_forest', 'minecraft:dark_forest', 
    'minecraft:jungle', 'minecraft:bamboo_jungle', 'minecraft:taiga', 'minecraft:swamp', 
    'minecraft:mangrove_swamp', 'minecraft:wooded_badlands'
]
TREE_LIGHT = [
    'minecraft:plains', 'minecraft:sunflower_plains', 'minecraft:savanna', 'minecraft:desert', 
    'minecraft:badlands', 'minecraft:snowy_tundra', 'minecraft:ice_spikes', 'minecraft:mushroom_fields'
]

@app.route('/api/top-biomes')
def top_biomes():
    conn = get_db_connection()
    query = "SELECT biome_name, COUNT(*) as count FROM chunks GROUP BY biome_name ORDER BY count DESC LIMIT 15"
    df = pd.read_sql_query(query, conn)
    conn.close()
    df['biome_name'] = df['biome_name'].fillna('Unknown')
    return jsonify(df.to_dict(orient='records'))

@app.route('/api/spawn-heatmap')
def spawn_heatmap():
    conn = get_db_connection()
    query = "SELECT DISTINCT spawn_x, spawn_z FROM chunks"
    df = pd.read_sql_query(query, conn)
    conn.close()
    return jsonify(df.to_dict(orient='records'))

@app.route('/api/diversity')
def diversity():
    conn = get_db_connection()
    query = """
        SELECT seed, COUNT(DISTINCT biome_name) as unique_biomes
        FROM chunks GROUP BY seed
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    counts = df['unique_biomes'].value_counts().sort_index().reset_index()
    counts.columns = ['unique_biomes', 'seed_count']
    return jsonify(counts.to_dict(orient='records'))

@app.route('/api/playability')
def playability():
    conn = get_db_connection()
    query = "SELECT biome_name, COUNT(*) as count FROM chunks GROUP BY biome_name"
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    # Apply the mapping
    def categorize(biome):
        if biome in TREE_HEAVY: return 'Tree-Heavy'
        if biome in TREE_LIGHT: return 'Tree-Light'
        return 'Watermass'
        
    df['category'] = df['biome_name'].apply(categorize)
    
    # Aggregate by the new categories
    result = df.groupby('category')['count'].sum().reset_index()
    return jsonify(result.to_dict(orient='records'))

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
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #121212; color: #e0e0e0; margin: 0; padding: 20px; }
            h2 { text-align: center; font-weight: 300; }
            .dashboard { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; max-width: 1400px; margin: auto; }
            .card { background-color: #1e1e1e; border-radius: 8px; padding: 15px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
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
                <h2>Early-Game Resource Distribution</h2>
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

            // 2. Playability Pie Chart (Dimensionality Reduction)
            fetch('/api/playability').then(res => res.json()).then(data => {
                const labels = data.map(d => d.category);
                const values = data.map(d => d.count);
                
                const trace = {
                    labels: labels,
                    values: values,
                    type: 'pie',
                    marker: {
                        colors: ['#4CAF50', '#FFC107', '#2196F3'] // Green, Yellow, Blue
                    },
                    hole: 0.4
                };
                
                Plotly.newPlot('playabilityChart', [trace], layoutTemplate);
            });

            fetch('/api/spawn-heatmap').then(res => res.json()).then(data => {
                if (data.length === 0) return;
                const x = data.map(d => Number(d.spawn_x));
                const y = data.map(d => Number(d.spawn_z));

                const trace = { 
                    x: x, y: y, type: 'histogram2d',
                    colorscale: [
                        [0.0, '#00CC00'], [0.05, '#88FF00'], [0.15, '#FFFF00'], [0.35, '#FF8800'], [1.0, '#FF0000']
                    ],
                    zmin: 0, zmax: 80, 
                    colorbar: { title: 'Seeds' }, nbinsx: 100, nbinsy: 100 
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
        </script>
    </body>
    </html>
    """
    return render_template_string(html_template)

if __name__ == '__main__':
    print("Starting Big Data Visualizer...")
    app.run(debug=True, port=5000)