import asyncio
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.providers.filevine import FilevineProvider

provider = FilevineProvider()
sample_path = Path(__file__).with_name("filevine_sample.json")

result = asyncio.run(
    provider.sync_cases(
        firm_id="firm-1",
        credentials={"sample_path": str(sample_path)}
    )
)

print("********** Filevine Test Result **********")
print(result)

print("********** Filevine Test Records **********")
print(result.records)

print("********** Filevine Test Result **********")
print(result.next_state)