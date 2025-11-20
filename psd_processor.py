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
import zipfile

import multiprocessing
from functools import partial
from pathlib import Path
from typing import Dict, Set, Optional, List, Tuple

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
    
    def __init__(self, input_dir: str, output_dir: str, state_file: str = "psd_state.json", no_copy: bool = False, refresh_list: bool = False, update_list_only: bool = False):
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
        self.no_copy = no_copy
        self.refresh_list = refresh_list
        self.update_list_only = update_list_only
        self.file_list_path = self.output_dir / "allFiles.txt"
        
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
        
        # Check if we should use cached list
        if not self.refresh_list and self.file_list_path.exists():
            logger.info(f"Loading file list from cache: {self.file_list_path}")
            try:
                with open(self.file_list_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        path_str = line.strip()
                        if path_str:
                            psd_files.append(Path(path_str))
                logger.info(f"Loaded {len(psd_files)} files from cache")
                return psd_files
            except Exception as e:
                logger.warning(f"Failed to load file list cache: {e}. Rescanning...")
        
        # Perform recursive scan
        logger.info("Scanning directory recursively...")
        for root, dirs, files in os.walk(self.input_dir):
            for file in files:
                if file.lower().endswith('.psd'):
                    psd_files.append(Path(root) / file)
                    
        # Save to cache
        try:
            with open(self.file_list_path, 'w', encoding='utf-8') as f:
                for path in psd_files:
                    f.write(f"{path}\n")
            logger.info(f"Saved {len(psd_files)} files to cache: {self.file_list_path}")
        except Exception as e:
            logger.warning(f"Failed to save file list cache: {e}")
            
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
        
        # Initialize counter if first time seeing this name
        if name_without_ext not in self.name_counters:
            self.name_counters[name_without_ext] = -1  # Will increment to 0 on first use
        
        # Generate output name with appropriate suffix
        while True:
            self.name_counters[name_without_ext] += 1
            counter = self.name_counters[name_without_ext]
            
            if counter == 0:
                output_name = f"{name_without_ext}.psd"
            else:
                output_name = f"{name_without_ext}-{counter}.psd"
            
            # Check if this name is available (not an external file)
            output_path = self.output_dir / output_name
            if not output_path.exists() or output_name in self.file_hashes.values():
                # Name is available (doesn't exist or is one of our tracked files)
                return output_name
            # Otherwise, loop and try next counter value
    
    def _sanitize_filename(self, name: str) -> str:
        """
        Sanitize a string to be safe for use as a filename.
        
        Args:
            name: Input string
            
        Returns:
            Sanitized string
        """
        # Replace invalid characters with underscore
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, '_')
        return name.strip()

    def _extract_layers_recursive(self, layer, output_dir: Path, counter: Dict[str, int]) -> int:
        """
        Recursively extract layers from a PSD layer/group.
        
        Args:
            layer: PSD layer or group object
            output_dir: Directory to save extracted layers
            counter: Dictionary to track layer name counts for uniqueness
            
        Returns:
            Number of layers extracted
        """
        count = 0
        
        # Handle groups/folders
        if layer.is_group():
            # Create subdirectory for the group
            group_name = self._sanitize_filename(layer.name)
            group_dir = output_dir / group_name
            group_dir.mkdir(parents=True, exist_ok=True)
            
            for child in layer:
                count += self._extract_layers_recursive(child, group_dir, counter)
            return count
            
        # Handle normal layers
        if hasattr(layer, 'topil') and layer.visible:
            try:
                layer_image = layer.topil()
                if layer_image:
                    # Generate unique filename based on layer name
                    base_name = self._sanitize_filename(layer.name)
                    if not base_name:
                        base_name = "unnamed_layer"
                        
                    # Ensure uniqueness within this directory
                    if base_name not in counter:
                        counter[base_name] = 0
                    else:
                        counter[base_name] += 1
                        
                    if counter[base_name] == 0:
                        layer_filename = f"{base_name}.png"
                    else:
                        layer_filename = f"{base_name}_{counter[base_name]}.png"
                        
                    layer_path = output_dir / layer_filename
                    layer_image.save(layer_path, 'PNG')
                    count += 1
                    logger.debug(f"Extracted layer: {layer_path.name}")
            except Exception as e:
                logger.warning(f"Failed to extract layer {layer.name}: {e}")
                
        return count

    def _extract_layers(self, psd_path: Path, layer_dir: Path) -> int:
        """
        Extract all layers from a PSD file as PNG images.
        
        Args:
            psd_path: Path to the PSD file
            layer_dir: Directory to save layer images
            
        Returns:
            Number of layers extracted
        """
        return extract_layers_from_psd(psd_path, layer_dir)

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

                # Copy PSD file to output directory unless disabled
                if not self.no_copy:
                    shutil.copy2(psd_path, output_path)
                    logger.info(f"Copied to: {output_path}")
                else:
                    logger.info(f"Skipping copy (no-copy enabled). Output name reserved: {output_name}")
                
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
        """Process all PSD files in the input directory using multiprocessing."""
        logger.info(f"Scanning for PSD files in: {self.input_dir}")
        
        # If update_list_only is set, force refresh
        if self.update_list_only:
            self.refresh_list = True
            
        psd_files = self._find_psd_files()
        total_files = len(psd_files)
        
        if self.update_list_only:
            logger.info("List updated. Exiting as requested by --update-list-only.")
            return
        
        if total_files == 0:
            logger.warning("No PSD files found")
            return
        
        logger.info(f"Found {total_files} PSD file(s)")
        
        # Filter out already processed files
        files_to_process = []
        skipped_count = 0
        
        for psd_path in psd_files:
            if str(psd_path.resolve()) in self.processed_files:
                skipped_count += 1
            else:
                files_to_process.append(psd_path)
                
        logger.info(f"Skipping {skipped_count} already processed files")
        logger.info(f"Processing {len(files_to_process)} files...")
        
        if not files_to_process:
            return

        # Multiprocessing setup
        with multiprocessing.Manager() as manager:
            shared_hashes = manager.dict(self.file_hashes)
            shared_counters = manager.dict(self.name_counters)
            shared_processed = manager.dict() # Not strictly needed for logic, but good for tracking
            lock = manager.Lock()
            
            # Prepare arguments
            func = partial(
                process_file_worker,
                input_dir=self.input_dir,
                output_dir=self.output_dir,
                no_copy=self.no_copy,
                shared_hashes=shared_hashes,
                shared_counters=shared_counters,
                shared_processed=shared_processed,
                lock=lock
            )
            
            # Run pool
            # Use slightly fewer processes than CPU count to leave room for system
            cpu_count = max(1, multiprocessing.cpu_count() - 1)
            logger.info(f"Starting pool with {cpu_count} processes")
            
            success_count = 0
            failed_count = 0
            
            with multiprocessing.Pool(processes=cpu_count) as pool:
                results = []
                for i, psd_path in enumerate(files_to_process, start=1):
                    logger.info(f"Queuing: {psd_path.name}")
                    results.append((psd_path, pool.apply_async(func, (psd_path,))))
                
                # Wait for results and update local state
                for psd_path, res in results:
                    try:
                        if res.get():
                            success_count += 1
                            self.processed_files.add(str(psd_path.resolve()))
                        else:
                            failed_count += 1
                    except Exception as e:
                        logger.error(f"Worker error for {psd_path}: {e}")
                        failed_count += 1
                        
                    # Periodically save state (optional, but good practice)
                    if success_count % 10 == 0:
                        self.file_hashes = dict(shared_hashes)
                        self.name_counters = dict(shared_counters)
                        self._save_state()

            # Final state update
            self.file_hashes = dict(shared_hashes)
            self.name_counters = dict(shared_counters)
            self._save_state()
            
            # Final summary
            logger.info("\n" + "="*50)
            logger.info("Processing Summary:")
            logger.info(f"Total files found: {total_files}")
            logger.info(f"Successfully processed: {success_count}")
            logger.info(f"Already processed (skipped): {skipped_count}")
            logger.info(f"Failed: {failed_count}")
            logger.info("="*50)

