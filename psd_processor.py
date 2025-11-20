#!/usr/bin/env python3
"""
PSD Layer Extraction Tool

Recursively scans for Photoshop (PSD) files, copies each to an output directory
using SHA256 to avoid duplicates (renaming if hashes differ), creates a folder 
per PSD, extracts all layers as PNG (layer1.png, layer2.png, ...), logs 
conversions, and allows resuming from the last scanned position for incremental runs.
"""

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, Set, Optional

try:
    from psd_tools import PSDImage
except ImportError:
    print("Error: psd-tools library not found. Please install it with: pip install psd-tools")
    sys.exit(1)


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('psd_processor.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class PSDProcessor:
    """Main class for processing PSD files."""
    
    def __init__(self, input_dir: str, output_dir: str, state_file: str = "psd_state.json"):
        """
        Initialize the PSD processor.
        
        Args:
            input_dir: Directory to scan for PSD files
            output_dir: Directory to output processed files
            state_file: JSON file to track processing state for resume capability
        """
        self.input_dir = Path(input_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.state_file = Path(state_file).resolve()
        
        # Create output directory if it doesn't exist
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # State tracking
        self.processed_files: Set[str] = set()
        self.file_hashes: Dict[str, str] = {}  # hash -> output_filename
        self.name_counters: Dict[str, int] = {}  # base_name -> counter for incremental naming
        
        # Load previous state if exists
        self._load_state()
        
    def _load_state(self):
        """Load processing state from JSON file."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    self.processed_files = set(state.get('processed_files', []))
                    self.file_hashes = state.get('file_hashes', {})
                    self.name_counters = state.get('name_counters', {})
                logger.info(f"Loaded state: {len(self.processed_files)} previously processed files")
            except Exception as e:
                logger.warning(f"Failed to load state file: {e}")
                
    def _save_state(self):
        """Save processing state to JSON file."""
        try:
            state = {
                'processed_files': list(self.processed_files),
                'file_hashes': self.file_hashes,
                'name_counters': self.name_counters
            }
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
            logger.debug("State saved successfully")
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
            
    def _calculate_sha256(self, file_path: Path) -> str:
        """
        Calculate SHA256 hash of a file.
        
        Args:
            file_path: Path to the file
            
        Returns:
            Hexadecimal SHA256 hash string
        """
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            # Read in chunks to handle large files
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    
    def _find_psd_files(self) -> list:
        """
        Recursively find all PSD files in input directory.
        
        Returns:
            List of Path objects for found PSD files
        """
        psd_files = []
        for root, dirs, files in os.walk(self.input_dir):
            for file in files:
                if file.lower().endswith('.psd'):
                    psd_files.append(Path(root) / file)
        return psd_files
    
    def _get_output_name(self, file_hash: str, original_name: str) -> str:
        """
        Generate output filename based on hash and original name.
        Uses incremental suffixes (file-1.psd, file-2.psd) when files have
        the same name but different content.
        
        Args:
            file_hash: SHA256 hash of the file
            original_name: Original filename
            
        Returns:
            Output filename
        """
        # Check if we've seen this exact hash before (true duplicate)
        if file_hash in self.file_hashes:
            existing_name = self.file_hashes[file_hash]
            logger.debug(f"Duplicate content detected: {original_name} matches existing {existing_name}")
            return existing_name
        
        # Get base name without extension
        name_without_ext = Path(original_name).stem
        
        # Check if this base name has been used before
        if name_without_ext not in self.name_counters:
            # First time seeing this name
            self.name_counters[name_without_ext] = 0
            output_name = f"{name_without_ext}.psd"
        else:
            # Name exists, increment counter
            self.name_counters[name_without_ext] += 1
            counter = self.name_counters[name_without_ext]
            output_name = f"{name_without_ext}-{counter}.psd"
        
        # Ensure we don't overwrite existing files (safety check for external additions)
        output_path = self.output_dir / output_name
        while output_path.exists() and output_name not in self.file_hashes.values():
            # File exists but not in our tracking - external file, skip to next number
            self.name_counters[name_without_ext] += 1
            counter = self.name_counters[name_without_ext]
            output_name = f"{name_without_ext}-{counter}.psd"
            output_path = self.output_dir / output_name
        
        return output_name
    
    def _extract_layers(self, psd_path: Path, layer_dir: Path) -> int:
        """
        Extract all layers from a PSD file as PNG images.
        
        Args:
            psd_path: Path to the PSD file
            layer_dir: Directory to save layer images
            
        Returns:
            Number of layers extracted
        """
        try:
            psd = PSDImage.open(psd_path)
            layer_count = 0
            
            # Create layer directory
            layer_dir.mkdir(parents=True, exist_ok=True)
            
            # Extract each layer
            for i, layer in enumerate(psd.descendants(), start=1):
                if hasattr(layer, 'topil') and layer.visible:
                    try:
                        layer_image = layer.topil()
                        if layer_image:
                            layer_filename = f"layer{i}.png"
                            layer_path = layer_dir / layer_filename
                            layer_image.save(layer_path, 'PNG')
                            layer_count += 1
                            logger.debug(f"Extracted layer {i}: {layer.name if hasattr(layer, 'name') else 'unnamed'}")
                    except Exception as e:
                        logger.warning(f"Failed to extract layer {i}: {e}")
            
            # Also save composite image
            try:
                composite = psd.topil()
                if composite:
                    composite_path = layer_dir / "composite.png"
                    composite.save(composite_path, 'PNG')
                    logger.debug("Saved composite image")
            except Exception as e:
                logger.warning(f"Failed to save composite image: {e}")
            
            return layer_count
            
        except Exception as e:
            logger.error(f"Failed to extract layers from {psd_path}: {e}")
            return 0
    
    def process_file(self, psd_path: Path) -> bool:
        """
        Process a single PSD file.
        
        Args:
            psd_path: Path to the PSD file
            
        Returns:
            True if processing was successful, False otherwise
        """
        # Convert to string for consistent state tracking
        file_key = str(psd_path.resolve())
        
        # Skip if already processed
        if file_key in self.processed_files:
            logger.info(f"Skipping already processed file: {psd_path.name}")
            return True
        
        try:
            logger.info(f"Processing: {psd_path}")
            
            # Calculate hash
            file_hash = self._calculate_sha256(psd_path)
            logger.debug(f"SHA256: {file_hash}")
            
            # Determine output filename
            output_name = self._get_output_name(file_hash, psd_path.name)
            output_path = self.output_dir / output_name
            
            # Check if this is a duplicate (same hash)
            if file_hash in self.file_hashes:
                logger.info(f"Duplicate detected (same hash): {psd_path.name}")
                logger.info(f"Original file: {self.file_hashes[file_hash]}")
            else:
                # Copy PSD file to output directory
                shutil.copy2(psd_path, output_path)
                logger.info(f"Copied to: {output_path}")
                
                # Store hash mapping
                self.file_hashes[file_hash] = output_name
            
            # Create layer extraction directory
            layer_dir_name = Path(output_name).stem + "_layers"
            layer_dir = self.output_dir / layer_dir_name
            
            # Extract layers
            logger.info(f"Extracting layers to: {layer_dir}")
            layer_count = self._extract_layers(psd_path, layer_dir)
            logger.info(f"Extracted {layer_count} layers")
            
            # Mark as processed
            self.processed_files.add(file_key)
            
            # Save state after each file
            self._save_state()
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to process {psd_path}: {e}")
            return False
    
    def process_all(self):
        """Process all PSD files in the input directory."""
        logger.info(f"Scanning for PSD files in: {self.input_dir}")
        
        psd_files = self._find_psd_files()
        total_files = len(psd_files)
        
        if total_files == 0:
            logger.warning("No PSD files found")
            return
        
        logger.info(f"Found {total_files} PSD file(s)")
        
        success_count = 0
        failed_count = 0
        skipped_count = 0
        
        for i, psd_path in enumerate(psd_files, start=1):
            logger.info(f"\n[{i}/{total_files}] Processing file...")
            
            if str(psd_path.resolve()) in self.processed_files:
                skipped_count += 1
            
            if self.process_file(psd_path):
                success_count += 1
            else:
                failed_count += 1
        
        # Final summary
        logger.info("\n" + "="*50)
        logger.info("Processing Summary:")
        logger.info(f"Total files found: {total_files}")
        logger.info(f"Successfully processed: {success_count}")
        logger.info(f"Already processed (skipped): {skipped_count}")
        logger.info(f"Failed: {failed_count}")
        logger.info("="*50)


def main():
    """Main entry point for the application."""
    parser = argparse.ArgumentParser(
        description='PSD Layer Extraction Tool - Recursively scan and extract layers from PSD files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /path/to/psd/files /path/to/output
  %(prog)s ./input ./output --state-file my_state.json
        """
    )
    
    parser.add_argument(
        'input_dir',
        help='Input directory to scan for PSD files (recursive)'
    )
    
    parser.add_argument(
        'output_dir',
        help='Output directory for processed files and extracted layers'
    )
    
    parser.add_argument(
        '--state-file',
        default='psd_state.json',
        help='State file for resume capability (default: psd_state.json)'
    )
    
    parser.add_argument(
        '--reset',
        action='store_true',
        help='Reset state and reprocess all files'
    )
    
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose debug logging'
    )
    
    args = parser.parse_args()
    
    # Set log level
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    
    # Validate input directory
    input_path = Path(args.input_dir)
    if not input_path.exists():
        logger.error(f"Input directory does not exist: {args.input_dir}")
        sys.exit(1)
    
    if not input_path.is_dir():
        logger.error(f"Input path is not a directory: {args.input_dir}")
        sys.exit(1)
    
    # Reset state if requested
    if args.reset:
        state_file = Path(args.state_file)
        if state_file.exists():
            state_file.unlink()
            logger.info("State file reset")
    
    # Create processor and run
    processor = PSDProcessor(args.input_dir, args.output_dir, args.state_file)
    processor.process_all()


if __name__ == "__main__":
    main()
