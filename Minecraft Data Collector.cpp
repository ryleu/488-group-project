#include <iostream>
#include <fstream>
#include <random>
#include <string>
#include <chrono>
#include <iomanip>
#include <filesystem>
#include <vector>
#include <mutex>
#include <atomic>
#include <sstream>
#include <algorithm>
#include <cmath>
#include <cstdint>

extern "C" {
    #include "generator.h"
    #include "finders.h"
    #include "biomes.h"
    #include "util.h"
}

// ===========================================================================
// Configuration
// ===========================================================================
constexpr int MC_VERSION = MC_1_21;
constexpr int SPAWN_CHUNK_RADIUS = 9;              // 19x19 chunk sampling grid
constexpr int GRID = 2 * SPAWN_CHUNK_RADIUS + 1;

// Each seed produces one spawn area plus this many randomly placed control
// areas, sampled with the identical 19x19 grid. Same-seed pairing is what makes
// the spawn-vs-world comparison a within-world comparison rather than a
// cross-world one.
constexpr int CONTROL_AREAS_PER_SEED = 3;
constexpr int CONTROL_CHUNK_SPREAD = 1250;         // +/- 20,000 blocks
constexpr int MIN_CONTROL_SEPARATION = 2000;

// Second control group, rejection-sampled onto land. Minecraft always spawns
// you on land but the uniform control is ~43% water, so without this group the
// land/water confound swamps any real spawn bias. See README.
constexpr int CONTROL_LAND_AREAS_PER_SEED = 3;
constexpr int LAND_REJECT_ATTEMPTS = 64;

// --- Tree-biome distance ---
// Read off a 1:16 biome map covering the grid plus a halo, with a wide point
// search as fallback. Sampling at block y 160 rather than the true surface
// keeps cave biomes (which would read as "no trees") under 0.1% of cells.
constexpr int TREE_SEARCH_RADIUS = 1024;           // blocks
constexpr int BIOME_MAP_SCALE = 16;
constexpr int BIOME_MAP_Y = 40;                    // 1:4 units -> block y 160
constexpr int MAP_HALO = TREE_SEARCH_RADIUS / 16;  // in chunks/cells
constexpr int MAP_SIZE = GRID + 2 * MAP_HALO;
constexpr int WIDE_TREE_RADIUS = 6400;             // matches /locate biome
constexpr int WIDE_TREE_STEP = 32;                 // matches /locate biome

// Hard cap only; the ring search expands just as far as it needs, so raising
// this is nearly free.
constexpr int STRUCT_CAP = 65536;

// Structures are gathered once per area and reused for all 361 chunks. A chunk
// sits at most ~212 blocks from the centre, so only structures within
// (nearest-to-centre + 2*212) can win for any chunk -- hence the slack.
constexpr double AREA_SLACK = 512.0;

// Strongholds are deliberately not collected: concentric-ring placement, so the
// distance mostly encodes ring geometry. See README to add them back cheaply.

constexpr float NOT_FOUND = -1.0f;
constexpr int DEFAULT_MAX_SEEDS = 5000;
const std::string OUTPUT_DIR = "data";

// ===========================================================================
// Feature category definitions
// ===========================================================================

// Biomes that reliably generate choppable wood. Excludes plains, meadow and the
// windswept hills: too sparse to count on, and common enough to pin the feature
// at 0 for most samples.
static const int TREE_BIOMES[] = {
    forest, flower_forest, birch_forest, old_growth_birch_forest,
    dark_forest, pale_garden,
    taiga, snowy_taiga, old_growth_pine_taiga, old_growth_spruce_taiga,
    jungle, sparse_jungle, bamboo_jungle,
    swamp, mangrove_swamp,
    cherry_grove, wooded_badlands, windswept_forest, grove,
    savanna, savanna_plateau, windswept_savanna,
};
constexpr int N_TREE_BIOMES = (int)(sizeof(TREE_BIOMES) / sizeof(TREE_BIOMES[0]));

