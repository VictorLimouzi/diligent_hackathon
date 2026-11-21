"""Standard-library robustness experiments; never change engine decisions."""
import sys

# Keep runs from writing bytecode into the read-only engine tree.
sys.dont_write_bytecode = True