def extract_layers_from_psd(psd_path: Path, layer_dir: Path) -> int:
    """
    Standalone function to extract layers (for multiprocessing).
    """
    try:
        psd = PSDImage.open(psd_path)
        layer_count = 0
        
        # Create layer directory
        layer_dir.mkdir(parents=True, exist_ok=True)
        
        # Extract layers recursively
        name_counter = {}
        for layer in psd:
            layer_count += _extract_layers_recursive_worker(layer, layer_dir, name_counter)
        
        # Also save composite image
        try:
            composite = psd.topil()
            if composite:
                composite_path = layer_dir / "composite.png"
                composite.save(composite_path, 'PNG')
                # logger.debug("Saved composite image") 
        except Exception as e:
            print(f"Warning: Failed to save composite image for {psd_path}: {e}")
        
        return layer_count
        
    except Exception as e:
        print(f"Error: Failed to extract layers from {psd_path}: {e}")
        return 0

def _sanitize_filename_worker(name: str) -> str:
    """Worker version of sanitize_filename."""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, '_')
    return name.strip()

def _extract_layers_recursive_worker(layer, output_dir: Path, counter: Dict[str, int]) -> int:
    """Worker version of recursive extraction."""
    count = 0
    
    # Handle groups/folders
    if layer.is_group():
        group_name = _sanitize_filename_worker(layer.name)
        group_dir = output_dir / group_name
        group_dir.mkdir(parents=True, exist_ok=True)
        
        for child in layer:
            count += _extract_layers_recursive_worker(child, group_dir, counter)
        return count
        
    # Handle normal layers
    if hasattr(layer, 'topil') and layer.visible:
        try:
            layer_image = layer.topil()
            if layer_image:
                base_name = _sanitize_filename_worker(layer.name)
                if not base_name:
                    base_name = "unnamed_layer"
                    
                if base_name not in counter:
                    counter[base_name] = 0
                else:
                    counter[base_name] += 1
                    
                if counter[base_name] == 0:
                    layer_filename = f"{base_name}.png"
                else:
                    layer_filename = f"{base_name}_{counter[base_name]}.png"
                    
                layer_path = output_dir / layer_filename
                layer_image.save(layer_path, 'PNG')
                count += 1
        except Exception as e:
            pass # Squelch individual layer errors in worker to avoid log spam
            
    return count