// Water/shore biomes, used to reject land-control candidates.
static const int WATER_BIOMES[] = {
    ocean, deep_ocean, warm_ocean, lukewarm_ocean, deep_lukewarm_ocean,
    cold_ocean, deep_cold_ocean, frozen_ocean, deep_frozen_ocean,
    river, frozen_river, beach, snowy_beach, stony_shore, mushroom_field_shore,
};
constexpr int N_WATER_BIOMES = (int)(sizeof(WATER_BIOMES) / sizeof(WATER_BIOMES[0]));

static bool is_water_biome(int b)
{
    for (int i = 0; i < N_WATER_BIOMES; ++i) if (b == WATER_BIOMES[i]) return true;
    return false;
}

// Every overworld structure cubiomes can place, each with its own column. The
// passive/hostile composites below are only a min() over a subset.
// Mineshaft has no region grid and is handled by collect_mineshafts().
struct StructEntry { int type; const char* col; };
static const StructEntry STRUCT_TYPES[] = {
    { Village,        "dist_village"         },
    { Shipwreck,      "dist_shipwreck"       },
    { Ocean_Ruin,     "dist_ocean_ruin"      },
    { Trail_Ruins,    "dist_trail_ruins"     },
    { Igloo,          "dist_igloo"           },
    { Desert_Pyramid, "dist_desert_pyramid"  },
    { Jungle_Temple,  "dist_jungle_temple"   },
    { Ruined_Portal,  "dist_ruined_portal"   },
    { Desert_Well,    "dist_desert_well"     },
    { Treasure,       "dist_buried_treasure" },
    { Outpost,        "dist_outpost"         },
    { Mansion,        "dist_mansion"         },
    { Monument,       "dist_monument"        },
    { Swamp_Hut,      "dist_swamp_hut"       },
    { Trial_Chambers, "dist_trial_chambers"  },
    { Ancient_City,   "dist_ancient_city"    },
    { Mineshaft,      "dist_mineshaft"       },
};
constexpr int N_STRUCT = (int)(sizeof(STRUCT_TYPES) / sizeof(STRUCT_TYPES[0]));

// Convenience categories; redefinable in SQL without re-running the collector.
static const int PASSIVE_SET[] = {
    Village, Shipwreck, Ocean_Ruin, Trail_Ruins, Igloo, Desert_Pyramid, Jungle_Temple,
};
static const int HOSTILE_SET[] = { Outpost, Mansion, Monument, Swamp_Hut };
constexpr int N_PASSIVE = (int)(sizeof(PASSIVE_SET) / sizeof(PASSIVE_SET[0]));
constexpr int N_HOSTILE = (int)(sizeof(HOSTILE_SET) / sizeof(HOSTILE_SET[0]));

// Other dimensions. Fortress/Bastion are in NETHER blocks from (x/8, z/8) --
// multiply by 8 for overworld-equivalent travel. End City is measured from the
// End arrival platform, so it is one value per seed rather than per point.
struct DimEntry { int type; int dim; const char* col; };
static const DimEntry DIM_STRUCTS[] = {
    { Fortress, DIM_NETHER, "dist_nether_fortress" },
    { Bastion,  DIM_NETHER, "dist_nether_bastion"  },
    { End_City, DIM_END,    "dist_end_city"        },
};
constexpr int N_DIM_STRUCT = (int)(sizeof(DIM_STRUCTS) / sizeof(DIM_STRUCTS[0]));
constexpr int END_SPAWN_X = 100, END_SPAWN_Z = 0;
constexpr int DIM_STRUCT_CAP = 16384;

static const char* CLIMATE_COLS[6] = {
    "clim_temperature", "clim_humidity", "clim_continentalness",
    "clim_erosion", "clim_depth", "clim_weirdness",
};

static std::vector<uint8_t> g_is_tree_biome;

// Thread safety
std::mutex file_mutex;
std::mutex io_mutex;
std::atomic<int> completed_seeds{0};
std::atomic<long long> total_rows_written{0};

// ===========================================================================
// Per-area context: everything gathered once and reused for all 361 chunks
// ===========================================================================
struct AreaCtx {
    std::vector<uint8_t> tree_mask;        // MAP_SIZE^2, 1 = tree-bearing chunk
    int map_x0 = 0, map_z0 = 0;            // NW corner of the map, in chunks
    std::vector<Pos> hits[N_STRUCT];       // candidate positions per type
    Generator* nether = nullptr;
    Generator* end = nullptr;
    const SurfaceNoise* end_sn = nullptr;
    uint64_t seed = 0;
    float d_end_city = NOT_FOUND;          // per-seed: measured from the End platform
};

