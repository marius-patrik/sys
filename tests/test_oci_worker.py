"""Real OCI sandbox verification on an ephemeral CI Docker engine."""
import os
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workers.oci.broker import execute_python

@unittest.skipUnless(os.getenv("LIVING_TEST_OCI")=="1","requires dedicated Docker test runner")
class OCISandbox(unittest.TestCase):
    def test_sandboxed_execution(self):
        r=execute_python("print(2+2)")
        self.assertEqual(r["exit_code"],0,r["stderr"])
        self.assertEqual(r["stdout"].strip(),"4")
    def test_reject_malformed_source(self):
        with self.assertRaises(ValueError):execute_python("")
        with self.assertRaises(ValueError):execute_python("a"*9000)
    def test_network_is_disabled(self):
        source="import socket\ns=socket.socket()\ntry:\n s.connect(('1.1.1.1',80))\n print('NETWORK_ON')\nexcept OSError:\n print('NETWORK_OFF')"
        r=execute_python(source)
        self.assertEqual(r["exit_code"],0,r["stderr"])
        self.assertIn("NETWORK_OFF",r["stdout"])
if __name__=="__main__":unittest.main()
