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
constexpr int MAX_SEEDS = 100000; 

const std::string OUTPUT_DIR = "C:/Users/istop/Documents/C488 Final/";
const std::string OUTPUT_CSV = OUTPUT_DIR + "spawn_chunk_biomes.csv";

// Thread safety
std::mutex file_mutex;
std::mutex io_mutex;
std::atomic<int> completed_seeds{0};
std::atomic<long long> total_rows_written{0};

void analyze_seed(uint64_t seed, std::string& out_csv, int& rows_generated) {
    Generator g;
    setupGenerator(&g, MC_VERSION, 0);
    applySeed(&g, DIM_OVERWORLD, seed);

    Pos spawn = getSpawn(&g);

    int spawn_chunk_x = spawn.x >> 4;
    int spawn_chunk_z = spawn.z >> 4;

    std::stringstream csv_ss;
    rows_generated = 0;

    for (int cx = spawn_chunk_x - SPAWN_CHUNK_RADIUS; cx <= spawn_chunk_x + SPAWN_CHUNK_RADIUS; ++cx) {
        for (int cz = spawn_chunk_z - SPAWN_CHUNK_RADIUS; cz <= spawn_chunk_z + SPAWN_CHUNK_RADIUS; ++cz) {
            int biome_id = getBiomeAt(&g, 1, cx * 16 + 8, 64, cz * 16 + 8);
            if (biome_id < 0) continue;

            const char* name_cstr = biome2str(MC_VERSION, biome_id);
            std::string name = (name_cstr != nullptr) ? name_cstr : "unknown";

            csv_ss << seed << ','
                   << spawn.x << ',' << spawn.z << ','
                   << cx << ',' << cz << ','
                   << biome_id << ',' << name << '\n';
            rows_generated++;
        }
    }
    out_csv = csv_ss.str();
}

int main() {
    std::error_code ec;
    std::filesystem::create_directories(OUTPUT_DIR, ec);

    std::ofstream csv(OUTPUT_CSV);
    if (!csv.is_open()) {
        std::cerr << "Could not open output file. Check permissions.\n";
        return 1;
    }

    csv << "seed,spawn_x,spawn_z,chunk_x,chunk_z,biome_id,biome_name\n";

    // Pre-generate all 100,000 random seeds
    std::vector<uint64_t> seeds(MAX_SEEDS);
    std::random_device rd;
    std::mt19937_64 rng(rd());
    std::uniform_int_distribution<uint64_t> dist;
    for (int i = 0; i < MAX_SEEDS; ++i) {
        seeds[i] = dist(rng);
    }

    std::cout << "Analyzing " << MAX_SEEDS << " seeds using OpenMP multi-threading...\n";
    std::cout << "This will generate approximately 36.1 million rows (~2.4 GB).\n\n";

    auto start = std::chrono::steady_clock::now();
    auto last_flush = std::chrono::steady_clock::now();

    #pragma omp parallel for schedule(dynamic, 10)
    for (int i = 0; i < MAX_SEEDS; ++i) {
        std::string local_csv;
        int rows = 0;
        analyze_seed(seeds[i], local_csv, rows);

        // Write to file safely
        {
            std::lock_guard<std::mutex> lock(file_mutex);
            csv << local_csv;
            total_rows_written += rows;

            auto now = std::chrono::steady_clock::now();
            if (now - last_flush > std::chrono::seconds(3)) {
                csv.flush();
                last_flush = now;
            }
        }

        // Update console progress every 1% (every 1000 seeds)
        int current = ++completed_seeds;
        if (current % 1000 == 0 || current == MAX_SEEDS) {
            std::lock_guard<std::mutex> lock(io_mutex);
            std::cout << "Seeds Processed: " << current << " / " << MAX_SEEDS 
                      << " (" << (current * 100 / MAX_SEEDS) << "%) | "
                      << total_rows_written.load() << " rows written.\r" << std::flush;
        }
    }

    csv.flush();
    csv.close();

    auto end = std::chrono::steady_clock::now();
    double secs = std::chrono::duration<double>(end - start).count();

    std::cout << "\n\nFinished!\n";
    std::cout << "Total seeds: " << MAX_SEEDS << "\n";
    std::cout << "Total rows written: " << total_rows_written.load() << "\n";
    std::cout << "Time taken: " << std::fixed << std::setprecision(2) << secs << " seconds.\n";
    if(secs > 0) std::cout << "Speed: " << (MAX_SEEDS / secs) << " seeds/second.\n";
    std::cout << "CSV written to: " << OUTPUT_CSV << "\n";

    return 0;
}