// ===========================================================================
// Search helpers
// ===========================================================================

// Nearest tree-bearing chunk, read off the coarse map. Expanding rings with an
// exact early exit, loosened by one ring since the origin sits inside a cell.
static float nearest_tree_on_map(const AreaCtx& a, int ox, int oz)
{
    const int si = (ox >> 4) - a.map_x0;
    const int sj = (oz >> 4) - a.map_z0;
    double best = -1.0;

    for (int r = 0; r < MAP_SIZE; ++r) {
        if (best >= 0 && best <= (double)(r - 1) * 16.0) break;
        const int i0 = si - r, i1 = si + r, j0 = sj - r, j1 = sj + r;

        for (int j = j0; j <= j1; ++j) {
            if (j < 0 || j >= MAP_SIZE) continue;
            auto consider = [&](int i) {
                if (i < 0 || i >= MAP_SIZE) return;
                if (!a.tree_mask[(size_t)j * MAP_SIZE + i]) return;
                const double dx = (double)((a.map_x0 + i) * 16 + 8) - ox;
                const double dz = (double)((a.map_z0 + j) * 16 + 8) - oz;
                const double d = std::sqrt(dx * dx + dz * dz);
                if (best < 0 || d < best) best = d;
            };
            if (j == j0 || j == j1) { for (int i = i0; i <= i1; ++i) consider(i); }
            else { consider(i0); consider(i1); }
        }
    }
    return best < 0 ? NOT_FOUND : (float)best;
}

// Fallback when the map holds no tree biome at all. Same grid and radius as
// /locate biome, but returns the Euclidean minimum, not the first spiral hit.
static float wide_tree_search(Generator* g, SurfaceNoise* sn, int ox, int oz)
{
    const int max_ring = WIDE_TREE_RADIUS / WIDE_TREE_STEP;
    double best = -1.0;
    for (int r = 0; r <= max_ring; ++r) {
        if (best >= 0 && best <= (double)r * WIDE_TREE_STEP) break;
        for (int di = -r; di <= r; ++di) {
            for (int dj = -r; dj <= r; ++dj) {
                if (std::abs(di) != r && std::abs(dj) != r) continue;
                const int x = ox + di * WIDE_TREE_STEP, z = oz + dj * WIDE_TREE_STEP;
                float sy = 64.0f;
                mapApproxHeight(&sy, nullptr, g, sn, x / 4, z / 4, 1, 1);
                const int y = std::clamp((int)sy + 1, -64, 320);
                const int b = getBiomeAt(g, 4, x >> 2, y >> 2, z >> 2);
                if (b < 0 || b >= (int)g_is_tree_biome.size() || !g_is_tree_biome[b]) continue;
                const double d = std::sqrt((double)(x - ox) * (x - ox) +
                                           (double)(z - oz) * (z - oz));
                if (best < 0 || d < best) best = d;
            }
        }
    }
    return best < 0 ? NOT_FOUND : (float)best;
}

