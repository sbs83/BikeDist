# BikeDist

BikeDist is a Streamlit app that helps compare travel time from a home address to Danish high schools (gymnasier), with map display and routing-based bike time estimates.

## What the project contains

- `dashboard.py` - Streamlit dashboard app
- `build_gymnasier_json.py` - scraper/builder for `gymnasier.json`
- `gymnasier.json` - school dataset with name, address, region, lat, lon
- `environment.yml` - Conda environment definition

## Features

- Home address lookup (Denmark)
- Region auto-detection from postcode
- Fast nearest-school list:
  - Phase 1: Euclidean distance on lat/lon for top-10 candidates
  - Phase 2: Haversine ranking to return top-5 nearest
- Route calculation against OSRM services
- Map visualization with route path

## Requirements

- Conda (Miniconda or Anaconda)
- Internet connection (Nominatim geocoding and OSRM routing)

## Quick start

1. Create environment:

```bash
conda env create -f environment.yml
```

2. Activate environment:

```bash
conda activate bikedist
```

3. Run dashboard:

```bash
streamlit run dashboard.py
```

4. Open in browser:

- http://localhost:8501

## Rebuild school dataset

Run the builder to fetch schools and generate a fresh JSON file:

```bash
python build_gymnasier_json.py --output gymnasier.json
```

Useful options:

```bash
python build_gymnasier_json.py --limit 20
python build_gymnasier_json.py --sleep 1.0
python build_gymnasier_json.py --source-url https://danskegymnasier.dk/find-gymnasier/
```

## Notes on routing

The dashboard supports multiple OSRM presets. If one service is slow or unavailable, switch provider/profile in the "Avancerede indstillinger" section.

## Troubleshooting

- If Streamlit is not found, ensure the Conda env is activated.
- If port 8501 is busy, run:

```bash
streamlit run dashboard.py --server.port 8502
```

- If geocoding/routing fails, retry or switch OSRM service.
- If you get `ModuleNotFoundError: osrm_bike_time`, make sure that module exists in the project or is installed in the active environment.

## License and data

- School list source: danskegymnasier.dk
- Map data and geocoding: OpenStreetMap ecosystem (Nominatim/OSRM)

Use this project for educational/informational purposes. Travel times and distances are estimates.
