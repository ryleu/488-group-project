#include <iostream>
#include <fstream>
#include <random>
#include <map>
#include <string>
#include <chrono>
#include <iomanip>
#include <filesystem>
#include <vector>
#include <mutex>
#include <atomic>
#include <sstream>
#include <algorithm>

extern "C" {
    #include "generator.h"
    #include "finders.h"
    #include "biomes.h"
    #include "util.h"
}

// Configuration
constexpr int MC_VERSION = MC_1_21;
constexpr int SPAWN_CHUNK_RADIUS = 9;

// EXACT SEED LIMIT
constexpr int MAX_SEEDS = 5000; 

const std::string OUTPUT_DIR = "C:/Users/istop/Documents/C488 Final/";
const std::string OUTPUT_SPAWN_CSV = OUTPUT_DIR + "spawn_chunk_biomes.csv";
const std::string OUTPUT_RAND_CSV = OUTPUT_DIR + "random_chunk_biomes.csv";

// Thread safety
std::mutex file_mutex;
std::mutex io_mutex;
std::atomic<int> completed_seeds{0};
std::atomic<long long> total_rows_written{0};

void analyze_seed(uint64_t seed, std::string& out_spawn_csv, std::string& out_rand_csv, int& rows_generated) {
    Generator g;
    setupGenerator(&g, MC_VERSION, 0);
    applySeed(&g, DIM_OVERWORLD, seed);

    // Fixes the Dripstone Cave issue by getting the actual surface height
    SurfaceNoise sn;
    initSurfaceNoise(&sn, DIM_OVERWORLD, seed);

    Pos spawn = getSpawn(&g);

    int spawn_chunk_x = spawn.x >> 4;
    int spawn_chunk_z = spawn.z >> 4;

    std::stringstream spawn_ss;
    std::stringstream rand_ss;

    std::mt19937_64 rng(seed);
    // Generates random coordinates roughly +/- 20,000 blocks from origin
    std::uniform_int_distribution<int> coord_dist(-1250, 1250); 

    rows_generated = 0;

    for (int cx = spawn_chunk_x - SPAWN_CHUNK_RADIUS; cx <= spawn_chunk_x + SPAWN_CHUNK_RADIUS; ++cx) {
        for (int cz = spawn_chunk_z - SPAWN_CHUNK_RADIUS; cz <= spawn_chunk_z + SPAWN_CHUNK_RADIUS; ++cz) {
            
            // --- 1. SPAWN CHUNK SAMPLING ---
            int bx = cx * 16 + 8; 
            int bz = cz * 16 + 8; 
            float surface_y = 64.0f; 
            mapApproxHeight(&surface_y, nullptr, &g, &sn, bx / 4, bz / 4, 1, 1);
            int sample_y = std::clamp((int)surface_y + 1, -64, 320);
            
            int spawn_biome_id = getBiomeAt(&g, 1, bx, sample_y, bz);
            if (spawn_biome_id >= 0) {
                const char* name_cstr = biome2str(MC_VERSION, spawn_biome_id);
                spawn_ss << seed << ',' << spawn.x << ',' << spawn.z << ',' << cx << ',' << cz << ',' << spawn_biome_id << ',' << (name_cstr ? name_cstr : "unknown") << '\n';
                rows_generated++;
            }

            // --- 2. RANDOM CHUNK SAMPLING ---
            int rx = coord_dist(rng);
            int rz = coord_dist(rng);
            int rbx = rx * 16 + 8;
            int rbz = rz * 16 + 8;
            
            float rand_surface_y = 64.0f;
            mapApproxHeight(&rand_surface_y, nullptr, &g, &sn, rbx / 4, rbz / 4, 1, 1);
            int rand_y = std::clamp((int)rand_surface_y + 1, -64, 320);

            int rand_biome_id = getBiomeAt(&g, 1, rbx, rand_y, rbz);
            if (rand_biome_id >= 0) {
                const char* name_cstr = biome2str(MC_VERSION, rand_biome_id);
                rand_ss << seed << ',' << rx << ',' << rz << ',' << rand_biome_id << ',' << (name_cstr ? name_cstr : "unknown") << '\n';
            }
        }
    }
    out_spawn_csv = spawn_ss.str();
    out_rand_csv = rand_ss.str();
}

int main() {
    std::error_code ec;
    std::filesystem::create_directories(OUTPUT_DIR, ec);

    std::ofstream csv_spawn(OUTPUT_SPAWN_CSV);
    std::ofstream csv_rand(OUTPUT_RAND_CSV);

    if (!csv_spawn.is_open() || !csv_rand.is_open()) {
        std::cerr << "Could not open output files. Check permissions.\n";
        return 1;
    }

    csv_spawn << "seed,spawn_x,spawn_z,chunk_x,chunk_z,biome_id,biome_name\n";
    csv_rand << "seed,chunk_x,chunk_z,biome_id,biome_name\n";

    std::vector<uint64_t> seeds(MAX_SEEDS);
    std::random_device rd;
    std::mt19937_64 rng(rd());
    std::uniform_int_distribution<uint64_t> dist;
    for (int i = 0; i < MAX_SEEDS; ++i) {
        seeds[i] = dist(rng);
    }

    std::cout << "Analyzing " << MAX_SEEDS << " seeds (Spawn vs Random)...\n\n";

    auto start = std::chrono::steady_clock::now();
    auto last_flush = std::chrono::steady_clock::now();

    #pragma omp parallel for schedule(dynamic, 10)
    for (int i = 0; i < MAX_SEEDS; ++i) {
        std::string local_spawn, local_rand;
        int rows = 0;
        analyze_seed(seeds[i], local_spawn, local_rand, rows);

        {
            std::lock_guard<std::mutex> lock(file_mutex);
            csv_spawn << local_spawn;
            csv_rand << local_rand;
            total_rows_written += rows;

            auto now = std::chrono::steady_clock::now();
            if (now - last_flush > std::chrono::seconds(3)) {
                csv_spawn.flush();
                csv_rand.flush();
                last_flush = now;
            }
        }

        int current = ++completed_seeds;
        if (current % 1000 == 0 || current == MAX_SEEDS) {
            std::lock_guard<std::mutex> lock(io_mutex);
            std::cout << "Seeds Processed: " << current << " / " << MAX_SEEDS 
                      << " (" << (current * 100 / MAX_SEEDS) << "%) | "
                      << total_rows_written.load() << " rows written.\r" << std::flush;
        }
    }

    csv_spawn.flush(); csv_spawn.close();
    csv_rand.flush(); csv_rand.close();

    double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    std::cout << "\n\nFinished in " << secs << " seconds!\n";

    return 0;
}