// Gathers every candidate of one type that could be nearest for ANY point in
// the area. A structure in ring r is at least (r-1)*span away, so stopping once
// the next ring cannot beat (best + AREA_SLACK) is exact.
static void collect_type(Generator* g, uint64_t seed, int type,
                         int cbx, int cbz, std::vector<Pos>& out)
{
    StructureConfig sc;
    if (!getStructureConfig(type, MC_VERSION, &sc)) return;
    if (sc.dim != DIM_OVERWORLD) return;

    const int span = sc.regionSize * 16;
    const int rcx = (int)std::floor((double)cbx / span);
    const int rcz = (int)std::floor((double)cbz / span);
    const int max_ring = STRUCT_CAP / span + 1;
    double best = -1.0;

    for (int r = 0; r <= max_ring; ++r) {
        if (best >= 0 && (double)(r - 1) * span > best + AREA_SLACK) break;

        for (int di = -r; di <= r; ++di) {
            for (int dj = -r; dj <= r; ++dj) {
                if (std::abs(di) != r && std::abs(dj) != r) continue;

                Pos p;
                if (!getStructurePos(type, MC_VERSION, seed, rcx + di, rcz + dj, &p)) continue;
                const double dx = p.x - cbx, dz = p.z - cbz;
                const double d = std::sqrt(dx * dx + dz * dz);
                if (d > STRUCT_CAP) continue;
                // The threshold only ever shrinks as 'best' improves, so
                // skipping here can never drop something we would have kept.
                if (best >= 0 && d > best + AREA_SLACK) continue;

                if (!isViableStructurePos(type, g, p.x, p.z, 0)) continue;
                out.push_back(p);
                if (best < 0 || d < best) best = d;
            }
        }
    }

    if (best < 0) return;
    const double keep = best + AREA_SLACK;
    out.erase(std::remove_if(out.begin(), out.end(), [&](const Pos& p) {
        const double dx = p.x - cbx, dz = p.z - cbz;
        return std::sqrt(dx * dx + dz * dz) > keep;
    }), out.end());
}

// Mineshafts are a flat per-chunk roll, so they come from a chunk box grown
// until it provably covers the nearest hit for every point in the area.
// NOTE: getMineshafts takes CORNERS (cx0,cz0,cx1,cz1), not origin+size as
// finders.h claims, and returns the chunk's NW corner block, not a chunk coord.
static void collect_mineshafts(uint64_t seed, int cbx, int cbz, std::vector<Pos>& out)
{
    const int cx = cbx >> 4, cz = cbz >> 4;

    for (int R = 48; R <= 512; R *= 2) {
        const long long cells = (long long)(2 * R + 1) * (2 * R + 1);
        size_t cap = (size_t)(cells / 200) + 128;   // ~1.25x the 0.004 expectation
        std::vector<Pos> buf(cap);

        int n = getMineshafts(MC_VERSION, seed, cx - R, cz - R, cx + R, cz + R,
                              buf.data(), (int)cap);
        if (n > (int)cap) {
            cap = (size_t)n;
            buf.resize(cap);
            n = getMineshafts(MC_VERSION, seed, cx - R, cz - R, cx + R, cz + R,
                              buf.data(), (int)cap);
        }

        double best = -1.0;
        out.clear();
        for (int i = 0; i < n && (size_t)i < cap; ++i) {
            // Chunk centre stands in for the unmodelled position inside it.
            const Pos c{ buf[i].x + 8, buf[i].z + 8 };
            const double dx = c.x - cbx, dz = c.z - cbz;
            const double d = std::sqrt(dx * dx + dz * dz);
            if (best < 0 || d < best) best = d;
            out.push_back(c);
        }
        // Exact for every point in the area once coverage clears best + slack.
        if (best >= 0 && (double)(R - 1) * 16.0 >= best + AREA_SLACK) {
            const double keep = best + AREA_SLACK;
            out.erase(std::remove_if(out.begin(), out.end(), [&](const Pos& p) {
                const double dx = p.x - cbx, dz = p.z - cbz;
                return std::sqrt(dx * dx + dz * dz) > keep;
            }), out.end());
            return;
        }
    }
    out.clear();
}

// Exact nearest structure of one type, for a generator in any dimension. End
// City needs a terrain gate on top of the biome check, hence the End noise.
static float nearest_dim_struct(Generator* g, const SurfaceNoise* end_sn,
                                uint64_t seed, int type, int ox, int oz)
{
    StructureConfig sc;
    if (!getStructureConfig(type, MC_VERSION, &sc)) return NOT_FOUND;

    const int span = sc.regionSize * 16;
    const int rcx = (int)std::floor((double)ox / span);
    const int rcz = (int)std::floor((double)oz / span);
    double best = -1.0;

    for (int r = 0; r <= DIM_STRUCT_CAP / span + 1; ++r) {
        if (best >= 0 && best <= (double)(r - 1) * span) break;
        for (int di = -r; di <= r; ++di) {
            for (int dj = -r; dj <= r; ++dj) {
                if (std::abs(di) != r && std::abs(dj) != r) continue;
                Pos p;
                if (!getStructurePos(type, MC_VERSION, seed, rcx + di, rcz + dj, &p)) continue;
                const double dx = p.x - ox, dz = p.z - oz;
                const double d = std::sqrt(dx * dx + dz * dz);
                if (d > DIM_STRUCT_CAP) continue;
                if (best >= 0 && d >= best) continue;
                if (!isViableStructurePos(type, g, p.x, p.z, 0)) continue;
                if (type == End_City && end_sn &&
                    !isViableEndCityTerrain(g, end_sn, p.x, p.z)) continue;
                best = d;
            }
        }
    }
    return best < 0 ? NOT_FOUND : (float)best;
}

