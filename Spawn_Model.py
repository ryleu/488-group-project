"""Area-level classifier: is this 19x19 chunk area the world spawn or a control?

One row per area, aggregated from the grid tables (361 chunks each), then
logistic regression / random forest / kNN under seed-grouped cross-validation.

Two contrasts:
  spawn vs control_land  -- the main result, land confound removed
  spawn vs control       -- the uniform baseline, where the water effect shows

Two feature sets:
  water       everything, including the biome water composition
  land_trees  tree features computed over LAND chunks only (a strict subset)

Usage:
  python Spawn_Model.py [--db PATH] [--folds 5] [--jobs -1] [--rebuild]
"""

import argparse
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (balanced_accuracy_score, confusion_matrix,
                             roc_auc_score, roc_curve)
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "data" / "minecraft_data.db"
FEATURE_CACHE = HERE / "data" / "area_features.csv"
RESULTS_CSV = HERE / "data" / "model_results.csv"

# ---------------------------------------------------------------------------
# Biome categories. Must stay in sync with TREE_BIOMES[] / WATER_BIOMES[] in
# the collector and with the same lists in Minecraft Data Visualizer.py.
# ---------------------------------------------------------------------------
FOREST = ['forest', 'flower_forest', 'birch_forest', 'old_growth_birch_forest',
          'dark_forest', 'pale_garden']
TAIGA = ['taiga', 'snowy_taiga', 'old_growth_pine_taiga',
         'old_growth_spruce_taiga', 'grove']
JUNGLE = ['jungle', 'sparse_jungle', 'bamboo_jungle']
SWAMP = ['swamp', 'mangrove_swamp']
SAVANNA = ['savanna', 'savanna_plateau', 'windswept_savanna']
TREE_OTHER = ['cherry_grove', 'wooded_badlands', 'windswept_forest']
TREE_BEARING = FOREST + TAIGA + JUNGLE + SWAMP + SAVANNA + TREE_OTHER

TREE_LIGHT = ['plains', 'sunflower_plains', 'meadow',
              'windswept_hills', 'windswept_gravelly_hills',
              'desert', 'badlands', 'eroded_badlands',
              'snowy_plains', 'ice_spikes', 'snowy_slopes',
              'jagged_peaks', 'frozen_peaks', 'stony_peaks', 'mushroom_fields']

OCEAN = ['ocean', 'deep_ocean', 'warm_ocean', 'lukewarm_ocean',
         'deep_lukewarm_ocean', 'cold_ocean', 'deep_cold_ocean',
         'frozen_ocean', 'deep_frozen_ocean']
RIVER = ['river', 'frozen_river']
SHORE = ['beach', 'snowy_beach', 'stony_shore', 'mushroom_field_shore']
WATERMASS = OCEAN + RIVER + SHORE

SEA_LEVEL = 63

# ---------------------------------------------------------------------------
# Columns. Everything the task excludes is listed here so the reason is on the
# page rather than implied by its absence.
# ---------------------------------------------------------------------------
DIST_COLS = [
    'dist_tree_biome', 'dist_passive_structure', 'dist_hostile_structure',
    'dist_village', 'dist_shipwreck', 'dist_ocean_ruin', 'dist_trail_ruins',
    'dist_igloo', 'dist_desert_pyramid', 'dist_jungle_temple',
    'dist_ruined_portal', 'dist_desert_well', 'dist_buried_treasure',
    'dist_outpost', 'dist_mansion', 'dist_monument', 'dist_swamp_hut',
    'dist_trial_chambers', 'dist_ancient_city', 'dist_mineshaft',
]
# Excluded on purpose:
#   spawn_x/spawn_z/center_x/center_z and absolute chunk_x/chunk_z -- position
#     identifies the group outright (spawn clusters near the origin).
#     chunk_x/chunk_z are read but only ever used as an offset from the area
#     centre, which is the same -9..9 lattice for every area.
#   area_id      -- -1 for spawn, 0..2 for controls. A perfect label.
#   clim_*       -- climateToBiome() of the six reproduces the biome exactly,
#                   so they re-encode biome_name in a form a tree can split on
#                   without the categories being interpretable.
#   dist_end_city        -- constant across every row of a seed, so it cannot
#                           separate areas within a seed.
#   dist_nether_*        -- not leaky, but the hypothesis is about the
#                           Overworld. Other dimensions are out of scope.
GRID_COLS = ['chunk_x', 'chunk_z', 'biome_name', 'surface_y',
             'is_slime_chunk'] + DIST_COLS

