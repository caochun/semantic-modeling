"""Workspace paths shared by QA commands; source documents stay local."""
import os
from pathlib import Path

ROOT = Path(os.environ.get('QA_PROJECT_ROOT', Path(__file__).resolve().parents[1])).resolve()
DATA = ROOT / 'data/qa'
RUNS = ROOT / '.semantic/qa/runs'
# Historical extraction is an input, not part of the versioned QA release.
SOURCE = ROOT / '.semantic/qa_operations_20261008'
FOCUSED = DATA / 'legacy/focused_qa.json'
TEMPLATE = Path(__file__).with_name('templates') / 'candidates.html'