static float nearest_in(const std::vector<Pos>& list, int bx, int bz)
{
    double best = -1.0;
    for (const Pos& p : list) {
        const double dx = p.x - bx, dz = p.z - bz;
        const double d2 = dx * dx + dz * dz;
        if (best < 0 || d2 < best) best = d2;
    }
    return best < 0 ? NOT_FOUND : (float)std::sqrt(best);
}

static float min_over_set(const float* per_type, const int* members, int n)
{
    float best = NOT_FOUND;
    for (int i = 0; i < N_STRUCT; ++i) {
        if (per_type[i] < 0) continue;
        bool in = false;
        for (int m = 0; m < n; ++m)
            if (STRUCT_TYPES[i].type == members[m]) { in = true; break; }
        if (in && (best < 0 || per_type[i] < best)) best = per_type[i];
    }
    return best;
}

// ===========================================================================
// Row emission
// ===========================================================================

// Writes every feature column for one point. Shared by grid and sample rows so
// the two cannot drift apart.
static void emit_features(std::stringstream& ss, Generator* g, SurfaceNoise* sn,
                          const AreaCtx& a, int bx, int bz)
{
    float surface_y = 64.0f;
    mapApproxHeight(&surface_y, nullptr, g, sn, bx / 4, bz / 4, 1, 1);
    const int sample_y = std::clamp((int)surface_y + 1, -64, 320);

    const int biome_id = getBiomeAt(g, 1, bx, sample_y, bz);
    const char* bname = biome_id >= 0 ? biome2str(MC_VERSION, biome_id) : nullptr;

    int64_t np[6] = {0, 0, 0, 0, 0, 0};
    uint64_t dat = 0;
    sampleBiomeNoise(&g->bn, np, bx >> 2, sample_y >> 2, bz >> 2, &dat, 0);

    float per_type[N_STRUCT];
    for (int i = 0; i < N_STRUCT; ++i) per_type[i] = nearest_in(a.hits[i], bx, bz);

    float d_tree = nearest_tree_on_map(a, bx, bz);
    if (d_tree < 0) d_tree = wide_tree_search(g, sn, bx, bz);

    float d_dim[N_DIM_STRUCT];
    for (int i = 0; i < N_DIM_STRUCT; ++i) {
        if (DIM_STRUCTS[i].dim == DIM_NETHER) {
            d_dim[i] = a.nether
                ? nearest_dim_struct(a.nether, nullptr, a.seed, DIM_STRUCTS[i].type,
                                     bx / 8, bz / 8)
                : NOT_FOUND;
        } else {
            d_dim[i] = a.d_end_city;       // same for every point in the seed
        }
    }

    ss << biome_id << ',' << (bname ? bname : "unknown") << ','
       << sample_y << ',' << (isSlimeChunk(g->seed, bx >> 4, bz >> 4) ? 1 : 0);
    for (int i = 0; i < 6; ++i) ss << ',' << (long long)np[i];

    ss << std::fixed << std::setprecision(1)
       << ',' << d_tree
       << ',' << min_over_set(per_type, PASSIVE_SET, N_PASSIVE)
       << ',' << min_over_set(per_type, HOSTILE_SET, N_HOSTILE);
    for (int i = 0; i < N_STRUCT; ++i) ss << ',' << per_type[i];
    for (int i = 0; i < N_DIM_STRUCT; ++i) ss << ',' << d_dim[i];
    ss << '\n';
}

