Minecraft Big Data Project

REQUIREMENTS:
- Python 3.10 or newer installed on your computer.
- The required libraries: Flask, Pandas, Plotly.

HOW TO RUN:
1. Open a terminal or command prompt in this folder.
2. Install the required libraries by running:
   pip install flask pandas plotly
3. Start the visualizer by running:
   python "Minecraft Data Visualizer.py"
4. Open your web browser and go to: http://127.0.0.1:5000

NOTE ON THE DATA:
The visualizer reads from "minecraft_data.db". If you want to rebuild the database from the raw CSV, run `python Data_to_DB.py`. 