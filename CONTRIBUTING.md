# Contributing

Use Python 3.11+. Install with `python -m pip install -e .` and run `python -m unittest discover -s tests -v`.

Add regression tests for behavior changes. Keep native operations behind the existing runner boundary and preserve structured JSON results. Do not replace failing checks with success based only on native process exit status.

Use synthetic fixtures only. Never submit vendor code, firmware, datasets, vehicle identities, captures, keys, credentials or native logs. Redact reports before opening an issue. Contributions are accepted under this project's MIT license; submit only material you can license on those terms.

New vehicle write operations require an explicit design for scope validation, fresh identity, immutable inputs, uncertain outcomes and recovery. Automated tests cannot establish physical-vehicle compatibility.
