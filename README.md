# PSD Layer Extraction Tool

A Python application that recursively scans for Photoshop (PSD) files, copies each to an output directory using SHA256 to avoid duplicates, creates a folder per PSD, extracts all layers as PNG files, logs conversions, and allows resuming from the last scanned position for incremental runs.

## Features

- 🔍 **Recursive Scanning**: Automatically finds all PSD files in a directory tree
- 🔐 **SHA256 Deduplication**: Uses file hashing to detect and skip duplicate files
- 📁 **Organized Output**: Creates a dedicated folder for each PSD file's extracted layers
- 🖼️ **Layer Extraction**: Extracts all visible layers as individual PNG files (layer1.png, layer2.png, ...)
- 📝 **Comprehensive Logging**: Logs all operations to console and log file
- ⏸️ **Resume Capability**: Tracks processing state to resume from where it left off
- 🔄 **Incremental Processing**: Skip already processed files on subsequent runs

## Installation

### Prerequisites

- Python 3.7 or higher
- pip (Python package installer)

### Install Dependencies

```bash
pip install -r requirements.txt
```

This will install:
- `psd-tools`: For reading and parsing PSD files
- `Pillow`: For image processing and PNG export

## Usage

### Basic Usage

```bash
python psd_processor.py <input_directory> <output_directory>
```

### Examples

```bash
# Process all PSD files in the current directory
python psd_processor.py ./psd_files ./output

# Process files with a custom state file
python psd_processor.py ./input ./output --state-file my_progress.json

# Reset and reprocess all files
python psd_processor.py ./input ./output --reset

# Enable verbose debug logging
python psd_processor.py ./input ./output --verbose
```

### Command-line Arguments

- `input_dir`: Input directory to scan for PSD files (required)
- `output_dir`: Output directory for processed files and extracted layers (required)
- `--state-file`: State file for resume capability (default: psd_state.json)
- `--reset`: Reset state and reprocess all files
- `--verbose`: Enable verbose debug logging

## How It Works

1. **Scanning**: The tool recursively scans the input directory for all `.psd` files
2. **Hashing**: Each PSD file is hashed using SHA256 to create a unique fingerprint
3. **Deduplication**: Files with identical hashes are recognized as duplicates and only processed once
4. **Copying**: Each unique PSD file is copied to the output directory with a hash-prefixed name
5. **Layer Extraction**: A dedicated folder is created for each PSD file
6. **PNG Export**: All visible layers are exported as individual PNG files (layer1.png, layer2.png, ...)
7. **State Tracking**: Progress is saved to a JSON file after each processed file
8. **Resume**: On subsequent runs, already processed files are skipped

## Output Structure

```
output/
├── 12abc34d_document1.psd
├── 12abc34d_document1_layers/
│   ├── layer1.png
│   ├── layer2.png
│   ├── layer3.png
│   └── composite.png
├── 56def78e_document2.psd
├── 56def78e_document2_layers/
│   ├── layer1.png
│   ├── layer2.png
│   └── composite.png
└── ...
```

## State File

The state file (`psd_state.json` by default) tracks:
- List of processed file paths
- Hash-to-filename mappings for deduplication

This allows the tool to:
- Resume processing after interruption
- Skip already processed files in incremental runs
- Maintain deduplication across multiple runs

To start fresh, either delete the state file or use the `--reset` flag.

## Logging

The tool creates two types of logs:

1. **Console Output**: Real-time progress and summary information
2. **Log File** (`psd_processor.log`): Detailed logs of all operations

Log entries include:
- Files being processed
- SHA256 hashes calculated
- Duplicate detections
- Layer extraction progress
- Errors and warnings
- Final processing summary

## Error Handling

The tool is designed to be resilient:
- Continues processing even if individual files fail
- Logs errors without stopping the entire batch
- Saves state after each successful file
- Allows resume after crashes or interruptions

## Requirements

See `requirements.txt` for the full list of dependencies.

## License

See LICENSE file for details.