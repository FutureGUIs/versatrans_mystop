import sys, types
from pathlib import Path
# Load the standalone protocol modules without Home Assistant on a development PC.
pkg=types.ModuleType('mystop_test')
pkg.__path__=[str(Path(__file__).parents[1]/'custom_components'/'versatrans_mystop')]
sys.modules['mystop_test']=pkg