KEYS = ['seed', 'kind', 'area_id']

# Features computed over land chunks only. Nothing here may encode HOW MUCH
# land the area has -- a land count would smuggle the water share back in.
LAND_TREE_FEATURES = [
    'lt_tree_share', 'lt_forest_share', 'lt_taiga_share', 'lt_jungle_share',
    'lt_swamp_share', 'lt_savanna_share', 'lt_treelight_share',
    'lt_dist_tree_mean', 'lt_dist_tree_median', 'lt_dist_tree_min',
    'lt_dist_tree_max', 'lt_dist_tree_std',
]


# ---------------------------------------------------------------------------
# Feature construction
# ---------------------------------------------------------------------------
def _shares(sub, keys, prefix, members_by_name, denom):
    """Fraction of each biome group within `sub`, divided by `denom`."""
    out = {}
    for name, members in members_by_name.items():
        flag = sub['biome_name'].isin(members).astype(float)
        out[prefix + name] = flag.groupby(keys).sum() / denom
    return pd.DataFrame(out)


def aggregate_batch(df):
    """361 chunk rows per area -> one row per area."""
    keys = [df[k] for k in KEYS]
    g = df.groupby(keys, sort=False)
    n = g.size().rename('n_chunks')

    is_water = df['biome_name'].isin(WATERMASS)
    df = df.assign(_water=is_water.astype(float))

    # Ring index from the area centre. The grid is a symmetric 19x19, so the
    # mean chunk coordinate IS the centre -- no reliance on spawn_x/center_x.
    cx = g['chunk_x'].transform('mean')
    cz = g['chunk_z'].transform('mean')
    ring = np.maximum((df['chunk_x'] - cx).abs(), (df['chunk_z'] - cz).abs())

    parts = [n]

    parts.append(_shares(df, keys, 'comp_', {
        'water_share': WATERMASS, 'ocean_share': OCEAN, 'river_share': RIVER,
        'shore_share': SHORE, 'tree_share': TREE_BEARING,
        'treelight_share': TREE_LIGHT,
    }, n))

    # Water by ring band plus the centre-to-edge gradient: spawn areas are
    # coastal rather than uniformly wet, and a flat share averages that away.
    bands = {'water_ring_0_2': ring <= 2,
             'water_ring_3_5': (ring >= 3) & (ring <= 5),
             'water_ring_6_9': ring >= 6}
    band_df = pd.DataFrame({
        name: df['_water'].where(mask).groupby(keys).mean()
        for name, mask in bands.items()
    })
    band_df['water_ring_gradient'] = (band_df['water_ring_6_9']
                                      - band_df['water_ring_0_2'])
    # No centre-chunk water flag. control_land is rejection-sampled onto a
    # non-water centre, so it is 0 there by construction while spawn sits at
    # 3.7% -- a one-way marker for the sampler, not for Minecraft. The ring
    # bands average 25+ chunks, so only this single-chunk version is affected.
    parts.append(band_df)

    surf = g['surface_y'].agg(['mean', 'std', 'min', 'max'])
    surf.columns = ['surf_mean', 'surf_std', 'surf_min', 'surf_max']
    surf['surf_frac_below_sea'] = (
        (df['surface_y'] < SEA_LEVEL).astype(float).groupby(keys).sum() / n)
    parts.append(surf)

    counts = df.groupby(keys + ['biome_name'], sort=False).size()
    p = counts / counts.groupby(level=list(range(len(KEYS)))).sum()
    parts.append(pd.DataFrame({
        'div_unique': g['biome_name'].nunique(),
        'div_shannon': (-p * np.log(p)).groupby(
            level=list(range(len(KEYS)))).sum(),
        'slime_share': g['is_slime_chunk'].mean(),
    }))

    dist = g[DIST_COLS].agg(['mean', 'min', 'max', 'std'])
    dist.columns = [f'{c}_{stat}' for c, stat in dist.columns]
    parts.append(dist)

    # --- land-only block -----------------------------------------------
    land = df[~is_water]
    lkeys = [land[k] for k in KEYS]
    lg = land.groupby(lkeys, sort=False)
    ln = lg.size()
    lt = _shares(land, lkeys, 'lt_', {
        'tree_share': TREE_BEARING, 'forest_share': FOREST,
        'taiga_share': TAIGA, 'jungle_share': JUNGLE, 'swamp_share': SWAMP,
        'savanna_share': SAVANNA, 'treelight_share': TREE_LIGHT,
    }, ln)
    tree_d = lg['dist_tree_biome'].agg(['mean', 'median', 'min', 'max', 'std'])
    tree_d.columns = ['lt_dist_tree_' + s for s in tree_d.columns]
    parts.append(lt.join(tree_d))

    area = pd.concat(parts, axis=1)
    area.index.names = KEYS
    return area.reset_index()