static std::string feature_header()
{
    std::string h = "biome_id,biome_name,surface_y,is_slime_chunk";
    for (int i = 0; i < 6; ++i) { h += ','; h += CLIMATE_COLS[i]; }
    h += ",dist_tree_biome,dist_passive_structure,dist_hostile_structure";
    for (int i = 0; i < N_STRUCT; ++i) { h += ','; h += STRUCT_TYPES[i].col; }
    for (int i = 0; i < N_DIM_STRUCT; ++i) { h += ','; h += DIM_STRUCTS[i].col; }
    return h;
}

// ===========================================================================
// Area analysis
// ===========================================================================
//
// Every sample row is measured from its CHUNK CENTRE, spawn rows included.
// Measuring spawn from its true position leaks the sub-chunk offset as a
// near-perfect group label; see README. True spawn is kept in exact_x/exact_z.
static int analyze_area(Generator* g, SurfaceNoise* sn, uint64_t seed,
                        int center_cx, int center_cz, int origin_bx, int origin_bz,
                        Generator* nether, Generator* end_g, const SurfaceNoise* end_sn,
                        float d_end_city,
                        const std::string& row_prefix, std::stringstream& ss,
                        const std::string& sample_prefix, std::stringstream& sample_ss)
{
    AreaCtx a;
    a.nether = nether;
    a.end = end_g;
    a.end_sn = end_sn;
    a.seed = seed;
    a.d_end_city = d_end_city;

    // --- Coarse biome map for the tree-distance feature ---
    Range r;
    r.scale = BIOME_MAP_SCALE;
    r.x = center_cx - SPAWN_CHUNK_RADIUS - MAP_HALO;
    r.z = center_cz - SPAWN_CHUNK_RADIUS - MAP_HALO;
    r.sx = MAP_SIZE; r.sz = MAP_SIZE;
    r.y = BIOME_MAP_Y; r.sy = 1;

    int* cache = allocCache(g, r);
    if (!cache) return 0;
    genBiomes(g, cache, r);

    a.map_x0 = r.x; a.map_z0 = r.z;
    a.tree_mask.assign((size_t)MAP_SIZE * MAP_SIZE, 0);
    for (size_t i = 0; i < a.tree_mask.size(); ++i) {
        const int b = cache[i];
        if (b >= 0 && b < (int)g_is_tree_biome.size()) a.tree_mask[i] = g_is_tree_biome[b];
    }
    free(cache);

    // --- Structure candidates, gathered once for the whole area ---
    const int cbx = center_cx * 16 + 8, cbz = center_cz * 16 + 8;
    for (int i = 0; i < N_STRUCT; ++i) {
        if (STRUCT_TYPES[i].type == Mineshaft) collect_mineshafts(seed, cbx, cbz, a.hits[i]);
        else collect_type(g, seed, STRUCT_TYPES[i].type, cbx, cbz, a.hits[i]);
    }

    // --- The area's own sample point ---
    sample_ss << sample_prefix;
    emit_features(sample_ss, g, sn, a, origin_bx, origin_bz);

    // --- Per-chunk grid ---
    int rows = 0;
    for (int cx = center_cx - SPAWN_CHUNK_RADIUS; cx <= center_cx + SPAWN_CHUNK_RADIUS; ++cx) {
        for (int cz = center_cz - SPAWN_CHUNK_RADIUS; cz <= center_cz + SPAWN_CHUNK_RADIUS; ++cz) {
            ss << row_prefix << cx << ',' << cz << ',';
            emit_features(ss, g, sn, a, cx * 16 + 8, cz * 16 + 8);
            rows++;
        }
    }
    return rows;
}

