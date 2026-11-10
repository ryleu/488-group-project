================================================================================
MINECRAFT BIG DATA: SPAWN CHUNK & BIOME ANALYSIS
================================================================================

PROJECT OVERVIEW
This project analyzes Minecraft's world generation to determine if the game 
biases certain biomes or early-game resources at the world spawn. It uses a 
C++ pipeline to generate data for thousands of random seeds, loads that data 
into a SQLite database, and uses a Flask web server to visualize the results.

For every seed the collector samples a 19x19 chunk grid at the world spawn 
AND the same 19x19 grid at 3 randomly placed control areas elsewhere in that 
same world. Because the areas are paired within a seed, between-world 
variation cancels out and a shift in the paired delta is evidence of a real 
spawn bias rather than a difference between worlds.

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
   
   * By default, this is set to DEFAULT_MAX_SEEDS = 5000 in the code, which
     generates roughly 12.6 million grid rows and 35,000 sample rows.
   * You can override the seed count on the command line: ./spawn_biomes 500
   * It outputs three files: "spawn_chunk_biomes.csv" (per-chunk grid at spawn),
     "random_chunk_biomes.csv" (per-chunk grid at the control areas), and
     "seed_samples.csv" (4 point-level rows per seed -- the ML feature table).
   * 5000 seeds takes ~7 minutes and writes ~2.9 GB (measured: 406 s,
     12,635,000 grid rows + 35,000 sample rows, 16 threads).


================================================================================
EXTRACTED FEATURES
================================================================================
This phase collects everything cubiomes can produce. Nothing is aggregated away
and nothing is censored -- pare it down during analysis, not here. All three
tables carry the SAME 30 feature columns, emitted by one shared function so
they cannot drift apart.

IDENTITY / TERRAIN
  biome_id, biome_name   surface biome, sampled at the real surface height
  surface_y              approximate surface height (mapApproxHeight)
  is_slime_chunk         0/1

CLIMATE (the raw generator inputs, fixed-point, roughly +/-10000)
  clim_temperature  clim_humidity  clim_continentalness
  clim_erosion      clim_depth     clim_weirdness
  These are what world generation feeds into biome selection, sampled at quart
  resolution. Verified: climateToBiome() of these six reproduces getBiomeAt()
  100% of the time, so they losslessly encode the biome decision.

