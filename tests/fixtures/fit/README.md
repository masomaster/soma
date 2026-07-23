# FIT fixtures for cycling power ingest tests

- `minimal_fit.py` — builds a tiny FIT with timestamp + power records (used by tests / regen).
- `ride_power_20min.fit` — generated sample: ~21.7 min ride with a 20-min block at 250 W.

Regenerate:

```bash
.venv/bin/python -c "
from datetime import datetime, timezone
from pathlib import Path
from tests.fixtures.fit.minimal_fit import build_minimal_fit_with_power
w = [200]*50 + [250]*1200 + [200]*50
Path('tests/fixtures/fit/ride_power_20min.fit').write_bytes(
    build_minimal_fit_with_power(start=datetime(2024,6,1,12,tzinfo=timezone.utc), watts=w)
)
"
```