def process_file_worker(
    psd_path: Path, 
    input_dir: Path, 
    output_dir: Path, 
    no_copy: bool,
    shared_hashes: Dict, 
    shared_counters: Dict, 
    shared_processed: Dict,
    lock
) -> bool:
    """
    Worker function to process a single PSD file.
    """
    try:
        # Calculate hash
        sha256_hash = hashlib.sha256()
        with open(psd_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        file_hash = sha256_hash.hexdigest()
        
        output_name = ""
        should_process = False
        
        # Critical section for checking/updating state
        with lock:
            if file_hash in shared_hashes:
                # Duplicate
                output_name = shared_hashes[file_hash]
                # print(f"Duplicate: {psd_path.name} -> {output_name}")
            else:
                # New file
                should_process = True
                
                # Determine name
                original_name = psd_path.name
                name_without_ext = Path(original_name).stem
                
                if name_without_ext not in shared_counters:
                    shared_counters[name_without_ext] = -1
                
                while True:
                    shared_counters[name_without_ext] += 1
                    counter = shared_counters[name_without_ext]
                    
                    if counter == 0:
                        temp_name = f"{name_without_ext}.psd"
                    else:
                        temp_name = f"{name_without_ext}-{counter}.psd"
                    
                    # Check if name is taken by another hash (unlikely but possible in race)
                    # or exists on disk (checked by main, but good to be safe)
                    # In this locked context, we trust shared_counters + shared_hashes
                    output_name = temp_name
                    break
                
                shared_hashes[file_hash] = output_name
        
        # Perform IO operations outside lock
        # output_path = output_dir / output_name # No longer needed as we copy to layer_dir
        
        if should_process:
            # Extract layers
            layer_dir_name = Path(output_name).stem + "_layers"
            layer_dir = output_dir / layer_dir_name
            
            # Create layer directory first
            layer_dir.mkdir(parents=True, exist_ok=True)

            if not no_copy:
                # Copy PSD to layer directory
                shutil.copy2(psd_path, layer_dir / output_name)
            
            extract_layers_from_psd(psd_path, layer_dir)
            
            # Zip the layer directory
            zip_path = output_dir / f"{layer_dir_name}.zip"
            try:
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for root, dirs, files in os.walk(layer_dir):
                        for file in files:
                            file_path = Path(root) / file
                            arcname = file_path.relative_to(layer_dir)
                            zipf.write(file_path, arcname)
                
                # Remove the layer directory after successful zipping
                shutil.rmtree(layer_dir)
                
            except Exception as e:
                print(f"Failed to zip or cleanup {layer_dir}: {e}")
                # Attempt to cleanup partial zip if it exists
                if zip_path.exists():
                    try:
                        zip_path.unlink()
                    except:
                        pass
            
        return True
        
    except Exception as e:
        print(f"Failed to process {psd_path}: {e}")
        return False
    



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
    
    parser.add_argument(
        '--no-copy',
        action='store_true',
        help='Skip copying the source PSD file to the output directory'
    )
    
    parser.add_argument(
        '--refresh-list',
        action='store_true',
        help='Force a rescan of the input directory instead of using cached allFiles.txt'
    )

    parser.add_argument(
        '--update-list-only',
        action='store_true',
        help='Scan and update the file list, then exit without processing files'
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
    processor = PSDProcessor(args.input_dir, args.output_dir, args.state_file, args.no_copy, args.refresh_list, args.update_list_only)
    processor.process_all()


if __name__ == "__main__":
    main()