DISTANCES (blocks, from the row's own point)
  dist_tree_biome              nearest tree-bearing biome
  dist_passive_structure       convenience min(), see below
  dist_hostile_structure       convenience min(), see below

  17 overworld structures, each its own column:
    dist_village         dist_shipwreck       dist_ocean_ruin
    dist_trail_ruins     dist_igloo           dist_desert_pyramid
    dist_jungle_temple   dist_ruined_portal   dist_desert_well
    dist_buried_treasure dist_outpost         dist_mansion
    dist_monument        dist_swamp_hut       dist_trial_chambers
    dist_ancient_city    dist_mineshaft

  Other dimensions:
    dist_nether_fortress  NETHER blocks, from the nether position this point
    dist_nether_bastion   maps to (x/8, z/8). Multiply by 8 for overworld-
                          equivalent travel distance.
    dist_end_city         from the End arrival platform at (100, 0), where you
                          always land -- so it is one value per SEED, identical
                          on every row of that seed. (Verified constant.)

TREE-BEARING BIOMES
  All forest / taiga / jungle types, swamp, mangrove swamp, cherry grove, pale
  garden, wooded badlands, windswept forest, grove, and the savanna family
  (acacia). Excludes plains, sunflower_plains, meadow and the windswept hills --
  their trees are too sparse to count on, and they are common enough that
  including them would pin the feature at 0 for most samples. Edit
  TREE_BIOMES[] to change this.

  Searched on a 1:16 biome map (1 cell = 1 chunk) with a 1024-block halo, with a
  6400-block point search as fallback for anything the map misses. The map is
  sampled at block y 160, which agrees with a surface-accurate sample ~93% of
  the time and keeps cave biomes (which would read as "no trees") under 0.1% of
  cells.

DELIBERATELY NOT COLLECTED
  Strongholds. They use concentric-ring placement rather than the random-spread
  grid every other structure uses: the ring radii are fixed and only the angles
  vary by seed, so the distance mostly encodes ring geometry rather than
  anything seed-specific. Collecting them exactly was also 80% of total runtime
  (18.6 min vs 3.0 min for 5000 seeds), because the exact path biome-checks all
  128. To bring them back cheaply, iterate initFirstStronghold/nextStronghold
  with a NULL generator -- that skips the biome check for ~2.5 ms/seed instead
  of ~1020, and over 80 sample points the mean nearest-stronghold distance
  differed by only 1.7 blocks (1591.4 exact vs 1593.1 approximate).

NOT AVAILABLE
  Dungeons (monster spawner rooms) are chunk feature generation, not structure
  placement, and cubiomes does not model them. That is the only thing asked for
  that is missing.


================================================================================
RELATION TO IN-GAME /locate
================================================================================
Both in-game commands are square-spiral, FIRST-HIT searches. Neither returns the
true nearest thing, and that matters if you normalize the distances into scores.

/locate biome <id>   ->  BiomeSource.findClosestBiome3d
  - works in quart coordinates (1 quart = 4 blocks; biomes are stored at 4x4x4,
    so that is the finest grid that exists)
  - samples a 32-block horizontal grid, walking BlockPos.spiralAround outward
  - at each column it tries several Y values, stepping 64 blocks out from the
    origin's Y, which is why it can land on cave biomes
  - max radius 6400 blocks
  - returns the FIRST match in spiral order

/locate structure <id|#tag>  ->  ChunkGenerator.findNearestMapStructure
  - walks region rings outward, ring index 0..100 for the command
  - one ring step = that structure's placement spacing (34 chunks / 544 blocks
    for villages, 80 chunks / 1280 blocks for mansions)
  - per candidate region it resolves the generation-attempt chunk and runs the
    biome viability check (cubiomes' isViableStructurePos is the same check)
  - within one placement's ring it returns the FIRST viable hit in loop order;
    across different types in a tag it takes the Euclidean min, but only among
    hits in the SAME ring
  - returns as soon as any ring produces a hit
  - strongholds are the exception: concentric-ring placement with precomputed
    positions, so those really are nearest

Consequence: a structure at 700 blocks in ring 2 beats one at 600 blocks in
ring 3. Measured over 240 sample points (60 seeds x 4 points), reproducing the
game's ordering instead of taking the exact minimum gives:

  tree biome          mean 92.6 vs 85.6 exact   overstated on 25% of samples
                      median error 0, p90 22, max 168 blocks
  passive structure   mean 376.0 vs 236.2       overstated on 60% of samples
                      median error 40, p90 435, max 958 blocks
  hostile structure   mean 981.0 vs 733.8       overstated on 41% of samples
                      median error 0, p90 880, max 3750 blocks

So this project does NOT reproduce /locate's ordering. It takes the exact
Euclidean minimum, because the spiral's overstatement is an artifact of the
search order rather than a property of the world, and it would show up as pure
noise once the distances are normalized into scores. Skipping the viability
check for any candidate already farther than the best confirmed hit is what
makes the exact search affordable (radius 8192 costs ~12 ms/seed for 4 points).


================================================================================
WHY THE COMPOSITES ARE ONLY A CONVENIENCE
================================================================================
dist_passive_structure and dist_hostile_structure are a min() over a category.
A min() hides which type produced it, and a dense type wins it almost every
time. Measured over 400 sample points, adding Trial Chambers to the hostile set
changes which type is nearest:

                     hostile set as shipped    + Trial Chambers / Ancient City
    Outpost                  33.5%                        2.5%
    Mansion                   2.8%                          -
    Monument                 57.0%                        6.0%
    Swamp Hut                 6.8%                        0.8%
    Trial Chambers              -                        85.0%

That is NOT a variance problem. Trial Chambers' own distance is well spread
(mean 229, sd 101, cv 0.44), as is Ruined Portal's (mean 264, sd 105, cv 0.40);
every structure column has cv between 0.40 and 0.74. The issue is only that
min() lets one dense type stand in for the category and silently discards the
outpost/mansion information.

So every type has its own column, and the composites are computed AS the min of
those per-type values -- the two can never disagree. Build whatever category
you want in SQL, no re-run needed:

    SELECT seed, sample_type,
           MIN(dist_outpost, dist_mansion, dist_monument, dist_swamp_hut,
               dist_trial_chambers, dist_ancient_city, dist_mineshaft)
             AS hostile_incl_underground
    FROM samples;


================================================================================
SEARCH STRATEGY AND COST
================================================================================
Structure candidates are gathered ONCE per area and reused for all 361 grid
chunks plus that area's sample point. A grid chunk sits at most ~212 blocks from
the area centre, so a structure can only be nearest for some chunk if it lies
within (nearest-to-centre + 2*212) of the centre; AREA_SLACK = 512 covers that.
Verified on corner chunks -- the worst case -- against exhaustive brute force:
0 mismatches.

The gather expands region ring by region ring, stopping only once the next ring
cannot beat the best hit so far. A structure in ring r is at least (r-1)*spacing
blocks away, which makes that exit exact. Dense types settle in ring 1-2; rare
types like Mansion expand as far as they need. That is what makes a 65536-block
cap affordable -- it runs FASTER than a fixed 8192-block box while censoring
nothing, where the fixed box left 7.8% of mansion distances NULL:

    fixed box   8192 : 11.4 s / 300 seeds, 117 NULLs
    fixed box  16384 : 15.6 s / 300 seeds,   0 NULLs
    fixed box  65536 : 56.1 s / 300 seeds,   0 NULLs
    expanding ring   :  8.1 s / 300 seeds,   0 NULLs   <-- what the collector does

Every distance column comes back fully populated. There are no NULLs to impute.

COST: ~7 min and ~2.9 GB of CSV for 5000 seeds, plus a 3.8 GB database
(Data_to_DB.py takes ~2 min to load it).
(7 areas per seed rather than 4, since the land control group was added.)


================================================================================
MINESHAFTS: A TRAP IN THE CUBIOMES HEADER
================================================================================
finders.h documents getMineshafts as
    (int chunkX, int chunkZ, int chunkW, int chunkH)   -- origin + size
but finders.c implements
    (int cx0, int cz0, int cx1, int cz1)               -- two corners
        for (i = cx0; i <= cx1; i++) ...

The header comment is stale. Passing a width where cx1 belongs scans the wrong
box, and an entirely empty one whenever cx0 exceeds the value passed as cx1 --
which looks exactly like "no mineshafts generate here". It also returns the
chunk's NW corner BLOCK (i*16), not a chunk coordinate.

Mineshaft density in 1.21 is uniform at every distance from the origin
(measured 0.0042 / 0.0038 / 0.0044 / 0.0045 over 201x201 chunk windows centred
at 0, 500, 5000 and 50000). The "nextInt(80) < max(|cx|,|cz|)" falloff that
suppresses mineshafts near spawn is pre-1.13 only; finders.c gates it behind
"mc < MC_1_13". Sanity check: dist_mineshaft comes out at mean 131.6 blocks
against the 2-D Poisson prediction 0.5/sqrt(0.004/256) = 126.5.


================================================================================
TWO THINGS THAT WILL BREAK YOUR ANALYSIS IF YOU UNDO THEM
================================================================================
1. MEASUREMENT ORIGIN -- every sample row is measured from its CHUNK CENTRE.

   dist_tree_biome and dist_mineshaft are both measured TO chunk centres (the
   biome map is 1:16 and mineshafts are a per-chunk roll), so the value depends
   on where the origin sits inside its own chunk. The world spawn point lands on
   a chunk corner (spawn.x % 16 == 0 for 199 of 200 seeds) while an area centre
   is always at +8,+8.

   Measuring the spawn row from its true position therefore made every control
   distance an exact multiple of 16*sqrt(n) and every spawn distance not:

        on the chunk-centre lattice:   spawn 0.0%   control 55.8%
        dist_tree_biome exactly 0:     spawn 0.0%   control 37.5%
        min non-zero tree distance:    spawn 8.00   control 16.00

   That is a deterministic group marker. A tree model splits on it directly and
   scores ~0.95 separating spawn from control while learning nothing about
   Minecraft. After the fix both groups sit at offset (8,8) and the mineshaft
   lattice rates are identical (16.8% vs 16.8%).

   The true spawn point is preserved in exact_x / exact_z, so nothing is lost.
   Consequence worth knowing: the spawn sample row is now byte-identical to the
   centre chunk of its spawn grid (verified, 400 seeds x 33 columns).

2. CONTROL GROUP -- compare against control_land, not control.

   Minecraft always spawns you on land. The uniform control is 43.1% water and
   shore, so "spawn beats uniform random" mostly measures land-vs-ocean. The
   land control is the same draw, rejection-sampled onto a non-water surface
   biome. Single-feature separability (AUC, 0.5 = no difference):

                              vs uniform    vs land
        surface_y                0.695        0.506
        dist_tree_biome          0.620        0.504

   Both collapse to nothing once the land confound is removed.


================================================================================
WHAT THE DATA SAYS (5000 seeds)
================================================================================
Hypothesis: spawns are easier than random locations -- more trees, closer to
good structures, farther from bad ones. Tested against control_land, with
AUC standard error 0.0047 under the null, so |AUC-0.5| > 0.0094 is 2-sigma.

    feature                  vs uniform   vs land     z      verdict vs land
    dist_tree_biome             0.357      0.504     +0.8    NO EFFECT
    surface_y                   0.704      0.507     +1.4    NO EFFECT
    dist_village                0.467      0.536     +7.6    FARTHER at spawn
    dist_passive_structure      0.577      0.447    -11.3    closer at spawn
    dist_hostile_structure      0.546      0.467     -7.1    CLOSER at spawn
    dist_monument               0.527      0.415    -18.0    closer at spawn
    dist_mineshaft              0.564      0.559    +12.6    farther at spawn

  (AUC < 0.5 means spawn values are smaller, i.e. closer.)

So against a land-matched control:
  * "more trees" does not hold -- the effect is entirely the land confound.
  * "closer to good structures" holds for the composite but NOT for villages,
    which are farther. The composite includes Shipwreck and Ocean Ruin.
  * "farther from bad structures" is REVERSED -- spawn is closer, driven by
    Ocean Monument (z = -18.0).

The mechanism behind all three looks like coastality. Water share of the
surrounding 19x19 area:

        spawn          mean 28.0%   median 21.6%
        control        mean 41.6%   median 25.5%
        control_land   mean 17.1%   median 12.2%

Spawn areas hold roughly twice the water of a random land area. That pulls them
toward ocean structures (monuments, shipwrecks, ocean ruins) and away from
inland ones (villages) -- which is exactly the pattern above. If you want to
test "is spawn better" independently of this, condition on water share or add a
coastality-matched control.

These are the final 5000-seed numbers. Every verdict above matches the
400-seed run; the z-scores grew with sqrt(n) and nothing changed sign.


================================================================================
OUTPUT SCHEMA
================================================================================
Three tables. All carry the identical 30 feature columns listed above; they
differ only in how the row is identified.

data/spawn_chunk_biomes.csv  ->  spawn_chunks
  seed, spawn_x, spawn_z, chunk_x, chunk_z, <30 feature columns>
  361 rows per seed: the 19x19 chunk grid centred on the world spawn.

data/random_chunk_biomes.csv  ->  random_chunks
  seed, area_kind, area_id, center_x, center_z, chunk_x, chunk_z,
  <30 feature columns>
  2166 rows per seed: the same 19x19 grid at 6 control areas.
  area_kind is 'control' (3 uniform random areas) or 'control_land' (3 areas
  rejection-sampled onto land). area_id is 0..2 within each kind.
  ALWAYS filter on area_kind -- see the pitfalls section above.

data/seed_samples.csv  ->  samples
  seed, sample_type, area_id, x, z, exact_x, exact_z, <30 feature columns>
  7 rows per seed -- THE POINT-LEVEL FEATURE TABLE. sample_type is 'spawn'
  (area_id -1), 'control' or 'control_land' (area_id 0..2 within each).

  x,z is the CHUNK CENTRE the distances were measured from, the same convention
  for every group. exact_x,exact_z is the true world spawn point for spawn rows
  and repeats x,z for control rows. See pitfall 1 above for why this matters.

  Cross-check: every sample row equals the centre chunk of its own grid area.
  Verified across 400 spawn areas and 600 control areas, all 30 feature columns
  identical.

Totals per seed: 2527 grid rows + 7 sample rows.

Example -- min-max normalise to a 0-1 score where 1 = best (closest wood,
closest loot, farthest from danger):

    SELECT seed, sample_type, area_id,
           1 - (dist_tree_biome      - MIN(dist_tree_biome)      OVER ())
             / (MAX(dist_tree_biome) OVER () - MIN(dist_tree_biome) OVER ()) AS wood_score,
           1 - (dist_village         - MIN(dist_village)         OVER ())
             / (MAX(dist_village)    OVER () - MIN(dist_village) OVER ())    AS village_score,
               (dist_outpost         - MIN(dist_outpost)         OVER ())
             / (MAX(dist_outpost)    OVER () - MIN(dist_outpost) OVER ())    AS safety_score
    FROM samples;

Normalising globally (as above) makes scores comparable across seeds.
Normalising per-seed instead changes what a model learns -- pick deliberately.

Paired spawn-vs-own-controls delta (negative = closer at spawn):

    SELECT AVG(s.dist_tree_biome - c.t) AS d_tree
    FROM samples s
    JOIN (SELECT seed, AVG(dist_tree_biome) t FROM samples
          WHERE sample_type='control_land' GROUP BY seed) c USING (seed)
    WHERE s.sample_type='spawn';

Use 'control_land' for anything testing a spawn bias. Swap in 'control' only
when you specifically want the uniform-random baseline, and expect the
land/ocean confound to dominate it.


================================================================================
STEP 2: BUILD THE SQLITE DATABASE
================================================================================
1. The generated .csv files are written to data/ next to the Python scripts.
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
2. Biome Category Share: Spawn vs Control Areas: Compares early-game
   playability categories (Tree-Bearing, Light, Water, Barren) across spawn,
   the uniform control and the land control. Plots SHARES, not counts --
   random_chunks holds 6 areas per seed against spawn's 1, so raw counts are 6x
   and lump both control groups together. The category lists mirror TREE_BIOMES[]
   and WATER_BIOMES[] in the collector, so savanna counts as tree-bearing in
   both places.
3. Spawn Density Heatmap: Proves the spawn algorithm heavily clusters near
   (0,0). Counts one row per SEED -- the old SELECT DISTINCT collapsed seeds
   that share a spawn point. At 5000 seeds there are only 1012 distinct spawn
   coordinates, so DISTINCT discarded 3988 seeds (80%), and it discarded them
   hardest exactly where spawns cluster.
4. Biome Diversity: Histogram showing unique biomes per spawn area.
5. Water and Tree Share vs Distance from Area Centre: Two line panels over
   ring distance 0-9 from the area centre. LEFT, water share: spawn climbs
   2.1% -> 32.2% from the centre chunk to the grid edge, against the land
   control's 0% -> 20.1%. Spawn areas really are about twice as coastal.
   RIGHT, tree-bearing share among LAND chunks only: flat at 64.7-65.3% on
   every ring, against the land control's 65.0-65.4%. That pair is the
   finding -- there is no tree bias at spawn; what looks like one is the water
   gradient on the left, and it vanishes once you condition on land.
   (Replaces the old 19x19 tree-heaviness heatmap, which averaged the water
   gradient and the tree share into one number per cell and showed neither.)
6. ML Feature Distributions: Normalised histograms of all three distance 
   features, spawn areas vs control areas.
7. Per-Seed Paired Delta: For each seed, its spawn-area mean minus its own 
   control-area mean, per feature. A distribution centred left of zero means 
   the feature is systematically closer at spawn.