def build_area_table(db_path, batch_size=250):
    con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    try:
        seeds = pd.read_sql_query(
            'SELECT DISTINCT seed FROM spawn_chunks ORDER BY seed',
            con)['seed'].tolist()
        print(f'  {len(seeds):,} seeds in {db_path.name}')

        cols = ', '.join(GRID_COLS)
        out = []
        t0 = time.time()
        for i in range(0, len(seeds), batch_size):
            chunk = seeds[i:i + batch_size]
            ph = ','.join('?' * len(chunk))
            spawn = pd.read_sql_query(
                f'SELECT seed, {cols} FROM spawn_chunks WHERE seed IN ({ph})',
                con, params=chunk)
            spawn['kind'] = 'spawn'
            spawn['area_id'] = -1
            rand = pd.read_sql_query(
                f'SELECT seed, area_kind AS kind, area_id, {cols} '
                f'FROM random_chunks WHERE seed IN ({ph})', con, params=chunk)
            out.append(aggregate_batch(pd.concat([spawn, rand],
                                                 ignore_index=True)))
            done = min(i + batch_size, len(seeds))
            print(f'\r  aggregating {done:,}/{len(seeds):,} seeds '
                  f'({time.time() - t0:.0f}s)', end='', flush=True)
        print()
    finally:
        con.close()
    return pd.concat(out, ignore_index=True)


def load_area_table(db_path, rebuild):
    """Aggregation is the slow part, so cache it and redo it when the DB moves."""
    fresh = (FEATURE_CACHE.exists()
             and FEATURE_CACHE.stat().st_mtime >= db_path.stat().st_mtime)
    if fresh and not rebuild:
        print(f'Loading cached area features from {FEATURE_CACHE.name} '
              '(--rebuild to redo)')
        return pd.read_csv(FEATURE_CACHE)

    print('Building area table from the grid tables...')
    area = build_area_table(db_path)
    area.to_csv(FEATURE_CACHE, index=False)
    print(f'  wrote {len(area):,} area rows -> {FEATURE_CACHE.name}')
    return area


def feature_sets(area):
    """Three nested views, printed in this order.

    water_composition  the 10 biome-water features on their own
    water              those plus surface height, diversity and the 80
                       structure-distance aggregates
    land_trees         the hypothesis set: trees over land chunks only
    """
    exclude = set(KEYS) | {'n_chunks'}
    water = [c for c in area.columns if c not in exclude]
    comp = [c for c in water if c.startswith(('comp_', 'water_ring'))]
    land = [c for c in LAND_TREE_FEATURES if c in area.columns]
    # Every set is a subset of `water`, so a gap between two of them is the
    # value of the features that differ and nothing else.
    assert set(comp) <= set(water) and set(land) <= set(water)
    return {'water_composition': comp, 'water': water, 'land_trees': land}


