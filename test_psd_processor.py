import unittest
from unittest.mock import MagicMock, patch, mock_open
import sys
import os
from pathlib import Path
import tempfile
import shutil
import zipfile

# Mock psd_tools before importing the module
sys.modules['psd_tools'] = MagicMock()

# Import the module under test
# We need to add the directory to sys.path
sys.path.append(r'j:\Home\Projects\Development\Sources\photoshop')
import psd_processor

class TestPSDProcessor(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.input_dir = Path(self.test_dir) / "input"
        self.output_dir = Path(self.test_dir) / "output"
        self.input_dir.mkdir()
        self.output_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_sanitize_filename(self):
        self.assertEqual(psd_processor._sanitize_filename_worker("test<file>name"), "test_file_name")
        self.assertEqual(psd_processor._sanitize_filename_worker("layer/1"), "layer_1")
        self.assertEqual(psd_processor._sanitize_filename_worker("valid_name"), "valid_name")

    @patch('psd_processor.PSDImage')
    def test_extract_layers_recursive(self, mock_psd_image):
        # Setup mock PSD structure
        mock_layer1 = MagicMock()
        mock_layer1.name = "Layer 1"
        mock_layer1.visible = True
        mock_layer1.is_group.return_value = False
        mock_layer1.topil.return_value = MagicMock() # Mock image
        
        mock_layer2 = MagicMock()
        mock_layer2.name = "Layer 2"
        mock_layer2.visible = True
        mock_layer2.is_group.return_value = False
        mock_layer2.topil.return_value = MagicMock()
        
        mock_layer3 = MagicMock()
        mock_layer3.name = "Layer 3"
        mock_layer3.visible = False
        mock_layer3.is_group.return_value = False
        
        mock_group = MagicMock()
        mock_group.name = "Group 1"
        mock_group.is_group.return_value = True
        mock_group.__iter__.return_value = [mock_layer2, mock_layer3]
        
        mock_psd = MagicMock()
        mock_psd.__iter__.return_value = [mock_layer1, mock_group]
        mock_psd_image.open.return_value = mock_psd
        
        # Run extraction
        psd_path = self.input_dir / "test.psd"
        layer_dir = self.output_dir / "test_layers"
        
        count = psd_processor.extract_layers_from_psd(psd_path, layer_dir)
        
        # Verify
        self.assertEqual(count, 2) # Layer 1 and Layer 2
        mock_layer1.topil.return_value.save.assert_called()
        mock_layer2.topil.return_value.save.assert_called()
        self.assertTrue((layer_dir / "Group 1").exists())

    def test_process_file_worker_logic(self):
        # Test the worker logic without actual multiprocessing
        
        psd_path = self.input_dir / "test.psd"
        with open(psd_path, "wb") as f:
            f.write(b"fake psd content")
            
        shared_hashes = {}
        shared_counters = {}
        shared_processed = {}
        lock = MagicMock() # Dummy lock
        
        # 1. Test new file
        with patch('psd_processor.extract_layers_from_psd') as mock_extract:
            psd_processor.process_file_worker(
                psd_path, 
                self.input_dir, 
                self.output_dir, 
                False, # no_copy=False
                shared_hashes, 
                shared_counters, 
                shared_processed, 
                lock
            )
            
            # Verify zip exists
            zip_path = self.output_dir / "test_layers.zip"
            self.assertTrue(zip_path.exists())
            
            # Verify layer directory is gone
            layer_dir = self.output_dir / "test_layers"
            self.assertFalse(layer_dir.exists())
            
            # Verify PSD is inside zip
            with zipfile.ZipFile(zip_path, 'r') as z:
                self.assertIn("test.psd", z.namelist())
            
            self.assertIn("test", shared_counters)
            self.assertEqual(shared_counters["test"], 0)
            mock_extract.assert_called()

        # 2. Test duplicate file (same content)
        # Reset output
        shutil.rmtree(self.output_dir)
        self.output_dir.mkdir()
        
        # Pre-populate hash
        import hashlib
        sha = hashlib.sha256()
        sha.update(b"fake psd content")
        actual_hash = sha.hexdigest()
        
        shared_hashes[actual_hash] = "existing.psd"
        
        with patch('psd_processor.extract_layers_from_psd') as mock_extract:
            psd_processor.process_file_worker(
                psd_path, 
                self.input_dir, 
                self.output_dir, 
                False, 
                shared_hashes, 
                shared_counters, 
                shared_processed, 
                lock
            )
            
            # Should NOT copy or extract
            self.assertFalse((self.output_dir / "test.psd").exists())
            mock_extract.assert_not_called()

    def test_no_copy_flag(self):
        psd_path = self.input_dir / "test.psd"
        with open(psd_path, "wb") as f:
            f.write(b"fake psd content")
            
        shared_hashes = {}
        shared_counters = {}
        shared_processed = {}
        lock = MagicMock()
        
        with patch('psd_processor.extract_layers_from_psd') as mock_extract:
            psd_processor.process_file_worker(
                psd_path, 
                self.input_dir, 
                self.output_dir, 
                True, # no_copy=True
                shared_hashes, 
                shared_counters, 
                shared_processed, 
                lock
            )
            
            # Should NOT copy file to output root
            self.assertFalse((self.output_dir / "test.psd").exists())
            
            # Verify zip exists
            zip_path = self.output_dir / "test_layers.zip"
            self.assertTrue(zip_path.exists())
            
            # Verify PSD is NOT inside zip
            with zipfile.ZipFile(zip_path, 'r') as z:
                self.assertNotIn("test.psd", z.namelist())

            # BUT should extract layers
            mock_extract.assert_called()

if __name__ == '__main__':
    unittest.main()