"""
Root conftest. Its only job is to make sure the project root is on
sys.path so `from src...` imports work no matter where pytest is invoked
from (repo root, tests/, CI runner, etc).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