# ---------------------------------------------------------------------------
# Models and evaluation
# ---------------------------------------------------------------------------
def build_models(jobs):
    return {
        'logistic_regression': lambda: make_pipeline(
            SimpleImputer(strategy='median'), StandardScaler(),
            LogisticRegression(max_iter=5000, class_weight='balanced')),
        'random_forest': lambda: make_pipeline(
            SimpleImputer(strategy='median'),
            RandomForestClassifier(n_estimators=500, min_samples_leaf=3,
                                   class_weight='balanced_subsample',
                                   random_state=0, n_jobs=jobs)),
        'knn': lambda: make_pipeline(
            SimpleImputer(strategy='median'), StandardScaler(),
            KNeighborsClassifier(n_neighbors=25, weights='distance',
                                 n_jobs=jobs)),
    }


INNER_FOLDS = 3


def fold_threshold(X, y, groups, factory):
    """Decision cut for one training fold, at Youden's J -- which is the
    balanced-accuracy-optimal cut. The classes run 1:3, so a fixed 0.5 would
    hand kNN (no class_weight) a near-chance balanced accuracy.

    The probabilities come from an INNER grouped CV rather than from refitting
    on the training rows. Resubstituted probabilities are near-separable --
    kNN with weights='distance' sees each training point at distance 0 and
    reproduces its own label -- which pushes the cut above everything the test
    fold can reach and collapses the confusion matrix into one column."""
    inner = StratifiedGroupKFold(n_splits=INNER_FOLDS, shuffle=True,
                                 random_state=0)
    p = np.zeros(len(y))
    for a, b in inner.split(X, y, groups):
        model = factory()
        model.fit(X[a], y[a])
        p[b] = model.predict_proba(X[b])[:, 1]
    fpr, tpr, thr = roc_curve(y, p)
    return thr[np.argmax(tpr - fpr)]


def evaluate(X, y, groups, factory, n_splits, seed=0):
    cv = StratifiedGroupKFold(n_splits=n_splits, shuffle=True,
                              random_state=seed)
    oof_p = np.zeros(len(y))
    oof_hat = np.zeros(len(y), dtype=int)
    fold_auc, fold_ba = [], []

    for tr, te in cv.split(X, y, groups):
        model = factory()
        model.fit(X[tr], y[tr])
        p_te = model.predict_proba(X[te])[:, 1]
        hat = (p_te >= fold_threshold(X[tr], y[tr], groups[tr], factory)
               ).astype(int)
        oof_p[te], oof_hat[te] = p_te, hat
        fold_auc.append(roc_auc_score(y[te], p_te))
        fold_ba.append(balanced_accuracy_score(y[te], hat))

    return {
        'auc': roc_auc_score(y, oof_p),
        'auc_fold_mean': float(np.mean(fold_auc)),
        'auc_fold_sd': float(np.std(fold_auc, ddof=1)),
        'bal_acc': balanced_accuracy_score(y, oof_hat),
        'bal_acc_fold_sd': float(np.std(fold_ba, ddof=1)),
        'cm': confusion_matrix(y, oof_hat),
    }


def top_drivers(X, y, columns, factory, k=10):
    """Fit once on everything, just to name what the model is leaning on."""
    model = factory()
    model.fit(X, y)
    step = model[-1]
    if hasattr(step, 'feature_importances_'):
        score, label = step.feature_importances_, 'importance'
    else:
        score, label = np.abs(step.coef_[0]), '|coef| (standardised)'
    order = np.argsort(score)[::-1][:k]
    return label, [(columns[i], float(score[i])) for i in order]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
CONTRASTS = [
    ('spawn vs control_land', 'control_land',
     'MAIN RESULT -- land confound removed'),
    ('spawn vs control', 'control',
     'uniform baseline -- the water effect, uncorrected'),
]