static void analyze_seed(uint64_t seed, std::string& out_spawn, std::string& out_control,
                         std::string& out_sample, int& rows_generated)
{
    Generator g;
    setupGenerator(&g, MC_VERSION, 0);
    applySeed(&g, DIM_OVERWORLD, seed);

    SurfaceNoise sn;
    initSurfaceNoise(&sn, DIM_OVERWORLD, seed);

    const Pos spawn = getSpawn(&g);

    // Built once per seed, shared by all areas.
    Generator g_nether, g_end;
    setupGenerator(&g_nether, MC_VERSION, 0);
    applySeed(&g_nether, DIM_NETHER, seed);
    setupGenerator(&g_end, MC_VERSION, 0);
    applySeed(&g_end, DIM_END, seed);

    SurfaceNoise sn_end;
    initSurfaceNoise(&sn_end, DIM_END, seed);

    // You always arrive at the fixed platform, so this is one value per seed.
    const float d_end_city = nearest_dim_struct(&g_end, &sn_end, seed, End_City,
                                                END_SPAWN_X, END_SPAWN_Z);

    // Signed, the way Minecraft displays them, so they fit SQLite's INTEGER.
    std::stringstream spawn_ss, control_ss, sample_ss;
    rows_generated = 0;

    {
        // Chunk centre, so spawn and control share a sub-chunk offset.
        const int sbx = (spawn.x >> 4) * 16 + 8;
        const int sbz = (spawn.z >> 4) * 16 + 8;

        std::stringstream pre, spre;
        pre  << (int64_t)seed << ',' << spawn.x << ',' << spawn.z << ',';
        spre << (int64_t)seed << ",spawn,-1," << sbx << ',' << sbz << ','
             << spawn.x << ',' << spawn.z << ',';
        rows_generated += analyze_area(&g, &sn, seed, spawn.x >> 4, spawn.z >> 4,
                                       sbx, sbz,
                                       &g_nether, &g_end, &sn_end, d_end_city,
                                       pre.str(), spawn_ss, spre.str(), sample_ss);
    }

    std::mt19937_64 rng(seed);
    std::uniform_int_distribution<int> coord_dist(-CONTROL_CHUNK_SPREAD, CONTROL_CHUNK_SPREAD);

    // 'control' is uniform random; 'control_land' is the same draw rejection-
    // sampled onto land, so it is comparable to a spawn point.
    for (int kind = 0; kind < 2; ++kind) {
        const bool land_only = (kind == 1);
        const int n_areas = land_only ? CONTROL_LAND_AREAS_PER_SEED : CONTROL_AREAS_PER_SEED;
        const char* kind_name = land_only ? "control_land" : "control";

        for (int area = 0; area < n_areas; ++area) {
            int ccx = 0, ccz = 0;
            const int tries = land_only ? LAND_REJECT_ATTEMPTS : 32;

            for (int attempt = 0; attempt < tries; ++attempt) {
                ccx = coord_dist(rng);
                ccz = coord_dist(rng);
                const int bx = ccx * 16 + 8, bz = ccz * 16 + 8;

                const long long dx = (long long)bx - spawn.x;
                const long long dz = (long long)bz - spawn.z;
                if (dx * dx + dz * dz <
                    (long long)MIN_CONTROL_SEPARATION * MIN_CONTROL_SEPARATION) continue;

                if (!land_only) break;

                float sy = 64.0f;
                mapApproxHeight(&sy, nullptr, &g, &sn, bx / 4, bz / 4, 1, 1);
                const int b = getBiomeAt(&g, 1, bx, std::clamp((int)sy + 1, -64, 320), bz);
                if (b >= 0 && !is_water_biome(b)) break;
            }
            const int obx = ccx * 16 + 8, obz = ccz * 16 + 8;

            std::stringstream pre, spre;
            pre  << (int64_t)seed << ',' << kind_name << ',' << area << ','
                 << obx << ',' << obz << ',';
            spre << (int64_t)seed << ',' << kind_name << ',' << area << ','
                 << obx << ',' << obz << ',' << obx << ',' << obz << ',';
            rows_generated += analyze_area(&g, &sn, seed, ccx, ccz, obx, obz,
                                           &g_nether, &g_end, &sn_end, d_end_city,
                                           pre.str(), control_ss, spre.str(), sample_ss);
        }
    }

    out_spawn = spawn_ss.str();
    out_control = control_ss.str();
    out_sample = sample_ss.str();
}

// ===========================================================================

