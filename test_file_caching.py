import unittest
from unittest.mock import MagicMock, patch, mock_open
import sys
import os
from pathlib import Path
import tempfile
import shutil

# Mock psd_tools before importing the module
sys.modules['psd_tools'] = MagicMock()

# Import the module under test
sys.path.append(r'j:\Home\Projects\Development\Sources\photoshop')
import psd_processor

class TestFileCaching(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.input_dir = Path(self.test_dir) / "input"
        self.output_dir = Path(self.test_dir) / "output"
        self.input_dir.mkdir()
        self.output_dir.mkdir()
        
        # Create some dummy PSD files
        (self.input_dir / "file1.psd").touch()
        (self.input_dir / "file2.psd").touch()
        (self.input_dir / "subdir").mkdir()
        (self.input_dir / "subdir" / "file3.psd").touch()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_cache_creation(self):
        """Test that allFiles.txt is created after scanning."""
        processor = psd_processor.PSDProcessor(
            str(self.input_dir), 
            str(self.output_dir), 
            refresh_list=False
        )
        
        files = processor._find_psd_files()
        
        self.assertEqual(len(files), 3)
        self.assertTrue((self.output_dir / "allFiles.txt").exists())
        
        # Verify content
        with open(self.output_dir / "allFiles.txt", 'r') as f:
            lines = f.readlines()
            self.assertEqual(len(lines), 3)

    def test_read_from_cache(self):
        """Test that files are read from cache if it exists."""
        # Create a fake cache file
        cache_file = self.output_dir / "allFiles.txt"
        fake_path = self.input_dir / "fake.psd"
        with open(cache_file, 'w') as f:
            f.write(str(fake_path) + "\n")
            
        processor = psd_processor.PSDProcessor(
            str(self.input_dir), 
            str(self.output_dir), 
            refresh_list=False
        )
        
        files = processor._find_psd_files()
        
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0], fake_path)

    def test_refresh_list_ignores_cache(self):
        """Test that --refresh-list ignores the existing cache."""
        # Create a fake cache file
        cache_file = self.output_dir / "allFiles.txt"
        fake_path = self.input_dir / "fake.psd"
        with open(cache_file, 'w') as f:
            f.write(str(fake_path) + "\n")
            
        processor = psd_processor.PSDProcessor(
            str(self.input_dir), 
            str(self.output_dir), 
            refresh_list=True
        )
        
        files = processor._find_psd_files()
        
        # Should find the 3 actual files, not the 1 fake file
        self.assertEqual(len(files), 3)
        self.assertNotIn(fake_path, files)
        
        # Cache should be updated
        with open(cache_file, 'r') as f:
            lines = f.readlines()
            self.assertEqual(len(lines), 3)

    def test_update_list_only(self):
        """Test that --update-list-only updates the list and exits without processing."""
        # Create a fake cache file
        cache_file = self.output_dir / "allFiles.txt"
        fake_path = self.input_dir / "fake.psd"
        with open(cache_file, 'w') as f:
            f.write(str(fake_path) + "\n")
            
        processor = psd_processor.PSDProcessor(
            str(self.input_dir), 
            str(self.output_dir), 
            update_list_only=True
        )
        
        # Mock process_file to ensure it's not called
        # We can't easily mock internal methods of the instance we just created without more complex patching,
        # but we can check if output files are created (or rather, NOT created).
        # Since we're using the real class, process_all would normally call _find_psd_files and then process.
        # If update_list_only works, it should return after _find_psd_files.
        
        processor.process_all()
        
        # Check that cache was updated (should have 3 files, not 1 fake)
        with open(cache_file, 'r') as f:
            lines = f.readlines()
            self.assertEqual(len(lines), 3)
            self.assertNotIn(str(fake_path) + "\n", lines)
            
        # Check that NO processing happened (output dir should only have allFiles.txt)
        # Note: psd_state.json might be created if _load_state is called, but no PSDs should be copied/processed.
        # The processor creates output_dir in __init__.
        output_files = list(self.output_dir.iterdir())
        # Filter out allFiles.txt and psd_state.json (if it exists)
        processed_files = [f for f in output_files if f.name not in ["allFiles.txt", "psd_state.json"]]
        self.assertEqual(len(processed_files), 0, f"Found unexpected processed files: {processed_files}")

if __name__ == '__main__':
    unittest.main()