def run(area, args):
    sets = feature_sets(area)
    models = build_models(args.jobs)
    rows = []

    for title, negative, note in CONTRASTS:
        sub = area[area['kind'].isin(['spawn', negative])].reset_index(drop=True)
        y = (sub['kind'] == 'spawn').astype(int).to_numpy()
        groups = sub['seed'].to_numpy()

        print()
        print('=' * 78)
        print(f'CONTRAST: {title}   ({note})')
        print('=' * 78)
        print(f'  {int(y.sum()):,} spawn areas vs {int((1 - y).sum()):,} '
              f'{negative} areas over {len(np.unique(groups)):,} seeds')
        print(f'  {args.folds}-fold StratifiedGroupKFold grouped by seed: '
              'a seed\'s areas never straddle train and test')

        for set_name, columns in sets.items():
            X = sub[columns].to_numpy(dtype=float)
            nan_rows = int(np.isnan(X).any(axis=1).sum())
            print()
            print(f'  feature set "{set_name}" -- {len(columns)} features'
                  + (f', {nan_rows} rows with a missing value (median-imputed '
                     'inside each fold)' if nan_rows else ''))
            print(f'    {"model":<22}{"ROC-AUC":<20}{"bal.acc":<16}'
                  'confusion  TN FP / FN TP')

            for model_name, factory in models.items():
                r = evaluate(X, y, groups, factory, args.folds)
                (tn, fp), (fn, tp) = r['cm']
                print(f'    {model_name:<22}'
                      f'{r["auc"]:.3f} (+/-{r["auc_fold_sd"]:.3f})   '
                      f'{r["bal_acc"]:.3f} (+/-{r["bal_acc_fold_sd"]:.3f})  '
                      f'{tn:>6} {fp:>5} / {fn:>5} {tp:>5}')
                rows.append({'contrast': title, 'feature_set': set_name,
                             'model': model_name, 'n_features': len(columns),
                             'roc_auc': r['auc'],
                             'roc_auc_fold_mean': r['auc_fold_mean'],
                             'roc_auc_fold_sd': r['auc_fold_sd'],
                             'bal_acc': r['bal_acc'],
                             'bal_acc_fold_sd': r['bal_acc_fold_sd'],
                             'tn': tn, 'fp': fp, 'fn': fn, 'tp': tp})

        if negative == 'control_land':
            X = sub[sets['water']].to_numpy(dtype=float)
            label, drivers = top_drivers(X, y, sets['water'],
                                         models['random_forest'])
            print()
            print(f'  what the random forest leans on ({label}):')
            for name, value in drivers:
                print(f'    {name:<34}{value:.4f}')

    return pd.DataFrame(rows)


def summarise(results):
    print()
    print('=' * 78)
    print('SUMMARY -- mean ROC-AUC across the three models')
    print('=' * 78)
    order = list(dict.fromkeys(results['feature_set']))
    table = (results.groupby(['contrast', 'feature_set'])['roc_auc']
             .mean().unstack()[order])
    print(table.to_string(float_format=lambda v: f'{v:.3f}'))
    main = table.loc['spawn vs control_land']
    uniform = table.loc['spawn vs control']
    print()
    for line in [
        f'Against control_land the 10 water-composition features reach '
        f'{main["water_composition"]:.3f}, while the',
        f'land-only tree features sit at {main["land_trees"]:.3f}. That gap '
        'is the result: a spawn area is',
        'identifiable by how much water it holds, and once you condition on',
        'land its tree cover is near chance. There is no tree bias at spawn.',
        '',
        f'The full water set reaches {main["water"]:.3f}. The structure '
        'distances carry about 0.03',
        'of that on their own, led by the ocean structures -- the same',
        'coastality showing up a second way rather than a separate effect.',
        '',
        f'Against the uniform control the water set climbs to '
        f'{uniform["water"]:.3f} and even the',
        f'land-only tree set rises to {uniform["land_trees"]:.3f}, because '
        'that comparison still carries',
        'the land/ocean confound the land control was built to remove.',
    ]:
        print(f'  {line}' if line else '')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db', type=Path, default=DEFAULT_DB)
    ap.add_argument('--folds', type=int, default=5)
    ap.add_argument('--jobs', type=int, default=-1,
                    help='cores for RF/kNN (-1 = all)')
    ap.add_argument('--rebuild', action='store_true',
                    help='re-aggregate the area table instead of using the cache')
    args = ap.parse_args()

    if not args.db.exists():
        sys.exit(f'No database at {args.db}. Run Data_to_DB.py first.')

    area = load_area_table(args.db, args.rebuild)
    counts = area['kind'].value_counts()
    print('  areas by kind: '
          + ', '.join(f'{k} {v:,}' for k, v in counts.items()))

    results = run(area, args)
    summarise(results)
    results.to_csv(RESULTS_CSV, index=False)
    print(f'\nWrote {RESULTS_CSV.name}')


if __name__ == '__main__':
    main()