int main(int argc, char** argv) {
    int max_seeds = DEFAULT_MAX_SEEDS;
    if (argc > 1) {
        try { max_seeds = std::stoi(argv[1]); }
        catch (const std::exception&) { std::cerr << "Invalid seed count: " << argv[1] << "\n"; return 1; }
        if (max_seeds <= 0) { std::cerr << "Seed count must be positive.\n"; return 1; }
    }

    {
        int max_id = 0;
        for (int i = 0; i < N_TREE_BIOMES; ++i) max_id = std::max(max_id, TREE_BIOMES[i]);
        g_is_tree_biome.assign(max_id + 1, 0);
        int kept = 0;
        for (int i = 0; i < N_TREE_BIOMES; ++i) {
            if (!biomeExists(MC_VERSION, TREE_BIOMES[i])) continue;
            g_is_tree_biome[TREE_BIOMES[i]] = 1;
            kept++;
        }
        std::cout << "Tree-bearing biomes: " << kept << " / " << N_TREE_BIOMES << " in this MC version\n";
    }

    const std::filesystem::path out_dir = OUTPUT_DIR;
    std::error_code ec;
    std::filesystem::create_directories(out_dir, ec);

    std::ofstream csv_spawn(out_dir / "spawn_chunk_biomes.csv");
    std::ofstream csv_control(out_dir / "random_chunk_biomes.csv");
    std::ofstream csv_sample(out_dir / "seed_samples.csv");
    if (!csv_spawn.is_open() || !csv_control.is_open() || !csv_sample.is_open()) {
        std::cerr << "Could not open output files in \"" << out_dir.string() << "\".\n";
        return 1;
    }

    const std::string feat = feature_header();
    csv_spawn   << "seed,spawn_x,spawn_z,chunk_x,chunk_z," << feat << '\n';
    csv_control << "seed,area_kind,area_id,center_x,center_z,chunk_x,chunk_z," << feat << '\n';
    csv_sample  << "seed,sample_type,area_id,x,z,exact_x,exact_z," << feat << '\n';

    std::cout << "Writing CSVs to: " << std::filesystem::absolute(out_dir).string() << "\n"
              << N_STRUCT << " overworld structure types + " << N_DIM_STRUCT
              << " other-dimension + 6 climate params + terrain, "
              << "structure cap " << STRUCT_CAP << "b\n";

    std::vector<uint64_t> seeds(max_seeds);
    std::random_device rd;
    std::mt19937_64 rng(rd());
    std::uniform_int_distribution<uint64_t> dist;
    for (int i = 0; i < max_seeds; ++i) seeds[i] = dist(rng);

    std::cout << "Analyzing " << max_seeds << " seeds ("
              << (1 + CONTROL_AREAS_PER_SEED + CONTROL_LAND_AREAS_PER_SEED)
              << " areas of " << GRID << "x" << GRID
              << ": 1 spawn, " << CONTROL_AREAS_PER_SEED << " uniform control, "
              << CONTROL_LAND_AREAS_PER_SEED << " land control)...\n\n";

    auto start = std::chrono::steady_clock::now();
    auto last_flush = start;

    #pragma omp parallel for schedule(dynamic, 1)
    for (int i = 0; i < max_seeds; ++i) {
        std::string ls, lc, lsam;
        int rows = 0;
        analyze_seed(seeds[i], ls, lc, lsam, rows);

        {
            std::lock_guard<std::mutex> lock(file_mutex);
            csv_spawn << ls;
            csv_control << lc;
            csv_sample << lsam;
            total_rows_written += rows;

            auto now = std::chrono::steady_clock::now();
            if (now - last_flush > std::chrono::seconds(3)) {
                csv_spawn.flush(); csv_control.flush(); csv_sample.flush();
                last_flush = now;
            }
        }

        const int current = ++completed_seeds;
        if (current % 100 == 0 || current == max_seeds) {
            std::lock_guard<std::mutex> lock(io_mutex);
            std::cout << "Seeds Processed: " << current << " / " << max_seeds
                      << " (" << (current * 100 / max_seeds) << "%) | "
                      << total_rows_written.load() << " rows written.\r" << std::flush;
        }
    }

    csv_spawn.flush(); csv_spawn.close();
    csv_control.flush(); csv_control.close();
    csv_sample.flush(); csv_sample.close();

    const double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    std::cout << "\n\nFinished in " << secs << " seconds!\n";
    return 0;
}
