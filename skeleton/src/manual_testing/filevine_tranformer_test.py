import json
from pathlib import Path
import sys
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from src.transformers.filevine_transformer import FilevineTransformer

sample_path = Path("skeleton/src/manual_testing/filevine_sample.json")
records = json.loads(sample_path.read_text(encoding="utf-8"))

transformer = FilevineTransformer()

case = transformer.transform(records[0], firm_id="firm-1")

print("********** Filevine Transformer Test Result **********")
print(case)

print("********** Filevine Transformer Test Normalized Client Name **********")
print(case.normalized_client_name)