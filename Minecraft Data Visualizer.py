import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template_string

DB_PATH = Path(__file__).resolve().parent / "data" / "minecraft_data.db"

app = Flask(__name__)
# Keep JSON in insertion order so the dashboard columns follow FEATURES.
app.json.sort_keys = False

def get_db_connection():
    return sqlite3.connect(DB_PATH)

# Must stay in sync with TREE_BIOMES[] / WATER_BIOMES[] in the collector.
TREE_BEARING = [
    'forest', 'flower_forest', 'birch_forest', 'old_growth_birch_forest',
    'dark_forest', 'pale_garden',
    'taiga', 'snowy_taiga', 'old_growth_pine_taiga', 'old_growth_spruce_taiga',
    'jungle', 'sparse_jungle', 'bamboo_jungle',
    'swamp', 'mangrove_swamp',
    'cherry_grove', 'wooded_badlands', 'windswept_forest', 'grove',
    'savanna', 'savanna_plateau', 'windswept_savanna',
]
# Land the collector excludes from TREE_BIOMES[], plus the other treeless land.
TREE_LIGHT = [
    'plains', 'sunflower_plains', 'meadow',
    'windswept_hills', 'windswept_gravelly_hills',
    'desert', 'badlands', 'eroded_badlands',
    'snowy_plains', 'ice_spikes', 'snowy_slopes',
    'jagged_peaks', 'frozen_peaks', 'stony_peaks',
    'mushroom_fields',
]
WATERMASS = [
    'ocean', 'deep_ocean', 'warm_ocean', 'lukewarm_ocean', 'deep_lukewarm_ocean',
    'cold_ocean', 'deep_cold_ocean', 'frozen_ocean', 'deep_frozen_ocean',
    'river', 'frozen_river', 'beach', 'snowy_beach', 'stony_shore',
    'mushroom_field_shore',
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
    # One row per seed. DISTINCT also collapsed seeds sharing a spawn point.
    query = "SELECT spawn_x, spawn_z FROM spawn_chunks GROUP BY seed"
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

CATEGORIES = ['Tree-Bearing', 'Tree-Light', 'Watermass', 'Barren/Other']

# (table, WHERE clause, json key, display label)
GROUPS = (
    ("spawn_chunks",  "",                              "spawn",        "Spawn"),
    ("random_chunks", "WHERE area_kind = 'control'",      "control",      "Control (uniform)"),
    ("random_chunks", "WHERE area_kind = 'control_land'", "control_land", "Control (land only)"),
)


def categorize(biome):
    if pd.isna(biome): return 'Barren/Other'
    clean = str(biome).lower().replace('minecraft:', '').strip()
    if clean in TREE_BEARING: return 'Tree-Bearing'
    if clean in TREE_LIGHT: return 'Tree-Light'
    if clean in WATERMASS: return 'Watermass'
    return 'Barren/Other'


@app.route('/api/playability')
def playability():
    """Category shares, split by control group. Counts would be meaningless:
    random_chunks holds 6 areas per seed against spawn's 1."""
    conn = get_db_connection()
    out = {'categories': CATEGORIES, 'labels': {}, 'totals': {}}
    for table, where, key, label in GROUPS:
        df = pd.read_sql_query(
            f"SELECT biome_name, COUNT(*) AS count FROM {table} {where} GROUP BY biome_name",
            conn)
        df['category'] = df['biome_name'].apply(categorize)
        counts = df.groupby('category')['count'].sum()
        total = int(counts.sum())
        out[key] = [float(counts.get(c, 0)) / total if total else 0.0 for c in CATEGORIES]
        out['labels'][key] = label
        out['totals'][key] = total
    conn.close()
    return jsonify(out)

@app.route('/api/radial-profile')
def radial_profile():
    """Water share and tree-share-among-land by ring distance from the area
    centre, for spawn and both controls. See README dashboard feature 5."""
    conn = get_db_connection()
    spawn = pd.read_sql_query(
        "SELECT spawn_x AS cx, spawn_z AS cz, chunk_x, chunk_z, biome_name FROM spawn_chunks",
        conn)
    rand = pd.read_sql_query(
        "SELECT area_kind, center_x AS cx, center_z AS cz, chunk_x, chunk_z, biome_name "
        "FROM random_chunks", conn)
    conn.close()

    def profile(df):
        # Chebyshev ring index: 0 is the centre chunk, 9 the edge of the 19x19.
        ring = np.maximum((df['chunk_x'] - (df['cx'] // 16)).abs(),
                          (df['chunk_z'] - (df['cz'] // 16)).abs())
        # Categorise the ~50 distinct names once, not all 650k rows.
        cat = df['biome_name'].map(
            {b: categorize(b) for b in df['biome_name'].unique()})
        d = pd.DataFrame({'ring': ring, 'water': cat == 'Watermass'})
        d['land'] = ~d['water']
        # Among land only; over all chunks it just re-measures the water gradient.
        d['tree_land'] = (cat == 'Tree-Bearing') & d['land']
        g = d.groupby('ring')[['water', 'land', 'tree_land']].sum()
        n = d.groupby('ring').size()
        return {'ring': g.index.tolist(),
                'water': (g['water'] / n).tolist(),
                'tree_among_land': (g['tree_land'] / g['land']).tolist(),
                'n': n.tolist()}

    return jsonify({
        'spawn': profile(spawn),
        'control': profile(rand[rand['area_kind'] == 'control']),
        'control_land': profile(rand[rand['area_kind'] == 'control_land']),
        'labels': {k: v for _, _, k, v in GROUPS},
    })


# (column, label, histogram bin width in blocks)
FEATURES = [
    ("dist_tree_biome", "Nearest Tree-Bearing Biome", 32),
    ("dist_passive_structure", "Nearest Passive Structure", 64),
    ("dist_hostile_structure", "Nearest Hostile Structure", 64),
]

@app.route('/api/feature-distributions')
def feature_distributions():
    """Normalised per-feature histograms, binned in SQL to stay cheap."""
    conn = get_db_connection()
    out = {}
    for col, label, binw in FEATURES:
        entry = {'label': label, 'binw': binw}
        sources = (("spawn_chunks", "", "spawn"),
                   ("random_chunks", "WHERE area_kind = 'control'", "control"),
                   ("random_chunks", "WHERE area_kind = 'control_land'", "control_land"))
        for table, where, key in sources:
            clause = f"{where} AND" if where else "WHERE"
            df = pd.read_sql_query(f"""
                SELECT CAST({col} / {binw} AS INTEGER) AS bin, COUNT(*) AS n
                FROM {table} {clause} {col} IS NOT NULL
                GROUP BY bin ORDER BY bin
            """, conn)
            total = int(df['n'].sum())
            entry[key] = {
                'x': ((df['bin'] * binw) + binw / 2).tolist(),
                'y': (df['n'] / total).tolist() if total else [],
            }
        out[col] = entry
    conn.close()
    return jsonify(out)

@app.route('/api/feature-deltas')
def feature_deltas():
    """Per-seed spawn mean minus that seed's control mean, for both control
    groups. control_land is the one that tests the hypothesis -- the uniform
    control is ~43% water, so against it you mostly measure land-vs-ocean."""
    conn = get_db_connection()
    agg = ", ".join(f"AVG({col}) AS {col}" for col, _, _ in FEATURES)
    spawn = pd.read_sql_query(f"SELECT seed, {agg} FROM spawn_chunks GROUP BY seed", conn)
    controls = {
        kind: pd.read_sql_query(
            f"SELECT seed, {agg} FROM random_chunks WHERE area_kind = '{kind}' GROUP BY seed",
            conn)
        for kind in ("control", "control_land")
    }
    conn.close()

    out = {}
    for col, label, _ in FEATURES:
        entry = {'label': label}
        for kind, df in controls.items():
            merged = spawn.merge(df, on='seed', suffixes=('_spawn', '_control'))
            delta = (merged[f'{col}_spawn'] - merged[f'{col}_control']).dropna()
            entry[kind] = {
                'values': delta.tolist(),
                'mean': float(delta.mean()) if len(delta) else 0.0,
                'median': float(delta.median()) if len(delta) else 0.0,
                'closer_at_spawn': float((delta < 0).mean()) if len(delta) else 0.0,
                'spawn_mean': float(merged[f'{col}_spawn'].mean()),
                'control_mean': float(merged[f'{col}_control'].mean()),
                'n': int(len(delta)),
            }
        out[col] = entry
    return jsonify(out)

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
            .stats-row { display: flex; gap: 15px; margin-top: 15px; flex-wrap: wrap; }
            .stat { flex: 1 1 260px; background-color: #2a2a2a; border-radius: 6px; padding: 12px 15px; border-left: 4px solid #9C27B0; }
            .stat .name { color: #bbb; font-size: 0.95em; margin-bottom: 6px; }
            .stat .val { font-family: monospace; font-size: 1.25em; font-weight: bold; }
            .stat .sub { color: #888; font-size: 0.85em; margin-top: 4px; font-family: monospace; }
        </style>
    </head>
    <body>
        <div class="dashboard">
            <div class="card">
                <h2>Top 15 Spawn Biomes</h2>
                <div id="biomeList" class="list-container"><div style="text-align:center; color:#888; margin-top:20%;">Querying Database...</div></div>
            </div>
            
            <div class="card">
                <h2>Biome Category Share: Spawn vs Control Areas</h2>
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


            <div class="card full-width">
                <h2>ML Feature Distributions: Spawn Area vs Control Areas</h2>
                <div id="featureDistChart" class="chart-container"></div>
            </div>

            <div class="card full-width">
                <h2>Per-Seed Paired Delta (Spawn Area &minus; Control Areas, same world)</h2>
                <div id="featureDeltaChart" class="chart-container"></div>
                <div id="featureDeltaStats" class="stats-row"></div>
            </div>

            <div class="card full-width">
                <h2>Water and Tree Share vs Distance from Area Centre</h2>
                <div id="radialChart" class="chart-container"></div>
                <div class="stats-row">
                    <div class="stat" style="border-left-color:#2196F3">
                        <div class="name">What the left panel shows</div>
                        <div class="sub">Spawn water share climbs from the centre chunk to the grid
                        edge at roughly twice the land control's rate. Spawn areas are genuinely
                        more coastal, not merely on land.</div>
                    </div>
                    <div class="stat" style="border-left-color:#4CAF50">
                        <div class="name">What the right panel shows</div>
                        <div class="sub">Among land chunks only, tree-bearing share is flat across
                        every ring and sits on top of the land control. No tree bias at spawn &mdash;
                        the apparent one is the water gradient on the left.</div>
                    </div>
                </div>
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
                const series = [
                    ['spawn',        '#4CAF50'],
                    ['control',      '#2196F3'],
                    ['control_land', '#FF9800'],
                ];
                const traces = series.map(([key, colour]) => ({
                    x: data.categories, y: data[key], type: 'bar',
                    name: `${data.labels[key]} (n=${Number(data.totals[key]).toLocaleString()})`,
                    marker: { color: colour },
                    hovertemplate: '%{y:.1%}<extra>%{fullData.name}</extra>'
                }));
                Plotly.newPlot('playabilityChart', traces, {
                    ...layoutTemplate, barmode: 'group',
                    legend: { orientation: 'h', y: 1.12 },
                    yaxis: { title: 'Share of chunks', tickformat: '.0%' }
                });
            });

            fetch('/api/spawn-heatmap').then(res => res.json()).then(data => {
                if (data.length === 0) return;
                const x = data.map(d => Number(d.spawn_x));
                const y = data.map(d => Number(d.spawn_z));
                const trace = { 
                    x: x, y: y, type: 'histogram2d',
                    // 0 maps to the background; most bins are empty.
                    colorscale: [[0.0, '#1e1e1e'], [0.01, '#00CC00'], [0.08, '#FFFF00'], [0.3, '#FF8800'], [1.0, '#FF0000']],
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

            fetch('/api/feature-distributions').then(res => res.json()).then(data => {
                const cols = Object.keys(data);
                const traces = [];
                const series = [
                    ['spawn',        'Spawn',               '#4CAF50'],
                    ['control',      'Control (uniform)',   '#2196F3'],
                    ['control_land', 'Control (land only)', '#FF9800'],
                ];
                cols.forEach((col, i) => {
                    const d = data[col];
                    series.forEach(([key, name, colour]) => {
                        if (!d[key]) return;
                        traces.push({ x: d[key].x, y: d[key].y, name: name, type: 'scatter',
                                      mode: 'lines', line: { color: colour, width: 2 },
                                      xaxis: 'x' + (i + 1), yaxis: 'y' + (i + 1),
                                      showlegend: i === 0, legendgroup: key });
                    });
                });
                const layout = { ...layoutTemplate, grid: { rows: 1, columns: cols.length, pattern: 'independent' } };
                cols.forEach((col, i) => {
                    layout['xaxis' + (i + 1)] = { title: data[col].label + ' (blocks)' };
                    layout['yaxis' + (i + 1)] = { title: i === 0 ? 'Share of chunks' : '' };
                });
                Plotly.newPlot('featureDistChart', traces, layout);
            });

            fetch('/api/feature-deltas').then(res => res.json()).then(data => {
                const cols = Object.keys(data);
                const kinds = [
                    ['control',      'vs uniform control', '#2196F3'],
                    ['control_land', 'vs land control',    '#FF9800'],
                ];
                const traces = [];
                cols.forEach((col, i) => {
                    kinds.forEach(([key, name, colour]) => {
                        if (!data[col][key]) return;
                        traces.push({
                            x: data[col][key].values, name: name, type: 'histogram',
                            marker: { color: colour }, opacity: 0.6,
                            xaxis: 'x' + (i + 1), yaxis: 'y' + (i + 1),
                            showlegend: i === 0, legendgroup: key
                        });
                    });
                });
                const layout = { ...layoutTemplate, barmode: 'overlay',
                                 grid: { rows: 1, columns: cols.length, pattern: 'independent' } };
                cols.forEach((col, i) => {
                    layout['xaxis' + (i + 1)] = { title: data[col].label + ' \u0394 (blocks)', zeroline: true, zerolinecolor: '#fff', zerolinewidth: 2 };
                    layout['yaxis' + (i + 1)] = { title: i === 0 ? 'Number of seeds' : '' };
                });
                Plotly.newPlot('featureDeltaChart', traces, layout);

                const box = document.getElementById('featureDeltaStats');
                box.innerHTML = cols.map(col => {
                    const d = data[col];
                    const rows = kinds.map(([key, name]) => {
                        const k = d[key];
                        if (!k) return '';
                        const sign = k.mean < 0 ? '' : '+';
                        const colour = k.mean < 0 ? '#4CAF50' : '#E91E63';
                        const verdict = k.mean < 0 ? 'closer at spawn' : 'farther at spawn';
                        return `<div class="sub" style="color:${colour}">${name}: ${sign}${k.mean.toFixed(1)} blocks &mdash; ${verdict} (${(k.closer_at_spawn * 100).toFixed(0)}% of ${k.n.toLocaleString()} seeds)</div>`;
                    }).join('');
                    return `<div class="stat"><div class="name">${d.label}</div>${rows}</div>`;
                }).join('');
            });

            fetch('/api/radial-profile').then(res => res.json()).then(data => {
                const series = [
                    ['spawn',        '#4CAF50'],
                    ['control',      '#2196F3'],
                    ['control_land', '#FF9800'],
                ];
                const panels = [
                    ['water',           'Water share',                 'x1', 'y1'],
                    ['tree_among_land', 'Tree-bearing share, land only','x2', 'y2'],
                ];
                const traces = [];
                panels.forEach(([field, _title, xa, ya], i) => {
                    series.forEach(([key, colour]) => {
                        const d = data[key];
                        traces.push({
                            x: d.ring, y: d[field], name: data.labels[key],
                            type: 'scatter', mode: 'lines+markers',
                            line: { color: colour, width: 2.5 },
                            marker: { color: colour, size: 6 },
                            xaxis: xa, yaxis: ya,
                            showlegend: i === 0, legendgroup: key,
                            hovertemplate: 'ring %{x}: %{y:.1%}<extra>%{fullData.name}</extra>'
                        });
                    });
                });
                // Full 0-100% on both: zooming the right panel would fake an effect.
                const axis = { range: [0, 1], tickformat: '.0%' };
                Plotly.newPlot('radialChart', traces, {
                    ...layoutTemplate,
                    grid: { rows: 1, columns: 2, pattern: 'independent' },
                    legend: { orientation: 'h', y: 1.12 },
                    xaxis:  { title: 'Chunks from centre (0 = spawn chunk)', dtick: 1 },
                    xaxis2: { title: 'Chunks from centre (0 = spawn chunk)', dtick: 1 },
                    yaxis:  { ...axis, title: 'Water share' },
                    yaxis2: { ...axis, title: 'Tree-bearing share among land' }
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
